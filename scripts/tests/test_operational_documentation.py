from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import stat
import sys
import tomllib
from pathlib import Path
from typing import cast

import pytest
from markdown_it import MarkdownIt
from scripts.bounded_process import CommandResult, spawn

ROOT = Path(__file__).resolve().parents[2]
POLICY_GUIDE = "manage-repository-policy.md"
EPOCH = "a" * 64
PREVIOUS_EPOCH = "b" * 64
MANIFEST = "proposal:" + "c" * 32
SYNTHETIC_VALUES = (
    "documentation-workload-witness-not-a-real-credential",
    "documentation-metrics-witness-not-a-real-credential",
)

# This executable records the real shell argv but has no HTTP implementation.
CURL_RECORDER = r"""
import argparse
import json
import os
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
for option in ("disable", "fail-with-body", "silent", "show-error", "get"):
    parser.add_argument("--" + option, action="store_true")
for option in ("request", "data", "output", "dump-header"):
    parser.add_argument("--" + option)
parser.add_argument("--header", action="append", default=[])
parser.add_argument("--data-urlencode", action="append", default=[])
parser.add_argument("url")
args = parser.parse_args()
assert sys.argv[1] == "--disable"
header_files = [value[1:] for value in args.header if value.startswith("@")]
for path in header_files:
    lines = Path(path).read_text().splitlines()
    assert len(lines) == 1 and lines[0].startswith("Authorization: Bearer ")
body = None
if args.data is not None:
    assert args.data.startswith("@")
    body = json.loads(Path(args.data[1:]).read_text())
record = {
    "argv": sys.argv[1:], "headers": args.header, "body": body,
    "method": args.request or "GET", "url": args.url,
}
with Path(os.environ["HTTP_LOG"]).open("a") as stream:
    stream.write(json.dumps(record) + "\n")
if args.url.endswith("/epochs"):
    response = {"epochId": "a" * 64}
elif args.url.endswith(("/activations", "/rollbacks")):
    response = {"revision": 42}
else:
    response = {"active": None, "epochs": [], "nextCursor": "d" * 64}
raw = json.dumps(response) + "\n"
if os.environ.get("MALFORMED_RESPONSE") == "1":
    raw = "not-json\n"
if args.output:
    Path(args.output).write_text(raw)
else:
    sys.stdout.write(raw)
if args.dump_header:
    Path(args.dump_header).write_text("HTTP/1.1 200 OK\n")
sys.exit(int(os.environ.get("HTTP_EXIT", "0")))
"""


def _blocks(name: str, heading: str | None = None) -> list[str]:
    tokens = MarkdownIt("commonmark").parse((ROOT / "docs/how-to" / name).read_text())
    section = ""
    blocks: list[str] = []
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag == "h2":
            section = tokens[index + 1].content
        if token.type == "fence" and token.info == "sh" and (heading is None or section == heading):
            blocks.append(token.content)
    assert blocks
    return blocks


@pytest.fixture
def environment(tmp_path: Path) -> dict[str, str]:
    assert shutil.which("bash") is not None, "Native documentation witness requires bash"
    assert shutil.which("jq") is not None, "Native documentation witness requires jq"
    commands = tmp_path / "bin"
    commands.mkdir()
    recorder = commands / "curl"
    recorder.write_text(f"#!{sys.executable}\n" + CURL_RECORDER)
    recorder.chmod(0o700)
    private = tmp_path / "private headers"
    private.mkdir(mode=0o700)
    workload_header = private / "workload.header"
    metrics_header = private / "metrics.header"
    for header, value in zip((workload_header, metrics_header), SYNTHETIC_VALUES, strict=True):
        header.write_text(f"Authorization: Bearer {value}\n")
        header.chmod(0o600)
    (tmp_path / "config.example.yaml").write_text("schemaVersion: fixture-only\n")
    return {
        "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
        "HOME": str(tmp_path),
        "TMPDIR": str(tmp_path),
        "HTTP_LOG": str(tmp_path / "http.jsonl"),
        "CONTROL_PLANE_HEADER_FILE": str(workload_header),
        "METRICS_HEADER_FILE": str(metrics_header),
        "COORDINATOR_URL": "https://coordinator.example.invalid",
        "INSTALLATION_ID": "101",
        "REPOSITORY_ID": "202",
        "PROPOSAL_MANIFEST_ID": MANIFEST,
        "epoch_id": EPOCH,
        "reviewed_epoch_id": EPOCH,
        "expected_revision": "null",
        "operation_id": "activation-witness-001",
        "active_revision": "999",
    }


def _run(tmp_path: Path, environment: dict[str, str], script: str) -> CommandResult:
    result = spawn(
        "bash",
        ("--noprofile", "--norc", "-s"),
        cwd=tmp_path,
        env=environment,
        input_text=script,
        max_buffer=128 * 1024,
        timeout_seconds=15,
    )
    assert result.failure_kind is None, result
    assert result.status == 0, result
    assert all(value not in result.stdout + result.stderr for value in SYNTHETIC_VALUES)
    return result


def _requests(environment: dict[str, str]) -> list[dict[str, object]]:
    path = Path(environment["HTTP_LOG"])
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def _activation_script(*, errexit: bool = False) -> str:
    setup = _blocks(POLICY_GUIDE, "Preconditions")[1]
    activation = _blocks(POLICY_GUIDE, "Activate the Epoch")[1]
    return (
        ("set -e\n" if errexit else "")
        + setup
        + activation
        + "printf 'parent-continued:%s\\n' \"${active_revision-unset}\"\n"
    )


def _assert_private(directory: Path) -> None:
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for path in directory.iterdir():
        assert path.is_file() and not path.is_symlink()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def _assert_transport(requests: list[dict[str, object]], header: str) -> None:
    assert all(value not in json.dumps(requests) for value in SYNTHETIC_VALUES)
    for request in requests:
        args = cast(list[str], request["argv"])
        assert args[0] == "--disable"
        assert "--fail-with-body" in args
        assert not any(
            arg.startswith(("--retry", "--trace", "--location", "--follow"))
            or arg in {"-L", "-v", "--verbose", "--insecure", "-k"}
            for arg in args
        )
        headers = cast(list[str], request["headers"])
        assert headers == (
            [f"@{header}", "Content-Type: application/json"]
            if request["method"] == "POST"
            else [f"@{header}"]
        )


def test_local_guide_mise_floor_matches_manifest() -> None:
    manifest = tomllib.loads((ROOT / "mise.toml").read_text())
    guide = (ROOT / "docs/how-to/evaluate-locally.md").read_text()
    versions = re.findall(r"mise (\d+\.\d+\.\d+) or newer", guide)
    assert versions == [manifest["min_version"]]
    assert "[`mise.toml`](../../mise.toml)" in guide


@pytest.mark.parametrize("errexit", [False, True])
def test_mismatched_epoch_sends_nothing_and_parent_continues(
    tmp_path: Path, environment: dict[str, str], errexit: bool
) -> None:
    environment["reviewed_epoch_id"] = PREVIOUS_EPOCH
    result = _run(tmp_path, environment, _activation_script(errexit=errexit))
    assert "parent-continued:unset" in result.stdout
    assert "Epoch mismatch; no activation sent." in result.stderr
    assert _requests(environment) == []
    [directory] = list(tmp_path.glob("ci-policy.*"))
    assert list(directory.iterdir()) == []


@pytest.mark.parametrize("revision", ["null", "7"])
def test_matching_epoch_preserves_exact_activation_request(
    tmp_path: Path, environment: dict[str, str], revision: str
) -> None:
    environment["expected_revision"] = revision
    result = _run(tmp_path, environment, _activation_script(errexit=True))
    assert "parent-continued:42" in result.stdout
    [request] = _requests(environment)
    assert request["method"] == "POST"
    assert request["url"] == "https://coordinator.example.invalid/api/v1/config/activations"
    assert request["body"] == {
        "schemaVersion": "ci-config-epoch-activation/v1",
        "installationId": 101,
        "repositoryId": 202,
        "targetEpochId": EPOCH,
        "proposalManifestId": MANIFEST,
        "expectedRevision": json.loads(revision),
        "operationId": "activation-witness-001",
    }
    _assert_transport([request], environment["CONTROL_PLANE_HEADER_FILE"])
    [directory] = list(tmp_path.glob("ci-policy.*"))
    _assert_private(directory)
    assert json.loads((directory / "activation.response.json").read_text()) == {"revision": 42}


def test_body_construction_failure_cannot_reach_http(
    tmp_path: Path, environment: dict[str, str]
) -> None:
    environment["expected_revision"] = "not-json"
    result = _run(tmp_path, environment, _activation_script(errexit=True))
    assert "parent-continued:unset" in result.stdout
    assert _requests(environment) == []


@pytest.mark.parametrize(("exit_code", "malformed"), [("22", "0"), ("28", "0"), ("0", "1")])
def test_failed_or_malformed_receipt_is_retained_without_revision_or_retry(
    tmp_path: Path, environment: dict[str, str], exit_code: str, malformed: str
) -> None:
    environment.update(HTTP_EXIT=exit_code, MALFORMED_RESPONSE=malformed)
    result = _run(tmp_path, environment, _activation_script(errexit=True))
    assert "parent-continued:unset" in result.stdout
    assert len(_requests(environment)) == 1
    [directory] = list(tmp_path.glob("ci-policy.*"))
    _assert_private(directory)
    assert (directory / "activation.request.json").is_file()
    assert (directory / "activation.response.json").read_text()


def test_policy_guide_all_requests_use_private_files_and_unique_workspaces(
    tmp_path: Path, environment: dict[str, str]
) -> None:
    script = "\n".join(_blocks(POLICY_GUIDE))
    script = script.replace(
        "/deployment-owned/control-plane-authorization.header",
        shlex.quote(environment["CONTROL_PLANE_HEADER_FILE"]),
    ).replace("replace-with-target-epoch-from-the-same-reviewed-proposal", EPOCH)
    script = script.replace("replace-with-retained-64-character-epoch-id", PREVIOUS_EPOCH)
    for _ in range(2):
        _run(tmp_path, environment, "set -e\n" + script)
    directories = list(tmp_path.glob("ci-policy.*"))
    assert len(directories) == 2
    for directory in directories:
        _assert_private(directory)
        assert len(list(directory.iterdir())) == 12
    requests = _requests(environment)
    assert len(requests) == 14
    _assert_transport(requests, environment["CONTROL_PLANE_HEADER_FILE"])
    first = requests[:7]
    assert [request["method"] for request in first] == ["POST"] * 4 + ["GET"] * 3
    assert [cast(str, request["url"]).rsplit("/", 1)[-1] for request in first] == [
        "validations",
        "epochs",
        "activations",
        "rollbacks",
        "status?limit=20",
        "status",
        "source",
    ]
    assert first[1]["body"] == {
        "schemaVersion": "ci-config-epoch-registration/v1",
        "sourceFormat": "yaml-1.2",
        "source": "schemaVersion: fixture-only\n",
        "operationId": "register-policy-001",
    }
    assert first[3]["body"] == {
        "schemaVersion": "ci-config-epoch-rollback/v1",
        "installationId": 100,
        "repositoryId": 200,
        "targetEpochId": PREVIOUS_EPOCH,
        "expectedRevision": 42,
        "operationId": "rollback-policy-001",
        "reason": "Restore the last admitted policy after failed shadow validation.",
    }


def test_metrics_probe_uses_file_header_without_bearer_argv(
    tmp_path: Path, environment: dict[str, str]
) -> None:
    metrics_header = Path(environment["METRICS_HEADER_FILE"])
    workload_header = Path(environment["CONTROL_PLANE_HEADER_FILE"])
    assert metrics_header != workload_header
    assert metrics_header.read_bytes() != workload_header.read_bytes()
    [script] = _blocks("deploy-container.md", "5. Probe And Admit The Instance")
    script = script.replace(
        "/deployment-owned/metrics-authorization.header",
        shlex.quote(environment["METRICS_HEADER_FILE"]),
    )
    _run(tmp_path, environment, script)
    requests = _requests(environment)
    assert [request["url"] for request in requests] == [
        f"http://127.0.0.1:3000/{path}" for path in ("healthz", "metrics", "readyz", "workbench")
    ]
    _assert_transport([requests[1]], environment["METRICS_HEADER_FILE"])
    assert all(requests[index]["headers"] == [] for index in (0, 2, 3))
    assert all(value not in json.dumps(requests) for value in SYNTHETIC_VALUES)

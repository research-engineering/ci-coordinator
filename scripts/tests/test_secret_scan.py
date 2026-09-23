from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, TypedDict, cast

import pytest
from ruamel.yaml import YAML
from scripts import secret_scan
from scripts.bounded_git import run_git
from scripts.bounded_process import CommandResult, spawn

scanner = secret_scan.scanner
_TYPE_DIGEST = "809fef365a87d4d1eb83535b52081177488e7fcd4ae7997f412f21a377acfe4b"
_POLICY_CASES = [
    ("backend/tests/unit/audit_replay/test_checkpoint.py", _TYPE_DIGEST),
    ("backend/tests/unit/capacity_qualification/_support.py", _TYPE_DIGEST),
    ("backend/tests/unit/production_cutover_support.py", _TYPE_DIGEST),
    ("backend/tests/unit/production_admission_support.py", _TYPE_DIGEST),
    ("backend/tests/unit/production_admission/test_cutover_drain.py", _TYPE_DIGEST),
    (
        "proofkit/routes/developer-environment.v2.json",
        "ba5028bc8f8303b7643383ad20e55c67b88e88ed7a1b72371d53117e50ab2b23",
    ),
    (
        "proofkit/routes/developer-environment.v2.json",
        "f189fe22609c7a3e713932720137a434578eaa798e94380e238388f7e19eefa2",
    ),
    (
        "proofkit/routes/developer-environment.v2.json",
        "e1969089fedd36310d7f581001ba0008e5e790defb1c99179361a1bc63aba7dc",
    ),
]


def test_reusable_workflow_does_not_attach_history_identity_to_a_tree_only_scan() -> None:
    root = secret_scan.ACTION_ROOT.parents[2]
    workflow = YAML(typ="safe").load(root / ".github/workflows/source-assurance.yml")
    steps = workflow["jobs"]["secrets"]["steps"]
    scan = next(step for step in steps if step.get("uses") == "$/.github/actions/secret-scan")
    inputs = scan["with"]
    condition = (
        "(github.event_name == 'pull_request' || github.event_name == 'merge_group' "
        "|| inputs.manual_base_sha != '')"
    )
    assert inputs["history"] == "${{ " + condition + " && 'required' || 'none' }}"
    assert inputs["head"] == "${{ " + condition + " && github.sha || '' }}"
    assert inputs["base"] == (
        "${{ github.event.pull_request.base.sha || github.event.merge_group.base_sha "
        "|| inputs.manual_base_sha }}"
    )
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0
    assert checkout["with"]["persist-credentials"] is False


class ScanSummary(TypedDict):
    scope: str
    tree_files: int
    tree_bytes: int
    history_commits: int
    findings: int
    locations: list[dict[str, int | str]]


@pytest.fixture
def binary() -> Path:
    configured = os.environ.get("GITLEAKS_TEST_BINARY") or shutil.which("gitleaks")
    if configured is None:
        pytest.fail("Native secret-scan fixtures require checksum-installed Gitleaks 8.30.1")
    return Path(configured)


def _commit(root: Path, message: str) -> str:
    run_git(root, ["add", "--all"])
    run_git(
        root,
        [
            "-c",
            "user.name=Secret scan fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--quiet",
            "--allow-empty",
            "--message",
            message,
        ],
    )
    return run_git(root, ["rev-parse", "HEAD"]).stdout.strip()


def _repository(root: Path) -> tuple[Path, str]:
    root.mkdir()
    run_git(root, ["init", "--quiet", "--template="])
    (root / "source.txt").write_text("ordinary source\n", encoding="utf-8")
    return root, _commit(root, "initial")


def _synthetic_secret() -> str:
    return "ghp_" + "1AZvZqMBtxSjTkERGXMFs97RSbMNZA96opY4"


def _public_identifier(digest: str) -> str:
    if digest == _TYPE_DIGEST:
        return "Ed25519" + "PrivateKey"
    owner = secret_scan.ACTION_ROOT.parents[2] / "proofkit/routes/developer-environment.v2.json"
    rows = json.loads(owner.read_text(encoding="utf-8"))["bindings"]
    matches = {
        row[2] for row in rows if hashlib.sha256(row[2].encode("utf-8")).hexdigest() == digest
    }
    assert len(matches) == 1
    value: str = matches.pop()
    return value


def _scan(root: Path, binary: Path, **kwargs: Any) -> ScanSummary:
    return cast(
        ScanSummary, scanner.scan(root, binary, secret_scan.run, secret_scan.read, **kwargs)
    )


def test_clean_tree_scans_only_git_observed_nonignored_files(tmp_path: Path, binary: Path) -> None:
    root, _ = _repository(tmp_path / "source")
    (root / ".gitignore").write_text(".private/\n", encoding="utf-8")
    (root / ".private").mkdir()
    (root / ".private" / "local.txt").write_text(_synthetic_secret(), encoding="utf-8")
    (root / "new.txt").write_text("untracked ordinary source\n", encoding="utf-8")
    result = _scan(root, binary)
    assert result == {
        "scope": "tree-only",
        "tree_files": 3,
        "tree_bytes": 52,
        "history_commits": 0,
        "findings": 0,
        "locations": [],
    }


@pytest.mark.parametrize("path", ["source.txt", "tests/fixture.txt"])
def test_current_tree_secret_is_rejected_without_a_test_exclusion(
    tmp_path: Path, binary: Path, path: str
) -> None:
    root, _ = _repository(tmp_path / "source")
    candidate = root / path
    candidate.parent.mkdir(exist_ok=True)
    candidate.write_text(_synthetic_secret() + "\n", encoding="utf-8")
    assert _scan(root, binary)["findings"] > 0


def test_added_then_deleted_intermediate_secret_is_detected(tmp_path: Path, binary: Path) -> None:
    root, base = _repository(tmp_path / "source")
    (root / "transient.txt").write_text(_synthetic_secret(), encoding="utf-8")
    _commit(root, "add transient fixture")
    (root / "transient.txt").unlink()
    head = _commit(root, "remove transient fixture")
    assert _scan(root, binary)["findings"] == 0
    result = _scan(root, binary, history=True, base=base, head=head)
    assert result["history_commits"] == 2
    assert result["findings"] > 0


def test_caller_config_ignore_and_inline_allow_cannot_suppress_a_secret(
    tmp_path: Path, binary: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, base = _repository(tmp_path / "source")
    (root / "leak.txt").write_text(_synthetic_secret() + " # gitleaks:allow\n", encoding="utf-8")
    (root / ".gitleaks.toml").write_text(
        '[extend]\nuseDefault = true\n[allowlist]\npaths = [".*"]\n', encoding="utf-8"
    )
    (root / ".gitleaksignore").write_text(
        "leak.txt:github-pat:1\nfiles/leak.txt:github-pat:1\n", encoding="utf-8"
    )
    head = _commit(root, "candidate-owned suppressions")
    monkeypatch.setenv("GITLEAKS_CONFIG", str(root / ".gitleaks.toml"))
    monkeypatch.setenv("GITLEAKS_CONFIG_TOML", '[allowlist]\npaths = [".*"]')
    assert _scan(root, binary)["findings"] > 0
    assert _scan(root, binary, history=True, base=base, head=head)["findings"] >= 2


def test_caller_ignore_contents_remain_scanned_as_data(tmp_path: Path, binary: Path) -> None:
    root, _ = _repository(tmp_path / "source")
    (root / ".gitleaksignore").write_text(_synthetic_secret() + "\n", encoding="utf-8")
    result = _scan(root, binary)
    assert any(
        item["file"] == ".gitleaksignore" and item["rule"] == "github-pat"
        for item in result["locations"]
    )


@pytest.mark.parametrize("owner,digest", _POLICY_CASES)
def test_exact_public_exception_rejects_a_changed_value_in_the_same_owner(
    tmp_path: Path, binary: Path, owner: str, digest: str
) -> None:
    root, _ = _repository(tmp_path / "source")
    target = root / owner
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"key": _public_identifier(digest)}) + "\n", encoding="utf-8")
    assert _scan(root, binary)["findings"] == 0
    changed = base64.b64encode(hashlib.sha256(b"unrelated synthetic replacement").digest()).decode()
    target.write_text(json.dumps({"key": changed}) + "\n", encoding="utf-8")
    result = _scan(root, binary)
    assert any(
        item["file"] == owner and item["rule"] == "generic-api-key" for item in result["locations"]
    )


@pytest.mark.parametrize("owner,digest", _POLICY_CASES)
def test_exact_public_exception_rejects_the_same_value_under_a_foreign_owner(
    tmp_path: Path, binary: Path, owner: str, digest: str
) -> None:
    root, _ = _repository(tmp_path / "source")
    relative = Path("foreign") / owner
    target = root / relative
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"key": _public_identifier(digest)}) + "\n", encoding="utf-8")
    result = _scan(root, binary)
    assert any(
        item["file"] == relative.as_posix() and item["rule"] == "generic-api-key"
        for item in result["locations"]
    )


def test_history_projection_prefix_never_acquires_exception_ownership(
    tmp_path: Path, binary: Path
) -> None:
    root, base = _repository(tmp_path / "source")
    owner, digest = _POLICY_CASES[0]
    relative = Path("tree/files") / owner
    target = root / relative
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"key": _public_identifier(digest)}) + "\n", encoding="utf-8")
    _commit(root, "add foreign policy fixture")
    target.unlink()
    head = _commit(root, "remove foreign policy fixture")
    result = _scan(root, binary, history=True, base=base, head=head)
    assert any(
        item["file"] == relative.as_posix()
        and item["rule"] == "generic-api-key"
        and item["scope"] == "history"
        for item in result["locations"]
    )


@pytest.mark.parametrize("base,head", [("", ""), ("bad", "bad"), ("0" * 40, "1" * 40)])
def test_missing_bad_or_zero_required_history_cannot_be_clean(
    tmp_path: Path, binary: Path, base: str, head: str
) -> None:
    root, _ = _repository(tmp_path / "source")
    with pytest.raises(scanner.ScanError, match="required history"):
        _scan(root, binary, history=True, base=base, head=head)


def test_identical_or_wrong_head_range_is_rejected(tmp_path: Path, binary: Path) -> None:
    root, base = _repository(tmp_path / "source")
    with pytest.raises(scanner.ScanError, match="distinct"):
        _scan(root, binary, history=True, base=base, head=base)
    head = _commit(root, "second")
    _commit(root, "third")
    with pytest.raises(scanner.ScanError, match="checked-out"):
        _scan(root, binary, history=True, base=base, head=head)


def test_missing_commit_and_shallow_history_are_failures(tmp_path: Path, binary: Path) -> None:
    root, base = _repository(tmp_path / "source")
    head = _commit(root, "second")
    with pytest.raises(scanner.ScanError, match="Git scope"):
        _scan(root, binary, history=True, base="1" * 40, head=head)
    (root / ".git" / "shallow").write_text(head + "\n", encoding="ascii")
    with pytest.raises(scanner.ScanError, match="shallow"):
        _scan(root, binary, history=True, base=base, head=head)


@pytest.mark.parametrize("kind", ["symlink", "directory-symlink", "fifo"])
def test_nonregular_tree_and_symlink_parents_are_rejected(
    tmp_path: Path, binary: Path, kind: str
) -> None:
    root, _ = _repository(tmp_path / "source")
    if kind == "directory-symlink":
        (root / "nested").mkdir()
        (root / "nested" / "file.txt").write_text("text", encoding="utf-8")
        _commit(root, "tracked nested file")
        shutil.rmtree(root / "nested")
        (root / "nested").symlink_to(tmp_path, target_is_directory=True)
    else:
        target = root / "source.txt"
        target.unlink()
        if kind == "symlink":
            target.symlink_to(tmp_path / "outside.txt")
        else:
            os.mkfifo(target)
    with pytest.raises(scanner.ScanError, match="regular files"):
        _scan(root, binary)


def test_empty_required_tree_is_not_clean(tmp_path: Path, binary: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    run_git(root, ["init", "--quiet"])
    with pytest.raises(scanner.ScanError, match="empty Git-observed"):
        _scan(root, binary)


def test_repository_subdirectory_cannot_claim_complete_tree(tmp_path: Path, binary: Path) -> None:
    root, _ = _repository(tmp_path / "source")
    subset = root / "nested"
    subset.mkdir()
    (subset / "file.txt").write_text("partial source", encoding="utf-8")
    with pytest.raises(scanner.ScanError, match="complete repository root"):
        _scan(subset, binary)


@pytest.mark.parametrize(
    "status,report,diagnostics",
    [
        (1, b"[]", b""),
        (0, None, b""),
        (0, b"", b""),
        (0, b"null", b""),
        (0, b"{}", b""),
        (23, b"[]", b""),
        (0, b'[{"Secret":"REDACTED","RuleID":"rule","File":"source","StartLine":1}]', b""),
        (0, b"[]", b"scanner fragment error"),
        (23, b"[{}]", b""),
    ],
)
def test_scanner_failure_missing_malformed_or_mismatched_report_is_not_clean(
    tmp_path: Path, status: int, report: bytes | None, diagnostics: bytes
) -> None:
    source = tmp_path / "tree"
    source.mkdir()

    def runner(
        argv: list[str], _cwd: Path, environment: dict[str, str], _limit: int, _timeout: int
    ) -> tuple[int, bytes]:
        assert "GITLEAKS_CONFIG" not in environment
        assert "--ignore-gitleaks-allow" in argv
        assert "--redact=100" in argv
        report_path = Path(argv[argv.index("--report-path") + 1])
        if report is not None:
            report_path.write_bytes(report)
        return status, diagnostics

    with pytest.raises(scanner.ScanError):
        scanner.scan_scope(runner, secret_scan.read, Path("gitleaks"), source, tmp_path, "dir")


def test_report_rejects_unredacted_secret_or_foreign_commit() -> None:
    finding = {
        "Secret": "REDACTED",
        "RuleID": "github-pat",
        "File": "leak.txt",
        "StartLine": 1,
        "Match": _synthetic_secret(),
    }
    payload = json.dumps([finding]).encode()
    assert scanner.admit_report(payload, 23) == [
        {"file": "leak.txt", "rule": "github-pat", "line": 1, "scope": "tree", "commit": ""}
    ]
    with pytest.raises(scanner.ScanError, match="scope"):
        scanner.admit_report(payload, 23, ("1" * 40,))
    finding["Secret"] = _synthetic_secret()
    with pytest.raises(scanner.ScanError, match="redaction"):
        scanner.admit_report(json.dumps([finding]).encode(), 23)


@pytest.mark.parametrize("foreign", ["foreign", "policy-data-invented/.gitleaksignore"])
def test_report_coordinate_must_belong_to_the_admitted_tree(foreign: str) -> None:
    finding = {"Secret": "REDACTED", "RuleID": "github-pat", "File": foreign, "StartLine": 1}
    coordinates = scanner.admit_report(json.dumps([finding]).encode(), 23)
    with pytest.raises(scanner.ScanError, match="outside the admitted tree"):
        scanner.bind_coordinates(
            coordinates,
            {"owned.txt": "owned.txt", "policy-data-owned/.gitleaksignore": ".gitleaksignore"},
            "dir",
        )


def test_missing_default_scanner_fails_without_running_a_scope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GITLEAKS_TEST_BINARY", raising=False)
    monkeypatch.setattr(shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(sys, "argv", ["secret_scan"])
    assert scanner.main(secret_scan.run, secret_scan.read) == 2
    assert "unavailable" in capsys.readouterr().out


def test_wrong_scanner_version_fails_before_scanning(tmp_path: Path) -> None:
    executable = tmp_path / "gitleaks"
    executable.write_text("synthetic tool identity", encoding="utf-8")

    def wrong_version(*_args: Any, **_kwargs: Any) -> tuple[int, bytes]:
        return 0, b"0.0.0\n"

    with pytest.raises(scanner.ScanError, match="version"):
        scanner.scan(tmp_path, executable, wrong_version, secret_scan.read)


def test_installed_scanner_symlink_resolves_to_the_pinned_regular_tool(
    tmp_path: Path, binary: Path
) -> None:
    root, _ = _repository(tmp_path / "source")
    link = tmp_path / "linked-gitleaks"
    link.symlink_to(binary.resolve())
    assert _scan(root, link)["findings"] == 0


def test_cli_error_reports_safe_stage_and_reason_without_exception_content(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def malformed_report(*_args: Any, **_kwargs: Any) -> None:
        raise scanner.ScanError(
            _synthetic_secret(), stage="report_admission", code="malformed_report"
        )

    monkeypatch.setattr(scanner, "scan", malformed_report)
    monkeypatch.setattr(sys, "argv", ["secret_scan", "--gitleaks", "/fixture/tool"])
    assert scanner.main(secret_scan.run, secret_scan.read) == 2
    output = capsys.readouterr().out
    assert json.loads(output) == {
        "status": "error",
        "stage": "report_admission",
        "reason": "malformed_report",
    }
    assert _synthetic_secret() not in output


def test_timeout_failure_from_bounded_process_is_not_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(*_args: Any, **_kwargs: Any) -> CommandResult:
        return CommandResult(None, "", "", error="timeout", failure_kind="timeout")

    monkeypatch.setattr(secret_scan, "spawn", timeout)
    with pytest.raises(scanner.ScanError, match="bounded scanner"):
        secret_scan.run(["/tool/gitleaks", "dir"], Path.cwd(), {}, 1024, 1)


def test_environment_projection_drops_provider_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "synthetic-provider-context")
    monkeypatch.setenv("GITLEAKS_CONFIG", "untrusted-policy")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    assert set(scanner.child_environment()) == {
        "PATH",
        "LC_ALL",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_CONFIG_GLOBAL",
        "GIT_OPTIONAL_LOCKS",
        "GIT_NO_REPLACE_OBJECTS",
        "GIT_TERMINAL_PROMPT",
        "GIT_ALLOW_PROTOCOL",
    }


def test_actual_scanner_timeout_cannot_return_clean(tmp_path: Path) -> None:
    sleeper = tmp_path / "scanner"
    sleeper.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(60)\n", encoding="utf-8")
    sleeper.chmod(0o700)
    with pytest.raises(scanner.ScanError, match="bounded scanner"):
        secret_scan.run([str(sleeper)], tmp_path, scanner.child_environment(), 1024, 1)


def test_packaged_entrypoint_runs_in_foreign_repository_without_coordinator_scripts(
    tmp_path: Path, binary: Path
) -> None:
    root, _ = _repository(tmp_path / "foreign")
    action_copy = tmp_path / "portable-action"
    shutil.copytree(secret_scan.ACTION_ROOT, action_copy)
    result = spawn(
        sys.executable,
        ["-I", str(action_copy / "entrypoint.py"), "--root", str(root)],
        cwd=root,
        env={**scanner.child_environment(), "GITLEAKS_TEST_BINARY": str(binary)},
        max_buffer=4096,
        timeout_seconds=60,
    )
    assert result.error is None
    assert result.status == 0
    assert json.loads(result.stdout)["scope"] == "tree-only"
    assert not (root / "scripts").exists()


def test_cli_output_never_contains_secret_bytes(tmp_path: Path, binary: Path) -> None:
    root, _ = _repository(tmp_path / "source")
    (root / "source.txt").write_text(_synthetic_secret(), encoding="utf-8")
    result = spawn(
        sys.executable,
        [
            "-I",
            str(secret_scan.ACTION_ROOT / "entrypoint.py"),
            "--gitleaks",
            str(binary),
            "--root",
            str(root),
        ],
        cwd=root,
        env=scanner.child_environment(),
        max_buffer=4096,
        timeout_seconds=60,
    )
    assert result.error is None
    assert result.status == 1
    report = json.loads(result.stdout)
    assert report["findings"] > 0
    assert report["locations"]
    for location in report["locations"]:
        assert set(location) == {"file", "rule", "line", "scope", "commit"}
        assert location["file"] == "source.txt"
        assert location["rule"] == "github-pat"
        assert location["line"] == 1
    assert _synthetic_secret() not in result.stdout + result.stderr

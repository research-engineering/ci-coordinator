from __future__ import annotations

import hashlib
import io
import json
import os
import shlex
import subprocess
import tarfile
from pathlib import Path
from typing import cast

import pytest
from ruamel.yaml import YAML
from scripts.tests.test_release_vulnerability_admission import (
    OBSERVED_AT,
    RELEASE_SHA256,
    RELEASE_URL,
    _database,
    _manifest,
    _repair_evidence,
    _scan,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPO_ROOT / ".github/workflows/release-artifact.yml"
ARTIFACT_DIGEST = f"sha256:{'a' * 64}"
RELEASE_IDENTITY = "b" * 64
SOURCE_COMMIT = "c" * 40
REPOSITORY = "research-engineering/ci-coordinator"
REPOSITORY_ID = "1001"
GATE_WORKFLOW_ID = "2001"
WORKFLOW_REF = (
    "research-engineering/ci-coordinator/.github/workflows/release-artifact.yml@refs/heads/master"
)
EXECUTED_SHELL_STEPS = {
    ("attest", "Pull by registry digest and inspect without execution"),
    ("attest", "Verify exact signer, source, provenance, and SBOM"),
    ("build", "Provision the independent locked predicate validator"),
    ("build", "Validate registry predicates without signing authority"),
    ("build", "Download and verify pinned Grype release"),
    ("build", "Admit final-image vulnerability evidence"),
    ("build", "Collect exact runtime repair evidence"),
    ("preflight", "Admit the immutable repository and event context"),
    ("preflight", "Load source-owned publisher identity"),
    ("preflight", "Resolve the exact successful Full Check"),
}


def _shell(job_name: str, step_name: str) -> str:
    workflow = YAML(typ="safe").load(WORKFLOW_PATH)
    assert type(workflow) is dict
    jobs = workflow["jobs"]
    assert type(jobs) is dict
    job = jobs[job_name]
    assert type(job) is dict
    steps = job["steps"]
    assert type(steps) is list
    matches = [
        cast(dict[str, object], step)
        for step in steps
        if type(step) is dict and step.get("name") == step_name
    ]
    assert len(matches) == 1
    return cast(str, matches[0]["run"])


def _write_executable(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n" + body, encoding="utf-8")
    path.chmod(0o755)
    return path


def _fake_docker(directory: Path) -> Path:
    executable = directory / "docker"
    return _write_executable(
        executable,
        'operation="${1}"\n'
        'if test "${operation}" = "buildx"; then\n'
        '  if [[ "$*" == *".Provenance.SLSA"* ]]; then\n'
        '    operation="provenance"\n'
        "  else\n"
        '    operation="sbom"\n'
        "  fi\n"
        "fi\n"
        'test "${operation}" != "${FAKE_DOCKER_FAIL:-}"\n'
        'case "${operation}" in\n'
        "  pull | rm) ;;\n"
        '  create) printf "container-1\\n" ;;\n'
        '  cp) cp "${FAKE_BUILD_IDENTITY}" "${3}" ;;\n'
        '  provenance) cat "${FAKE_PROVENANCE}" ;;\n'
        '  sbom) cat "${FAKE_SBOM}" ;;\n'
        '  *) printf "unexpected docker operation: %s\\n" "${operation}" >&2; exit 64 ;;\n'
        "esac\n",
    )


def _evidence(tmp_path: Path) -> dict[str, str]:
    provenance = tmp_path / "provenance.json"
    sbom = tmp_path / "sbom.json"
    build_identity = tmp_path / "build-identity.json"
    provenance.write_text(
        json.dumps(
            {
                "buildDefinition": {
                    "buildType": (
                        "https://github.com/moby/buildkit/blob/master/docs/"
                        "attestations/slsa-definitions.md"
                    ),
                    "externalParameters": {
                        "configSource": {"path": "Dockerfile"},
                        "request": {
                            "args": {"build-arg:RELEASE": "true"},
                            "frontend": "dockerfile.v0",
                            "locals": [{"name": "context"}, {"name": "dockerfile"}],
                        },
                    },
                    "internalParameters": {
                        "buildConfig": {"llbDefinition": [{"id": "step0"}]},
                        "builderPlatform": "linux/amd64",
                    },
                    "resolvedDependencies": [
                        {
                            "digest": {"sha256": "f" * 64},
                            "uri": "pkg:docker/python@example",
                        }
                    ],
                },
                "runDetails": {
                    "builder": {"id": ""},
                    "metadata": {
                        "buildkit_completeness": {
                            "request": True,
                            "resolvedDependencies": False,
                        },
                        "buildkit_metadata": {},
                        "finishedOn": "2026-07-28T10:01:00Z",
                        "invocationId": "build-1",
                        "startedOn": "2026-07-28T10:00:00Z",
                    },
                },
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    sbom.write_text(
        json.dumps(
            {
                "SPDXID": "SPDXRef-DOCUMENT",
                "creationInfo": {
                    "created": "2026-07-28T10:01:00Z",
                    "creators": ["Tool: BuildKit"],
                },
                "dataLicense": "CC0-1.0",
                "documentNamespace": "https://example.invalid/spdx/build-1",
                "name": "ci-coordinator",
                "packages": [
                    {
                        "SPDXID": "SPDXRef-Package-ci-coordinator",
                        "downloadLocation": "NOASSERTION",
                        "name": "ci-coordinator",
                    }
                ],
                "spdxVersion": "SPDX-2.3",
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    build_identity.write_text(
        '{"productionEligible":true,'
        f'"releaseIdentity":"{RELEASE_IDENTITY}",'
        '"schemaVersion":"ci-coordinator-build-identity/v1",'
        f'"sourceCommit":"{SOURCE_COMMIT}"}}\n',
        encoding="utf-8",
    )
    return {
        "FAKE_BUILD_IDENTITY": str(build_identity),
        "FAKE_PROVENANCE": str(provenance),
        "FAKE_PROVENANCE_SHA256": hashlib.sha256(provenance.read_bytes()).hexdigest(),
        "FAKE_SBOM": str(sbom),
        "FAKE_SBOM_SHA256": hashlib.sha256(sbom.read_bytes()).hexdigest(),
    }


def _environment(tmp_path: Path) -> dict[str, str]:
    executable_directory = tmp_path / "bin"
    executable_directory.mkdir()
    _fake_docker(executable_directory)
    evidence = _evidence(tmp_path)
    vulnerability_dir = tmp_path / "vulnerability-evidence"
    vulnerability_dir.mkdir()
    vulnerability_receipt = vulnerability_dir / "release-vulnerability-evidence.json"
    vulnerability_receipt.write_text('{"admission":"accepted"}\n', encoding="utf-8")
    repair_dir = tmp_path / "repair-evidence"
    repair_dir.mkdir()
    repair_raw = (json.dumps(_repair_evidence()) + "\n").encode()
    (repair_dir / "release-runtime-repairs.json").write_bytes(repair_raw)
    (tmp_path / "release-runtime-repairs.json").write_bytes(repair_raw)
    return {
        **os.environ,
        **evidence,
        "ARTIFACT_DIGEST": ARTIFACT_DIGEST,
        "GITHUB_OUTPUT": str(tmp_path / "github-output"),
        "GITHUB_REPOSITORY": REPOSITORY,
        "GITHUB_WORKSPACE": str(REPO_ROOT),
        "IMAGE_NAME": f"ghcr.io/{REPOSITORY}",
        "REPOSITORY": REPOSITORY,
        "REPAIR_PREDICATE": f"https://github.com/{REPOSITORY}/attestations/runtime-repairs/v1",
        "PATH": f"{executable_directory}:{os.environ['PATH']}",
        "PROVENANCE_SHA256": evidence["FAKE_PROVENANCE_SHA256"],
        "RELEASE_IDENTITY": RELEASE_IDENTITY,
        "RUNNER_TEMP": str(tmp_path),
        "SBOM_SHA256": evidence["FAKE_SBOM_SHA256"],
        "SIGNER_WORKFLOW": (
            "github.com/research-engineering/ci-coordinator/.github/workflows/release-artifact.yml"
        ),
        "SOURCE_COMMIT": SOURCE_COMMIT,
        "REPAIR_EVIDENCE_SHA256": hashlib.sha256(repair_raw).hexdigest(),
        "VULNERABILITY_EVIDENCE_SHA256": hashlib.sha256(
            vulnerability_receipt.read_bytes()
        ).hexdigest(),
    }


def _execute(command: str, environment: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("bash", "-c", command),
        capture_output=True,
        check=False,
        cwd=REPO_ROOT,
        env=environment,
        text=True,
    )


def _context_environment(tmp_path: Path) -> dict[str, str]:
    environment = _environment(tmp_path)
    _write_executable(
        tmp_path / "bin/git",
        'test "$*" = "rev-parse HEAD"\n'
        'test "${FAKE_GIT_FAIL:-0}" = "0"\n'
        'printf "%s\\n" "${FAKE_GIT_HEAD}"\n',
    )
    environment.update(
        {
            "ACTUAL_EVENT": "workflow_dispatch",
            "ACTUAL_REF": "refs/heads/master",
            "ACTUAL_REPOSITORY": REPOSITORY,
            "ACTUAL_REPOSITORY_ID": REPOSITORY_ID,
            "ACTUAL_SHA": SOURCE_COMMIT,
            "ACTUAL_WORKFLOW_REF": WORKFLOW_REF,
            "ACTUAL_WORKFLOW_SHA": SOURCE_COMMIT,
            "EXPECTED_REPOSITORY": REPOSITORY,
            "EXPECTED_REPOSITORY_ID": REPOSITORY_ID,
            "EXPECTED_WORKFLOW_REF": WORKFLOW_REF,
            "FAKE_GIT_HEAD": SOURCE_COMMIT,
        }
    )
    return environment


def _gate_response() -> str:
    repository = {"full_name": REPOSITORY, "id": int(REPOSITORY_ID)}
    return json.dumps(
        {
            "total_count": 1,
            "workflow_runs": [
                {
                    "conclusion": "success",
                    "event": "workflow_dispatch",
                    "head_branch": "master",
                    "head_repository": repository,
                    "head_sha": SOURCE_COMMIT,
                    "id": 8001,
                    "name": "Full Check",
                    "path": ".github/workflows/python-persistence.yml",
                    "repository": repository,
                    "run_attempt": 2,
                    "status": "completed",
                    "workflow_id": int(GATE_WORKFLOW_ID),
                }
            ],
        },
        separators=(",", ":"),
    )


def _gate_environment(tmp_path: Path) -> dict[str, str]:
    environment = _environment(tmp_path)
    response = tmp_path / "fake-gate-response.json"
    response.write_text(_gate_response(), encoding="utf-8")
    _write_executable(
        tmp_path / "bin/gh",
        'test "${FAKE_GH_FAIL:-0}" = "0"\n'
        'printf "%s\\n" "$@" > "${FAKE_GH_ARGUMENTS}"\n'
        'cat "${FAKE_GATE_RESPONSE}"\n',
    )
    environment.update(
        {
            "FAKE_GATE_RESPONSE": str(response),
            "FAKE_GH_ARGUMENTS": str(tmp_path / "gh-arguments"),
            "GATE_WORKFLOW_ID": GATE_WORKFLOW_ID,
            "RELEASE_RUN_ATTEMPT": "1",
            "RELEASE_RUN_ID": "9001",
            "REPOSITORY": REPOSITORY,
            "REPOSITORY_ID": REPOSITORY_ID,
            "SOURCE_COMMIT": SOURCE_COMMIT,
        }
    )
    return environment


def test_every_workflow_shell_step_has_an_executable_oracle() -> None:
    workflow = YAML(typ="safe").load(WORKFLOW_PATH)
    jobs = cast(dict[str, object], cast(dict[str, object], workflow)["jobs"])
    actual = {
        (job_name, cast(str, step["name"]))
        for job_name, job in jobs.items()
        for step in cast(list[dict[str, object]], cast(dict[str, object], job)["steps"])
        if "run" in step
    }

    assert actual == EXECUTED_SHELL_STEPS


@pytest.mark.parametrize("missing", ["repository", "workflow", "none"])
def test_publisher_loading_requires_explicit_owner_ids(tmp_path: Path, missing: str) -> None:
    environment = _environment(tmp_path)
    environment.update(
        CI_COORDINATOR_RELEASE_REPOSITORY_ID=REPOSITORY_ID,
        CI_COORDINATOR_FULL_CHECK_WORKFLOW_ID=GATE_WORKFLOW_ID,
        GITHUB_REPOSITORY_ID=REPOSITORY_ID,
    )
    if missing == "repository":
        environment.pop("CI_COORDINATOR_RELEASE_REPOSITORY_ID")
    elif missing == "workflow":
        environment.pop("CI_COORDINATOR_FULL_CHECK_WORKFLOW_ID")
    result = _execute(_shell("preflight", "Load source-owned publisher identity"), environment)
    outputs = dict(
        line.split("=", 1) for line in (tmp_path / "github-output").read_text().splitlines()
    )
    if missing != "none":
        assert result.returncode != 0
        assert outputs == {}
    else:
        assert result.returncode == 0, result.stderr
        assert outputs["repository"] == REPOSITORY
        assert outputs["repository_id"] == REPOSITORY_ID
        assert outputs["gate_workflow_id"] == GATE_WORKFLOW_ID
        assert outputs["image"] == f"ghcr.io/{REPOSITORY}"
        assert outputs["workflow_ref"] == WORKFLOW_REF
        assert outputs["signer_workflow"] == environment["SIGNER_WORKFLOW"]
        assert outputs["repair_predicate"] == environment["REPAIR_PREDICATE"]


@pytest.mark.parametrize(
    ("variable", "replacement"),
    [
        ("ACTUAL_EVENT", "pull_request"),
        ("ACTUAL_REF", "refs/heads/feature/unsafe"),
        ("ACTUAL_REPOSITORY", "other/repository"),
        ("ACTUAL_REPOSITORY_ID", "1"),
        ("ACTUAL_SHA", "d" * 40),
        ("ACTUAL_WORKFLOW_REF", "other/workflow@refs/heads/master"),
        ("ACTUAL_WORKFLOW_SHA", "d" * 40),
    ],
)
def test_repository_context_rejects_each_authority_substitution(
    tmp_path: Path,
    variable: str,
    replacement: str,
) -> None:
    environment = _context_environment(tmp_path)
    environment[variable] = replacement

    result = _execute(
        _shell("preflight", "Admit the immutable repository and event context"),
        environment,
    )

    assert result.returncode != 0


def test_repository_context_propagates_git_failure_and_accepts_exact_context(
    tmp_path: Path,
) -> None:
    command = _shell("preflight", "Admit the immutable repository and event context")
    environment = _context_environment(tmp_path)

    assert _execute(command, {**environment, "FAKE_GIT_FAIL": "1"}).returncode != 0
    assert _execute(command, environment).returncode == 0


@pytest.mark.parametrize("failure", ["provider", "validator"])
def test_gate_resolution_propagates_each_external_failure(
    tmp_path: Path,
    failure: str,
) -> None:
    environment = _gate_environment(tmp_path)
    if failure == "provider":
        environment["FAKE_GH_FAIL"] = "1"
    else:
        Path(environment["FAKE_GATE_RESPONSE"]).write_text("{}\n", encoding="utf-8")

    result = _execute(
        _shell("preflight", "Resolve the exact successful Full Check"),
        environment,
    )

    assert result.returncode != 0


def test_gate_resolution_accepts_the_exact_provider_response(tmp_path: Path) -> None:
    environment = _gate_environment(tmp_path)

    result = _execute(
        _shell("preflight", "Resolve the exact successful Full Check"),
        environment,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "gh-arguments").read_text().splitlines() == [
        "api",
        "--method",
        "GET",
        "--header",
        "X-GitHub-Api-Version: 2026-03-10",
        f"/repos/{REPOSITORY}/actions/workflows/{GATE_WORKFLOW_ID}/runs",
        "--raw-field",
        "branch=master",
        "--raw-field",
        "event=workflow_dispatch",
        "--raw-field",
        f"head_sha={SOURCE_COMMIT}",
        "--raw-field",
        "per_page=10",
        "--raw-field",
        "status=success",
    ]
    assert {
        line.partition("=")[0] for line in (tmp_path / "github-output").read_text().splitlines()
    } == {"gate_run_attempt", "gate_run_id", "release_identity", "source_commit"}


def test_validator_provisioning_propagates_failure_and_uses_exact_lock(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    arguments = tmp_path / "uv-arguments"
    _write_executable(
        tmp_path / "bin/uv",
        'test "${FAKE_UV_FAIL:-0}" = "0"\nprintf "%s\\n" "$*" > "${FAKE_UV_ARGUMENTS}"\n',
    )
    environment["FAKE_UV_ARGUMENTS"] = str(arguments)
    command = _shell("build", "Provision the independent locked predicate validator")

    assert _execute(command, {**environment, "FAKE_UV_FAIL": "1"}).returncode != 0
    assert _execute(command, environment).returncode == 0
    assert arguments.read_text().strip() == (
        "sync --project backend --frozen --no-dev --no-install-project"
    )


@pytest.mark.parametrize("failure", ["pull", "provenance", "sbom"])
def test_predicate_validation_propagates_each_docker_failure(
    tmp_path: Path,
    failure: str,
) -> None:
    environment = _environment(tmp_path)
    environment["FAKE_DOCKER_FAIL"] = failure

    result = _execute(
        _shell("build", "Validate registry predicates without signing authority"),
        environment,
    )

    assert result.returncode != 0


def test_predicate_validation_propagates_validator_failure(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    Path(environment["FAKE_PROVENANCE"]).write_text("{}\n")

    result = _execute(
        _shell("build", "Validate registry predicates without signing authority"),
        environment,
    )

    assert result.returncode != 0


def test_predicate_validation_emits_only_the_admitted_hashes(tmp_path: Path) -> None:
    environment = _environment(tmp_path)

    result = _execute(
        _shell("build", "Validate registry predicates without signing authority"),
        environment,
    )

    assert result.returncode == 0, result.stderr
    assert (tmp_path / "github-output").read_text().splitlines() == [
        f"provenance_sha256={environment['FAKE_PROVENANCE_SHA256']}",
        f"sbom_sha256={environment['FAKE_SBOM_SHA256']}",
    ]


@pytest.mark.parametrize(
    "failure",
    ["pull", "create", "cp", "provenance", "sbom"],
)
def test_attestation_inspection_propagates_each_docker_failure(
    tmp_path: Path,
    failure: str,
) -> None:
    environment = _environment(tmp_path)
    environment["FAKE_DOCKER_FAIL"] = failure

    result = _execute(
        _shell("attest", "Pull by registry digest and inspect without execution"),
        environment,
    )

    assert result.returncode != 0


@pytest.mark.parametrize(
    "substitution",
    ["identity", "provenance-hash", "sbom-hash", "vulnerability-hash", "repair-hash"],
)
def test_attestation_inspection_rejects_each_cross_job_substitution(
    tmp_path: Path,
    substitution: str,
) -> None:
    environment = _environment(tmp_path)
    if substitution == "identity":
        Path(environment["FAKE_BUILD_IDENTITY"]).write_text("{}\n")
    elif substitution == "provenance-hash":
        environment["PROVENANCE_SHA256"] = "d" * 64
    elif substitution == "sbom-hash":
        environment["SBOM_SHA256"] = "e" * 64
    elif substitution == "repair-hash":
        environment["REPAIR_EVIDENCE_SHA256"] = "e" * 64
    else:
        environment["VULNERABILITY_EVIDENCE_SHA256"] = "f" * 64

    result = _execute(
        _shell("attest", "Pull by registry digest and inspect without execution"),
        environment,
    )

    assert result.returncode != 0


def test_attestation_inspection_accepts_exact_identity_and_predicate_bytes(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)

    result = _execute(
        _shell("attest", "Pull by registry digest and inspect without execution"),
        environment,
    )

    assert result.returncode == 0, result.stderr


def test_attestation_verification_propagates_each_provider_failure(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    gh = tmp_path / "bin/gh"
    _write_executable(
        gh,
        'count_file="${FAKE_GH_COUNT_FILE}"\n'
        'count="$(cat "${count_file}" 2>/dev/null || printf 0)"\n'
        "count=$((count + 1))\n"
        'printf "%s" "${count}" > "${count_file}"\n'
        'test "${count}" -ne "${FAKE_GH_FAIL_AT:-0}"\n',
    )
    count_file = tmp_path / "gh-count"
    environment["FAKE_GH_COUNT_FILE"] = str(count_file)
    verification = _shell("attest", "Verify exact signer, source, provenance, and SBOM")

    for failure_index in range(1, 7):
        count_file.unlink(missing_ok=True)
        result = _execute(
            verification,
            {**environment, "FAKE_GH_FAIL_AT": str(failure_index)},
        )
        assert result.returncode != 0

    count_file.unlink(missing_ok=True)
    result = _execute(verification, environment)
    assert result.returncode == 0, result.stderr
    assert count_file.read_text() == "6"


def _vulnerability_shell_diagnostics(
    tmp_path: Path, result: subprocess.CompletedProcess[str]
) -> tuple[int, str, dict[str, int | None]]:
    artifacts = {}
    for name in (
        "release-grype.json",
        "registry-manifest.json",
        "grype-db-status.json",
        "release-vulnerability-scan.json",
        "grype-calls",
    ):
        path = tmp_path / name
        artifacts[name] = path.stat().st_size if path.is_file() else None
    return result.returncode, result.stderr[:2048], artifacts


def _vulnerability_environment(tmp_path: Path, failure: str = "") -> dict[str, str]:
    environment = _environment(tmp_path)
    _write_executable(tmp_path / "bin/jq", "exit 127\n")
    cache = str(tmp_path / "release-grype-db")
    for filename, value in (
        ("fixture-scan.json", _scan(cache=cache)),
        ("fixture-db.json", _database(cache=cache)),
        ("fixture-manifest.json", _manifest()),
    ):
        (tmp_path / filename).write_text(json.dumps(value), encoding="utf-8")
    _write_executable(
        tmp_path / "bin/date",
        f"printf '%s\\n' {shlex.quote(OBSERVED_AT)}\n",
    )
    manifest_file = shlex.quote(str(tmp_path / "fixture-manifest.json"))
    _write_executable(
        tmp_path / "bin/docker",
        f'test "$*" = \'buildx imagetools inspect '
        f"{environment['IMAGE_NAME']}@{ARTIFACT_DIGEST} --format {{{{json .Manifest}}}}'\n"
        + ("exit 9\n" if failure == "manifest" else f"cat {manifest_file}\n"),
    )
    call_log = shlex.quote(str(tmp_path / "grype-calls"))
    grype = _write_executable(
        tmp_path / "bin/grype",
        f'test "$HOME" = {shlex.quote(str(tmp_path))}\n'
        'test "$' + '{GRYPE_ONLY_FIXED+x}" != x\n'
        'test "$' + '{SYFT_EXCLUDE+x}" != x\n'
        'test "$' + '{GH_TOKEN+x}" != x\n'
        'test "$1" = "-c"\n'
        'test -f "$2"\n'
        "shift 2\n"
        f'printf "%s\\n" "$*" >> {call_log}\n'
        'case "$*" in\n'
        f'  "db update") exit {9 if failure == "update" else 0} ;;\n'
        '  "db status --output json")\n'
        f"    cat {shlex.quote(str(tmp_path / 'fixture-db.json'))}\n"
        f"    exit {9 if failure == 'status' else 0} ;;\n"
        f'  "{environment["IMAGE_NAME"]}@{ARTIFACT_DIGEST} --from registry '
        '--platform linux/amd64 --scope squashed --output json --fail-on high")\n'
        f"    cat {shlex.quote(str(tmp_path / 'fixture-scan.json'))}\n"
        f"    exit {9 if failure == 'scan' else 0} ;;\n"
        "  *) exit 64 ;;\n"
        "esac\n",
    )
    environment.update(
        {
            "HOME": str(tmp_path),
            "GRYPE_BIN": str(grype),
            "GRYPE_VERSION": "0.119.0",
            "GRYPE_RELEASE_URL": RELEASE_URL,
            "GRYPE_RELEASE_SHA256": RELEASE_SHA256,
            "GRYPE_BINARY_SHA256": hashlib.sha256(grype.read_bytes()).hexdigest(),
            "GRYPE_ONLY_FIXED": "true",
            "SYFT_EXCLUDE": "/**",
            "GH_TOKEN": "fixture-not-a-real-credential",
        }
    )
    return environment


@pytest.mark.parametrize(
    ("failure", "reason"),
    [
        ("update", "database-update-failed"),
        ("status", "database-status-failed"),
        ("scan", "scanner-nonzero"),
        ("manifest", "malformed-evidence"),
    ],
)
def test_vulnerability_shell_propagates_external_failures_with_rejection_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    reason: str,
) -> None:
    monkeypatch.delenv("HOME", raising=False)
    environment = _vulnerability_environment(tmp_path, failure)
    result = _execute(_shell("build", "Admit final-image vulnerability evidence"), environment)
    assert result.returncode != 0
    receipt_path = tmp_path / "release-vulnerability-evidence.json"
    assert receipt_path.is_file(), _vulnerability_shell_diagnostics(tmp_path, result)
    receipt = json.loads(receipt_path.read_text())
    assert receipt["admission"] == "rejected"
    assert receipt["reason"] == reason
    assert not Path(environment["GITHUB_OUTPUT"]).read_text()


def test_vulnerability_shell_has_clean_environment_explicit_config_and_hash_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("HOME", raising=False)
    environment = _vulnerability_environment(tmp_path)
    result = _execute(_shell("build", "Admit final-image vulnerability evidence"), environment)
    assert result.returncode == 0, _vulnerability_shell_diagnostics(tmp_path, result)
    assert (tmp_path / "grype-calls").read_text().splitlines() == [
        "db update",
        "db status --output json",
        f"{environment['IMAGE_NAME']}@{ARTIFACT_DIGEST} --from registry "
        "--platform linux/amd64 --scope squashed --output json --fail-on high",
    ]
    config = json.loads((tmp_path / "release-grype.json").read_text())
    assert config["db"] == {
        "cache-dir": str(tmp_path / "release-grype-db"),
        "update-url": "https://grype.anchore.io/databases/v6/latest.json",
        "auto-update": False,
        "validate-age": True,
        "validate-by-hash-on-start": True,
        "require-update-check": True,
        "max-allowed-built-age": "24h",
    }
    assert config["match-upstream-kernel-headers"] is True
    assert config["ignore"] == config["exclude"] == config["vex-documents"] == []
    evidence = (tmp_path / "release-vulnerability-evidence.json").read_bytes()
    assert Path(environment["GITHUB_OUTPUT"]).read_text().splitlines() == [
        f"vulnerability_evidence_sha256={hashlib.sha256(evidence).hexdigest()}",
        f"vulnerability_evidence_bytes={len(evidence)}",
        "vulnerability_match_count=0",
        f"repair_evidence_sha256={environment['REPAIR_EVIDENCE_SHA256']}",
    ]


@pytest.mark.parametrize("failed", [False, True])
def test_repair_collection_shell_binds_subject_source_and_propagates_failure(
    tmp_path: Path, failed: bool
) -> None:
    environment = _environment(tmp_path)
    arguments = tmp_path / "repair-arguments"
    executable = _write_executable(
        tmp_path / "bin/collector",
        'test "$PYTHONPATH" = "backend/src"\n'
        'printf "%s\\n" "$@" > "${RUNNER_TEMP}/repair-arguments"\n'
        + ("exit 9\n" if failed else ""),
    )
    shell = _shell("build", "Collect exact runtime repair evidence").replace(
        "backend/.venv/bin/python", shlex.quote(str(executable))
    )
    result = _execute(shell, environment)
    assert (result.returncode != 0) is failed
    assert arguments.read_text().splitlines() == [
        "-m",
        "scripts.release_repair_evidence",
        "--subject",
        environment["IMAGE_NAME"] + "@" + ARTIFACT_DIGEST,
        "--source-commit",
        SOURCE_COMMIT,
        "--policy",
        "docker/runtime/security/repaired-matches.v1.json",
        "--output",
        str(tmp_path / "release-runtime-repairs.json"),
    ]


@pytest.mark.parametrize("failure", ["", "download", "checksum"])
def test_scanner_download_verifies_archive_before_install(tmp_path: Path, failure: str) -> None:
    environment = _environment(tmp_path)
    archive = tmp_path / "fixture-grype.tar.gz"
    binary = b"#!/bin/sh\nexit 0\n"
    with tarfile.open(archive, "w:gz") as bundle:
        entry = tarfile.TarInfo("grype")
        entry.size = len(binary)
        entry.mode = 0o755
        bundle.addfile(entry, io.BytesIO(binary))
    _write_executable(
        tmp_path / "bin/curl",
        ("exit 9\n" if failure == "download" else "")
        + 'test "$1 $2 $3 $4 $5" = "--fail --location --proto =https --tlsv1.2"\n'
        + 'test "$6" = "--output"\n'
        + f'test "$8" = {shlex.quote(RELEASE_URL)}\n'
        + f'cp {shlex.quote(str(archive))} "$7"\n',
    )
    environment.update(
        {
            "GRYPE_VERSION": "0.119.0",
            "GRYPE_RELEASE_URL": RELEASE_URL,
            "GRYPE_RELEASE_SHA256": "0" * 64
            if failure == "checksum"
            else hashlib.sha256(archive.read_bytes()).hexdigest(),
            "GITHUB_ENV": str(tmp_path / "github-env"),
        }
    )
    result = _execute(_shell("build", "Download and verify pinned Grype release"), environment)
    installed = tmp_path / "grype-0.119.0/grype"
    if failure:
        assert result.returncode != 0
        assert not installed.exists()
    else:
        assert result.returncode == 0, result.stderr
        assert installed.read_bytes() == binary
        assert (
            f"GRYPE_BINARY_SHA256={hashlib.sha256(binary).hexdigest()}"
            in (tmp_path / "github-env").read_text()
        )

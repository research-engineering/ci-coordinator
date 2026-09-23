from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from scripts import scheduled_advisories as audit
from scripts import scheduled_release_scan as image_scan
from scripts.bounded_process import CommandResult, spawn
from scripts.ci_utility_checks import UtilityCommand
from scripts.dependency_audit import audit_inventory
from scripts.dependency_audit import commands as dependency_commands
from scripts.release_repair_admission import REPAIR_PREDICATE, SOURCE_PATHS
from scripts.scheduled_release_subject import (
    WORKFLOW_PATH,
    admit_attestation,
    admit_build_identity,
    admit_repair_attestation,
    latest_release,
    registry_subject,
    strict_json,
)
from scripts.tests.test_dependency_audit import uv_report
from scripts.tests.test_release_vulnerability_admission import SUBJECT as OWNER_SUBJECT
from scripts.tests.test_release_vulnerability_admission import _admit as admit_image_fixture
from scripts.tests.test_release_vulnerability_admission import _repair_evidence, _scan

REPO_ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40
OBSERVED = "2026-09-20T12:00:00Z"
REPOSITORY_ID = 1001
IMAGE = "ghcr.io/research-engineering/ci-coordinator"
SUBJECT = f"{IMAGE}@sha256:{'b' * 64}"


@pytest.fixture(autouse=True)
def configured_publisher(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI_COORDINATOR_RELEASE_REPOSITORY_ID", str(REPOSITORY_ID))


def release_page() -> dict[str, object]:
    repository = {"id": REPOSITORY_ID, "full_name": audit.REPOSITORY}
    return {
        "total_count": 1,
        "workflow_runs": [
            {
                "id": 42,
                "run_attempt": 2,
                "head_sha": SHA,
                "head_branch": "master",
                "name": "Release Artifact",
                "path": WORKFLOW_PATH,
                "event": "workflow_dispatch",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-09-19T12:00:00Z",
                "repository": repository,
                "head_repository": repository,
            }
        ],
    }


def attestation() -> list[dict[str, object]]:
    return [
        {
            "verificationResult": {
                "signature": {
                    "certificate": {
                        "runInvocationURI": f"https://github.com/{audit.REPOSITORY}/actions/runs/42/attempts/2",
                        "sourceRepositoryIdentifier": str(REPOSITORY_ID),
                        "sourceRepositoryURI": f"https://github.com/{audit.REPOSITORY}",
                        "sourceRepositoryDigest": SHA,
                        "sourceRepositoryRef": "refs/heads/master",
                        "buildConfigURI": f"https://github.com/{audit.REPOSITORY}/{WORKFLOW_PATH}@refs/heads/master",
                        "buildConfigDigest": SHA,
                        "runnerEnvironment": "github-hosted",
                    }
                },
                "statement": {
                    "predicateType": "https://slsa.dev/provenance/v1",
                    "subject": [{"name": IMAGE, "digest": {"sha256": "b" * 64}}],
                },
            }
        }
    ]


@pytest.mark.parametrize(
    "mutation", ["none", "subject", "source", "missing", "empty", "ambiguous", "run"]
)
def test_signed_repair_proof_requires_exact_payload_and_certificate(mutation: str) -> None:
    payload = attestation()
    result = cast(dict[str, object], payload[0]["verificationResult"])
    statement = cast(dict[str, object], result["statement"])
    proof = _repair_evidence()
    proof.update(subject=SUBJECT, sourceCommit=SHA, repoDigests=[SUBJECT])
    statement.update(predicateType=REPAIR_PREDICATE, predicate=proof)
    if mutation == "subject":
        proof["subject"] = OWNER_SUBJECT
    elif mutation == "source":
        proof["sourceCommit"] = "c" * 40
    elif mutation == "missing":
        statement.pop("predicate")
    elif mutation == "empty":
        statement["predicate"] = {}
    elif mutation == "run":
        signature = cast(dict[str, object], result["signature"])
        cast(dict[str, object], signature["certificate"])["runInvocationURI"] = "other"
    elif mutation == "ambiguous":
        payload.append(deepcopy(payload[0]))
        other_result = cast(dict[str, object], payload[1]["verificationResult"])
        other_statement = cast(dict[str, object], other_result["statement"])
        cast(dict[str, object], other_statement["predicate"])["observedAt"] = "2026-09-21T11:01:00Z"
    run = latest_release(release_page(), observed_at=OBSERVED)
    if mutation == "none":
        accepted = admit_repair_attestation(payload, run, SUBJECT)
        assert json.loads(accepted) == proof
        assert (
            admit_repair_attestation([payload[0], deepcopy(payload[0])], run, SUBJECT) == accepted
        )
    else:
        with pytest.raises(ValueError):
            admit_repair_attestation(payload, run, SUBJECT)


def test_disagreeing_attestation_stores_reject_before_image_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def execute(argv: tuple[str, ...], *args: object, **kwargs: object) -> CommandResult:
        calls.append(argv)
        if argv == ("gh", "--version"):
            content = "gh version 2.101.0 (2026-09-15)"
        elif argv[:2] == ("gh", "api"):
            content = json.dumps(release_page())
        elif argv[:2] == ("docker", "buildx"):
            content = json.dumps(
                {
                    "mediaType": "application/vnd.oci.image.index.v1+json",
                    "digest": "sha256:" + "b" * 64,
                }
            )
        elif argv[:3] == ("gh", "attestation", "verify"):
            payload = attestation()
            if REPAIR_PREDICATE in argv:
                proof = _repair_evidence()
                proof.update(subject=SUBJECT, sourceCommit=SHA, repoDigests=[SUBJECT])
                if "--bundle-from-oci" in argv:
                    proof["observedAt"] = "2026-09-21T11:01:00Z"
                result = cast(dict[str, object], payload[0]["verificationResult"])
                cast(dict[str, object], result["statement"]).update(
                    predicateType=REPAIR_PREDICATE, predicate=proof
                )
            content = json.dumps(payload)
        else:
            pytest.fail("unverified repair evidence reached image inspection")
        return CommandResult(0, content, "")

    monkeypatch.setattr(image_scan, "execute", execute)
    monkeypatch.setattr(image_scan, "now", lambda: OBSERVED)
    with pytest.raises(ValueError, match="stores-disagree"):
        image_scan.scan_release(REPO_ROOT, {}, tmp_path)
    assert not any(argv[:2] == ("docker", "pull") for argv in calls)


def receipt(scope: str = "python") -> dict[str, object]:
    return {
        "schemaVersion": audit.SCHEMA,
        "repository": audit.REPOSITORY,
        "scope": scope,
        "sourceCommit": SHA,
        "state": "passed",
        "observedAt": OBSERVED,
        "startedAt": OBSERVED,
        "maxAgeSeconds": 129600,
        "inputs": audit.input_digests(REPO_ROOT, scope),
        "commands": [
            audit.command_fact(
                command.argv,
                CommandResult(0, "audit completed", ""),
                cwd=command.cwd.relative_to(REPO_ROOT).as_posix(),
            )
            for command in audit.package_commands(REPO_ROOT, scope)
        ],
    }


@pytest.mark.parametrize(
    "changed",
    [
        "Dockerfile",
        "docker/runtime/security/repaired-matches.v1.json",
        "docker/runtime/security/zlib.patch",
    ],
)
def test_scheduled_image_receipt_binds_every_repair_input(
    monkeypatch: pytest.MonkeyPatch, changed: str
) -> None:
    paths = SOURCE_PATHS | {
        "docs/specs/ci-coordinator-release/vulnerability-gate-policy.v1.json",
        "docker/runtime/security/repaired-matches.v1.json",
    }
    values = {name: name for name in paths}
    monkeypatch.setattr(audit, "repository_paths", lambda root: tuple(sorted(paths)))
    monkeypatch.setattr(audit, "source_text", lambda root, name: values[name])
    before = audit.input_digests(REPO_ROOT, "released-image")
    assert set(before) == paths
    values[changed] += "changed"
    after = audit.input_digests(REPO_ROOT, "released-image")
    assert {name for name in paths if before[name] != after[name]} == {changed}


def test_latest_release_and_verified_certificate_bind_immutable_subject() -> None:
    run = latest_release(release_page(), observed_at=OBSERVED)
    assert run.tag == f"{IMAGE}:run-42-2"
    assert (
        registry_subject(
            {"mediaType": "application/vnd.oci.image.index.v1+json", "digest": "sha256:" + "b" * 64}
        )
        == SUBJECT
    )
    admit_attestation(attestation(), run, SUBJECT)
    identity = {
        "schemaVersion": "ci-coordinator-build-identity/v1",
        "releaseIdentity": "d" * 64,
        "sourceCommit": SHA,
        "productionEligible": True,
    }
    raw = (json.dumps(identity, sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert admit_build_identity(raw, run) == "d" * 64
    identity["sourceCommit"] = "f" * 40
    with pytest.raises(ValueError, match="build-identity-binding"):
        admit_build_identity(
            (json.dumps(identity, sort_keys=True, separators=(",", ":")) + "\n").encode(), run
        )


def test_release_discovery_requires_configured_id_not_the_provider_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CI_COORDINATOR_RELEASE_REPOSITORY_ID")
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", str(REPOSITORY_ID))
    with pytest.raises(ValueError, match="CI_COORDINATOR_RELEASE_REPOSITORY_ID"):
        latest_release(release_page(), observed_at=OBSERVED)


@pytest.mark.parametrize("repository_field", ["repository", "head_repository"])
@pytest.mark.parametrize(
    "field,value",
    [("id", 1002), ("id", True), ("id", "1001"), ("full_name", "foreign/ci-coordinator")],
)
def test_release_discovery_checks_both_repository_identities(
    repository_field: str, field: str, value: object
) -> None:
    page = release_page()
    runs = cast(list[dict[str, object]], page["workflow_runs"])
    runs[0][repository_field] = {
        **cast(dict[str, object], runs[0][repository_field]),
        field: value,
    }
    with pytest.raises(ValueError):
        latest_release(page, observed_at=OBSERVED)


def test_repository_recreation_requires_fresh_operator_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = release_page()
    runs = cast(list[dict[str, object]], page["workflow_runs"])
    for key in ("repository", "head_repository"):
        runs[0][key] = {"id": 3001, "full_name": audit.REPOSITORY}
    with pytest.raises(ValueError, match="release-repository-identity"):
        latest_release(page, observed_at=OBSERVED)
    monkeypatch.setenv("CI_COORDINATOR_RELEASE_REPOSITORY_ID", "3001")
    run = latest_release(page, observed_at=OBSERVED)
    with pytest.raises(ValueError, match="certificate-binding"):
        admit_attestation(attestation(), run, SUBJECT)
    payload = attestation()
    result = cast(dict[str, object], payload[0]["verificationResult"])
    signature = cast(dict[str, object], result["signature"])
    cast(dict[str, object], signature["certificate"])["sourceRepositoryIdentifier"] = "3001"
    admit_attestation(payload, run, SUBJECT)
    monkeypatch.delenv("CI_COORDINATOR_RELEASE_REPOSITORY_ID")
    with pytest.raises(ValueError, match="CI_COORDINATOR_RELEASE_REPOSITORY_ID"):
        admit_attestation(payload, run, SUBJECT)


@pytest.mark.parametrize(
    "subject",
    [
        "ghcr.io/foreign/ci-coordinator@sha256:" + "b" * 64,
        "ghcrXio/research-engineering/ci-coordinator@sha256:" + "b" * 64,
        IMAGE + ":latest",
        IMAGE + "@sha256:" + "B" * 64,
    ],
)
def test_release_attestation_rejects_other_image_with_the_same_digest(subject: str) -> None:
    with pytest.raises(ValueError, match="subject-binding"):
        admit_attestation(
            attestation(), latest_release(release_page(), observed_at=OBSERVED), subject
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("runInvocationURI", f"https://github.com/{audit.REPOSITORY}/actions/runs/42/attempts/1"),
        ("sourceRepositoryIdentifier", "999"),
        ("sourceRepositoryURI", "https://github.com/foreign/repository"),
        ("sourceRepositoryDigest", "e" * 40),
        ("sourceRepositoryRef", "refs/heads/foreign"),
        ("runnerEnvironment", "self-hosted"),
        ("buildConfigDigest", "e" * 40),
        ("buildConfigURI", "https://github.com/foreign/workflow"),
    ],
)
def test_valid_signature_for_wrong_run_or_source_is_not_latest_release(
    field: str, value: str
) -> None:
    payload = attestation()
    result = cast(dict[str, object], payload[0]["verificationResult"])
    signature = cast(dict[str, object], result["signature"])
    certificate = cast(dict[str, object], signature["certificate"])
    certificate[field] = value
    with pytest.raises(ValueError, match="certificate-binding"):
        admit_attestation(payload, latest_release(release_page(), observed_at=OBSERVED), SUBJECT)


@pytest.mark.parametrize("payload", [[], {}, [{"verificationResult": {}}]])
def test_missing_attestation_is_not_success(payload: object) -> None:
    with pytest.raises(ValueError):
        admit_attestation(payload, latest_release(release_page(), observed_at=OBSERVED), SUBJECT)


def test_retagged_digest_cannot_borrow_release_attestation() -> None:
    with pytest.raises(ValueError, match="subject-binding"):
        admit_attestation(
            attestation(),
            latest_release(release_page(), observed_at=OBSERVED),
            f"{IMAGE}@sha256:{'c' * 64}",
        )


@pytest.mark.parametrize(
    "field,value",
    [("conclusion", "failure"), ("event", "push"), ("head_branch", "other"), ("run_attempt", True)],
)
def test_release_discovery_rejects_nonrelease_runs(field: str, value: object) -> None:
    page = release_page()
    runs = cast(list[dict[str, object]], page["workflow_runs"])
    runs[0][field] = value
    with pytest.raises(ValueError):
        latest_release(page, observed_at=OBSERVED)


def test_release_discovery_rejects_missing_partial_and_ambiguous_pages() -> None:
    with pytest.raises(ValueError):
        latest_release({"total_count": 0, "workflow_runs": []}, observed_at=OBSERVED)
    page = release_page()
    page["total_count"] = 2
    with pytest.raises(ValueError, match="page-incomplete"):
        latest_release(page, observed_at=OBSERVED)
    runs = cast(list[dict[str, object]], page["workflow_runs"])
    other = deepcopy(runs[0])
    other["id"] = 43
    runs.append(other)
    with pytest.raises(ValueError, match="ambiguous-newest"):
        latest_release(page, observed_at=OBSERVED)


def test_receipt_acceptance_binds_age_source_and_all_native_results() -> None:
    audit.admit_fresh_receipt(
        receipt(), root=REPO_ROOT, scope="python", source_commit=SHA, observed_at=OBSERVED
    )
    for key, value in (
        ("sourceCommit", "d" * 40),
        ("state", "failed"),
        ("inputs", {}),
        ("commands", []),
        ("observedAt", "2026-09-18T23:59:59Z"),
        ("observedAt", "2026-09-20T12:00:01Z"),
    ):
        changed = receipt()
        changed[key] = value
        with pytest.raises(ValueError):
            audit.admit_fresh_receipt(
                changed, root=REPO_ROOT, scope="python", source_commit=SHA, observed_at=OBSERVED
            )


def test_exit_zero_with_lifecycle_error_is_not_a_successful_receipt() -> None:
    changed = receipt()
    commands = cast(list[dict[str, object]], changed["commands"])
    commands[0]["processError"] = True
    commands[0]["failureKind"] = "pipe-closure"
    with pytest.raises(ValueError, match="command-failed"):
        audit.admit_fresh_receipt(
            changed, root=REPO_ROOT, scope="python", source_commit=SHA, observed_at=OBSERVED
        )
    assert "private failure" not in json.dumps(changed)


@pytest.mark.parametrize("scope", ["python", "npm", "go"])
def test_receipts_require_exact_owned_native_plan_and_inputs(scope: str) -> None:
    observed = receipt(scope)
    audit.admit_fresh_receipt(
        observed, root=REPO_ROOT, scope=scope, source_commit=SHA, observed_at=OBSERVED
    )
    changed = deepcopy(observed)
    inputs = cast(dict[str, str], changed["inputs"])
    inputs[next(iter(inputs))] = "f" * 64
    with pytest.raises(ValueError, match="inputs-mismatch"):
        audit.admit_fresh_receipt(
            changed, root=REPO_ROOT, scope=scope, source_commit=SHA, observed_at=OBSERVED
        )


@pytest.mark.parametrize("scope", ["python", "go"])
@pytest.mark.parametrize(
    "mutation", ["omit", "duplicate", "reorder", "foreign-argv", "foreign-cwd"]
)
def test_each_command_plan_operand_is_required(scope: str, mutation: str) -> None:
    observed = receipt(scope)
    commands = cast(list[dict[str, object]], observed["commands"])
    assert len(commands) >= 2
    if mutation == "omit":
        commands.pop()
    elif mutation == "duplicate":
        commands[-1] = deepcopy(commands[0])
    elif mutation == "reorder":
        commands.reverse()
    elif mutation == "foreign-argv":
        commands[0]["argv"] = ["uv", "audit"] if scope == "python" else ["govulncheck", "-version"]
    else:
        commands[0]["cwd"] = "foreign-directory"
    with pytest.raises(ValueError, match="command-plan-mismatch"):
        audit.admit_fresh_receipt(
            observed, root=REPO_ROOT, scope=scope, source_commit=SHA, observed_at=OBSERVED
        )


def test_go_version_alone_does_not_establish_vulnerability_coverage() -> None:
    observed = receipt("go")
    observed["commands"] = cast(list[dict[str, object]], observed["commands"])[:1]
    with pytest.raises(ValueError, match="command-plan-mismatch"):
        audit.admit_fresh_receipt(
            observed, root=REPO_ROOT, scope="go", source_commit=SHA, observed_at=OBSERVED
        )


def test_flat_certificate_shape_is_required() -> None:
    payload = attestation()
    result = cast(dict[str, object], payload[0]["verificationResult"])
    signature = cast(dict[str, object], result["signature"])
    signature["certificate"] = {"extensions": signature["certificate"]}
    with pytest.raises(ValueError, match="certificate-binding"):
        admit_attestation(payload, latest_release(release_page(), observed_at=OBSERVED), SUBJECT)


def test_image_readback_correlates_existing_owner_evidence(tmp_path: Path) -> None:
    admission = admit_image_fixture(tmp_path)
    policy = tmp_path / image_scan.POLICY_PATH
    policy.parent.mkdir(parents=True)
    policy.write_bytes((tmp_path / "policy.json").read_bytes())
    evidence = tmp_path / "released-image-vulnerabilities.json"
    evidence.write_bytes((tmp_path / "evidence.json").read_bytes())
    observed: dict[str, object] = {
        "releasedSubject": {"image": OWNER_SUBJECT, "platform": "linux/amd64"},
        "vulnerabilityEvidenceSha256": admission.evidence_sha256,
        "commands": [_scan_command(tmp_path, 0)],
    }
    image_scan.admit_scan_receipt(tmp_path, observed, tmp_path)
    original = evidence.read_bytes()
    for mutation in ("hash", "subject", "policy", "rejected", "index", "child"):
        changed = json.loads(original)
        altered = deepcopy(observed)
        if mutation == "hash":
            altered["vulnerabilityEvidenceSha256"] = "f" * 64
        elif mutation == "subject":
            changed["subject"]["image"] = SUBJECT
        elif mutation == "policy":
            changed["policySha256"] = "f" * 64
        elif mutation == "rejected":
            changed["admission"] = "rejected"
        elif mutation == "index":
            changed["manifest"]["indexDigest"] = "sha256:" + "f" * 64
        else:
            changed["manifest"].pop("platformManifestDigest")
        raw = (json.dumps(changed) + "\n").encode()
        evidence.write_bytes(raw)
        if mutation != "hash":
            altered["vulnerabilityEvidenceSha256"] = audit.digest(raw)
        with pytest.raises(ValueError):
            image_scan.admit_scan_receipt(tmp_path, altered, tmp_path)
    evidence.unlink()
    with pytest.raises((ValueError, OSError)):
        image_scan.admit_scan_receipt(tmp_path, observed, tmp_path)


def _scan_command(directory: Path, exit_code: int) -> dict[str, object]:
    return audit.command_fact(
        (
            str(directory / "grype"),
            "-c",
            str(directory / "grype.json"),
            OWNER_SUBJECT,
            "--from",
            "registry",
            "--platform",
            "linux/amd64",
            "--scope",
            "squashed",
            "--output",
            "json",
            "--fail-on",
            "high",
        ),
        CommandResult(exit_code, (directory / "scan.json").read_text(), ""),
        cwd=".",
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "none",
        "db-exit",
        "status-exit",
        "scan-exit",
        "stdout",
        "argv",
        "duplicate",
        "receipt",
        "hash",
        "missing-hash",
        "unknown",
        "unproved",
        "layer",
    ],
)
def test_repaired_scan_and_scheduled_summary_compose_without_a_status_waiver(
    tmp_path: Path, mutation: str
) -> None:
    from scripts.release_vulnerability_admission import VulnerabilityAdmissionError
    from scripts.tests.test_release_vulnerability_admission import OBSERVED_AT

    proof = _repair_evidence()
    finding = {
        "vulnerability": {
            "id": "CVE-2026-82049",
            "severity": "High",
            "fix": {"state": "not-fixed", "versions": []},
        },
        "artifact": {
            "name": "python",
            "version": "3.13.15",
            "type": "binary",
            "purl": "pkg:generic/python@3.13.15",
            "locations": [
                {"path": path, "layerID": proof["runtimeLayer"], "annotations": {"evidence": kind}}
                for path, kind in [
                    ("/usr/local/bin/python3.13", "primary"),
                    ("/usr/local/lib/libpython3.13.so.1.0", "supporting"),
                ]
            ],
        },
    }
    if mutation == "unknown":
        cast(dict[str, object], finding["vulnerability"])["severity"] = "Unknown"
    elif mutation == "unproved":
        cast(dict[str, object], finding["vulnerability"])["id"] = "CVE-2026-99999"
    elif mutation == "layer":
        cast(list[dict[str, object]], cast(dict[str, object], finding["artifact"])["locations"])[
            0
        ].pop("layerID")
    if mutation in {"unknown", "unproved", "layer"}:
        with pytest.raises(VulnerabilityAdmissionError):
            admit_image_fixture(tmp_path, scan=_scan([finding]), scanner_exit_code=2)
    else:
        admit_image_fixture(tmp_path, scan=_scan([finding]), scanner_exit_code=2)
    evidence = tmp_path / "released-image-vulnerabilities.json"
    evidence.write_bytes((tmp_path / "evidence.json").read_bytes())
    commands = [
        audit.command_fact(
            (str(tmp_path / "grype"), "-c", str(tmp_path / "grype.json"), "db", "update"),
            CommandResult(0, "", ""),
            cwd=".",
        ),
        audit.command_fact(
            (
                str(tmp_path / "grype"),
                "-c",
                str(tmp_path / "grype.json"),
                "db",
                "status",
                "--output",
                "json",
            ),
            CommandResult(0, "", ""),
            cwd=".",
        ),
        _scan_command(tmp_path, 2),
    ]
    observed = receipt()
    observed.update(
        scope="released-image",
        inputs=audit.input_digests(REPO_ROOT, "released-image"),
        startedAt=OBSERVED_AT,
        observedAt=OBSERVED_AT,
        commands=commands,
        releasedSubject={"image": OWNER_SUBJECT, "platform": "linux/amd64"},
    )
    if mutation == "db-exit":
        commands[0]["exitCode"] = 2
    elif mutation == "status-exit":
        commands[1]["exitCode"] = 2
    elif mutation == "scan-exit":
        commands[2]["exitCode"] = 0
    elif mutation == "stdout":
        commands[2]["stdoutSha256"] = "0" * 64
    elif mutation == "argv":
        cast(list[str], commands[2]["argv"])[3] = SUBJECT
    elif mutation == "duplicate":
        commands.append(deepcopy(commands[2]))
    elif mutation in {"receipt", "missing-hash"}:
        changed = json.loads(evidence.read_bytes())
        if mutation == "receipt":
            changed["admission"] = "rejected"
        else:
            changed["inputs"].pop("scanReportSha256")
            commands[2].pop("stdoutSha256")
        evidence.write_text(json.dumps(changed))
    observed["vulnerabilityEvidenceSha256"] = (
        "0" * 64 if mutation == "hash" else audit.digest(evidence.read_bytes())
    )

    def admit() -> None:
        audit.admit_fresh_receipt(
            observed,
            root=REPO_ROOT,
            scope="released-image",
            source_commit=SHA,
            observed_at=OBSERVED_AT,
            evidence_directory=tmp_path,
        )

    if mutation == "none":
        admit()
    else:
        with pytest.raises(ValueError):
            admit()


def test_current_native_pnpm_lock_stream_is_nonempty() -> None:
    content = (REPO_ROOT / "pnpm-lock.yaml").read_text()
    assert content.count("---\n") == 2
    audit.admit_npm_lock(content)


@pytest.mark.parametrize(
    "mutation", ["empty-tail", "broken-tail", "third-document", "bootstrap-only"]
)
def test_multidocument_lock_cannot_hide_invalid_project_content(mutation: str) -> None:
    content = (REPO_ROOT / "pnpm-lock.yaml").read_text()
    bootstrap = content.split("\n---\n", 1)[0]
    if mutation == "empty-tail":
        content = bootstrap + "\n---\n"
    elif mutation == "broken-tail":
        content = bootstrap + "\n---\nimporters: [\n"
    elif mutation == "third-document":
        content += "\n---\ninvalid: true\n"
    else:
        content = bootstrap
    with pytest.raises(ValueError):
        audit.admit_npm_lock(content)


def test_input_parse_failure_retains_failed_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = receipt()
    initial.update({"scope": "npm", "state": "failed", "inputs": {}, "commands": []})
    monkeypatch.setattr(audit, "new_receipt", lambda _root, _scope, _sha: initial)

    def fail_intake(_root: Path, _scope: str) -> dict[str, str]:
        raise ValueError("invalid-npm-lock-stream")

    monkeypatch.setattr(audit, "input_digests", fail_intake)
    output = tmp_path / "npm.json"
    assert audit.main(["npm", "--source-commit", SHA, "--output", str(output)]) == 1
    retained = json.loads(output.read_bytes())
    assert retained["state"] == "failed"
    assert retained["commands"] == []
    assert retained["observedAt"]


def test_unconfigured_release_scan_fails_before_provider_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    initial = receipt()
    initial.update({"scope": "released-image", "state": "failed", "inputs": {}, "commands": []})
    monkeypatch.setattr(audit, "new_receipt", lambda _root, _scope, _sha: initial)
    monkeypatch.delenv("CI_COORDINATOR_RELEASE_REPOSITORY_ID")

    def forbidden(*args: object) -> None:
        pytest.fail("unconfigured publisher reached provider access")

    monkeypatch.setattr(image_scan, "scan_release", forbidden)
    output = tmp_path / "released-image.json"
    assert audit.main(["released-image", "--source-commit", SHA, "--output", str(output)]) == 1
    retained = json.loads(output.read_bytes())
    assert retained["state"] == "failed"
    assert retained["commands"] == []
    assert retained["reason"] == "native-or-subject-admission-failed"


@pytest.mark.parametrize("timestamp", ["2026-09-18T23:59:59Z", "2026-09-20T12:00:01Z"])
def test_freshness_rejects_otherwise_valid_scan_windows(timestamp: str) -> None:
    observed = receipt()
    observed["startedAt"] = timestamp
    observed["observedAt"] = timestamp
    with pytest.raises(ValueError, match="stale-or-future"):
        audit.admit_fresh_receipt(
            observed, root=REPO_ROOT, scope="python", source_commit=SHA, observed_at=OBSERVED
        )


def test_python_audits_continue_independent_locks_after_a_finding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = dependency_commands(REPO_ROOT)[:-1]
    monkeypatch.setattr(audit, "dependency_commands", lambda _root: commands)
    report = uv_report()
    report["summary"]["audited_packages"] = len(audit_inventory(REPO_ROOT, commands[1]).packages)
    statuses = iter(
        (CommandResult(1, "GHSA-abcd-1234-efgh", ""), CommandResult(0, json.dumps(report), ""))
    )
    seen: list[tuple[str, ...]] = []

    def fake_execute(argv: tuple[str, ...], _root: Path, environment: object) -> CommandResult:
        assert isinstance(environment, dict) and environment["UV_NO_CACHE"] == "true"
        seen.append(argv)
        return next(statuses)

    monkeypatch.setattr(audit, "execute", fake_execute)
    observed: dict[str, object] = {}
    audit.scan_packages(REPO_ROOT, "python", observed)
    assert seen == [command.argv for command in commands]
    assert observed["state"] == "failed"
    assert len(cast(list[object], observed["commands"])) == 2


def test_scheduled_zero_exit_with_malformed_audit_json_is_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "execute", lambda *_args: CommandResult(0, "clean", ""))
    observed: dict[str, object] = {}
    audit.scan_packages(REPO_ROOT, "python", observed)
    assert observed["state"] == "failed"
    rows = cast(list[dict[str, object]], observed["commands"])
    assert rows and all(row["admissionError"] == "ValueError" for row in rows)


def test_scheduled_receipt_cannot_hide_machine_report_admission_failure() -> None:
    observed = receipt()
    rows = cast(list[dict[str, object]], observed["commands"])
    rows[0]["admissionError"] = "ValidationError"
    with pytest.raises(ValueError, match="command-failed"):
        audit.admit_fresh_receipt(
            observed,
            root=REPO_ROOT,
            scope="python",
            source_commit=SHA,
            observed_at=OBSERVED,
        )


@pytest.mark.parametrize("content", ['{"x":1,"x":2}', '{"x":NaN}'])
def test_provider_json_rejects_ambiguous_values(content: str) -> None:
    with pytest.raises(ValueError):
        strict_json(content)


@pytest.mark.parametrize("scope", ["python", "npm", "go"])
def test_empty_native_plan_cannot_produce_clean_coverage(
    scope: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "dependency_commands", lambda _root: ())
    monkeypatch.setattr(audit, "utility_commands", lambda _root, _check: ())
    with pytest.raises(ValueError, match="empty-native-audit-plan"):
        audit.scan_packages(tmp_path, scope, {})


@pytest.mark.parametrize("output", ["", "{}", "Usage: govulncheck [flags] [patterns]\n"])
def test_scheduled_go_scan_rejects_zero_exit_without_completed_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    command = UtilityCommand(("govulncheck", "-format=text", "./..."), tmp_path, "govulncheck")
    monkeypatch.setattr(audit, "package_commands", lambda *_args: (command,))
    monkeypatch.setattr(audit, "execute", lambda *_args: CommandResult(0, output, ""))
    observed: dict[str, object] = {"state": "failed"}
    with pytest.raises(ValueError, match="complete clean symbol-scan"):
        audit.scan_packages(tmp_path, "go", observed)
    assert observed["state"] == "failed"
    rows = cast(list[dict[str, object]], observed["commands"])
    assert len(rows) == 1 and rows[0]["exitCode"] == 0


def test_retagged_image_is_rejected_before_pull_or_container_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, ...]] = []
    responses = iter(
        (
            "gh version 2.101.0 (2026-09-15)\n",
            json.dumps(release_page()),
            json.dumps(
                {
                    "mediaType": "application/vnd.oci.image.index.v1+json",
                    "digest": "sha256:" + "c" * 64,
                }
            ),
            json.dumps(attestation()),
        )
    )

    def fake_execute(argv: tuple[str, ...], _root: Path, _environment: object) -> CommandResult:
        seen.append(argv)
        return CommandResult(0, next(responses), "")

    monkeypatch.setattr(image_scan, "execute", fake_execute)
    monkeypatch.setattr(image_scan, "now", lambda: OBSERVED)
    observed: dict[str, object] = {}
    with pytest.raises(ValueError, match="subject-binding"):
        image_scan.scan_release(tmp_path, observed, tmp_path / "receipts")
    assert len(seen) == 4
    assert seen[-1][3] == f"oci://{IMAGE}@sha256:{'c' * 64}"
    assert all(
        command[:2] not in {("docker", "pull"), ("docker", "create"), ("docker", "run")}
        for command in seen
    )
    assert observed.get("state") != "passed"


def test_schedule_has_independent_scopes_read_only_permissions_and_no_full_check() -> None:
    from ruamel.yaml import YAML

    root = Path(__file__).resolve().parents[2]
    workflow = YAML(typ="safe").load(
        (root / ".github/workflows/scheduled-advisories.yml").read_text()
    )
    assert workflow["on"] == {"schedule": [{"cron": "23 3 * * *"}], "workflow_dispatch": None}
    assert workflow["permissions"] == {}
    jobs = workflow["jobs"]
    assert jobs["packages"]["strategy"] == {
        "fail-fast": False,
        "matrix": {"scope": ["python", "npm", "go"]},
    }
    assert jobs["coverage"]["if"] == "always()"
    assert set(jobs["coverage"]["needs"]) == {"packages", "released-image"}
    guard = jobs["coverage"]["steps"][0]
    assert guard["env"] == {
        "PACKAGES_RESULT": "${{ needs.packages.result }}",
        "RELEASED_IMAGE_RESULT": "${{ needs.released-image.result }}",
    }
    for job in jobs.values():
        assert set(job["permissions"].values()) == {"read"}
        for step in job["steps"]:
            assert "python-persistence" not in step.get("run", "")
            assert "continue-on-error" not in step


@pytest.mark.parametrize(
    "packages,image,expected",
    [
        ("success", "success", 0),
        ("failure", "success", 1),
        ("success", "failure", 1),
        ("cancelled", "success", 1),
        ("success", "skipped", 1),
    ],
)
def test_coverage_cannot_pass_after_prerequisite_job_failure(
    packages: str,
    image: str,
    expected: int,
    tmp_path: Path,
) -> None:
    from ruamel.yaml import YAML

    workflow = YAML(typ="safe").load(
        (REPO_ROOT / ".github/workflows/scheduled-advisories.yml").read_text()
    )
    guard = workflow["jobs"]["coverage"]["steps"][0]["run"]
    result = spawn(
        "bash",
        ("-c", guard),
        cwd=tmp_path,
        env={"PACKAGES_RESULT": packages, "RELEASED_IMAGE_RESULT": image},
        max_buffer=1024,
        timeout_seconds=3,
    )
    assert result.error is None
    assert result.status == expected

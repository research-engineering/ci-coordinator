from __future__ import annotations

import io
import json
import os
import re
import sys
import tarfile
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from scripts import release_repair_evidence as collector
from scripts.bounded_process import CommandResult, spawn
from scripts.release_repair_admission import (
    INSTALLED_PATHS,
    SOURCE_PATHS,
    RepairEvidence,
    RepairPolicy,
    _expected_witnesses,
    admit_local_qualification,
    evidence_bytes,
    file_hash,
    occurrence_is_repaired,
    read_policy,
    validate_evidence,
)
from scripts.release_repair_evidence import archive_file, cleanup, command

ROOT = Path(__file__).resolve().parents[2]
SUBJECT = "ghcr.io/research-engineering/ci-coordinator@sha256:" + "a" * 64
IMAGE = "sha256:" + "b" * 64
LAYER = "sha256:" + "c" * 64
COMMIT = "d" * 40
OBSERVED = datetime(2026, 9, 21, 12, tzinfo=UTC)


def policy_value() -> dict[str, object]:
    return {
        "schemaVersion": "ci-coordinator.runtime-repair-policy/v1",
        "validFrom": "2026-09-21T00:00:00Z",
        "validUntil": "2026-10-05T00:00:00Z",
        "sourceInputs": {path: file_hash(ROOT / path) for path in SOURCE_PATHS},
        "installedFiles": dict.fromkeys(INSTALLED_PATHS, "e" * 64),
        "occurrences": [
            {
                "cve": "CVE-2026-82049",
                "severity": "High",
                "package": "python",
                "version": "3.13.16",
                "packageType": "binary",
                "purl": "pkg:generic/python@3.13.16",
                "locations": [
                    {"path": "/usr/local/bin/python3.13", "evidence": "primary"},
                    {"path": "/usr/local/lib/libpython3.13.so.1.0", "evidence": "supporting"},
                ],
            }
        ],
    }


def evidence_value(policy: RepairPolicy) -> dict[str, object]:
    return {
        "schemaVersion": "ci-coordinator.runtime-repair-evidence/v1",
        "sourceKind": "registry",
        "subject": SUBJECT,
        "repoDigests": [SUBJECT],
        "imageId": IMAGE,
        "runtimeLayer": LAYER,
        "sourceCommit": COMMIT,
        "productionEligible": True,
        "observedAt": OBSERVED.isoformat(),
        "policySha256": "f" * 64,
        "sourceInputs": policy.sourceInputs,
        "installedFiles": policy.installedFiles,
        "witnesses": _expected_witnesses(),
    }


def admit(policy: RepairPolicy, evidence: dict[str, object]) -> None:
    validate_evidence(
        policy=policy,
        policy_sha256="f" * 64,
        evidence=RepairEvidence.model_validate(evidence),
        subject=SUBJECT,
        image_id=IMAGE,
        source_commit=COMMIT,
        observed=OBSERVED,
    )


def test_exact_owner_policy_and_evidence_are_admitted(tmp_path: Path) -> None:
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(policy_value()))
    digest, policy = read_policy(path, ROOT)
    assert digest == file_hash(path)
    admit(policy, evidence_value(policy))


@pytest.mark.parametrize(
    "field,value",
    [
        ("sourceKind", "local"),
        ("subject", SUBJECT + "x"),
        ("repoDigests", []),
        ("repoDigests", [SUBJECT, "other"]),
        ("imageId", "sha256:" + "1" * 64),
        ("sourceCommit", "1" * 40),
        ("productionEligible", False),
        ("productionEligible", 1),
        ("policySha256", "1" * 64),
        ("sourceInputs", {}),
        ("installedFiles", {}),
        ("witnesses", {}),
        ("observedAt", "2026-09-21T12:00:01Z"),
        ("observedAt", "2026-09-20T23:59:59Z"),
    ],
)
def test_each_release_binding_is_necessary(field: str, value: object) -> None:
    policy = RepairPolicy.model_validate(policy_value())
    evidence = evidence_value(policy)
    evidence[field] = value
    with pytest.raises((ValueError, ValidationError)):
        admit(policy, evidence)


@pytest.mark.parametrize("name", sorted(_expected_witnesses()))
def test_every_causal_witness_is_required(name: str) -> None:
    policy = RepairPolicy.model_validate(policy_value())
    evidence = evidence_value(policy)
    witnesses = deepcopy(_expected_witnesses())
    witnesses[name] = {"state": "passed"}
    evidence["witnesses"] = witnesses
    with pytest.raises(ValueError, match="causal-witnesses"):
        admit(policy, evidence)


def test_numeric_truth_cannot_replace_a_boolean_witness() -> None:
    policy = RepairPolicy.model_validate(policy_value())
    evidence = evidence_value(policy)
    witnesses = _expected_witnesses()
    witnesses["python-final"] = {"cve": "CVE-2026-82049", "safeFilters": {"data": 1, "tar": True}}
    evidence["witnesses"] = witnesses
    with pytest.raises(ValueError, match="causal-witnesses"):
        admit(policy, evidence)


@pytest.mark.parametrize("expiry", ["2026-09-21T12:00:00Z", "2026-09-20T00:00:00Z"])
def test_expiry_boundary_is_exclusive(expiry: str) -> None:
    value = policy_value()
    value["validUntil"] = expiry
    policy = RepairPolicy.model_validate(value)
    with pytest.raises(ValueError, match="expired"):
        admit(policy, evidence_value(policy))


@pytest.mark.parametrize(
    "field,value",
    [
        ("sourceInputs", {}),
        ("installedFiles", {}),
        ("validUntil", "2026-10-06T00:00:00Z"),
        ("occurrences", []),
    ],
)
def test_policy_cannot_broaden_its_source_file_or_time_universe(
    tmp_path: Path, field: str, value: object
) -> None:
    document = policy_value()
    document[field] = value
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(document))
    with pytest.raises((ValueError, ValidationError)):
        read_policy(path, ROOT)


@pytest.mark.parametrize(
    "change",
    [
        "cve",
        "severity",
        "package",
        "version",
        "package_type",
        "purl",
        "foreign-layer",
        "foreign-path",
        "duplicate",
        "extra",
    ],
)
def test_repair_does_not_admit_a_different_scanner_occurrence(change: str) -> None:
    policy = RepairPolicy.model_validate(policy_value())
    evidence = RepairEvidence.model_validate(evidence_value(policy))
    values = {
        "cve": "CVE-2026-82049",
        "severity": "High",
        "package": "python",
        "version": "3.13.16",
        "package_type": "binary",
        "purl": "pkg:generic/python@3.13.16",
    }
    locations = tuple((item.path, item.evidence, LAYER) for item in policy.occurrences[0].locations)
    assert occurrence_is_repaired(policy, evidence, **values, locations=locations)
    if change in values:
        values[change] = "foreign"
    elif change == "foreign-layer":
        locations = ((locations[0][0], "primary", "sha256:" + "2" * 64), locations[1])
    elif change == "foreign-path":
        locations = (("/other/python3.13", "primary", LAYER), locations[1])
    elif change == "duplicate":
        locations = (*locations, locations[0])
    else:
        locations = (*locations, ("/other/python3.13", "supporting", LAYER))
    assert not occurrence_is_repaired(policy, evidence, **values, locations=locations)


@pytest.mark.parametrize("kind", ["file", "symlink", "hardlink", "directory", "foreign", "extra"])
def test_image_file_archive_is_exactly_one_regular_named_member(kind: str) -> None:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        member = tarfile.TarInfo("foreign" if kind == "foreign" else "module.py")
        if kind in {"symlink", "hardlink", "directory"}:
            member.type = {
                "symlink": tarfile.SYMTYPE,
                "hardlink": tarfile.LNKTYPE,
                "directory": tarfile.DIRTYPE,
            }[kind]
            member.linkname = "/external"
            archive.addfile(member)
        else:
            member.size = 3
            archive.addfile(member, io.BytesIO(b"abc"))
        if kind == "extra":
            archive.addfile(tarfile.TarInfo("extra"))
    if kind == "file":
        assert archive_file(buffer.getvalue(), "module.py") == b"abc"
    else:
        with pytest.raises(ValueError):
            archive_file(buffer.getvalue(), "module.py")


def test_binary_docker_transport_preserves_arbitrary_bytes(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = b"\xff\xfe\x00abc"

    def run(*args: object, **kwargs: object) -> CommandResult:
        assert kwargs["decode_errors"] == "surrogateescape"
        assert kwargs["max_buffer"] == 64 * 1024 * 1024
        return CommandResult(0, raw.decode("utf-8", errors="surrogateescape"), "")

    monkeypatch.setattr("scripts.release_repair_evidence.spawn", run)
    assert command("cp", "owned:/path", "-", binary=True) == raw


@pytest.mark.parametrize("rows", ["", "a" * 64 + "\n", "unexpected\n"])
def test_cleanup_is_confined_to_the_owned_label(monkeypatch: pytest.MonkeyPatch, rows: str) -> None:
    calls: list[tuple[str, ...]] = []

    def run(*args: str, **kwargs: object) -> bytes:
        calls.append(args)
        return rows.encode() if args[0] == "ps" else b""

    monkeypatch.setattr("scripts.release_repair_evidence.command", run)
    if rows == "unexpected\n":
        with pytest.raises(ValueError, match="cleanup identity"):
            cleanup("owner=nonce")
        assert len(calls) == 1
    else:
        cleanup("owner=nonce")
        assert len(calls) == (2 if rows else 1)
        if rows:
            assert calls[-1] == ("rm", "--force", "a" * 64)
    assert "label=owner=nonce" in calls[0]


@pytest.mark.parametrize(
    "failure", ["none", "pull", "platform", "config", "create", "copy", "buildx", "run", "save"]
)
def test_collector_binds_image_files_and_cleans_owned_resources(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    calls: list[tuple[str, ...]] = []
    cleanups: list[str] = []
    expected = _expected_witnesses()
    config_digest = "sha256:" + "e" * 64

    def observe_config(image: dict[str, object], *, subject: str) -> str:
        assert image["Id"] == IMAGE
        assert subject == SUBJECT and subject != image["Id"]
        if failure == "save":
            raise ValueError("injected save failure")
        return config_digest

    def execute(*args: str, **kwargs: object) -> bytes:
        calls.append(args)
        if args[0] == failure:
            raise ValueError("injected failure")
        if args[0] in {"create", "run"}:
            assert args[args.index("--entrypoint") + 2] == SUBJECT
            assert args[args.index("--platform") + 1] == "linux/amd64"
        if args[:2] == ("image", "inspect"):
            return json.dumps(
                [
                    {
                        "Architecture": "arm64" if failure == "platform" else "amd64",
                        "Os": "linux",
                        "Id": "--wrong" if failure == "config" else IMAGE,
                        "RepoDigests": [SUBJECT],
                        "RootFS": {"Layers": [LAYER]},
                    }
                ]
            ).encode()
        if args[0] == "buildx":
            directory = Path(args[args.index("--output") + 1].removeprefix("type=local,dest="))
            (directory / "check_zlib").write_bytes(b"native fixture")
            for key, value in expected.items():
                if key.endswith(("-before", "-after")):
                    (directory / (key + ".json")).write_text(json.dumps(value))
        if args[0] == "run":
            assert "none" in args and "--read-only" in args and "10001:10001" in args
            component = (
                "zlib"
                if "/native/check_zlib" in args
                else "stdlib"
                if "/proof/check_stdlib.py" in args
                else "python"
            )
            return json.dumps(expected[component + "-final"]).encode()
        return b""

    def copy(container: str, path: str) -> bytes:
        assert container.startswith("repair-")
        if failure == "copy":
            raise ValueError("injected copy failure")
        if path == collector.BUILD_IDENTITY:
            return (
                json.dumps(
                    {
                        "schemaVersion": "ci-coordinator-build-identity/v1",
                        "sourceCommit": COMMIT,
                        "productionEligible": True,
                        "releaseIdentity": "a" * 64,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                + "\n"
            ).encode()
        return b"observed image file"

    monkeypatch.setattr(collector, "command", execute)
    monkeypatch.setattr(collector, "docker_image_config_digest", observe_config)
    monkeypatch.setattr(collector, "copied_file", copy)
    monkeypatch.setattr(collector, "cleanup", cleanups.append)
    if failure == "none":
        result = collector.collect(
            subject=SUBJECT,
            source_commit=COMMIT,
            policy_path=ROOT / "docker/runtime/security/repaired-matches.v1.json",
        )
        assert result.imageId == config_digest and result.repoDigests == [SUBJECT]
        assert result.witnesses == expected
        assert set(result.installedFiles) == INSTALLED_PATHS
        assert {args[0] for args in calls} == {"pull", "image", "create", "buildx", "run"}
    else:
        with pytest.raises(ValueError):
            collector.collect(
                subject=SUBJECT,
                source_commit=COMMIT,
                policy_path=ROOT / "docker/runtime/security/repaired-matches.v1.json",
            )
    assert cleanups and len(set(cleanups)) == 1
    assert cleanups[0].startswith("ci-coordinator.repair-proof=repair-")


@pytest.mark.parametrize(
    "subject,source",
    [
        ("tag:latest", COMMIT),
        (SUBJECT, "HEAD"),
        ("ghcr.io/foreign/ci-coordinator@sha256:" + "a" * 64, COMMIT),
        ("ghcrXio/research-engineering/ci-coordinator@sha256:" + "a" * 64, COMMIT),
    ],
)
def test_collector_rejects_mutable_identity_before_any_effect(
    monkeypatch: pytest.MonkeyPatch, subject: str, source: str
) -> None:
    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("mutable identity reached Docker")

    monkeypatch.setattr(collector, "command", forbidden)
    with pytest.raises(ValueError):
        collector.collect(subject=subject, source_commit=source, policy_path=Path("unused"))


@pytest.mark.parametrize("observation", [False, True])
@pytest.mark.parametrize("state", ["current", "stale-installed", "expired"])
def test_observation_diagnostic_never_changes_repair_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, state: str, observation: bool
) -> None:
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(policy_value()))
    digest, policy = read_policy(policy_path, ROOT)
    value = evidence_value(policy)
    value["policySha256"] = digest
    if state == "stale-installed":
        value["installedFiles"] = dict.fromkeys(INSTALLED_PATHS, "1" * 64)
    evidence = RepairEvidence.model_validate(value)
    observed = datetime(2026, 10, 5, tzinfo=UTC) if state == "expired" else OBSERVED
    monkeypatch.setattr(collector, "datetime", SimpleNamespace(now=lambda _zone: observed))
    monkeypatch.setattr(collector, "collect", lambda **_arguments: evidence)
    output = tmp_path / "evidence.json"
    diagnostic = tmp_path / "observation.json"
    argv = [
        "release_repair_evidence",
        "--subject",
        SUBJECT,
        "--source-commit",
        COMMIT,
        "--policy",
        str(policy_path),
        "--output",
        str(output),
    ]
    if observation:
        argv += ["--observation-output", str(diagnostic)]
    monkeypatch.setattr("sys.argv", argv)
    if state == "current":
        assert collector.main() == 0
        assert output.read_bytes() == evidence_bytes(evidence)
    else:
        reason = "repair-installed-files" if state == "stale-installed" else "repair-expired"
        with pytest.raises(ValueError, match=reason):
            collector.main()
        assert not output.exists()
    if observation:
        assert diagnostic.read_bytes() == evidence_bytes(evidence)
    else:
        assert not diagnostic.exists()


QUALIFICATION_WORKFLOW = ROOT / ".github/workflows/runtime-image-qualification.yml"
QUALIFICATION_ADMISSION_SCRIPT = ROOT / "scripts/release_repair_admission.py"
ISOLATED_INTERPRETER_FLAGS = ("-I", "-B")
QUALIFICATION_ADMISSION_COMMAND = (
    "backend/.venv/bin/python -I -B scripts/release_repair_admission.py \\\n"
    '            --qualification-root "${root}" \\\n'
    "            --policy docker/runtime/security/repaired-matches.v1.json\n"
)
REPAIRED_HIGH_MATCH: dict[str, object] = {
    "id": "CVE-2026-82049",
    "severity": "High",
    "package": "python",
    "version": "3.13.16",
    "type": "binary",
    "purl": "pkg:generic/python@3.13.16",
    "locations": [
        {
            "path": "/usr/local/bin/python3.13",
            "layerID": LAYER,
            "annotations": {"evidence": "primary"},
        },
        {
            "path": "/usr/local/lib/libpython3.13.so.1.0",
            "layerID": LAYER,
            "annotations": {"evidence": "supporting"},
        },
    ],
}
# The jq projection keeps null fields; only High/Critical rows need repair identity.
ADMITTED_LOW_MATCH: dict[str, object] = {
    "id": "CVE-2026-1",
    "severity": "Low",
    "package": "other",
    "version": "1",
    "type": "deb",
    "purl": None,
    "locations": [],
}
UNREPAIRED_CRITICAL_MATCH: dict[str, object] = {
    **REPAIRED_HIGH_MATCH,
    "id": "CVE-2026-99999",
    "severity": "Critical",
}


def _rfc3339(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_local_qualification(
    tmp_path: Path, matches: list[dict[str, object]]
) -> tuple[Path, Path, datetime]:
    observed = datetime.now(UTC).replace(microsecond=0)
    document = policy_value()
    document["validFrom"] = _rfc3339(observed - timedelta(days=1))
    document["validUntil"] = _rfc3339(observed + timedelta(days=1))
    policy_path = tmp_path / "policy.json"
    policy_path.write_text(json.dumps(document))
    evidence = evidence_value(RepairPolicy.model_validate(document))
    evidence.update(
        sourceKind="local",
        subject=IMAGE,
        repoDigests=[],
        productionEligible=False,
        observedAt=_rfc3339(observed - timedelta(hours=1)),
        policySha256=file_hash(policy_path),
    )
    root = tmp_path / "qualification"
    root.mkdir()
    (root / "repair-evidence.json").write_text(json.dumps(evidence))
    (root / "image.json").write_text(json.dumps({"Id": IMAGE, "Size": 1}))
    (root / "vulnerabilities.json").write_text(json.dumps({"imageId": IMAGE, "matches": matches}))
    return root, policy_path, observed


def _hostile_import_root(tmp_path: Path) -> Path:
    hostile = tmp_path / "hostile"
    for package in ("scripts", "pydantic"):
        (hostile / package).mkdir(parents=True)
        (hostile / package / "__init__.py").write_text(
            'raise SystemExit("hostile import root was used")\n'
        )
    return hostile


def _run_isolated_admission(
    root: Path,
    policy_path: Path,
    *,
    cwd: Path,
    extra_flags: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
) -> tuple[int | None, str, str]:
    result = spawn(
        sys.executable,
        (
            *ISOLATED_INTERPRETER_FLAGS,
            *extra_flags,
            str(QUALIFICATION_ADMISSION_SCRIPT),
            "--qualification-root",
            str(root),
            "--policy",
            str(policy_path),
        ),
        cwd=cwd,
        env={"PATH": os.environ["PATH"], **(env or {})},
        timeout_seconds=60,
        max_buffer=65_536,
    )
    assert result.error is None, result
    return result.status, result.stdout, result.stderr


def test_runtime_qualification_runs_the_isolated_admission_entrypoint_once() -> None:
    workflow = QUALIFICATION_WORKFLOW.read_text()
    assert workflow.count(QUALIFICATION_ADMISSION_COMMAND) == 1
    assert QUALIFICATION_ADMISSION_COMMAND.startswith(
        "backend/.venv/bin/python "
        + " ".join(ISOLATED_INTERPRETER_FLAGS)
        + " scripts/release_repair_admission.py "
    )
    assert "release_repair_admission import" not in workflow


def test_isolated_qualification_admission_imports_only_its_checkout(tmp_path: Path) -> None:
    root, policy_path, _observed = _write_local_qualification(
        tmp_path, [REPAIRED_HIGH_MATCH, ADMITTED_LOW_MATCH]
    )
    hostile = _hostile_import_root(tmp_path)
    assert _run_isolated_admission(
        root,
        policy_path,
        cwd=hostile,
        env={"PYTHONPATH": str(hostile), "PYTHONOPTIMIZE": "1"},
    ) == (0, "runtime qualification repair admission passed\n", "")


@pytest.mark.parametrize(
    ("extra_flags", "variation"),
    [
        ((), "isolated"),
        (("-O",), "optimized-flag"),
        ((), "optimized-environment"),
        ((), "hostile-pythonpath"),
    ],
)
def test_isolated_qualification_admission_rejects_identically_under_variations(
    tmp_path: Path, extra_flags: tuple[str, ...], variation: str
) -> None:
    root, policy_path, _observed = _write_local_qualification(
        tmp_path, [REPAIRED_HIGH_MATCH, UNREPAIRED_CRITICAL_MATCH]
    )
    hostile = _hostile_import_root(tmp_path)
    env = {
        "isolated": {},
        "optimized-flag": {},
        "optimized-environment": {"PYTHONOPTIMIZE": "1"},
        "hostile-pythonpath": {"PYTHONPATH": str(hostile)},
    }[variation]
    cwd = hostile if variation == "hostile-pythonpath" else tmp_path
    assert _run_isolated_admission(
        root, policy_path, cwd=cwd, extra_flags=extra_flags, env=env
    ) == (
        1,
        "",
        "runtime qualification repair admission failed: "
        "repair-qualification-unrepaired:CVE-2026-99999\n",
    )


@pytest.mark.parametrize(
    ("change", "cause"),
    [
        ("registry-source", "repair-non-local"),
        ("foreign-report-image", "repair-qualification-image"),
        ("foreign-image-subject", "repair-qualification-subject"),
        ("unknown-severity", "QualificationMatch"),
        ("null-disallowed-purl", "DisallowedQualificationMatch"),
        ("unrepaired-critical", "repair-qualification-unrepaired:CVE-2026-99999"),
        ("truncated-report", "QualificationReport is not strict JSON"),
        ("missing-report", "QualificationReport is unavailable"),
        ("empty-image", "QualificationImage size is outside its bound"),
        ("malformed-evidence", "RepairEvidence is not strict JSON"),
        ("expired-policy", "repair-expired"),
    ],
)
def test_local_qualification_admission_rejects_each_invalid_input_with_its_cause(
    tmp_path: Path, change: str, cause: str
) -> None:
    root, policy_path, observed = _write_local_qualification(tmp_path, [REPAIRED_HIGH_MATCH])
    evidence_path = root / "repair-evidence.json"
    report_path = root / "vulnerabilities.json"
    if change == "registry-source":
        evidence = json.loads(evidence_path.read_text())
        evidence["sourceKind"] = "registry"
        evidence_path.write_text(json.dumps(evidence))
    elif change == "foreign-report-image":
        report_path.write_text(json.dumps({"imageId": "sha256:" + "1" * 64, "matches": []}))
    elif change == "foreign-image-subject":
        (root / "image.json").write_text(json.dumps({"Id": "sha256:" + "1" * 64}))
    elif change == "unknown-severity":
        report_path.write_text(
            json.dumps(
                {"imageId": IMAGE, "matches": [{**ADMITTED_LOW_MATCH, "severity": "Unknown"}]}
            )
        )
    elif change == "null-disallowed-purl":
        report_path.write_text(
            json.dumps({"imageId": IMAGE, "matches": [{**REPAIRED_HIGH_MATCH, "purl": None}]})
        )
    elif change == "unrepaired-critical":
        report_path.write_text(
            json.dumps({"imageId": IMAGE, "matches": [UNREPAIRED_CRITICAL_MATCH]})
        )
    elif change == "truncated-report":
        report_path.write_bytes(report_path.read_bytes()[:-1])
    elif change == "missing-report":
        report_path.unlink()
    elif change == "empty-image":
        (root / "image.json").write_bytes(b"")
    elif change == "malformed-evidence":
        evidence_path.write_text('{"schemaVersion": 1, "schemaVersion": 2}')
    else:
        observed += timedelta(days=2)
    with pytest.raises(ValueError, match=re.escape(cause)):
        admit_local_qualification(
            qualification_root=root,
            policy_path=policy_path,
            source_root=ROOT,
            observed=observed,
        )


def test_local_qualification_admission_accepts_repaired_and_lower_severity_matches(
    tmp_path: Path,
) -> None:
    root, policy_path, observed = _write_local_qualification(
        tmp_path, [REPAIRED_HIGH_MATCH, ADMITTED_LOW_MATCH]
    )
    admit_local_qualification(
        qualification_root=root, policy_path=policy_path, source_root=ROOT, observed=observed
    )

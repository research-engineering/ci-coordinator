from __future__ import annotations

import io
import json
import tarfile
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from scripts import release_repair_evidence as collector
from scripts.bounded_process import CommandResult
from scripts.release_repair_admission import (
    INSTALLED_PATHS,
    SOURCE_PATHS,
    RepairEvidence,
    RepairPolicy,
    _expected_witnesses,
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
                "version": "3.13.15",
                "packageType": "binary",
                "purl": "pkg:generic/python@3.13.15",
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
        "version": "3.13.15",
        "package_type": "binary",
        "purl": "pkg:generic/python@3.13.15",
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
    "failure", ["none", "pull", "platform", "config", "create", "copy", "buildx", "run"]
)
def test_collector_binds_image_files_and_cleans_owned_resources(
    monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    calls: list[tuple[str, ...]] = []
    cleanups: list[str] = []
    expected = _expected_witnesses()

    def execute(*args: str, **kwargs: object) -> bytes:
        calls.append(args)
        if args[0] == failure:
            raise ValueError("injected failure")
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
            assert args[args.index("--entrypoint") + 2] == IMAGE
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
    monkeypatch.setattr(collector, "copied_file", copy)
    monkeypatch.setattr(collector, "cleanup", cleanups.append)
    if failure == "none":
        result = collector.collect(
            subject=SUBJECT,
            source_commit=COMMIT,
            policy_path=ROOT / "docker/runtime/security/repaired-matches.v1.json",
        )
        assert result.imageId == IMAGE and result.repoDigests == [SUBJECT]
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

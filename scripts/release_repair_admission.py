from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool

from scripts.release_predicate_admission import _load_strict_json, _timestamp
from scripts.release_publisher_identity import REPAIR_PREDICATE as REPAIR_PREDICATE

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Digest = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Commit = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
MAX_REPAIR_BYTES = 65_536
STDLIB = "/usr/local/lib/python3.13/"
ZLIB = "/usr/lib/x86_64-linux-gnu/libz.so.1.3.2"
BUILDER_ROOT = "/tmp/zlib-"  # noqa: S108 -- expected protocol data, never opened on the host
INSTALLED_PATHS = frozenset(
    (
        "/usr/local/bin/python3.13",
        "/usr/local/lib/libpython3.13.so.1.0",
        ZLIB,
        "/usr/share/ci-coordinator/security-repairs.json",
        *(
            STDLIB + name
            for name in (
                "tarfile.py",
                "stringprep.py",
                "urllib/request.py",
                "poplib.py",
                "zipfile/__init__.py",
            )
        ),
    )
)
INSTALLED_PATHS |= {
    str(
        PurePosixPath(path).parent / "__pycache__" / (PurePosixPath(path).stem + ".cpython-313.pyc")
    )
    for path in INSTALLED_PATHS
    if path.endswith(".py")
}
SOURCE_PATHS = frozenset(
    "docker/runtime/security/" + name
    for name in (
        "stdlib-backports.json",
        "zlib.patch",
        "zlib-control",
        "build.sh",
        "manifest.py",
        "apply_stdlib.py",
        "check_stdlib.py",
        "check_tarfile.py",
        "check_zlib.c",
        "python-tarfile.patch",
        "python-stringprep.patch",
        "python-urllib-request.patch",
        "python-poplib.patch",
        "python-zipfile-__init__.patch",
    )
)
SOURCE_PATHS |= {
    "Dockerfile",
    "docker/runtime/install.sh",
    "docker/runtime/ubuntu-snapshot.conf",
    "docker/runtime/assemble.sh",
    "docker/runtime/package_metadata.py",
    "docker/runtime/application_layer.py",
}
STDLIB_CVES = frozenset(
    (
        "CVE-2026-17084",
        "CVE-2026-15806",
        "CVE-2025-15367",
        "CVE-2026-19672",
        "CVE-2026-87910",
        "CVE-2026-15310",
    )
)


class Closed(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class Location(Closed):
    path: str
    evidence: Literal["primary", "supporting"]


class RepairedOccurrence(Closed):
    cve: Annotated[str, Field(pattern=r"^CVE-[0-9]{4}-[0-9]+$")]
    severity: Literal["Low", "Medium", "High"]
    package: str
    version: str
    packageType: Literal["binary", "deb"]
    purl: str
    locations: Annotated[list[Location], Field(min_length=1, max_length=4)]


class RepairPolicy(Closed):
    schemaVersion: Literal["ci-coordinator.runtime-repair-policy/v1"]
    validFrom: str
    validUntil: str
    sourceInputs: dict[str, Hash]
    installedFiles: dict[str, Hash]
    occurrences: Annotated[list[RepairedOccurrence], Field(min_length=1, max_length=8)]


class RepairEvidence(Closed):
    schemaVersion: Literal["ci-coordinator.runtime-repair-evidence/v1"]
    sourceKind: Literal["registry", "local"]
    subject: str
    imageId: Digest
    repoDigests: list[str]
    runtimeLayer: Digest
    sourceCommit: Commit
    productionEligible: StrictBool
    observedAt: str
    policySha256: Hash
    sourceInputs: dict[str, Hash]
    installedFiles: dict[str, Hash]
    witnesses: dict[str, JsonValue]


def evidence_bytes(evidence: RepairEvidence) -> bytes:
    return (
        json.dumps(
            evidence.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        )
        + "\n"
    ).encode()


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError("repair-" + reason)


def file_hash(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def read_policy(path: Path, root: Path) -> tuple[str, RepairPolicy]:
    raw, value = _load_strict_json(path, label="RepairPolicy", maximum=MAX_REPAIR_BYTES)
    policy = RepairPolicy.model_validate(value)
    _require(set(policy.sourceInputs) == SOURCE_PATHS, "source-set")
    _require(set(policy.installedFiles) == INSTALLED_PATHS, "file-set")
    for relative, expected in policy.sourceInputs.items():
        _require(file_hash(root / relative) == expected, "source-drift")
    start = _timestamp(policy.validFrom, "repair-valid-from")
    expiry = _timestamp(policy.validUntil, "repair-valid-until")
    _require(timedelta(0) < expiry - start <= timedelta(days=14), "lifetime")
    identities = [(item.cve, item.package, item.version) for item in policy.occurrences]
    _require(len(identities) == len(set(identities)), "duplicate-occurrence")
    _require(
        {item.cve for item in policy.occurrences}
        <= STDLIB_CVES | {"CVE-2026-82049", "CVE-2026-85091"},
        "unknown-advisory",
    )
    for item in policy.occurrences:
        locations = [(location.path, location.evidence) for location in item.locations]
        _require(len(locations) == len(set(locations)), "duplicate-location")
        identity = (item.package, item.version, item.packageType)
        if item.cve == "CVE-2026-85091":
            _require(identity == ("zlib1g", "1:1.3.2-0+ci1", "deb"), "zlib-owner")
            _require(
                set(locations)
                == {
                    ("/var/lib/dpkg/status", "primary"),
                    ("/usr/share/doc/zlib1g/copyright", "supporting"),
                },
                "zlib-location",
            )
        else:
            _require(identity == ("python", "3.13.15", "binary"), "python-owner")
            _require(item.purl == "pkg:generic/python@3.13.15", "python-purl")
            _require(
                set(locations)
                == {
                    ("/usr/local/bin/python3.13", "primary"),
                    ("/usr/local/lib/libpython3.13.so.1.0", "supporting"),
                },
                "python-location",
            )
    return hashlib.sha256(raw).hexdigest(), policy


def _expected_witnesses() -> dict[str, object]:
    result: dict[str, object] = {}
    for phase in ("before", "after", "final"):
        repaired = phase != "before"
        result["python-" + phase] = {
            "cve": "CVE-2026-82049",
            "safeFilters": {"data": repaired, "tar": repaired},
        }
        result["stdlib-" + phase] = {
            "phase": "after" if repaired else "before",
            "repairs": dict.fromkeys(sorted(STDLIB_CVES), repaired),
            "positiveControls": "passed",
            "fileUriMutationsKilled": ["file-content"],
            "legacyMutationsKilled": ["input-drain", "terminal"] if repaired else [],
        }
        result["zlib-" + phase] = {
            "cve": "CVE-2026-85091",
            "library": ZLIB
            if phase == "final"
            else BUILDER_ROOT + ("repaired" if repaired else "baseline") + "/libz.so.1.3.2",
            "failureReached": True,
            "inputReset": repaired,
        }
    return result


def validate_evidence(
    *,
    policy: RepairPolicy,
    policy_sha256: str,
    evidence: RepairEvidence,
    subject: str,
    image_id: str,
    source_commit: str,
    observed: datetime,
) -> None:
    _require(evidence.sourceKind == "registry", "non-registry")
    _require(evidence.subject == subject and evidence.repoDigests == [subject], "subject")
    _require(evidence.imageId == image_id, "image-config")
    _require(evidence.sourceCommit == source_commit, "source-commit")
    _require(evidence.productionEligible, "non-production")
    validate_observations(
        policy=policy, policy_sha256=policy_sha256, evidence=evidence, observed=observed
    )


def validate_observations(
    *,
    policy: RepairPolicy,
    policy_sha256: str,
    evidence: RepairEvidence,
    observed: datetime,
) -> None:
    _require(evidence.policySha256 == policy_sha256, "policy-binding")
    _require(evidence.sourceInputs == policy.sourceInputs, "source-inputs")
    _require(evidence.installedFiles == policy.installedFiles, "installed-files")
    _require(
        _timestamp(policy.validFrom, "repair-valid-from")
        <= observed
        < _timestamp(policy.validUntil, "repair-valid-until"),
        "expired",
    )
    age = observed - _timestamp(evidence.observedAt, "repair-observed-at")
    _require(timedelta(0) <= age <= timedelta(days=14), "evidence-age")
    _require(
        _timestamp(evidence.observedAt, "repair-observed-at")
        >= _timestamp(policy.validFrom, "repair-valid-from"),
        "evidence-before-policy",
    )
    # Canonical JSON distinguishes booleans from numerically equal integers.
    _require(
        json.dumps(evidence.witnesses, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        == json.dumps(
            _expected_witnesses(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ),
        "causal-witnesses",
    )


def occurrence_is_repaired(
    policy: RepairPolicy,
    evidence: RepairEvidence,
    *,
    cve: str,
    severity: str | None,
    package: str,
    version: str,
    package_type: str,
    purl: str,
    locations: tuple[tuple[str, str, str], ...],
) -> bool:
    candidates = [
        item
        for item in policy.occurrences
        if (item.cve, item.severity, item.package, item.version, item.packageType, item.purl)
        == (cve, severity, package, version, package_type, purl)
    ]
    if len(candidates) != 1 or len(locations) != len(set(locations)):
        return False
    expected = {
        (item.path, item.evidence, evidence.runtimeLayer) for item in candidates[0].locations
    }
    return set(locations) == expected

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

import pytest

from ci_coordinator.persistence.compatibility_profile import (
    COMPATIBILITY_FENCE_KEY_DOMAIN,
    PROFILE_RESOURCE_NAME,
    PROFILE_SHA256,
    CompatibilityTimeouts,
    compatibility_fence_key,
    load_bundled_profile,
    parse_profile,
)


def test_bundled_profile_is_digest_pinned_and_projects_admitted_coordinates() -> None:
    profile_bytes = (
        files("ci_coordinator.persistence.resources").joinpath(PROFILE_RESOURCE_NAME).read_bytes()
    )
    profile_document = json.loads(profile_bytes)
    profile = load_bundled_profile()
    repository_root = Path(__file__).resolve().parents[4]
    documented_profile_bytes = (
        repository_root / "docs/specs/ci-coordinator-core/database-compatibility-profile.v1.json"
    ).read_bytes()

    assert profile.source_digest == PROFILE_SHA256
    assert documented_profile_bytes == profile_bytes
    assert profile.lineage_id == "ci-coordinator-postgresql/v1"
    assert profile.database_major_version == 18
    assert profile.isolation_level == "READ COMMITTED"
    assert profile.snapshot_isolation_level == "READ COMMITTED"
    assert profile.snapshot_read_only_statement == "SET TRANSACTION READ ONLY"
    assert (
        profile.participant_timeouts.lock_timeout_ms
        < profile.participant_timeouts.statement_timeout_ms
    )
    assert (
        profile.participant_timeouts.statement_timeout_ms
        < profile.participant_timeouts.transaction_timeout_ms
    )
    assert profile.capability_read_limit == profile.maximum_capabilities_per_revision + 1
    assert parse_profile(profile_bytes) == profile
    assert len(profile_document["implementationInventory"]) == 59
    assert {
        "backend/alembic/versions/20260926_0016_retire_pending_review_authority.py",
        "backend/alembic/versions/20260926_0017_explicit_review_renewal.py",
        "backend/src/ci_coordinator/persistence/review_renewal_migration.py",
        "backend/tests/integration/persistence/test_review_renewal_migration.py",
        "backend/alembic/versions/20260913_0012_administrator_activity.py",
        "backend/src/ci_coordinator/persistence/activity_schema_contract.py",
        "backend/src/ci_coordinator/persistence/activity_schema_attestation.py",
        "backend/alembic/versions/20260913_0013_analytics_purpose.py",
        "backend/src/ci_coordinator/persistence/analytics_purpose_schema_contract.py",
        "backend/alembic/versions/20260915_0014_retire_economics_evidence_v3.py",
        "backend/alembic/versions/20260915_0015_total_collection_state.py",
        "backend/src/ci_coordinator/persistence/ci_economics_v4_schema_contract.py",
        "backend/tests/integration/persistence/test_collection_state_migration.py",
    } <= {item["path"] for item in profile_document["implementationInventory"]}
    assert all(
        (repository_root / item["path"]).is_file()
        for item in profile_document["implementationInventory"]
    )
    assert profile_document["proofRouting"]["mutationInventory"] == {
        "path": "fixtures/conformance/v1/python-database-compatibility-mutants.v1.json",
        "expectedKilled": 36,
        "exactSelectorsRequired": True,
        "committedHeadRequired": True,
    }


def test_profile_rejects_unknown_or_missing_top_level_fields() -> None:
    value = json.loads(
        files("ci_coordinator.persistence.resources").joinpath(PROFILE_RESOURCE_NAME).read_bytes()
    )
    value["unexpected"] = True

    with pytest.raises(ValueError, match="unexpected or missing"):
        parse_profile(json.dumps(value).encode("utf-8"))


def test_profile_rejects_unordered_or_disabled_timeouts() -> None:
    value = json.loads(
        files("ci_coordinator.persistence.resources").joinpath(PROFILE_RESOURCE_NAME).read_bytes()
    )
    value["fence"]["timeoutProfiles"]["participant"]["lockTimeoutMs"] = 30_000

    with pytest.raises(ValueError, match="timeout order"):
        parse_profile(json.dumps(value).encode("utf-8"))

    value = json.loads(
        files("ci_coordinator.persistence.resources").joinpath(PROFILE_RESOURCE_NAME).read_bytes()
    )
    value["fence"]["timeoutProfiles"]["zeroIsForbidden"] = False

    with pytest.raises(ValueError, match="forbid zero"):
        parse_profile(json.dumps(value).encode("utf-8"))

    value = json.loads(
        files("ci_coordinator.persistence.resources").joinpath(PROFILE_RESOURCE_NAME).read_bytes()
    )
    value["fence"]["timeoutProfiles"]["participant"]["lockTimeoutMs"] = 0

    with pytest.raises(ValueError, match="positive integer"):
        parse_profile(json.dumps(value).encode("utf-8"))


def test_fence_key_is_derived_from_the_code_owned_key_domain() -> None:
    value = json.loads(
        files("ci_coordinator.persistence.resources").joinpath(PROFILE_RESOURCE_NAME).read_bytes()
    )
    profile = load_bundled_profile()

    assert value["fence"]["keyDomain"] == COMPATIBILITY_FENCE_KEY_DOMAIN
    assert compatibility_fence_key(COMPATIBILITY_FENCE_KEY_DOMAIN) == (-170580017, 1725555266)
    assert (profile.fence_class_id, profile.fence_object_id) == compatibility_fence_key(
        COMPATIBILITY_FENCE_KEY_DOMAIN
    )


@pytest.mark.parametrize(
    ("alteration", "message"),
    (
        ("class-id", "derive from the admitted key domain"),
        ("object-id", "derive from the admitted key domain"),
        ("swapped-pair", "derive from the admitted key domain"),
        ("self-consistent-key-domain", "keyDomain must equal its admitted literal"),
        ("key-derivation", "keyDerivation must equal its admitted literal"),
    ),
)
def test_profile_rejects_an_altered_fence_key_or_key_domain(alteration: str, message: str) -> None:
    value = json.loads(
        files("ci_coordinator.persistence.resources").joinpath(PROFILE_RESOURCE_NAME).read_bytes()
    )
    fence = value["fence"]
    if alteration == "class-id":
        fence["classId"] += 1
    elif alteration == "object-id":
        fence["objectId"] -= 1
    elif alteration == "swapped-pair":
        fence["classId"], fence["objectId"] = fence["objectId"], fence["classId"]
    elif alteration == "self-consistent-key-domain":
        fence["keyDomain"] = "ci-coordinator/database-compatibility-writer-fence/v2"
        fence["classId"], fence["objectId"] = compatibility_fence_key(fence["keyDomain"])
    else:
        fence["keyDerivation"] = "first eight SHA-256 bytes as one signed big-endian int64"

    with pytest.raises(ValueError, match=message):
        parse_profile(json.dumps(value).encode("utf-8"))


def test_timeout_value_object_rejects_zero() -> None:
    with pytest.raises(ValueError, match="positive integers"):
        CompatibilityTimeouts(
            lock_timeout_ms=0,
            statement_timeout_ms=30_000,
            transaction_timeout_ms=60_000,
        )

from datetime import timedelta

import pytest
from ci_economics.archive_factories import ARCHIVE_TIME

from ci_coordinator.ci_economics.archive_retention import (
    ArchiveDetailRetention,
    DetailPolicyReference,
    DetailPolicySource,
    DetailRetentionPolicy,
)
from ci_coordinator.ci_economics.history_configuration import HistoryDefaults
from ci_coordinator.persistence.ci_history_retention_codec import (
    decode_history_defaults,
    decode_history_policy,
    decode_history_retention,
    encode_history_defaults,
    encode_history_policy,
    encode_history_retention,
)


@pytest.mark.parametrize(
    "policy",
    [
        DetailRetentionPolicy("disabled"),
        DetailRetentionPolicy.default(),
        DetailRetentionPolicy("forever"),
    ],
)
def test_each_policy_variant_roundtrips_without_coercion(policy: DetailRetentionPolicy) -> None:
    raw = encode_history_policy(policy)
    assert decode_history_policy(raw) == policy
    assert decode_history_policy(memoryview(raw)) == policy
    with pytest.raises(ValueError):
        decode_history_policy(raw + b" ")


@pytest.mark.parametrize(
    "raw",
    [
        b"{}",
        b'{"mode":"forever","mode":"disabled"}',
        b'{"days":1,"mode":"days"}',
        b'{"days":true,"mode":"days"}',
        b'{"mode":"forever","unknown":1}',
        b"{" * 1025,
    ],
)
def test_policy_payload_is_closed_canonical_and_bounded(raw: bytes) -> None:
    with pytest.raises(ValueError):
        decode_history_policy(raw)


@pytest.mark.parametrize("source", ["service_default", "repository_override"])
def test_applied_policy_source_and_import_clock_survive_roundtrip(
    source: DetailPolicySource,
) -> None:
    retention = ArchiveDetailRetention(
        "retained", ARCHIVE_TIME, DetailRetentionPolicy.default(), DetailPolicyReference(source, 4)
    )
    row = encode_history_retention(retention)
    assert row["detail_policy_source"] == source
    assert row["detail_expires_at"] == ARCHIVE_TIME + timedelta(days=365)
    assert decode_history_retention(row) == retention
    assert decode_history_retention({**row, "detail_state": "expired"}).state == "expired"


@pytest.mark.parametrize(
    "field,value",
    [
        ("detail_state", "not_imported"),
        ("detail_first_imported_at", None),
        ("detail_first_imported_at", ARCHIVE_TIME.replace(tzinfo=None)),
        ("detail_policy_canonical", None),
        ("detail_policy_source", None),
        ("detail_policy_source", "unknown"),
        ("detail_policy_revision", None),
        ("detail_policy_revision", True),
        ("detail_policy_revision", 0),
        ("detail_expires_at", None),
        ("detail_expires_at", ARCHIVE_TIME + timedelta(days=364)),
    ],
)
def test_independent_applied_policy_operands_cannot_be_omitted_or_substituted(
    field: str, value: object
) -> None:
    retention = ArchiveDetailRetention(
        "retained",
        ARCHIVE_TIME,
        DetailRetentionPolicy.default(),
        DetailPolicyReference("repository_override", 4),
    )
    with pytest.raises((ValueError, TypeError)):
        decode_history_retention({**encode_history_retention(retention), field: value})


def test_never_imported_state_does_not_acquire_a_policy_or_import_clock() -> None:
    prior = ArchiveDetailRetention()
    row = encode_history_retention(prior)
    assert decode_history_retention(row) == prior
    assert row["detail_first_imported_at"] is None
    with pytest.raises((TypeError, ValueError)):
        decode_history_retention({**row, "detail_first_imported_at": ARCHIVE_TIME})


def test_default_policy_retains_its_own_revision_and_update_time() -> None:
    value = HistoryDefaults(100, DetailRetentionPolicy.default(), ARCHIVE_TIME)
    assert decode_history_defaults(encode_history_defaults(value)) == value


@pytest.mark.parametrize(
    "field,value",
    [
        ("singleton", False),
        ("singleton", 1),
        ("revision", True),
        ("revision", 0),
        ("updated_at", ARCHIVE_TIME.replace(tzinfo=None)),
        ("detail_policy_canonical", b"{}"),
    ],
)
def test_default_policy_columns_are_not_coerced(field: str, value: object) -> None:
    defaults = HistoryDefaults(100, DetailRetentionPolicy.default(), ARCHIVE_TIME)
    with pytest.raises(ValueError):
        decode_history_defaults({**encode_history_defaults(defaults), field: value})

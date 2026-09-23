from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from pydantic import ValidationError

from ci_coordinator.ci_economics.archive_retention import (
    ArchiveDetailRetention,
    DetailPolicyReference,
    DetailPolicySource,
    DetailRetentionMode,
    DetailRetentionPolicy,
    apply_detail_policy,
    expire_archive_detail,
    record_first_detail_import,
)
from ci_coordinator.ci_economics.archive_retention_payload import DETAIL_RETENTION_ADAPTER

NOW = datetime(2026, 9, 12, tzinfo=UTC)


def _reference(revision: int) -> DetailPolicyReference:
    return DetailPolicyReference("repository_override", revision)


@pytest.mark.parametrize(
    "raw",
    [
        {"mode": "disabled"},
        {"mode": "forever"},
        {"mode": "days", "days": 1, "anchor": "first_successful_detail_import"},
        {"mode": "days", "days": 365, "anchor": "first_successful_detail_import"},
        {"mode": "days", "days": 36500, "anchor": "first_successful_detail_import"},
    ],
)
def test_payload_round_trip_preserves_exact_variant(raw: dict[str, object]) -> None:
    payload = DETAIL_RETENTION_ADAPTER.validate_python(raw)
    assert payload.model_dump() == raw
    assert payload.to_policy().canonical_mapping() == raw


@pytest.mark.parametrize(
    "raw",
    [
        {},
        None,
        {"mode": None},
        {"mode": "year"},
        {"mode": "disabled", "days": None},
        {"mode": "forever", "anchor": None},
        {"mode": "days", "days": 365},
        {"mode": "days", "anchor": "first_successful_detail_import"},
        {"mode": "days", "days": True, "anchor": "first_successful_detail_import"},
        {"mode": "days", "days": 1.0, "anchor": "first_successful_detail_import"},
        {"mode": "days", "days": "365", "anchor": "first_successful_detail_import"},
        {"mode": "days", "days": 0, "anchor": "first_successful_detail_import"},
        {"mode": "days", "days": 36501, "anchor": "first_successful_detail_import"},
        {"mode": "days", "days": 365, "anchor": "latest_import"},
        {"mode": "forever", "statistics": "delete"},
    ],
)
def test_payload_rejects_coercion_unknown_fields_and_contradictory_variants(raw: object) -> None:
    with pytest.raises(ValidationError):
        DETAIL_RETENTION_ADAPTER.validate_python(raw)


def test_disabled_import_does_not_start_a_retention_clock() -> None:
    prior = ArchiveDetailRetention()
    assert (
        record_first_detail_import(prior, DetailRetentionPolicy("disabled"), _reference(1), now=NOW)
        is prior
    )
    assert prior.first_imported_at is None and prior.expires_at is None


@pytest.mark.parametrize(
    "policy", [DetailRetentionPolicy.default(), DetailRetentionPolicy("forever")]
)
def test_first_import_binds_policy_revision_and_replay_preserves_every_operand(
    policy: DetailRetentionPolicy,
) -> None:
    retained = record_first_detail_import(ArchiveDetailRetention(), policy, _reference(3), now=NOW)
    assert retained == ArchiveDetailRetention("retained", NOW, policy, _reference(3))
    assert (
        record_first_detail_import(
            retained, DetailRetentionPolicy("days", 10), _reference(99), now=NOW + timedelta(days=2)
        )
        is retained
    )
    assert retained.expires_at == (NOW + timedelta(days=365) if policy.mode == "days" else None)


@pytest.mark.parametrize(("offset", "due"), [(-1, False), (0, True), (1, True)])
def test_expiry_boundary_uses_the_original_import_instant(offset: int, due: bool) -> None:
    prior = ArchiveDetailRetention("retained", NOW, DetailRetentionPolicy.default(), _reference(1))
    at = NOW + timedelta(days=365, microseconds=offset)
    assert prior.due_at(at) is due
    after = expire_archive_detail(prior, now=at)
    assert after == (replace(prior, state="expired") if due else prior)
    assert after.first_imported_at == NOW
    assert after.applied_reference == _reference(1)
    assert after.applied_policy == prior.applied_policy


def test_expired_detail_cannot_be_resurrected_by_rescan_or_a_longer_policy() -> None:
    prior = ArchiveDetailRetention("retained", NOW, DetailRetentionPolicy("days", 1), _reference(1))
    expired = expire_archive_detail(prior, now=NOW + timedelta(days=1))
    assert (
        record_first_detail_import(
            expired, DetailRetentionPolicy("forever"), _reference(2), now=NOW + timedelta(days=2)
        )
        is expired
    )
    assert expire_archive_detail(expired, now=NOW + timedelta(days=100)) is expired


@pytest.mark.parametrize(
    "record",
    [
        ArchiveDetailRetention(),
        ArchiveDetailRetention("retained", NOW, DetailRetentionPolicy("forever"), _reference(1)),
    ],
)
def test_non_expiring_variants_have_a_total_false_expiry(record: ArchiveDetailRetention) -> None:
    assert not record.due_at(NOW + timedelta(days=3650))
    assert expire_archive_detail(record, now=NOW + timedelta(days=3650)) is record


@pytest.mark.parametrize("days", [None, True, 0, -1, 1.0, "1", 36501])
def test_domain_duration_is_not_coerced(days: object) -> None:
    with pytest.raises(ValueError):
        DetailRetentionPolicy("days", cast(int, days))


@pytest.mark.parametrize("mode", ["disabled", "forever"])
def test_non_finite_domain_variants_reject_a_duration(mode: str) -> None:
    with pytest.raises(ValueError):
        DetailRetentionPolicy(cast(DetailRetentionMode, mode), 1)


@pytest.mark.parametrize(
    ("first", "policy", "revision"),
    [(NOW, None, None), (None, DetailRetentionPolicy.default(), None), (None, None, 1)],
)
def test_never_imported_record_rejects_applied_operands(
    first: datetime | None, policy: DetailRetentionPolicy | None, revision: int | None
) -> None:
    with pytest.raises(ValueError):
        ArchiveDetailRetention(
            "not_imported", first, policy, None if revision is None else _reference(revision)
        )


def test_invalid_time_or_unrepresentable_deadline_is_rejected() -> None:
    with pytest.raises(ValueError):
        ArchiveDetailRetention(
            "retained", NOW.replace(tzinfo=None), DetailRetentionPolicy.default(), _reference(1)
        )
    with pytest.raises(ValueError):
        ArchiveDetailRetention(
            "retained",
            datetime(9999, 12, 31, tzinfo=UTC),
            DetailRetentionPolicy.default(),
            _reference(1),
        )
    with pytest.raises(ValueError):
        ArchiveDetailRetention().due_at(NOW.replace(tzinfo=None))


@pytest.mark.parametrize(
    ("policy", "state", "deadline"),
    [
        (DetailRetentionPolicy("disabled"), "expired", None),
        (DetailRetentionPolicy("forever"), "retained", None),
        (DetailRetentionPolicy("days", 1), "expired", NOW + timedelta(days=1)),
        (DetailRetentionPolicy("days", 20), "retained", NOW + timedelta(days=20)),
    ],
)
def test_explicit_policy_application_preserves_first_import_and_recomputes_expiry(
    policy: DetailRetentionPolicy, state: str, deadline: datetime | None
) -> None:
    prior = ArchiveDetailRetention(
        "retained", NOW, DetailRetentionPolicy("days", 10), _reference(1)
    )
    changed = apply_detail_policy(prior, policy, _reference(2), now=NOW + timedelta(days=2))
    assert changed.state == state
    assert changed.first_imported_at == NOW
    assert changed.expires_at == deadline
    assert changed.applied_policy == policy
    assert changed.applied_reference == _reference(2)
    assert prior.applied_reference == _reference(1)


@pytest.mark.parametrize("delta", [-1, 0, 1])
def test_policy_extension_cannot_revive_expired_but_not_yet_cleaned_detail(delta: int) -> None:
    prior = ArchiveDetailRetention("retained", NOW, DetailRetentionPolicy("days", 1), _reference(1))
    now = NOW + timedelta(days=1, microseconds=delta)
    changed = apply_detail_policy(prior, DetailRetentionPolicy("forever"), _reference(2), now=now)
    assert changed.first_imported_at == NOW
    assert changed.state == ("retained" if delta < 0 else "expired")
    assert changed.applied_reference == _reference(2 if delta < 0 else 1)


@pytest.mark.parametrize("delta", [-1, 0, 1])
@pytest.mark.parametrize("already_expired", [False, True])
def test_policy_identity_is_consistent_before_at_and_after_expiry(
    delta: int, already_expired: bool
) -> None:
    prior = ArchiveDetailRetention(
        "expired" if already_expired else "retained",
        NOW,
        DetailRetentionPolicy.default(),
        _reference(3),
    )
    now = NOW + timedelta(days=365, microseconds=delta)
    replay = apply_detail_policy(prior, DetailRetentionPolicy.default(), _reference(3), now=now)
    assert replay == expire_archive_detail(prior, now=now)
    assert replay.first_imported_at == NOW
    for policy, revision in [
        (DetailRetentionPolicy.default(), 2),
        (DetailRetentionPolicy("forever"), 3),
    ]:
        with pytest.raises(ValueError):
            apply_detail_policy(prior, policy, _reference(revision), now=now)


@pytest.mark.parametrize(
    "prior",
    [
        ArchiveDetailRetention(),
        ArchiveDetailRetention("expired", NOW, DetailRetentionPolicy.default(), _reference(1)),
    ],
)
def test_policy_application_cannot_create_missing_or_deleted_detail(
    prior: ArchiveDetailRetention,
) -> None:
    assert (
        apply_detail_policy(prior, DetailRetentionPolicy("forever"), _reference(2), now=NOW)
        is prior
    )


@pytest.mark.parametrize(
    "origin,target",
    [
        ("service_default", "repository_override"),
        ("repository_override", "service_default"),
    ],
)
def test_cross_source_policy_references_are_not_compared_as_one_counter(
    origin: DetailPolicySource, target: DetailPolicySource
) -> None:
    prior = ArchiveDetailRetention(
        "retained", NOW, DetailRetentionPolicy.default(), DetailPolicyReference(origin, 100)
    )
    changed = apply_detail_policy(
        prior, DetailRetentionPolicy("days", 30), DetailPolicyReference(target, 4), now=NOW
    )
    assert changed.applied_reference == DetailPolicyReference(target, 4)
    assert changed.first_imported_at == NOW and changed.expires_at == NOW + timedelta(days=30)


@pytest.mark.parametrize(
    "source,revision", [("unknown", 1), ("service_default", True), ("repository_override", 0)]
)
def test_policy_reference_rejects_unknown_source_and_invalid_revision(
    source: str, revision: int
) -> None:
    with pytest.raises(ValueError):
        DetailPolicyReference(cast(DetailPolicySource, source), revision)


def test_imported_policy_requires_a_source_tag_not_just_a_bare_version() -> None:
    with pytest.raises(ValueError):
        ArchiveDetailRetention("retained", NOW, DetailRetentionPolicy.default())

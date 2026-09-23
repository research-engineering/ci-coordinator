from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest

from ci_coordinator.ci_economics.model import AttemptIdentity
from ci_coordinator.ci_economics.sources import (
    ProviderRunCollectionSource,
    ReconciliationCollectionSource,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object
from ci_coordinator.reconciliation import ReconciliationSubject

from .factories import ATTEMPT, CONTRACT, NOW, SUBJECT


def test_reconciliation_source_preserves_identity_and_contract_without_aliasing() -> None:
    source = ReconciliationCollectionSource(SUBJECT, CONTRACT)
    alternate = ReconciliationCollectionSource(
        ReconciliationSubject.create(
            installation_id=SUBJECT.installation_id,
            repository_id=SUBJECT.repository_id,
            event_name=SUBJECT.event_name,
            ref="refs/heads/other",
            base_sha=SUBJECT.base_sha,
            head_sha=SUBJECT.head_sha,
            workflow_run_id=SUBJECT.workflow_run_id,
            run_attempt=SUBJECT.run_attempt,
        ),
        CONTRACT,
    )

    assert source.kind == "reconciliation"
    assert source.subject is SUBJECT
    assert source.contract is CONTRACT
    assert source.source_id == SUBJECT.subject_id
    assert alternate.source_id != source.source_id
    assert alternate.attempt == source.attempt == ATTEMPT


def test_provider_identity_is_domain_separated_and_excludes_observation_provenance() -> None:
    source = _source()
    changed_provenance = replace(
        source,
        run_created_at=NOW + timedelta(days=1),
        source_evidence_digest="f" * 64,
        provider_api_version="2026-03-11",
    )

    assert source.kind == "provider_run"
    assert source.source_id == hash_object(
        {
            "schemaVersion": "ci-economics-provider-run-source/v1",
            "attempt": ATTEMPT.canonical_mapping(),
        }
    )
    assert source.source_id not in {SUBJECT.subject_id, ATTEMPT.identity_hash}
    assert changed_provenance != source
    assert changed_provenance.source_id == source.source_id


@pytest.mark.parametrize(
    "attempt",
    [
        replace(ATTEMPT, scope=RepositoryScope(102, 202)),
        replace(ATTEMPT, scope=RepositoryScope(101, 203)),
        replace(ATTEMPT, workflow_run_id=304),
        replace(ATTEMPT, run_attempt=3),
        replace(ATTEMPT, head_sha="c" * 40),
    ],
    ids=["installation", "repository", "run", "attempt", "head"],
)
def test_every_attempt_operand_changes_provider_identity(attempt: AttemptIdentity) -> None:
    assert replace(_source(), attempt=attempt).source_id != _source().source_id


def test_provider_creation_time_normalizes_without_changing_the_instant() -> None:
    observed = NOW.astimezone(timezone(timedelta(hours=2)))

    source = replace(_source(), run_created_at=observed)

    assert source.run_created_at == observed
    assert source.run_created_at.tzinfo is UTC


@pytest.mark.parametrize("value", ["", "2026-02-30", "20260310", "2026-3-10", "2026-03-10x", 1])
def test_provider_source_rejects_invalid_api_version(value: object) -> None:
    with pytest.raises(ValueError):
        replace(_source(), provider_api_version=cast(str, value))


@pytest.mark.parametrize("value", ["", "g" * 64, "A" * 64, "a" * 63, 1, None])
def test_provider_source_rejects_invalid_evidence_digest(value: object) -> None:
    with pytest.raises(ValueError, match="SHA-256"):
        replace(_source(), source_evidence_digest=cast(str, value))


@pytest.mark.parametrize("value", [NOW.replace(tzinfo=None), None, "2026-09-04T10:00:00Z"])
def test_provider_source_rejects_naive_or_non_datetime_anchor(value: object) -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        replace(_source(), run_created_at=cast(datetime, value))


def test_sources_require_nominal_owner_values() -> None:
    with pytest.raises(TypeError, match="attempt identity"):
        replace(_source(), attempt=cast(AttemptIdentity, SUBJECT))
    with pytest.raises(TypeError, match="subject"):
        ReconciliationCollectionSource(cast(ReconciliationSubject, ATTEMPT), CONTRACT)


def _source() -> ProviderRunCollectionSource:
    return ProviderRunCollectionSource(ATTEMPT, NOW, "2026-03-10", "e" * 64)

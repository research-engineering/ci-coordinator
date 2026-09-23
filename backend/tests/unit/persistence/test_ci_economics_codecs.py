from __future__ import annotations

import json
from datetime import timedelta
from typing import cast

import pytest
from ci_economics.factories import ATTEMPT, CONTRACT, NOW, POLICY, SUBJECT, job

from ci_coordinator.ci_economics import (
    CollectionClaimAcquired,
    RunnerIdentity,
    acquire_collection_claim,
    initial_collection_state,
)
from ci_coordinator.ci_economics.sources import (
    CollectionSource,
    ProviderRunCollectionSource,
    ReconciliationCollectionSource,
)
from ci_coordinator.github_ingestion.events import (
    NormalizedWorkflowJobEvent,
    WorkflowJobRunnerIdentity,
)
from ci_coordinator.github_ingestion.provenance import GitHubRepository, WebhookProvenance
from ci_coordinator.kernel import canonical_json
from ci_coordinator.persistence.ci_economics_codec import (
    CiEconomicsCodecError,
    decode_workflow_job_observation,
    encode_workflow_observation,
)
from ci_coordinator.persistence.ci_economics_collection_codec import (
    COLLECTION_TRANSITION_COLUMNS,
    PROVIDER_SOURCE_COLUMNS,
    decode_collection_source,
    decode_collection_state,
    encode_collection_record,
    encode_collection_state,
    encode_collection_transition,
)

_DELIVERY_ID = "delivery-1"
_RUNNER = WorkflowJobRunnerIdentity(11, "runner-a", 12, "linux")
_LEGACY_SOURCE = ReconciliationCollectionSource(SUBJECT, CONTRACT)
_PROVIDER_SOURCE = ProviderRunCollectionSource(ATTEMPT, NOW, "2026-03-10", "d" * 64)


@pytest.mark.parametrize("source", [_LEGACY_SOURCE, _PROVIDER_SOURCE])
def test_collection_record_preserves_source_and_state_without_cross_case_fields(
    source: CollectionSource,
) -> None:
    state = initial_collection_state(source.source_id, NOW, NOW, POLICY)
    row = encode_collection_record(state, source)

    assert decode_collection_state(row) == state
    assert (
        decode_collection_source(
            row, reconciliation=_LEGACY_SOURCE if source is _LEGACY_SOURCE else None
        )
        == source
    )
    assert row["source_created_at"] == NOW
    if source is _LEGACY_SOURCE:
        assert row["legacy_subject_id"] == SUBJECT.subject_id
        assert all(row[column] is None for column in PROVIDER_SOURCE_COLUMNS)
    else:
        assert row["legacy_subject_id"] is None
        assert all(row[column] is not None for column in PROVIDER_SOURCE_COLUMNS)


@pytest.mark.parametrize("source", [_LEGACY_SOURCE, _PROVIDER_SOURCE])
@pytest.mark.parametrize(
    "column", ["subject_id", "source_kind", "legacy_subject_id", *PROVIDER_SOURCE_COLUMNS]
)
def test_source_decoder_requires_every_case_column(source: CollectionSource, column: str) -> None:
    state = initial_collection_state(source.source_id, NOW, NOW, POLICY)
    row = encode_collection_record(state, source)
    del row[column]

    with pytest.raises(ValueError, match="columns are incomplete"):
        decode_collection_source(
            row, reconciliation=_LEGACY_SOURCE if source is _LEGACY_SOURCE else None
        )


@pytest.mark.parametrize(
    ("column", "replacement"),
    [
        ("source_kind", "other"),
        ("legacy_subject_id", SUBJECT.subject_id),
        ("subject_id", "f" * 64),
        ("installation_id", ATTEMPT.scope.installation_id + 1),
        ("repository_id", ATTEMPT.scope.repository_id + 1),
        ("workflow_run_id", ATTEMPT.workflow_run_id + 1),
        ("run_attempt", ATTEMPT.run_attempt + 1),
        ("head_sha", "e" * 40),
        ("installation_id", True),
        ("repository_id", 1.0),
        ("workflow_run_id", "303"),
        ("run_attempt", 0),
        ("provider_api_version", "2026-02-30"),
        ("source_evidence_digest", "not-a-digest"),
        ("source_created_at", NOW.replace(tzinfo=None)),
        *[(column, None) for column in PROVIDER_SOURCE_COLUMNS],
    ],
)
def test_provider_source_decoder_rejects_each_malformed_or_conflicting_operand(
    column: str, replacement: object
) -> None:
    state = initial_collection_state(_PROVIDER_SOURCE.source_id, NOW, NOW, POLICY)
    row = encode_collection_record(state, _PROVIDER_SOURCE)
    row[column] = replacement

    with pytest.raises(ValueError):
        decode_collection_source(row)


@pytest.mark.parametrize("column", PROVIDER_SOURCE_COLUMNS)
def test_legacy_source_rejects_every_independent_source_column(column: str) -> None:
    state = initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY)
    row = encode_collection_record(state, _LEGACY_SOURCE)
    row[column] = "unexpected"

    with pytest.raises(ValueError, match="exact reconciliation link"):
        decode_collection_source(row, reconciliation=_LEGACY_SOURCE)


@pytest.mark.parametrize(
    ("source", "reconciliation"),
    [
        (_LEGACY_SOURCE, None),
        (_PROVIDER_SOURCE, _LEGACY_SOURCE),
    ],
)
def test_source_decoder_requires_only_its_exact_legacy_provenance(
    source: CollectionSource, reconciliation: ReconciliationCollectionSource | None
) -> None:
    state = initial_collection_state(source.source_id, NOW, NOW, POLICY)

    with pytest.raises(ValueError):
        decode_collection_source(
            encode_collection_record(state, source), reconciliation=reconciliation
        )


@pytest.mark.parametrize("replace_both_ids", [False, True])
def test_legacy_source_link_cannot_substitute_the_loaded_subject(replace_both_ids: bool) -> None:
    state = initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY)
    row = encode_collection_record(state, _LEGACY_SOURCE)
    row["legacy_subject_id"] = "f" * 64
    if replace_both_ids:
        row["subject_id"] = "f" * 64

    with pytest.raises(ValueError, match="exact reconciliation link"):
        decode_collection_source(row, reconciliation=_LEGACY_SOURCE)


@pytest.mark.parametrize("mismatch", ["identity", "time"])
def test_record_encoding_cannot_override_the_state_identity_or_retention_anchor(
    mismatch: str,
) -> None:
    state = initial_collection_state(
        "e" * 64 if mismatch == "identity" else _PROVIDER_SOURCE.source_id,
        NOW - timedelta(seconds=1) if mismatch == "time" else NOW,
        NOW,
        POLICY,
    )

    with pytest.raises(ValueError, match="source identity or time"):
        encode_collection_record(state, _PROVIDER_SOURCE)


@pytest.mark.parametrize(
    ("event_runner", "expected_runner"),
    [
        (None, RunnerIdentity(None, None, None, None)),
        (_RUNNER, RunnerIdentity(11, "runner-a", 12, "linux")),
    ],
    ids=("runner-absent", "runner-present"),
)
def test_workflow_job_observation_round_trips_through_the_canonical_model(
    event_runner: WorkflowJobRunnerIdentity | None,
    expected_runner: RunnerIdentity,
) -> None:
    row = encode_workflow_observation(_job_event(event_runner), delivery_id=_DELIVERY_ID)

    assert decode_workflow_job_observation(row) == job(runner=expected_runner)


@pytest.mark.parametrize("status", ["pending", "leased"])
def test_collection_state_round_trip_preserves_exact_column_values(status: str) -> None:
    state = initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY)
    if status == "leased":
        outcome = acquire_collection_claim(
            state,
            ReconciliationCollectionSource(SUBJECT, CONTRACT),
            worker_id="c" * 64,
            now=NOW,
            policy=POLICY,
        )
        assert isinstance(outcome, CollectionClaimAcquired)
        state = outcome.state

    row = encode_collection_state(state)
    decoded = decode_collection_state(row)

    assert decoded == state
    assert encode_collection_state(decoded) == row


def test_collection_transition_projects_only_runtime_mutable_columns() -> None:
    state = initial_collection_state(SUBJECT.subject_id, NOW, NOW, POLICY)
    row = encode_collection_state(state)

    transition = encode_collection_transition(state)

    assert tuple(transition) == COLLECTION_TRANSITION_COLUMNS
    assert transition == {column: row[column] for column in COLLECTION_TRANSITION_COLUMNS}
    assert "updated_at" not in row
    assert set(row) - set(transition) == {
        "subject_id",
        "policy_hash",
        "source_created_at",
        "deadline_at",
        "evidence_retain_until",
        "tombstone_retain_until",
        "max_attempts",
        "max_backoff_seconds",
    }


@pytest.mark.parametrize(
    ("column", "replacement"),
    [
        ("installation_id", float(ATTEMPT.scope.installation_id)),
        ("repository_id", float(ATTEMPT.scope.repository_id)),
        ("workflow_run_id", float(ATTEMPT.workflow_run_id)),
        ("run_attempt", float(ATTEMPT.run_attempt)),
        ("head_sha", "c" * 40),
        ("provider_job_id", 404.0),
    ],
)
def test_workflow_job_decoder_rejects_column_and_canonical_divergence(
    column: str,
    replacement: object,
) -> None:
    row = encode_workflow_observation(_job_event(_RUNNER), delivery_id=_DELIVERY_ID)
    row[column] = replacement

    with pytest.raises(CiEconomicsCodecError, match=r"positive integer|diverge"):
        decode_workflow_job_observation(row)


@pytest.mark.parametrize(
    "runner",
    [
        {
            "runnerId": 11,
            "runnerName": None,
            "runnerGroupId": 12,
            "runnerGroupName": "linux",
        },
        {
            "runnerId": None,
            "runnerName": None,
            "runnerGroupId": 12,
            "runnerGroupName": "linux",
        },
        {
            "runnerId": 11,
            "runnerName": "runner-a",
            "runnerGroupId": 12,
            "runnerGroupName": None,
        },
    ],
    ids=("missing-name", "group-without-runner", "missing-group-name"),
)
def test_workflow_job_decoder_rejects_malformed_runner_identity(
    runner: dict[str, object],
) -> None:
    row = encode_workflow_observation(_job_event(_RUNNER), delivery_id=_DELIVERY_ID)
    raw = row["observation_canonical_json"]
    assert type(raw) is bytes
    payload = json.loads(raw)
    assert type(payload) is dict
    mapping = cast(dict[str, object], payload)
    mapping["runner"] = runner
    row["observation_canonical_json"] = canonical_json(mapping)

    with pytest.raises(CiEconomicsCodecError, match="stored workflow job observation is invalid"):
        decode_workflow_job_observation(row)


def _job_event(
    runner: WorkflowJobRunnerIdentity | None,
) -> NormalizedWorkflowJobEvent:
    return NormalizedWorkflowJobEvent(
        provenance=WebhookProvenance(
            delivery_id=_DELIVERY_ID,
            event_name="workflow_job",
            body_sha256="a" * 64,
            verified_at=NOW,
            verifier_version="test-verifier/v1",
        ),
        repository=GitHubRepository(
            ATTEMPT.scope.installation_id,
            ATTEMPT.scope.repository_id,
            "acme",
            "service",
        ),
        action="completed",
        workflow_run_id=ATTEMPT.workflow_run_id,
        run_attempt=ATTEMPT.run_attempt,
        head_sha=ATTEMPT.head_sha,
        workflow_job_id=404,
        job_name="backend",
        status="completed",
        conclusion="success",
        created_at=NOW,
        started_at=NOW + timedelta(minutes=1),
        completed_at=NOW + timedelta(minutes=3),
        labels=("linux", "self-hosted"),
        runner=runner,
    )

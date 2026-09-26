from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from hashlib import sha256
from importlib.resources import files
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.execution_orchestration.provider_signal import ProviderSignal
from ci_coordinator.kernel import canonical_json
from ci_coordinator.persistence.reconciliation_state_codec import (
    ReconciliationStateCodecError,
    decode_observation_row,
    decode_result_row,
    decode_subject_row,
    encode_observation_row,
    encode_result_row,
    encode_subject_row,
)
from ci_coordinator.persistence.reconciliation_state_repository import (
    _PostgresReconciliationRepository,
)
from ci_coordinator.persistence.shadow_evidence_codec import (
    ShadowEvidenceCodecError,
    decode_shadow_evidence_row,
    encode_shadow_evidence_row,
)
from ci_coordinator.persistence.shadow_evidence_repository import _PostgresShadowEvidenceRepository
from ci_coordinator.persistence.shadow_reconciliation_state_profile import (
    PROFILE_RESOURCE_NAME,
    PROFILE_SHA256,
    load_bundled_shadow_reconciliation_state_profile,
    parse_shadow_reconciliation_state_profile,
)
from ci_coordinator.reconciliation import (
    CandidateEvidenceContext,
    OmittedSignal,
    PlanningEvidenceContext,
    ReconciliationContract,
    ReconciliationResult,
    ReconciliationSubject,
    SignalObservation,
    initial_convergence_state,
)
from ci_coordinator.shadow_mode import (
    CoverageRelation,
    FullCiObservation,
    FullCiResult,
    ShadowCandidate,
    ShadowEvidenceRecord,
    compare_full_ci,
)

_NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
_SIGNAL = ProviderSignal.derive(
    execution_profile_id="python-313",
    shard_id="ci_shard_0123456789abcdef0123456789abcdef",
)
_NATIVE_SIGNAL = ProviderSignal.declared_native(
    workflow_path=".github/workflows/full-check.yml",
    job_id="full-check-gate",
    job_name="Full Check",
)


class _DatetimeSubclass(datetime):
    pass


class _UnknownOffset(tzinfo):
    def utcoffset(self, dt: datetime | None) -> None:
        return None

    def dst(self, dt: datetime | None) -> None:
        return None

    def tzname(self, dt: datetime | None) -> None:
        return None


@pytest.mark.parametrize(
    "observed_at",
    (
        datetime(2026, 7, 14, 12),
        datetime(2026, 7, 14, 12, tzinfo=_UnknownOffset()),
        _DatetimeSubclass(2026, 7, 14, 12, tzinfo=UTC),
    ),
    ids=("naive", "offset-none", "aware-subclass"),
)
def test_shadow_codec_rejects_non_exact_or_unaware_time_after_constructor_bypass(
    observed_at: datetime,
) -> None:
    record = _shadow_record("plan-a", _NOW)
    object.__setattr__(record, "observed_at", observed_at)
    profile = load_bundled_shadow_reconciliation_state_profile()

    with pytest.raises(
        ShadowEvidenceCodecError, match=r"^shadow evidence timestamp must be timezone-aware$"
    ):
        encode_shadow_evidence_row(record, profile)


@pytest.mark.parametrize(
    "observed_at",
    (
        datetime(2026, 7, 14, 14, 0, 0, 123456, tzinfo=timezone(timedelta(hours=2))),
        datetime(2026, 7, 14, 7, 0, 0, 123456, tzinfo=timezone(timedelta(hours=-5))),
    ),
    ids=("positive-offset", "negative-offset"),
)
def test_shadow_codec_preserves_equivalent_instants_and_microsecond_bytes(
    observed_at: datetime,
) -> None:
    profile = load_bundled_shadow_reconciliation_state_profile()
    utc_record = _shadow_record("plan-a", datetime(2026, 7, 14, 12, 0, 0, 123456, tzinfo=UTC))
    offset_record = _shadow_record("plan-a", observed_at)

    utc_row = encode_shadow_evidence_row(utc_record, profile)
    offset_row = encode_shadow_evidence_row(offset_record, profile)

    assert offset_record.key == utc_record.key
    assert offset_record.has_same_semantics_as(utc_record)
    assert offset_row == utc_row
    assert b'"observedAt":"2026-07-14T12:00:00.123456Z"' in cast(
        bytes, offset_row["record_canonical_json"]
    )
    assert decode_shadow_evidence_row(offset_row, profile) == utc_record


def test_profile_is_digest_pinned_and_rejects_unknown_fields() -> None:
    resource = files("ci_coordinator.persistence.resources").joinpath(PROFILE_RESOURCE_NAME)
    profile_bytes = resource.read_bytes()
    profile = load_bundled_shadow_reconciliation_state_profile()

    assert sha256(profile_bytes).hexdigest() == PROFILE_SHA256
    assert parse_shadow_reconciliation_state_profile(profile_bytes) == profile

    altered = json.loads(profile_bytes)
    altered["unexpected"] = True
    with pytest.raises(ValueError, match="unexpected or missing"):
        parse_shadow_reconciliation_state_profile(json.dumps(altered).encode("utf-8"))


def test_shadow_codec_replays_semantics_without_timestamp_inflation() -> None:
    profile = load_bundled_shadow_reconciliation_state_profile()
    first = _shadow_record("plan-a", _NOW)
    replay = _shadow_record("plan-a", _NOW + timedelta(seconds=1))

    first_row = encode_shadow_evidence_row(first, profile)
    replay_row = encode_shadow_evidence_row(replay, profile)

    assert decode_shadow_evidence_row(first_row, profile) == first
    assert first_row["semantic_hash"] == replay_row["semantic_hash"]
    canonical = cast(bytes, first_row["record_canonical_json"])
    assert b'"schemaVersion":"ci-shadow-evidence-record/v2"' in canonical
    assert b'"baselinePlan"' in canonical
    assert b'"candidatePlan"' in canonical

    first_row["semantic_hash"] = "0" * 64
    with pytest.raises(ShadowEvidenceCodecError, match="semantic hash is invalid"):
        decode_shadow_evidence_row(first_row, profile)

    tampered = encode_shadow_evidence_row(first, profile)
    tampered["record_canonical_json"] = canonical.replace(
        b"ci-shadow-evidence-record/v2", b"ci-shadow-evidence-record/v1"
    )
    with pytest.raises(ShadowEvidenceCodecError, match="schema version is unsupported"):
        decode_shadow_evidence_row(tampered, profile)


@pytest.mark.parametrize(
    "signal",
    (_SIGNAL, _NATIVE_SIGNAL),
    ids=("derived-shard", "declared-native"),
)
def test_reconciliation_codecs_reconstruct_exact_domain_values(
    signal: ProviderSignal,
) -> None:
    profile = load_bundled_shadow_reconciliation_state_profile()
    subject = _subject()
    contract = ReconciliationContract((signal,), ())
    convergence = initial_convergence_state(_NOW)
    observation = SignalObservation(
        "obs-1",
        subject.subject_id,
        signal.signal_id,
        subject.workflow_run_id,
        1,
        401,
        "completed",
        "success",
    )
    result = ReconciliationResult(subject.subject_id, "success", ())

    decoded_subject, decoded_contract, revision, decoded_convergence = decode_subject_row(
        encode_subject_row(subject, contract, convergence, profile), profile
    )
    decoded_observation_revision, decoded_observation = decode_observation_row(
        encode_observation_row(subject, 1, observation, profile), subject, profile
    )
    decoded_result_revision, decoded_result = decode_result_row(
        encode_result_row(subject, 1, result, profile), subject, profile
    )

    assert (decoded_subject, decoded_contract, revision) == (subject, contract, 0)
    assert decoded_convergence == convergence
    assert (decoded_observation_revision, decoded_observation) == (1, observation)
    assert (decoded_result_revision, decoded_result) == (1, result)

    malformed = encode_subject_row(subject, contract, convergence, profile)
    malformed["identity_hash"] = "0" * 64
    with pytest.raises(ReconciliationStateCodecError, match="identity hash is invalid"):
        decode_subject_row(malformed, profile)


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("workflowRunId", "another workflow run"),
        ("runAttempt", "another run attempt"),
    ],
)
def test_reconciliation_observation_codec_rejects_foreign_run_identity(
    field: str,
    message: str,
) -> None:
    profile = load_bundled_shadow_reconciliation_state_profile()
    subject = _subject()
    valid = SignalObservation(
        "obs-foreign-attempt",
        subject.subject_id,
        _SIGNAL.signal_id,
        subject.workflow_run_id,
        subject.run_attempt,
        401,
        "completed",
        "success",
    )
    foreign = replace(
        valid,
        workflow_run_id=(
            subject.workflow_run_id + 1 if field == "workflowRunId" else subject.workflow_run_id
        ),
        run_attempt=subject.run_attempt + 1 if field == "runAttempt" else subject.run_attempt,
    )

    with pytest.raises(ReconciliationStateCodecError, match=message):
        encode_observation_row(subject, 1, foreign, profile)

    row = encode_observation_row(subject, 1, valid, profile)
    mapping = json.loads(cast(bytes, row["observation_canonical_json"]))
    mapping[field] = cast(int, mapping[field]) + 1
    canonical = canonical_json(mapping)
    row["observation_canonical_json"] = canonical
    row["semantic_hash"] = sha256(canonical).hexdigest()

    with pytest.raises(ReconciliationStateCodecError, match=message):
        decode_observation_row(row, subject, profile)


def test_reconciliation_contract_codec_preserves_candidate_evidence() -> None:
    profile = load_bundled_shadow_reconciliation_state_profile()
    subject = _subject()
    omission = OmittedSignal("backend-tests", "Backend tests")
    contract = ReconciliationContract(
        (_SIGNAL,),
        (),
        CandidateEvidenceContext(
            profile_id="profile-1",
            repository="example-org/ci-coordinator",
            config_epoch="epoch-1",
            policy_hash="policy-1",
            diff_hash="diff-1",
            graph_hash="graph-1",
            baseline_plan_id="full-ci-1",
            candidate_plan_id="candidate-1",
            omitted_signals=(omission,),
        ),
        PlanningEvidenceContext(
            request_hash="1" * 64,
            input_hash="2" * 64,
            config_epoch_id="3" * 64,
            repo_epoch_hash="4" * 64,
            diff_hash="5" * 64,
            policy_hash="6" * 64,
            graph_hash="7" * 64,
            validation_catalog_hash="8" * 64,
            deterministic_plan_id="candidate-plan-1",
            verified_plan_id="verified-plan-1",
            verified_plan_hash="9" * 64,
            planner_version="candidate-planning-core/v1",
            verifier_version="verification-core/v1",
            fallback_reason=None,
        ),
    )

    _, decoded, _, _ = decode_subject_row(
        encode_subject_row(subject, contract, initial_convergence_state(_NOW), profile),
        profile,
    )

    assert decoded == contract
    assert ReconciliationContract((_SIGNAL,), ()).canonical_mapping() == {
        "providerSignals": [_SIGNAL.to_identity_mapping()],
        "omittedSignals": [],
    }


class _CancelledScalarConnection:
    async def scalar(self, _statement: object) -> object:
        raise asyncio.CancelledError("database operation cancelled")


def test_shadow_and_reconciliation_write_cancellation_marks_rollback() -> None:
    async def scenario() -> None:
        profile = load_bundled_shadow_reconciliation_state_profile()
        rollback_required = False

        def mark_rollback_required() -> None:
            nonlocal rollback_required
            rollback_required = True

        connection = cast(AsyncConnection, _CancelledScalarConnection())
        shadow = _PostgresShadowEvidenceRepository(
            connection,
            profile,
            lambda: None,
            mark_rollback_required,
        )
        with pytest.raises(asyncio.CancelledError, match="database operation cancelled"):
            await shadow.record(_shadow_record("plan-a", _NOW))
        assert rollback_required

        rollback_required = False
        reconciliation = _PostgresReconciliationRepository(
            connection,
            profile,
            lambda: None,
            mark_rollback_required,
        )
        with pytest.raises(asyncio.CancelledError, match="database operation cancelled"):
            await reconciliation.register_subject(
                _subject(),
                ReconciliationContract((_SIGNAL,), ()),
                initial_convergence_state(_NOW),
            )
        assert rollback_required

    asyncio.run(scenario())


def _subject() -> ReconciliationSubject:
    return ReconciliationSubject.create(
        installation_id=101,
        repository_id=202,
        event_name="pull_request",
        ref="refs/pull/42/merge",
        base_sha="a" * 40,
        head_sha="b" * 40,
        workflow_run_id=303,
        run_attempt=1,
    )


def _shadow_record(plan: str, observed_at: datetime) -> ShadowEvidenceRecord:
    candidate = ShadowCandidate(
        repo="example-org/ci-coordinator",
        event="delivery-42",
        base_sha="a" * 40,
        head_sha="b" * 40,
        config_epoch="epoch-1",
        policy_hash="policy-1",
        diff_hash="diff-1",
        graph_hash="graph-1",
        baseline_plan="baseline-plan-1",
        candidate_plan=plan,
        surface="pull-request",
        coverage_relation=CoverageRelation.COVERED,
        actual_full_ci_result=FullCiResult.PASSED,
    )
    observation = FullCiObservation(
        repo=candidate.repo,
        event=candidate.event,
        base_sha=candidate.base_sha,
        head_sha=candidate.head_sha,
        config_epoch=candidate.config_epoch,
        policy_hash=candidate.policy_hash,
        diff_hash=candidate.diff_hash,
        graph_hash=candidate.graph_hash,
        baseline_plan=candidate.baseline_plan,
        candidate_plan=candidate.candidate_plan,
        actual_full_ci_result=candidate.actual_full_ci_result,
    )
    return ShadowEvidenceRecord(
        "shadow-profile/v1", observed_at, compare_full_ci(candidate, observation)
    )

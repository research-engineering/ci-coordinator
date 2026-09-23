"""Canonical subject, contract, and convergence row codec."""

from __future__ import annotations

from hashlib import sha256

from ci_coordinator.execution_orchestration import ProviderSignal
from ci_coordinator.kernel import canonical_json
from ci_coordinator.persistence._reconciliation_codec_support import (
    ReconciliationStateCodecError,
    _aware_datetime,
    _bounded_non_negative,
    _bounded_positive,
    _mapping,
    _positive,
    _revision,
    _text,
)
from ci_coordinator.persistence.canonical_row import (
    decode_canonical_object,
    encode_canonical_object,
    require_digest,
    require_exact_keys,
)
from ci_coordinator.persistence.shadow_reconciliation_state_profile import (
    ShadowReconciliationStateProfile,
)
from ci_coordinator.reconciliation import (
    CandidateEvidenceContext,
    OmittedSignal,
    PlanningEvidenceContext,
    ReconciliationContract,
    ReconciliationConvergenceState,
    ReconciliationSubject,
)
from ci_coordinator.reconciliation.subject import RECONCILIATION_SUBJECT_SCHEMA_VERSION


def encode_subject_row(
    subject: ReconciliationSubject,
    contract: ReconciliationContract,
    convergence: ReconciliationConvergenceState,
    profile: ShadowReconciliationStateProfile,
) -> dict[str, object]:
    if (
        type(subject) is not ReconciliationSubject
        or type(contract) is not ReconciliationContract
        or type(convergence) is not ReconciliationConvergenceState
    ):
        raise ReconciliationStateCodecError(
            "reconciliation subject, contract, and convergence state must be exact"
        )
    _validate_convergence_profile(convergence, profile)
    subject_mapping = subject.identity_mapping()
    subject_canonical = encode_canonical_object(
        subject_mapping,
        maximum_bytes=profile.reconciliation_subject_canonical_bytes,
        context="reconciliation subject",
    )
    contract_mapping = contract.canonical_mapping()
    contract_canonical = encode_canonical_object(
        contract_mapping,
        maximum_bytes=profile.reconciliation_contract_canonical_bytes,
        context="reconciliation contract",
    )
    return {
        "subject_id": subject.subject_id,
        "installation_id": subject.installation_id,
        "repository_id": subject.repository_id,
        "subject_canonical_json": subject_canonical,
        "contract_canonical_json": contract_canonical,
        "identity_hash": subject.subject_id,
        "contract_hash": contract.contract_hash,
        "revision": 0,
        "created_at": convergence.created_at,
        "deadline_at": convergence.deadline_at,
        "next_attempt_at": convergence.next_attempt_at,
        "attempt_count": convergence.attempt_count,
        "max_attempts": convergence.max_attempts,
        "backoff_seconds": convergence.backoff_seconds,
        "max_backoff_seconds": convergence.max_backoff_seconds,
        "claim_generation": convergence.claim_generation,
        "lease_token": convergence.lease_token,
        "lease_acquired_at": convergence.lease_acquired_at,
        "lease_expires_at": convergence.lease_expires_at,
    }


def decode_subject_row(
    row: dict[str, object],
    profile: ShadowReconciliationStateProfile,
) -> tuple[
    ReconciliationSubject,
    ReconciliationContract,
    int,
    ReconciliationConvergenceState,
]:
    subject_id = require_digest(row.get("subject_id"), "stored reconciliation subject id")
    identity_hash = require_digest(row.get("identity_hash"), "stored reconciliation identity hash")
    if subject_id != identity_hash:
        raise ReconciliationStateCodecError(
            "stored reconciliation subject identity hash is invalid"
        )
    subject_mapping = decode_canonical_object(
        row.get("subject_canonical_json"),
        maximum_bytes=profile.reconciliation_subject_canonical_bytes,
        context="reconciliation subject",
    )
    if sha256(canonical_json(subject_mapping)).hexdigest() != subject_id:
        raise ReconciliationStateCodecError("stored reconciliation subject digest is invalid")
    subject = _subject_from_mapping(subject_mapping, subject_id)
    if (
        _positive(row.get("installation_id"), "stored reconciliation installation id")
        != subject.installation_id
        or _positive(row.get("repository_id"), "stored reconciliation repository id")
        != subject.repository_id
    ):
        raise ReconciliationStateCodecError(
            "stored reconciliation scope does not match its canonical subject"
        )
    contract_mapping = decode_canonical_object(
        row.get("contract_canonical_json"),
        maximum_bytes=profile.reconciliation_contract_canonical_bytes,
        context="reconciliation contract",
    )
    contract = _contract_from_mapping(contract_mapping)
    contract_hash = require_digest(row.get("contract_hash"), "stored reconciliation contract hash")
    if contract_hash != contract.contract_hash:
        raise ReconciliationStateCodecError("stored reconciliation contract digest is invalid")
    revision = _revision(row.get("revision"), "stored reconciliation revision")
    convergence = _convergence_from_row(row, profile)
    return subject, contract, revision, convergence


def _subject_from_mapping(mapping: dict[str, object], subject_id: str) -> ReconciliationSubject:
    require_exact_keys(
        mapping,
        {
            "schemaVersion",
            "installationId",
            "repositoryId",
            "eventName",
            "ref",
            "baseSha",
            "headSha",
            "workflowRunId",
            "runAttempt",
        },
        "reconciliation subject",
    )
    if mapping["schemaVersion"] != RECONCILIATION_SUBJECT_SCHEMA_VERSION:
        raise ReconciliationStateCodecError(
            "stored reconciliation subject schema version is unsupported"
        )
    try:
        return ReconciliationSubject(
            schema_version=RECONCILIATION_SUBJECT_SCHEMA_VERSION,
            installation_id=_positive(mapping["installationId"], "reconciliation installation id"),
            repository_id=_positive(mapping["repositoryId"], "reconciliation repository id"),
            event_name=_text(mapping["eventName"], "reconciliation event name"),
            ref=_text(mapping["ref"], "reconciliation ref"),
            base_sha=_text(mapping["baseSha"], "reconciliation base SHA"),
            head_sha=_text(mapping["headSha"], "reconciliation head SHA"),
            workflow_run_id=_positive(mapping["workflowRunId"], "reconciliation workflow run id"),
            run_attempt=_positive(mapping["runAttempt"], "reconciliation run attempt"),
            subject_id=subject_id,
        )
    except (TypeError, ValueError) as error:
        raise ReconciliationStateCodecError("stored reconciliation subject is invalid") from error


def _convergence_from_row(
    row: dict[str, object],
    profile: ShadowReconciliationStateProfile,
) -> ReconciliationConvergenceState:
    lease_token = row.get("lease_token")
    if lease_token is not None:
        lease_token = require_digest(lease_token, "stored reconciliation lease token")
    lease_acquired_at = row.get("lease_acquired_at")
    if lease_acquired_at is not None:
        lease_acquired_at = _aware_datetime(
            lease_acquired_at,
            "stored reconciliation lease acquisition",
        )
    lease_expires_at = row.get("lease_expires_at")
    if lease_expires_at is not None:
        lease_expires_at = _aware_datetime(
            lease_expires_at,
            "stored reconciliation lease expiry",
        )
    try:
        state = ReconciliationConvergenceState(
            created_at=_aware_datetime(
                row.get("created_at"),
                "stored reconciliation creation time",
            ),
            deadline_at=_aware_datetime(
                row.get("deadline_at"),
                "stored reconciliation deadline",
            ),
            next_attempt_at=_aware_datetime(
                row.get("next_attempt_at"),
                "stored reconciliation due time",
            ),
            attempt_count=_bounded_non_negative(
                row.get("attempt_count"),
                profile.reconciliation_max_attempts,
                "stored reconciliation attempt count",
            ),
            max_attempts=_bounded_positive(
                row.get("max_attempts"),
                profile.reconciliation_max_attempts,
                "stored maximum reconciliation attempts",
            ),
            backoff_seconds=_bounded_positive(
                row.get("backoff_seconds"),
                profile.reconciliation_max_backoff_seconds,
                "stored reconciliation backoff",
            ),
            max_backoff_seconds=_bounded_positive(
                row.get("max_backoff_seconds"),
                profile.reconciliation_max_backoff_seconds,
                "stored maximum reconciliation backoff",
            ),
            claim_generation=_revision(
                row.get("claim_generation"),
                "stored reconciliation claim generation",
            ),
            lease_token=lease_token,
            lease_acquired_at=lease_acquired_at,
            lease_expires_at=lease_expires_at,
        )
        _validate_convergence_profile(state, profile)
        return state
    except (TypeError, ValueError) as error:
        raise ReconciliationStateCodecError(
            "stored reconciliation convergence state is invalid"
        ) from error


def _validate_convergence_profile(
    state: ReconciliationConvergenceState,
    profile: ShadowReconciliationStateProfile,
) -> None:
    if state.max_attempts > profile.reconciliation_max_attempts:
        raise ReconciliationStateCodecError("reconciliation attempt bound exceeds its profile")
    if state.max_backoff_seconds > profile.reconciliation_max_backoff_seconds:
        raise ReconciliationStateCodecError("reconciliation backoff bound exceeds its profile")
    if (
        state.deadline_at - state.created_at
    ).total_seconds() > profile.reconciliation_max_deadline_seconds:
        raise ReconciliationStateCodecError("reconciliation deadline exceeds its profile")
    if (
        state.lease_acquired_at is not None
        and state.lease_expires_at is not None
        and (state.lease_expires_at - state.lease_acquired_at).total_seconds()
        > profile.reconciliation_max_lease_seconds
    ):
        raise ReconciliationStateCodecError("reconciliation lease exceeds its profile")


def _contract_from_mapping(mapping: dict[str, object]) -> ReconciliationContract:
    keys = {"providerSignals", "omittedSignals"}
    if "candidateEvidence" in mapping:
        keys.add("candidateEvidence")
    if "planningEvidence" in mapping:
        keys.add("planningEvidence")
    require_exact_keys(mapping, keys, "reconciliation contract")
    try:
        return ReconciliationContract(
            provider_signals=_provider_signals(mapping["providerSignals"]),
            omitted_signals=tuple(
                OmittedSignal(signal_id, name)
                for signal_id, name in _signal_pairs(mapping["omittedSignals"])
            ),
            candidate_evidence=(
                _candidate_evidence_from_mapping(mapping["candidateEvidence"])
                if "candidateEvidence" in mapping
                else None
            ),
            planning_evidence=(
                _planning_evidence_from_mapping(mapping["planningEvidence"])
                if "planningEvidence" in mapping
                else None
            ),
        )
    except (TypeError, ValueError) as error:
        raise ReconciliationStateCodecError("stored reconciliation contract is invalid") from error


def _provider_signals(value: object) -> tuple[ProviderSignal, ...]:
    if type(value) is not list:
        raise ReconciliationStateCodecError("stored provider signals must be a list")
    signals: list[ProviderSignal] = []
    for item in value:
        mapping = _mapping(item, "provider signal")
        require_exact_keys(
            mapping,
            {
                "signalId",
                "jobName",
                "kind",
                "executionProfileId",
                "shardId",
                "workflowPath",
                "jobId",
            },
            "provider signal",
        )
        kind = mapping["kind"]
        if kind not in {"derived-shard", "declared-native"}:
            raise ReconciliationStateCodecError("stored provider signal kind is invalid")
        signals.append(
            ProviderSignal(
                signal_id=_text(mapping["signalId"], "provider signal id"),
                job_name=_text(mapping["jobName"], "provider signal job name"),
                kind=kind,
                execution_profile_id=_optional_text(
                    mapping["executionProfileId"],
                    "provider signal execution profile id",
                ),
                shard_id=_optional_text(
                    mapping["shardId"],
                    "provider signal shard id",
                ),
                workflow_path=_optional_text(
                    mapping["workflowPath"],
                    "provider signal workflow path",
                ),
                job_id=_optional_text(mapping["jobId"], "provider signal job id"),
            )
        )
    return tuple(signals)


def _candidate_evidence_from_mapping(value: object) -> CandidateEvidenceContext:
    mapping = _mapping(value, "candidate evidence")
    require_exact_keys(
        mapping,
        {
            "profileId",
            "repository",
            "configEpoch",
            "policyHash",
            "diffHash",
            "graphHash",
            "baselinePlanId",
            "candidatePlanId",
            "omittedSignals",
        },
        "candidate evidence",
    )
    return CandidateEvidenceContext(
        profile_id=_text(mapping["profileId"], "candidate evidence profile id"),
        repository=_text(mapping["repository"], "candidate evidence repository"),
        config_epoch=_text(mapping["configEpoch"], "candidate evidence config epoch"),
        policy_hash=_text(mapping["policyHash"], "candidate evidence policy hash"),
        diff_hash=_text(mapping["diffHash"], "candidate evidence diff hash"),
        graph_hash=_text(mapping["graphHash"], "candidate evidence graph hash"),
        baseline_plan_id=_text(mapping["baselinePlanId"], "candidate evidence baseline plan id"),
        candidate_plan_id=_text(mapping["candidatePlanId"], "candidate evidence candidate plan id"),
        omitted_signals=tuple(
            OmittedSignal(signal_id, name)
            for signal_id, name in _signal_pairs(mapping["omittedSignals"])
        ),
    )


def _planning_evidence_from_mapping(value: object) -> PlanningEvidenceContext:
    mapping = _mapping(value, "planning evidence")
    require_exact_keys(
        mapping,
        {
            "requestHash",
            "inputHash",
            "configEpochId",
            "repoEpochHash",
            "diffHash",
            "policyHash",
            "graphHash",
            "validationCatalogHash",
            "deterministicPlanId",
            "verifiedPlanId",
            "verifiedPlanHash",
            "plannerVersion",
            "verifierVersion",
            "fallbackReason",
        },
        "planning evidence",
    )
    fallback_reason = mapping["fallbackReason"]
    if fallback_reason is not None:
        fallback_reason = _text(fallback_reason, "planning evidence fallback reason")
    return PlanningEvidenceContext(
        request_hash=_text(mapping["requestHash"], "planning evidence request hash"),
        input_hash=_text(mapping["inputHash"], "planning evidence input hash"),
        config_epoch_id=_text(mapping["configEpochId"], "planning evidence config epoch id"),
        repo_epoch_hash=_text(mapping["repoEpochHash"], "planning evidence repository epoch hash"),
        diff_hash=_text(mapping["diffHash"], "planning evidence diff hash"),
        policy_hash=_text(mapping["policyHash"], "planning evidence policy hash"),
        graph_hash=_text(mapping["graphHash"], "planning evidence graph hash"),
        validation_catalog_hash=_text(
            mapping["validationCatalogHash"], "planning evidence validation catalog hash"
        ),
        deterministic_plan_id=_text(
            mapping["deterministicPlanId"], "planning evidence deterministic plan id"
        ),
        verified_plan_id=_text(mapping["verifiedPlanId"], "planning evidence verified plan id"),
        verified_plan_hash=_text(
            mapping["verifiedPlanHash"], "planning evidence verified plan hash"
        ),
        planner_version=_text(mapping["plannerVersion"], "planning evidence planner version"),
        verifier_version=_text(mapping["verifierVersion"], "planning evidence verifier version"),
        fallback_reason=fallback_reason,
    )


def _signal_pairs(value: object) -> tuple[tuple[str, str], ...]:
    if type(value) is not list:
        raise ReconciliationStateCodecError("stored reconciliation signals must be a list")
    values: list[tuple[str, str]] = []
    for item in value:
        mapping = _mapping(item, "reconciliation signal")
        require_exact_keys(mapping, {"signalId", "name"}, "reconciliation signal")
        values.append(
            (
                _text(mapping["signalId"], "reconciliation signal id"),
                _text(mapping["name"], "reconciliation signal name"),
            )
        )
    return tuple(values)


def _optional_text(value: object, context: str) -> str | None:
    return None if value is None else _text(value, context)

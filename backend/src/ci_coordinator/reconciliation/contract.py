"""Immutable provider-signal and omitted-proof contract for one subject."""

from __future__ import annotations

from dataclasses import dataclass

from ci_coordinator.execution_orchestration import ProviderSignal
from ci_coordinator.kernel import hash_object, utf16_sort_key
from ci_coordinator.reconciliation.observation import OmittedSignal


@dataclass(frozen=True, slots=True)
class PlanningEvidenceContext:
    """Replay coordinates for one deterministic planner/verifier decision."""

    request_hash: str
    input_hash: str
    config_epoch_id: str
    repo_epoch_hash: str
    diff_hash: str
    policy_hash: str
    graph_hash: str
    validation_catalog_hash: str
    deterministic_plan_id: str
    verified_plan_id: str
    verified_plan_hash: str
    planner_version: str
    verifier_version: str
    fallback_reason: str | None

    def __post_init__(self) -> None:
        for field_name in (
            "request_hash",
            "input_hash",
            "config_epoch_id",
            "repo_epoch_hash",
            "diff_hash",
            "policy_hash",
            "graph_hash",
            "validation_catalog_hash",
            "verified_plan_hash",
        ):
            _require_digest(getattr(self, field_name), field_name)
        for field_name in (
            "deterministic_plan_id",
            "verified_plan_id",
            "planner_version",
            "verifier_version",
        ):
            value = getattr(self, field_name)
            if type(value) is not str or not value:
                raise ValueError(f"{field_name} must be non-empty text")
        if self.fallback_reason is not None and (
            type(self.fallback_reason) is not str or not self.fallback_reason
        ):
            raise ValueError("fallback_reason must be non-empty text or absent")

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "requestHash": self.request_hash,
            "inputHash": self.input_hash,
            "configEpochId": self.config_epoch_id,
            "repoEpochHash": self.repo_epoch_hash,
            "diffHash": self.diff_hash,
            "policyHash": self.policy_hash,
            "graphHash": self.graph_hash,
            "validationCatalogHash": self.validation_catalog_hash,
            "deterministicPlanId": self.deterministic_plan_id,
            "verifiedPlanId": self.verified_plan_id,
            "verifiedPlanHash": self.verified_plan_hash,
            "plannerVersion": self.planner_version,
            "verifierVersion": self.verifier_version,
            "fallbackReason": self.fallback_reason,
        }


@dataclass(frozen=True, slots=True)
class CandidateEvidenceContext:
    """Immutable candidate facts needed after Full CI reaches a terminal state."""

    profile_id: str
    repository: str
    config_epoch: str
    policy_hash: str
    diff_hash: str
    graph_hash: str
    baseline_plan_id: str
    candidate_plan_id: str
    omitted_signals: tuple[OmittedSignal, ...]

    def __post_init__(self) -> None:
        for field_name in (
            "profile_id",
            "repository",
            "config_epoch",
            "policy_hash",
            "diff_hash",
            "graph_hash",
            "baseline_plan_id",
            "candidate_plan_id",
        ):
            value = getattr(self, field_name)
            if type(value) is not str or not value:
                raise ValueError(f"{field_name} must be non-empty text")
        _require_exact_tuple(self.omitted_signals, OmittedSignal, "omitted_signals")
        _require_sorted_unique(self.omitted_signals, "omitted_signals")
        if not self.omitted_signals:
            raise ValueError("candidate evidence requires at least one omitted signal")

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "profileId": self.profile_id,
            "repository": self.repository,
            "configEpoch": self.config_epoch,
            "policyHash": self.policy_hash,
            "diffHash": self.diff_hash,
            "graphHash": self.graph_hash,
            "baselinePlanId": self.baseline_plan_id,
            "candidatePlanId": self.candidate_plan_id,
            "omittedSignals": [
                {"signalId": signal.signal_id, "name": signal.name}
                for signal in self.omitted_signals
            ],
        }


@dataclass(frozen=True, slots=True)
class ReconciliationContract:
    provider_signals: tuple[ProviderSignal, ...]
    omitted_signals: tuple[OmittedSignal, ...]
    candidate_evidence: CandidateEvidenceContext | None = None
    planning_evidence: PlanningEvidenceContext | None = None

    def __post_init__(self) -> None:
        _require_exact_tuple(self.provider_signals, ProviderSignal, "provider_signals")
        _require_exact_tuple(self.omitted_signals, OmittedSignal, "omitted_signals")
        _require_sorted_unique(self.provider_signals, "provider_signals")
        _require_sorted_unique(self.omitted_signals, "omitted_signals")
        expected_ids = {signal.signal_id for signal in self.provider_signals}
        omitted_ids = {signal.signal_id for signal in self.omitted_signals}
        if expected_ids & omitted_ids:
            raise ValueError("provider_signals and omitted_signals must not overlap")
        job_names = tuple(signal.job_name for signal in self.provider_signals)
        if len(job_names) != len(set(job_names)):
            raise ValueError("provider signal job names must be unique")
        if (
            self.candidate_evidence is not None
            and type(self.candidate_evidence) is not CandidateEvidenceContext
        ):
            raise TypeError("candidate_evidence must be exact or absent")
        if (
            self.planning_evidence is not None
            and type(self.planning_evidence) is not PlanningEvidenceContext
        ):
            raise TypeError("planning_evidence must be exact or absent")

    @property
    def contract_hash(self) -> str:
        return hash_object(self.canonical_mapping())

    def canonical_mapping(self) -> dict[str, object]:
        mapping: dict[str, object] = {
            "providerSignals": [signal.to_identity_mapping() for signal in self.provider_signals],
            "omittedSignals": [
                {"signalId": signal.signal_id, "name": signal.name}
                for signal in self.omitted_signals
            ],
        }
        if self.candidate_evidence is not None:
            mapping["candidateEvidence"] = self.candidate_evidence.canonical_mapping()
        if self.planning_evidence is not None:
            mapping["planningEvidence"] = self.planning_evidence.canonical_mapping()
        return mapping


def _require_digest(value: object, field_name: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 digest")


def _require_exact_tuple(
    value: object,
    expected_type: type[ProviderSignal | OmittedSignal],
    field_name: str,
) -> None:
    if type(value) is not tuple or any(type(item) is not expected_type for item in value):
        raise TypeError(f"{field_name} must be a tuple of exact {expected_type.__name__} values")


def _require_sorted_unique(
    signals: tuple[ProviderSignal, ...] | tuple[OmittedSignal, ...],
    field_name: str,
) -> None:
    signal_ids = tuple(signal.signal_id for signal in signals)
    if signal_ids != tuple(sorted(signal_ids, key=utf16_sort_key)) or len(signal_ids) != len(
        set(signal_ids)
    ):
        raise ValueError(f"{field_name} must be strictly ordered by unique signal_id")

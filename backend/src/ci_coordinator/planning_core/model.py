from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ci_coordinator.kernel import hash_object, utf16_sort_key
from ci_coordinator.repo_context.freshness import is_unicode_scalar_string
from ci_coordinator.validation_contract import ValidationDepth, depth_rank

type PlanEvidenceKind = Literal["selected", "omitted", "fallback"]


@dataclass(frozen=True, slots=True)
class SelectedObligation:
    obligation_id: str
    depth: ValidationDepth
    required_witness_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_non_empty_string(self.obligation_id, field_name="obligation_id")
        depth_rank(self.depth)
        _require_sorted_unique_strings(
            self.required_witness_ids,
            field_name="required_witness_ids",
            require_non_empty=True,
        )

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "obligationId": self.obligation_id,
            "depth": self.depth,
            "requiredWitnessIds": list(self.required_witness_ids),
        }


@dataclass(frozen=True, slots=True)
class SelectedWitness:
    witness_id: str
    depth: ValidationDepth
    required_by_obligation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_non_empty_string(self.witness_id, field_name="witness_id")
        depth_rank(self.depth)
        _require_sorted_unique_strings(
            self.required_by_obligation_ids,
            field_name="required_by_obligation_ids",
            require_non_empty=True,
        )

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "witnessId": self.witness_id,
            "depth": self.depth,
            "requiredByObligationIds": list(self.required_by_obligation_ids),
        }


@dataclass(frozen=True, slots=True)
class OmissionPredicates:
    known_diff: Literal[True] = True
    graph_fresh: Literal[True] = True
    surface_known: Literal[True] = True
    no_impact_intersection: Literal[True] = True
    no_global_risk_file: Literal[True] = True
    omission_allowed_by_policy: Literal[True] = True

    def to_identity_mapping(self) -> dict[str, bool]:
        return {
            "knownDiff": self.known_diff,
            "graphFresh": self.graph_fresh,
            "surfaceKnown": self.surface_known,
            "noImpactIntersection": self.no_impact_intersection,
            "noGlobalRiskFile": self.no_global_risk_file,
            "omissionAllowedByPolicy": self.omission_allowed_by_policy,
        }


@dataclass(frozen=True, slots=True)
class OmissionProof:
    obligation_id: str
    rule_id: str
    repo_epoch_hash: str
    diff_hash: str
    policy_hash: str
    graph_hash: str
    catalog_hash: str
    predicates: OmissionPredicates
    assumptions: tuple[str, ...]
    invalidates_when: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_non_empty_string(self.obligation_id, field_name="obligation_id")
        _require_non_empty_string(self.rule_id, field_name="rule_id")
        for field_name, value in (
            ("repo_epoch_hash", self.repo_epoch_hash),
            ("diff_hash", self.diff_hash),
            ("policy_hash", self.policy_hash),
            ("graph_hash", self.graph_hash),
            ("catalog_hash", self.catalog_hash),
        ):
            _require_sha256(value, field_name=field_name)
        _require_sorted_unique_strings(self.assumptions, field_name="assumptions")
        _require_sorted_unique_strings(self.invalidates_when, field_name="invalidates_when")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "obligationId": self.obligation_id,
            "ruleId": self.rule_id,
            "repoEpochHash": self.repo_epoch_hash,
            "diffHash": self.diff_hash,
            "policyHash": self.policy_hash,
            "graphHash": self.graph_hash,
            "catalogHash": self.catalog_hash,
            "predicates": self.predicates.to_identity_mapping(),
            "assumptions": list(self.assumptions),
            "invalidatesWhen": list(self.invalidates_when),
        }


@dataclass(frozen=True, slots=True)
class OmittedObligation:
    obligation_id: str
    required_witness_ids: tuple[str, ...]
    proof: OmissionProof

    def __post_init__(self) -> None:
        if self.obligation_id != self.proof.obligation_id:
            raise ValueError("omitted obligation id must match proof obligation id")
        _require_sorted_unique_strings(
            self.required_witness_ids,
            field_name="required_witness_ids",
            require_non_empty=True,
        )

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "obligationId": self.obligation_id,
            "requiredWitnessIds": list(self.required_witness_ids),
            "proof": self.proof.to_identity_mapping(),
        }


@dataclass(frozen=True, slots=True)
class PlanFallback:
    timeout_seconds: int
    triggered: bool
    reason: str | None
    trigger_on: tuple[Literal["timeout", "invalid-plan", "stale-input", "verifier-error"], ...] = (
        "timeout",
        "invalid-plan",
        "stale-input",
        "verifier-error",
    )

    def __post_init__(self) -> None:
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 3600:
            raise ValueError("timeout_seconds must be an integer in [1, 3600]")
        if type(self.triggered) is not bool:
            raise ValueError("triggered must be a boolean")
        if self.triggered != (self.reason is not None):
            raise ValueError("fallback reason presence must equal triggered")
        if self.reason is not None:
            _require_non_empty_string(self.reason, field_name="reason")
        expected = ("timeout", "invalid-plan", "stale-input", "verifier-error")
        if self.trigger_on != expected:
            raise ValueError("trigger_on must preserve the planner fallback trigger inventory")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "kind": "full-ci",
            "timeoutSeconds": self.timeout_seconds,
            "triggerOn": list(self.trigger_on),
            "triggered": self.triggered,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class PlanEvidence:
    kind: PlanEvidenceKind
    obligation_id: str | None
    message: str

    def __post_init__(self) -> None:
        if self.kind not in {"selected", "omitted", "fallback"}:
            raise ValueError("kind is not an admitted plan-evidence kind")
        if self.obligation_id is not None:
            _require_non_empty_string(self.obligation_id, field_name="obligation_id")
        _require_non_empty_string(self.message, field_name="message")

    def to_identity_mapping(self) -> dict[str, str | None]:
        return {
            "kind": self.kind,
            "obligationId": self.obligation_id,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class PlanningRejected:
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_sorted_unique_strings(self.reasons, field_name="reasons")


@dataclass(frozen=True, slots=True)
class DeterministicPlan:
    plan_id: str
    schema_version: Literal["deterministic-plan/v1"]
    planner_version: Literal["planning-core/v1"]
    config_epoch_id: str
    repo_epoch_hash: str
    input_hash: str
    diff_hash: str
    compiled_policy_hash: str
    policy_hash: str
    dependency_graph_hash: str
    catalog_hash: str
    selected_obligations: tuple[SelectedObligation, ...]
    selected_witnesses: tuple[SelectedWitness, ...]
    omitted_obligations: tuple[OmittedObligation, ...]
    fallback: PlanFallback
    evidence: tuple[PlanEvidence, ...]

    def __post_init__(self) -> None:
        _require_non_empty_string(self.plan_id, field_name="plan_id")
        if self.schema_version != "deterministic-plan/v1":
            raise ValueError("unsupported deterministic plan schema version")
        if self.planner_version != "planning-core/v1":
            raise ValueError("unsupported planner version")
        for field_name, value in (
            ("config_epoch_id", self.config_epoch_id),
            ("repo_epoch_hash", self.repo_epoch_hash),
            ("input_hash", self.input_hash),
            ("diff_hash", self.diff_hash),
            ("compiled_policy_hash", self.compiled_policy_hash),
            ("policy_hash", self.policy_hash),
            ("dependency_graph_hash", self.dependency_graph_hash),
            ("catalog_hash", self.catalog_hash),
        ):
            _require_sha256(value, field_name=field_name)
        _require_ordered_ids(
            tuple(item.obligation_id for item in self.selected_obligations),
            field_name="selected_obligations",
        )
        _require_ordered_ids(
            tuple(item.witness_id for item in self.selected_witnesses),
            field_name="selected_witnesses",
        )
        _require_ordered_ids(
            tuple(item.obligation_id for item in self.omitted_obligations),
            field_name="omitted_obligations",
        )
        selected_ids = {item.obligation_id for item in self.selected_obligations}
        omitted_ids = {item.obligation_id for item in self.omitted_obligations}
        if selected_ids.intersection(omitted_ids):
            raise ValueError("selected and omitted obligations must be disjoint")
        validate_witness_closure(self.selected_obligations, self.selected_witnesses)
        expected_id = "dynamic_ci_plan_" + hash_object(self.identity_mapping())[:32]
        if self.plan_id != expected_id:
            raise ValueError("plan_id does not bind deterministic plan identity")

    def identity_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "plannerVersion": self.planner_version,
            "configEpochId": self.config_epoch_id,
            "repoEpochHash": self.repo_epoch_hash,
            "inputHash": self.input_hash,
            "diffHash": self.diff_hash,
            "compiledPolicyHash": self.compiled_policy_hash,
            "policyHash": self.policy_hash,
            "dependencyGraphHash": self.dependency_graph_hash,
            "catalogHash": self.catalog_hash,
            "selectedObligations": [
                item.to_identity_mapping() for item in self.selected_obligations
            ],
            "selectedWitnesses": [item.to_identity_mapping() for item in self.selected_witnesses],
            "omittedObligations": [item.to_identity_mapping() for item in self.omitted_obligations],
            "fallback": self.fallback.to_identity_mapping(),
            "evidence": [item.to_identity_mapping() for item in self.evidence],
        }


type PlanningResult = DeterministicPlan | PlanningRejected


def validate_witness_closure(
    selected_obligations: tuple[SelectedObligation, ...],
    selected_witnesses: tuple[SelectedWitness, ...],
) -> None:
    selected_ids = {item.obligation_id for item in selected_obligations}
    selected_depths = {item.obligation_id: item.depth for item in selected_obligations}
    required_by_witness: dict[str, set[str]] = {}
    for obligation in selected_obligations:
        for witness_id in obligation.required_witness_ids:
            required_by_witness.setdefault(witness_id, set()).add(obligation.obligation_id)
    actual_witness_ids = {item.witness_id for item in selected_witnesses}
    if actual_witness_ids != set(required_by_witness):
        raise ValueError("selected witnesses must exactly close obligation requirements")
    for witness in selected_witnesses:
        required_by = set(witness.required_by_obligation_ids)
        if not required_by.issubset(selected_ids):
            raise ValueError("witness coverage references an unselected obligation")
        if required_by != required_by_witness[witness.witness_id]:
            raise ValueError("witness reverse edges must exactly match obligation requirements")
        expected_depth = max(
            (selected_depths[item] for item in witness.required_by_obligation_ids),
            key=depth_rank,
        )
        if witness.depth != expected_depth:
            raise ValueError("witness depth must equal its strongest requiring obligation")


def _require_non_empty_string(value: object, *, field_name: str) -> None:
    if not isinstance(value, str) or not value or not is_unicode_scalar_string(value):
        raise ValueError(f"{field_name} must be a non-empty Unicode scalar string")


def _require_sha256(value: object, *, field_name: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{field_name} must be lowercase SHA-256 hexadecimal")


def _require_sorted_unique_strings(
    values: tuple[str, ...],
    *,
    field_name: str,
    require_non_empty: bool = False,
) -> None:
    if require_non_empty and not values:
        raise ValueError(f"{field_name} must not be empty")
    if any(not value or not is_unicode_scalar_string(value) for value in values):
        raise ValueError(f"{field_name} must contain non-empty Unicode scalar strings")
    if tuple(sorted(set(values), key=utf16_sort_key)) != values:
        raise ValueError(f"{field_name} must be sorted and unique by ECMAScript UTF-16 order")


def _require_ordered_ids(values: tuple[str, ...], *, field_name: str) -> None:
    if tuple(sorted(set(values), key=utf16_sort_key)) != values:
        raise ValueError(f"{field_name} must be ordered and unique by identity")

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ci_coordinator.agent_risk_advice import AdviceAuditMetadata
from ci_coordinator.kernel import hash_object, utf16_sort_key
from ci_coordinator.planning_core import (
    DeterministicPlan,
    OmittedObligation,
    PlanEvidence,
    PlanFallback,
    SelectedObligation,
    SelectedWitness,
    depth_rank,
    validate_witness_closure,
)
from ci_coordinator.validation_contract import ValidationCatalog


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    kind: Literal["deterministic", "agent_advice_accepted", "agent_advice_rejected"]
    message: str


@dataclass(frozen=True, slots=True)
class VerifiedPlan:
    execution_plan_id: str
    source_plan: DeterministicPlan
    catalog: ValidationCatalog
    selected_obligations: tuple[SelectedObligation, ...]
    selected_witnesses: tuple[SelectedWitness, ...]
    omitted_obligations: tuple[OmittedObligation, ...]
    fallback: PlanFallback
    deterministic_evidence: tuple[PlanEvidence, ...]
    verification_evidence: tuple[VerificationEvidence, ...]
    agent_advice: AdviceAuditMetadata | None

    def __post_init__(self) -> None:
        if not isinstance(self.catalog, ValidationCatalog):
            raise TypeError("catalog must be a ValidationCatalog")
        if self.catalog.catalog_hash != self.source_plan.catalog_hash:
            raise ValueError("verified catalog must match the source plan catalog hash")
        _require_canonical_ids(
            tuple(item.obligation_id for item in self.selected_obligations),
            field_name="verified selected obligations",
        )
        _require_canonical_ids(
            tuple(item.witness_id for item in self.selected_witnesses),
            field_name="verified selected witnesses",
        )
        _require_canonical_ids(
            tuple(item.obligation_id for item in self.omitted_obligations),
            field_name="verified omitted obligations",
        )
        validate_witness_closure(self.selected_obligations, self.selected_witnesses)

        source_selected = {
            item.obligation_id: item for item in self.source_plan.selected_obligations
        }
        source_omitted = {item.obligation_id: item for item in self.source_plan.omitted_obligations}
        candidate_selected = {item.obligation_id: item for item in self.selected_obligations}
        candidate_omitted = {item.obligation_id: item for item in self.omitted_obligations}
        if set(candidate_selected).intersection(candidate_omitted):
            raise ValueError("verified plan obligations must be disjoint")
        if set(candidate_selected).union(candidate_omitted) != set(source_selected).union(
            source_omitted
        ):
            raise ValueError("verified plan must classify every deterministic obligation")

        for obligation_id, source_obligation in source_selected.items():
            candidate_obligation = candidate_selected.get(obligation_id)
            if candidate_obligation is None:
                raise ValueError("verified plan cannot omit a selected obligation")
            if (
                candidate_obligation.required_witness_ids != source_obligation.required_witness_ids
                or depth_rank(candidate_obligation.depth) < depth_rank(source_obligation.depth)
            ):
                raise ValueError("verified plan cannot weaken deterministic obligation coverage")
        for obligation_id, candidate_obligation in candidate_selected.items():
            source_selected_obligation = source_selected.get(obligation_id)
            source_omitted_obligation = source_omitted.get(obligation_id)
            source_required_witnesses = (
                source_selected_obligation.required_witness_ids
                if source_selected_obligation is not None
                else source_omitted_obligation.required_witness_ids
                if source_omitted_obligation is not None
                else None
            )
            if (
                source_required_witnesses is None
                or candidate_obligation.required_witness_ids != source_required_witnesses
            ):
                raise ValueError("verified obligation requirements must preserve source identity")
        for obligation_id, candidate_omission in candidate_omitted.items():
            source_omission = source_omitted.get(obligation_id)
            if source_omission is None or candidate_omission != source_omission:
                raise ValueError("verified omissions must preserve deterministic proofs")

        candidate_witnesses = {item.witness_id: item for item in self.selected_witnesses}
        for source_witness in self.source_plan.selected_witnesses:
            candidate_witness = candidate_witnesses.get(source_witness.witness_id)
            if candidate_witness is None or depth_rank(candidate_witness.depth) < depth_rank(
                source_witness.depth
            ):
                raise ValueError("verified plan cannot weaken deterministic witness coverage")
            if not set(source_witness.required_by_obligation_ids).issubset(
                candidate_witness.required_by_obligation_ids
            ):
                raise ValueError("verified plan cannot remove witness requirement edges")

        expected = "verified_plan_" + hash_object(self.identity_mapping())[:32]
        if self.execution_plan_id != expected:
            raise ValueError("verified plan id does not bind its execution identity")

    def identity_mapping(self) -> dict[str, object]:
        return {
            "sourcePlan": self.source_plan.identity_mapping(),
            "validationCatalog": self.catalog.to_identity_mapping(),
            "selectedObligations": [
                item.to_identity_mapping() for item in self.selected_obligations
            ],
            "selectedWitnesses": [item.to_identity_mapping() for item in self.selected_witnesses],
            "omittedObligations": [item.to_identity_mapping() for item in self.omitted_obligations],
            "fallback": self.fallback.to_identity_mapping(),
            "deterministicEvidence": [
                item.to_identity_mapping() for item in self.deterministic_evidence
            ],
            "verificationEvidence": [
                {"kind": item.kind, "message": item.message} for item in self.verification_evidence
            ],
            "agentAdvice": None
            if self.agent_advice is None
            else _advice_identity(self.agent_advice),
        }

    @property
    def deterministic_plan_id(self) -> str:
        return self.source_plan.plan_id


def make_verified_plan(
    *,
    source_plan: DeterministicPlan,
    catalog: ValidationCatalog,
    selected_obligations: tuple[SelectedObligation, ...],
    selected_witnesses: tuple[SelectedWitness, ...],
    omitted_obligations: tuple[OmittedObligation, ...],
    fallback: PlanFallback,
    deterministic_evidence: tuple[PlanEvidence, ...],
    verification_evidence: tuple[VerificationEvidence, ...],
    agent_advice: AdviceAuditMetadata | None,
) -> VerifiedPlan:
    selected = tuple(
        sorted(selected_obligations, key=lambda item: utf16_sort_key(item.obligation_id))
    )
    witnesses = tuple(sorted(selected_witnesses, key=lambda item: utf16_sort_key(item.witness_id)))
    omitted = tuple(
        sorted(omitted_obligations, key=lambda item: utf16_sort_key(item.obligation_id))
    )
    identity = {
        "sourcePlan": source_plan.identity_mapping(),
        "validationCatalog": catalog.to_identity_mapping(),
        "selectedObligations": [item.to_identity_mapping() for item in selected],
        "selectedWitnesses": [item.to_identity_mapping() for item in witnesses],
        "omittedObligations": [item.to_identity_mapping() for item in omitted],
        "fallback": fallback.to_identity_mapping(),
        "deterministicEvidence": [item.to_identity_mapping() for item in deterministic_evidence],
        "verificationEvidence": [
            {"kind": item.kind, "message": item.message} for item in verification_evidence
        ],
        "agentAdvice": None if agent_advice is None else _advice_identity(agent_advice),
    }
    return VerifiedPlan(
        execution_plan_id="verified_plan_" + hash_object(identity)[:32],
        source_plan=source_plan,
        catalog=catalog,
        selected_obligations=selected,
        selected_witnesses=witnesses,
        omitted_obligations=omitted,
        fallback=fallback,
        deterministic_evidence=deterministic_evidence,
        verification_evidence=verification_evidence,
        agent_advice=agent_advice,
    )


def _require_canonical_ids(values: tuple[str, ...], *, field_name: str) -> None:
    if tuple(sorted(set(values), key=utf16_sort_key)) != values:
        raise ValueError(field_name + " must be canonical")


def _advice_identity(advice: AdviceAuditMetadata) -> dict[str, object]:
    return {
        "adviceHash": advice.advice_hash,
        "adviceId": advice.advice_id,
        "inputHash": advice.input_hash,
        "modelId": advice.model_id,
        "promptHash": advice.prompt_hash,
        "evaluationId": advice.evaluation_id,
        "evaluationPolicyHash": advice.evaluation_policy_hash,
        "evaluationPassed": advice.evaluation_passed,
        "parseValid": advice.parse_valid,
        "accepted": advice.accepted,
        "reasons": list(advice.reasons),
    }

from __future__ import annotations

import math
from typing import Literal, cast

from ci_coordinator.agent_risk_advice.model import (
    AGENT_ADVICE_SCHEMA_VERSION,
    AdmittedAdvice,
    AdviceAdmission,
    AdviceAuditMetadata,
    AdviceDepthIncrease,
    AdviceExecutionEnvelope,
    AdviceInputPackage,
    AdviceRiskFinding,
    AgentAdvice,
    RejectedAdvice,
)
from ci_coordinator.kernel import StrictJsonError, load_strict_json, utf16_sort_key
from ci_coordinator.planning_core import DeterministicPlan, PlanningPolicy
from ci_coordinator.repo_context import PlanningInput
from ci_coordinator.repo_context.freshness import is_unicode_scalar_string
from ci_coordinator.validation_contract import depth_rank, validation_depth

_MAX_ADVICE_ITEMS = 1_024
_ADVICE_KEYS = frozenset(
    {
        "schemaVersion",
        "adviceId",
        "confidence",
        "addObligations",
        "increaseDepth",
        "riskFindings",
        "fallbackRecommendation",
        "rationale",
    }
)
_REQUIRED_ADVICE_KEYS = _ADVICE_KEYS - {"fallbackRecommendation"}


def build_advice_input(input: PlanningInput, plan: DeterministicPlan) -> AdviceInputPackage:
    obligation_ids = tuple(
        sorted(
            {
                *(item.obligation_id for item in plan.selected_obligations),
                *(item.obligation_id for item in plan.omitted_obligations),
            },
            key=utf16_sort_key,
        )
    )
    return AdviceInputPackage(
        input_hash=input.input_hash,
        deterministic_plan_id=plan.plan_id,
        policy_hash=plan.policy_hash,
        obligation_ids=obligation_ids,
    )


def admit_advice(
    envelope: AdviceExecutionEnvelope,
    *,
    input: PlanningInput,
    policy: PlanningPolicy,
) -> AdviceAdmission:
    if type(envelope) is not AdviceExecutionEnvelope:
        raise TypeError("agent advice admission requires an exact execution envelope")
    parsed = _parse(envelope.raw_output)
    if isinstance(parsed, tuple):
        return RejectedAdvice(_audit(envelope, None, False, False, parsed))

    reasons = _policy_reasons(parsed, envelope, input, policy)
    if reasons:
        return RejectedAdvice(_audit(envelope, parsed.advice_id, True, False, reasons))
    return AdmittedAdvice(parsed, _audit(envelope, parsed.advice_id, True, True, ()))


def _parse(raw_output: bytes) -> AgentAdvice | tuple[str, ...]:
    try:
        decoded = load_strict_json(raw_output, max_bytes=len(raw_output))
    except StrictJsonError:
        return ("agent_advice_invalid_json",)
    if type(decoded) is not dict:
        return ("agent_advice_not_object",)
    decoded = cast(dict[str, object], decoded)
    unknown = tuple(sorted(set(decoded) - _ADVICE_KEYS, key=utf16_sort_key))
    if unknown:
        return ("agent_advice_unknown_fields:" + ",".join(unknown),)
    missing = tuple(sorted(_REQUIRED_ADVICE_KEYS - set(decoded), key=utf16_sort_key))
    if missing:
        return ("agent_advice_missing_fields:" + ",".join(missing),)
    try:
        return AgentAdvice(
            schema_version=_schema_version(decoded.get("schemaVersion")),
            advice_id=_string(decoded.get("adviceId"), "advice_id", maximum_bytes=256),
            confidence=_confidence(decoded.get("confidence")),
            add_obligations=_strings(decoded.get("addObligations"), "add_obligations"),
            increase_depth=_depth_increases(decoded.get("increaseDepth")),
            risk_findings=_risk_findings(decoded.get("riskFindings")),
            fallback_recommendation=_fallback(decoded.get("fallbackRecommendation")),
            rationale=_string(decoded.get("rationale"), "rationale", maximum_bytes=4_096),
        )
    except ValueError as error:
        return (str(error),)


def _policy_reasons(
    advice: AgentAdvice,
    envelope: AdviceExecutionEnvelope,
    input: PlanningInput,
    policy: PlanningPolicy,
) -> tuple[str, ...]:
    reasons: set[str] = set()
    admission = policy.agent_advice
    if not admission.enabled:
        reasons.add("agent_advice_disabled")
    if envelope.input_hash != input.input_hash:
        reasons.add("agent_advice_input_hash_mismatch")
    if envelope.model_id not in admission.model_id_allowlist:
        reasons.add("agent_advice_model_not_allowed")
    if envelope.prompt_hash not in admission.prompt_hash_allowlist:
        reasons.add("agent_advice_prompt_not_allowed")
    if advice.confidence < admission.min_confidence:
        reasons.add("agent_advice_confidence_too_low")
    if admission.prompt_injection_eval_required and not envelope.evaluation.passed:
        reasons.add("agent_advice_evaluation_rejected")

    obligations = {item.obligation_id: item for item in policy.catalog.obligations}
    witnesses = {item.witness_id: item for item in policy.catalog.witnesses}
    for obligation_id in advice.add_obligations:
        if obligation_id not in obligations:
            reasons.add("agent_advice_unknown_obligation")
    for increase in advice.increase_depth:
        obligation = obligations.get(increase.obligation_id)
        if obligation is None:
            reasons.add("agent_advice_unknown_obligation")
            continue
        if depth_rank(increase.min_depth) > depth_rank(obligation.full_depth) or any(
            increase.min_depth not in witnesses[witness_id].supported_depths
            for witness_id in obligation.required_witness_ids
        ):
            reasons.add("agent_advice_depth_unsupported")
    for finding in advice.risk_findings:
        if finding.obligation_id not in obligations:
            reasons.add("agent_advice_unknown_obligation")
    return tuple(sorted(reasons, key=utf16_sort_key))


def _audit(
    envelope: AdviceExecutionEnvelope,
    advice_id: str | None,
    parse_valid: bool,
    accepted: bool,
    reasons: tuple[str, ...],
) -> AdviceAuditMetadata:
    evaluation = envelope.evaluation
    return AdviceAuditMetadata(
        advice_hash=evaluation.output_hash,
        advice_id=advice_id,
        input_hash=envelope.input_hash,
        model_id=envelope.model_id,
        prompt_hash=envelope.prompt_hash,
        evaluation_id=evaluation.evaluator_id,
        evaluation_policy_hash=evaluation.policy_hash,
        evaluation_passed=evaluation.passed,
        parse_valid=parse_valid,
        accepted=accepted,
        reasons=tuple(sorted(set(reasons), key=utf16_sort_key)),
    )


def _schema_version(value: object) -> Literal["agent-risk-advice/v2"]:
    if value != AGENT_ADVICE_SCHEMA_VERSION:
        raise ValueError("agent_advice_schema_unsupported")
    return AGENT_ADVICE_SCHEMA_VERSION


def _string(value: object, field_name: str, *, maximum_bytes: int = 256) -> str:
    if (
        type(value) is not str
        or not value
        or not is_unicode_scalar_string(value)
        or "\0" in value
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError("agent_advice_" + field_name + "_invalid")
    return value


def _confidence(value: object) -> float:
    if type(value) is int:
        numeric = float(value)
    elif type(value) is float:
        numeric = value
    else:
        raise ValueError("agent_advice_confidence_invalid")
    if not math.isfinite(numeric) or not 0 <= numeric <= 1:
        raise ValueError("agent_advice_confidence_invalid")
    return numeric


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if type(value) is not list or len(value) > _MAX_ADVICE_ITEMS:
        raise ValueError("agent_advice_" + field_name + "_invalid")
    return tuple(_string(item, field_name) for item in value)


def _depth_increases(value: object) -> tuple[AdviceDepthIncrease, ...]:
    if type(value) is not list or len(value) > _MAX_ADVICE_ITEMS:
        raise ValueError("agent_advice_increase_depth_invalid")
    result: list[AdviceDepthIncrease] = []
    for item in value:
        if type(item) is not dict or set(item) != {"obligationId", "minDepth"}:
            raise ValueError("agent_advice_increase_depth_invalid")
        try:
            depth = validation_depth(item.get("minDepth"), field_name="agent advice minimum depth")
        except ValueError as error:
            raise ValueError("agent_advice_increase_depth_invalid") from error
        result.append(
            AdviceDepthIncrease(
                _string(item.get("obligationId"), "obligation_id"),
                depth,
            )
        )
    return tuple(result)


def _risk_findings(value: object) -> tuple[AdviceRiskFinding, ...]:
    if type(value) is not list or len(value) > _MAX_ADVICE_ITEMS:
        raise ValueError("agent_advice_risk_findings_invalid")
    result: list[AdviceRiskFinding] = []
    for item in value:
        if type(item) is not dict or set(item) != {"obligationId", "reason"}:
            raise ValueError("agent_advice_risk_findings_invalid")
        result.append(
            AdviceRiskFinding(
                _string(item.get("obligationId"), "obligation_id"),
                _string(item.get("reason"), "risk_reason", maximum_bytes=4_096),
            )
        )
    return tuple(result)


def _fallback(value: object) -> Literal["full-ci"] | None:
    if value is None:
        return None
    if value != "full-ci":
        raise ValueError("agent_advice_fallback_recommendation_invalid")
    return "full-ci"

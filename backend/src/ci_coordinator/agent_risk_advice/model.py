from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Final, Literal

from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.validation_contract import ValidationDepth

AGENT_ADVICE_SCHEMA_VERSION: Final = "agent-risk-advice/v2"
MAX_AGENT_ADVICE_OUTPUT_BYTES: Final = 64 * 1024


@dataclass(frozen=True, slots=True)
class AdviceEvaluationEvidence:
    output_hash: str
    evaluator_id: str
    policy_hash: str
    passed: bool

    def __post_init__(self) -> None:
        _require_sha256(self.output_hash, field_name="evaluation output hash")
        _require_bounded_text(self.evaluator_id, field_name="evaluator id", maximum_bytes=256)
        _require_sha256(self.policy_hash, field_name="evaluation policy hash")
        if type(self.passed) is not bool:
            raise TypeError("advice evaluation result must be an exact boolean")


@dataclass(frozen=True, slots=True)
class AdviceExecutionEnvelope:
    raw_output: bytes = field(repr=False)
    input_hash: str
    model_id: str
    prompt_hash: str
    evaluation: AdviceEvaluationEvidence

    def __post_init__(self) -> None:
        if type(self.raw_output) is not bytes or not self.raw_output:
            raise ValueError("agent advice output must be non-empty bytes")
        if len(self.raw_output) > MAX_AGENT_ADVICE_OUTPUT_BYTES:
            raise ValueError("agent advice output exceeds its byte bound")
        _require_sha256(self.input_hash, field_name="advice input hash")
        _require_bounded_text(self.model_id, field_name="advice model id", maximum_bytes=256)
        _require_sha256(self.prompt_hash, field_name="advice prompt hash")
        if type(self.evaluation) is not AdviceEvaluationEvidence:
            raise TypeError("agent advice evaluation evidence must be exact")
        if hashlib.sha256(self.raw_output).hexdigest() != self.evaluation.output_hash:
            raise ValueError("agent advice evaluation is bound to another output")


@dataclass(frozen=True, slots=True)
class AdviceDepthIncrease:
    obligation_id: str
    min_depth: ValidationDepth


@dataclass(frozen=True, slots=True)
class AdviceRiskFinding:
    obligation_id: str
    reason: str


@dataclass(frozen=True, slots=True)
class AgentAdvice:
    schema_version: Literal["agent-risk-advice/v2"]
    advice_id: str
    confidence: float
    add_obligations: tuple[str, ...]
    increase_depth: tuple[AdviceDepthIncrease, ...]
    risk_findings: tuple[AdviceRiskFinding, ...]
    rationale: str
    fallback_recommendation: Literal["full-ci"] | None = None

    def __post_init__(self) -> None:
        _require_canonical(self.add_obligations, field_name="add_obligations")
        _require_canonical(
            tuple(item.obligation_id for item in self.increase_depth),
            field_name="increase_depth",
        )
        finding_keys = tuple(item.obligation_id + ":" + item.reason for item in self.risk_findings)
        _require_canonical(finding_keys, field_name="risk_findings")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "adviceId": self.advice_id,
            "confidence": self.confidence,
            "addObligations": list(self.add_obligations),
            "increaseDepth": [
                {"obligationId": item.obligation_id, "minDepth": item.min_depth}
                for item in self.increase_depth
            ],
            "riskFindings": [
                {"obligationId": item.obligation_id, "reason": item.reason}
                for item in self.risk_findings
            ],
            "fallbackRecommendation": self.fallback_recommendation,
            "rationale": self.rationale,
        }


@dataclass(frozen=True, slots=True)
class AdviceAuditMetadata:
    advice_hash: str
    advice_id: str | None
    input_hash: str
    model_id: str
    prompt_hash: str
    evaluation_id: str
    evaluation_policy_hash: str
    evaluation_passed: bool
    parse_valid: bool
    accepted: bool
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_sha256(self.advice_hash, field_name="advice hash")
        _require_sha256(self.input_hash, field_name="advice input hash")
        _require_bounded_text(self.model_id, field_name="advice model id", maximum_bytes=256)
        _require_sha256(self.prompt_hash, field_name="advice prompt hash")
        _require_bounded_text(self.evaluation_id, field_name="evaluator id", maximum_bytes=256)
        _require_sha256(self.evaluation_policy_hash, field_name="evaluation policy hash")
        if type(self.evaluation_passed) is not bool:
            raise TypeError("advice audit evaluation result must be an exact boolean")
        if tuple(sorted(set(self.reasons), key=utf16_sort_key)) != self.reasons:
            raise ValueError("advice audit reasons must be canonical")


@dataclass(frozen=True, slots=True)
class AdmittedAdvice:
    advice: AgentAdvice
    audit: AdviceAuditMetadata


@dataclass(frozen=True, slots=True)
class RejectedAdvice:
    audit: AdviceAuditMetadata


type AdviceAdmission = AdmittedAdvice | RejectedAdvice


@dataclass(frozen=True, slots=True)
class AdviceInputPackage:
    input_hash: str
    deterministic_plan_id: str
    policy_hash: str
    obligation_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_canonical(self.obligation_ids, field_name="obligation_ids")


def _require_canonical(values: tuple[str, ...], *, field_name: str) -> None:
    if tuple(sorted(set(values), key=utf16_sort_key)) != values:
        raise ValueError("agent_advice_" + field_name + "_not_canonical")


def _require_sha256(value: object, *, field_name: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(field_name + " must be a lowercase SHA-256 digest")


def _require_bounded_text(value: object, *, field_name: str, maximum_bytes: int) -> None:
    if (
        type(value) is not str
        or not value
        or "\0" in value
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError(field_name + " must be bounded Unicode scalar text")

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
from planning_core._support import make_input_value, make_policy_value

from ci_coordinator.agent_risk_advice import (
    MAX_AGENT_ADVICE_OUTPUT_BYTES,
    AdmittedAdvice,
    AdviceEvaluationEvidence,
    AdviceExecutionEnvelope,
    RejectedAdvice,
    admit_advice,
    build_advice_input,
)
from ci_coordinator.kernel import canonical_json
from ci_coordinator.planning_core import (
    AgentAdvicePolicy,
    DeterministicPlan,
    PlanningPolicy,
    PlanningRejected,
    plan,
)
from ci_coordinator.repo_context import DiffFileChangeInput

_PROMPT_HASH = "e" * 64
_EVALUATION_POLICY_HASH = "f" * 64


def test_advice_is_admitted_only_for_allowlisted_obligation_expansion() -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = _advice_policy()

    admitted = admit_advice(
        _envelope(input.input_hash, _advice(add_obligations=["backend-tests"])),
        input=input,
        policy=policy,
    )

    assert isinstance(admitted, AdmittedAdvice)
    assert admitted.audit.accepted is True
    assert admitted.advice.add_obligations == ("backend-tests",)


@pytest.mark.parametrize(
    "forbidden_field",
    ["credentialProfileId", "inputHash", "modelId", "promptHash", "requiredFixtures"],
)
def test_advice_cannot_name_execution_authority(forbidden_field: str) -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    raw = _advice()
    raw[forbidden_field] = ["privileged"]

    rejected = admit_advice(_envelope(input.input_hash, raw), input=input, policy=_advice_policy())

    assert isinstance(rejected, RejectedAdvice)
    assert rejected.audit.reasons == ("agent_advice_unknown_fields:" + forbidden_field,)


def test_unknown_or_unsupported_obligation_advice_is_rejected() -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    unknown = _advice(add_obligations=["unknown"])
    unsupported = _advice(
        increase_depth=[{"obligationId": "docs-lint", "minDepth": "exhaustive"}],
    )

    unknown_result = admit_advice(
        _envelope(input.input_hash, unknown), input=input, policy=_advice_policy()
    )
    unsupported_result = admit_advice(
        _envelope(input.input_hash, unsupported), input=input, policy=_advice_policy()
    )

    assert isinstance(unknown_result, RejectedAdvice)
    assert unknown_result.audit.reasons == ("agent_advice_unknown_obligation",)
    assert isinstance(unsupported_result, RejectedAdvice)
    assert unsupported_result.audit.reasons == ("agent_advice_depth_unsupported",)


def test_advice_input_exposes_obligations_without_execution_profiles() -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy_value()
    candidate = _candidate(plan(input, policy))

    package = build_advice_input(input, candidate)

    assert package.obligation_ids == (
        "backend-tests",
        "docs-lint",
        "required-baseline",
    )
    assert not hasattr(package, "profile_ids")
    assert not hasattr(package, "credential_ids")


def test_failed_independent_evaluation_rejects_otherwise_valid_advice() -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))

    result = admit_advice(
        _envelope(input.input_hash, _advice(), evaluation_passed=False),
        input=input,
        policy=_advice_policy(),
    )

    assert isinstance(result, RejectedAdvice)
    assert result.audit.reasons == ("agent_advice_evaluation_rejected",)


@pytest.mark.parametrize(
    ("input_hash", "model_id", "prompt_hash", "confidence", "reason"),
    [
        ("a" * 64, "model", _PROMPT_HASH, 0.9, "agent_advice_input_hash_mismatch"),
        (None, "other-model", _PROMPT_HASH, 0.9, "agent_advice_model_not_allowed"),
        (None, "model", "d" * 64, 0.9, "agent_advice_prompt_not_allowed"),
        (None, "model", _PROMPT_HASH, 0.7, "agent_advice_confidence_too_low"),
    ],
    ids=("input", "model", "prompt", "confidence"),
)
def test_execution_identity_and_confidence_must_match_policy(
    input_hash: str | None,
    model_id: str,
    prompt_hash: str,
    confidence: float,
    reason: str,
) -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    output = _advice()
    output["confidence"] = confidence
    envelope = _envelope(
        input_hash or input.input_hash,
        output,
        model_id=model_id,
        prompt_hash=prompt_hash,
    )

    result = admit_advice(envelope, input=input, policy=_advice_policy())

    assert isinstance(result, RejectedAdvice)
    assert result.audit.reasons == (reason,)


def test_advice_collections_and_evaluation_hash_are_bounded() -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    oversized = _advice(add_obligations=["backend-tests"] * 1_025)

    result = admit_advice(
        _envelope(input.input_hash, oversized),
        input=input,
        policy=_advice_policy(),
    )

    assert isinstance(result, RejectedAdvice)
    assert result.audit.reasons == ("agent_advice_add_obligations_invalid",)
    with pytest.raises(ValueError, match="another output"):
        replace(_envelope(input.input_hash, _advice()), raw_output=b"{}")


def test_disabled_advice_is_not_misreported_as_evaluation_failure() -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = replace(
        make_policy_value(),
        agent_advice=AgentAdvicePolicy(False, ("model",), (_PROMPT_HASH,), 0.8, False),
    )

    result = admit_advice(
        _envelope(input.input_hash, _advice(), evaluation_passed=False),
        input=input,
        policy=policy,
    )

    assert isinstance(result, RejectedAdvice)
    assert result.audit.reasons == ("agent_advice_disabled",)


def test_advice_output_is_strict_json_and_bounded_before_parsing() -> None:
    input = make_input_value(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    duplicate_keys = b'{"schemaVersion":"agent-risk-advice/v2","schemaVersion":"other"}'

    result = admit_advice(
        _envelope_bytes(input.input_hash, duplicate_keys),
        input=input,
        policy=_advice_policy(),
    )

    assert isinstance(result, RejectedAdvice)
    assert result.audit.reasons == ("agent_advice_invalid_json",)
    with pytest.raises(ValueError, match="byte bound"):
        _envelope_bytes(input.input_hash, b"x" * (MAX_AGENT_ADVICE_OUTPUT_BYTES + 1))


def _advice_policy() -> PlanningPolicy:
    return replace(
        make_policy_value(),
        agent_advice=AgentAdvicePolicy(True, ("model",), (_PROMPT_HASH,), 0.8, True),
    )


def _advice(
    *,
    add_obligations: list[str] | None = None,
    increase_depth: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    return {
        "schemaVersion": "agent-risk-advice/v2",
        "adviceId": "advice-1",
        "confidence": 0.9,
        "addObligations": add_obligations or [],
        "increaseDepth": increase_depth or [],
        "riskFindings": [],
        "rationale": "conservative expansion",
    }


def _envelope(
    input_hash: str,
    output: object,
    *,
    evaluation_passed: bool = True,
    model_id: str = "model",
    prompt_hash: str = _PROMPT_HASH,
) -> AdviceExecutionEnvelope:
    return _envelope_bytes(
        input_hash,
        canonical_json(output),
        evaluation_passed=evaluation_passed,
        model_id=model_id,
        prompt_hash=prompt_hash,
    )


def _envelope_bytes(
    input_hash: str,
    output: bytes,
    *,
    evaluation_passed: bool = True,
    model_id: str = "model",
    prompt_hash: str = _PROMPT_HASH,
) -> AdviceExecutionEnvelope:
    output_hash = hashlib.sha256(output).hexdigest()
    return AdviceExecutionEnvelope(
        raw_output=output,
        input_hash=input_hash,
        model_id=model_id,
        prompt_hash=prompt_hash,
        evaluation=AdviceEvaluationEvidence(
            output_hash=output_hash,
            evaluator_id="prompt-injection-evaluator/v1",
            policy_hash=_EVALUATION_POLICY_HASH,
            passed=evaluation_passed,
        ),
    )


def _candidate(value: DeterministicPlan | PlanningRejected) -> DeterministicPlan:
    if isinstance(value, PlanningRejected):
        pytest.fail("unexpected planning rejection")
    return value

from __future__ import annotations

from ci_coordinator.ci_economics.budget_payload import BudgetPolicyPayload
from ci_coordinator.ci_economics.budget_policy import BudgetPolicySnapshot
from ci_coordinator.persistence.canonical_row import (
    decode_canonical_object,
    encode_canonical_object,
)

MAX_POLICY_BYTES = 2_048


def encode_budget_policy(policy: BudgetPolicySnapshot) -> bytes:
    return encode_canonical_object(
        policy.canonical_mapping(), maximum_bytes=MAX_POLICY_BYTES, context="budget policy"
    )


def decode_budget_policy(payload: object) -> BudgetPolicySnapshot:
    return budget_policy_from_mapping(
        decode_canonical_object(payload, maximum_bytes=MAX_POLICY_BYTES, context="budget policy")
    )


def budget_policy_from_mapping(value: object) -> BudgetPolicySnapshot:
    return BudgetPolicyPayload.model_validate(value).to_policy()

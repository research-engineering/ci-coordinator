"""Wire admission and projection for effective-governance observations."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from ci_coordinator.api.http.governance_state_contracts import (
    EffectiveGovernanceRuleResponse,
    GovernanceRepositoryResponse,
    governance_state_projection,
)
from ci_coordinator.api.http.model_contracts import ResponseModel
from ci_coordinator.governance_observation import (
    MAX_GOVERNANCE_RULES,
    GovernanceObservation,
    GovernanceState,
)


class GovernanceObservationResponse(ResponseModel):
    ok: Literal[True]
    repository: GovernanceRepositoryResponse
    api_version: Literal["2026-03-10"]
    observed_at: datetime
    consistency: Literal["best_effort"]
    baseline_state: Literal["unbaselined"]
    state_digest: str = Field(
        min_length=64,
        max_length=64,
        pattern=r"^[0-9a-f]{64}$",
    )
    rules: tuple[EffectiveGovernanceRuleResponse, ...] = Field(max_length=MAX_GOVERNANCE_RULES)


type GovernanceObservationErrorCode = Literal[
    "unauthenticated",
    "forbidden",
    "unavailable",
    "rate_limited",
    "not_found",
    "malformed_provider_response",
    "provider_binding_mismatch",
    "observation_limit_exceeded",
]


class GovernanceObservationErrorResponse(ResponseModel):
    ok: Literal[False]
    error: GovernanceObservationErrorCode
    retry_after_seconds: int | None = Field(
        ge=0,
        le=3_600,
    )


def governance_observation_projection(
    observation: GovernanceObservation,
) -> dict[str, object]:
    if type(observation) is not GovernanceObservation:
        raise TypeError("governance observation projection requires an exact observation")
    return {
        "ok": True,
        **governance_state_projection(
            GovernanceState(
                observation.repository,
                observation.api_version,
                observation.rules,
            )
        ),
        "observed_at": observation.observed_at,
        "consistency": observation.consistency,
        "baseline_state": observation.baseline_state,
        "state_digest": observation.state_digest,
    }

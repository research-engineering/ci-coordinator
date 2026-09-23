"""Wire admission and projection for exact governance comparison."""

from __future__ import annotations

from typing import Literal, Self

from fastapi import status
from fastapi.responses import JSONResponse
from pydantic import Field, model_validator

from ci_coordinator.api.http.governance_baseline_contracts import (
    GovernanceBaselineRecordResponse,
    governance_baseline_record_projection,
)
from ci_coordinator.api.http.governance_observation_contracts import (
    GovernanceObservationResponse,
    governance_observation_projection,
)
from ci_coordinator.api.http.governance_state_contracts import GovernanceScopeResponse
from ci_coordinator.api.http.model_contracts import ResponseModel
from ci_coordinator.api.http.security import WWW_AUTHENTICATE_HEADER
from ci_coordinator.app.governance_comparison import GovernanceComparisonEvidence
from ci_coordinator.governance_comparison import (
    GOVERNANCE_CHANGED_COORDINATES,
    GovernanceChangedCoordinate,
)

_NO_STORE = {"Cache-Control": "no-store"}


class ExactGovernanceComparisonResponse(ResponseModel):
    relation: Literal["matches", "differs"]
    baseline_state_digest: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    current_state_digest: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    changed_coordinates: tuple[GovernanceChangedCoordinate, ...] = Field(
        max_length=len(GOVERNANCE_CHANGED_COORDINATES),
    )
    added_rule_count: int = Field(ge=0, le=1_000)
    removed_rule_count: int = Field(ge=0, le=1_000)

    @model_validator(mode="after")
    def _bind_relation(self) -> Self:
        expected_order = tuple(
            coordinate
            for coordinate in GOVERNANCE_CHANGED_COORDINATES
            if coordinate in self.changed_coordinates
        )
        rules_changed = "rules" in self.changed_coordinates
        equal_projection = (
            not self.changed_coordinates and self.baseline_state_digest == self.current_state_digest
        )
        if (
            self.changed_coordinates != expected_order
            or rules_changed != (self.added_rule_count > 0 or self.removed_rule_count > 0)
            or (
                not equal_projection if self.relation == "matches" else not self.changed_coordinates
            )
        ):
            raise ValueError("governance comparison projection is inconsistent")
        return self


class GovernanceComparisonResponse(ResponseModel):
    ok: Literal[True]
    state: Literal["unbaselined", "compared"]
    scope: GovernanceScopeResponse
    observation: GovernanceObservationResponse
    baseline: GovernanceBaselineRecordResponse | None
    comparison: ExactGovernanceComparisonResponse | None

    @model_validator(mode="after")
    def _bind_complete_evidence(self) -> Self:
        observation_scope = self.observation.repository.scope
        if observation_scope != self.scope:
            raise ValueError("governance comparison observation crosses repository scope")
        compared = self.state == "compared"
        if compared != (self.baseline is not None and self.comparison is not None):
            raise ValueError("governance comparison response has an incomplete relation")
        if self.state == "unbaselined" and (
            self.baseline is not None or self.comparison is not None
        ):
            raise ValueError("unbaselined governance evidence contains a comparison")
        if (
            self.baseline is not None
            and self.comparison is not None
            and (
                self.baseline.state.repository.scope != self.scope
                or self.baseline.pointer.state_digest != self.comparison.baseline_state_digest
                or self.observation.state_digest != self.comparison.current_state_digest
            )
        ):
            raise ValueError("governance comparison response crosses exact evidence identity")
        return self


type GovernanceComparisonErrorCode = Literal[
    "unauthenticated",
    "forbidden",
    "stale",
    "unavailable",
    "rate_limited",
    "not_found",
    "malformed_provider_response",
    "provider_binding_mismatch",
    "observation_limit_exceeded",
]


class GovernanceComparisonErrorResponse(ResponseModel):
    ok: Literal[False]
    error: GovernanceComparisonErrorCode
    retry_after_seconds: int | None = Field(
        ge=0,
        le=3_600,
    )

    @model_validator(mode="after")
    def _bind_retry_delay(self) -> Self:
        if self.retry_after_seconds is not None and self.error != "rate_limited":
            raise ValueError("governance comparison retry delay requires a rate limit")
        return self


def governance_comparison_response(
    evidence: GovernanceComparisonEvidence,
) -> JSONResponse:
    response = governance_comparison_projection(evidence)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=response.to_wire_mapping(),
        headers=_NO_STORE,
    )


def governance_comparison_projection(
    evidence: GovernanceComparisonEvidence,
) -> GovernanceComparisonResponse:
    if type(evidence) is not GovernanceComparisonEvidence:
        raise TypeError("governance comparison projection requires exact evidence")
    comparison = evidence.comparison
    return GovernanceComparisonResponse.model_validate(
        {
            "ok": True,
            "state": evidence.state,
            "scope": evidence.observation.repository.scope,
            "observation": governance_observation_projection(evidence.observation),
            "baseline": (
                None
                if evidence.baseline is None
                else governance_baseline_record_projection(evidence.baseline)
            ),
            "comparison": (
                None
                if comparison is None
                else {
                    "relation": comparison.relation,
                    "baseline_state_digest": comparison.baseline_state_digest,
                    "current_state_digest": comparison.current_state_digest,
                    "changed_coordinates": comparison.changed_coordinates,
                    "added_rule_count": comparison.added_rule_count,
                    "removed_rule_count": comparison.removed_rule_count,
                }
            ),
        }
    )


def governance_comparison_error_response(
    status_code: int,
    error: GovernanceComparisonErrorCode,
    *,
    retry_after_seconds: int | None = None,
) -> JSONResponse:
    response = GovernanceComparisonErrorResponse(
        ok=False,
        error=error,
        retry_after_seconds=retry_after_seconds,
    )
    headers = dict(_NO_STORE)
    if status_code == status.HTTP_401_UNAUTHORIZED:
        headers.update(WWW_AUTHENTICATE_HEADER)
    if retry_after_seconds is not None:
        headers["Retry-After"] = str(retry_after_seconds)
    return JSONResponse(
        status_code=status_code,
        content=response.to_wire_mapping(),
        headers=headers,
    )

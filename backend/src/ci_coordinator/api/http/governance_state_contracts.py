"""Shared HTTP admission and projection of one complete governance state."""

from __future__ import annotations

from typing import Self

from pydantic import (
    Field,
    field_validator,
    model_validator,
)

from ci_coordinator.api.http.model_contracts import ProjectedResponseModel, ResponseModel
from ci_coordinator.governance_observation import (
    MAX_GOVERNANCE_DEFAULT_BRANCH_BYTES,
    MAX_GOVERNANCE_RULE_BYTES,
    GovernanceState,
)
from ci_coordinator.kernel import git_branch_name_is_admitted
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

_GIT_BRANCH_JSON_SCHEMA_PATTERN = (
    r"^(?![-/])(?!.*(?:\.\.|@\{|//))(?!.*(?:^|/)\.)"
    r"(?!.*(?:^|/)[^/]*\.lock(?:/|$))(?!.*[./]$)"
    r"[^\x00-\x20\x7f~^:?*\[\\]+$"
)


class GovernanceScopeResponse(ProjectedResponseModel):
    installation_id: int = Field(
        ge=1,
        le=MAX_SAFE_JSON_INTEGER,
    )
    repository_id: int = Field(ge=1, le=MAX_SAFE_JSON_INTEGER)


class GovernanceRepositoryResponse(ProjectedResponseModel):
    scope: GovernanceScopeResponse
    owner_id: int = Field(ge=1, le=MAX_SAFE_JSON_INTEGER)
    owner: str = Field(min_length=1, max_length=512)
    name: str = Field(min_length=1, max_length=512)
    full_name: str = Field(min_length=3, max_length=1_025)
    default_branch: str = Field(
        min_length=1,
        max_length=MAX_GOVERNANCE_DEFAULT_BRANCH_BYTES,
        json_schema_extra={"pattern": _GIT_BRANCH_JSON_SCHEMA_PATTERN},
    )

    @field_validator("default_branch")
    @classmethod
    def _admit_default_branch(cls, value: str) -> str:
        try:
            bounded = len(value.encode("utf-8")) <= MAX_GOVERNANCE_DEFAULT_BRANCH_BYTES
        except UnicodeEncodeError:
            bounded = False
        if not bounded or value != value.strip() or not git_branch_name_is_admitted(value):
            raise ValueError("default branch is outside the governance response contract")
        return value

    @model_validator(mode="after")
    def _bind_full_name(self) -> Self:
        if self.full_name != f"{self.owner}/{self.name}":
            raise ValueError("repository full name contradicts owner and name")
        return self


class EffectiveGovernanceRuleResponse(ResponseModel):
    rule_type: str = Field(min_length=1, max_length=256)
    ruleset_source_type: str = Field(
        min_length=1,
        max_length=256,
    )
    ruleset_source: str = Field(min_length=1, max_length=1_025)
    ruleset_id: int = Field(ge=1, le=MAX_SAFE_JSON_INTEGER)
    canonical_json: str = Field(
        min_length=2,
        max_length=MAX_GOVERNANCE_RULE_BYTES,
    )


def governance_state_projection(state: GovernanceState) -> dict[str, object]:
    if type(state) is not GovernanceState:
        raise TypeError("governance HTTP projection requires an exact state")
    return {
        "repository": state.repository,
        "api_version": state.api_version,
        "rules": tuple(
            {
                "rule_type": rule.rule_type,
                "ruleset_source_type": rule.ruleset_source_type,
                "ruleset_source": rule.ruleset_source,
                "ruleset_id": rule.ruleset_id,
                "canonical_json": rule.canonical_text,
            }
            for rule in state.rules
        ),
    }

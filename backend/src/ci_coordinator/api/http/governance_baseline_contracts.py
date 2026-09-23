"""Wire admission and projection for governance-baseline HTTP responses."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self

from fastapi import status
from fastapi.responses import JSONResponse
from pydantic import Field, field_validator, model_validator

from ci_coordinator.api.http.governance_state_contracts import (
    EffectiveGovernanceRuleResponse,
    GovernanceRepositoryResponse,
    GovernanceScopeResponse,
    governance_state_projection,
)
from ci_coordinator.api.http.model_contracts import RequestModel, ResponseModel
from ci_coordinator.api.http.security import WWW_AUTHENTICATE_HEADER
from ci_coordinator.app.governance_baseline import (
    GovernanceBaselineApprovalOutcome,
    GovernanceBaselineReadOutcome,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_baseline import (
    MAX_BASELINE_OPERATION_ID_BYTES,
    GovernanceBaselineCommand,
    GovernanceBaselinePointer,
    GovernanceBaselineRecord,
    governance_baseline_reason_is_admitted,
)
from ci_coordinator.governance_observation import MAX_GOVERNANCE_RULES
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

_NO_STORE = {"Cache-Control": "no-store"}


class ExpectedGovernanceBaselineRequest(RequestModel):
    baseline_id: str = Field(
        validation_alias="baselineId",
        pattern=r"^governance-baseline:[0-9a-f]{64}$",
    )
    version: int = Field(ge=1, le=MAX_SAFE_JSON_INTEGER)
    state_digest: str = Field(
        validation_alias="stateDigest",
        pattern=r"^[0-9a-f]{64}$",
    )


class GovernanceBaselineApprovalRequest(RequestModel):
    operation_id: str = Field(
        validation_alias="operationId",
        min_length=1,
        max_length=256,
    )
    expected_state_digest: str = Field(
        validation_alias="expectedStateDigest",
        pattern=r"^[0-9a-f]{64}$",
    )
    expected_active: ExpectedGovernanceBaselineRequest | None = Field(
        validation_alias="expectedActive"
    )
    reason: str = Field(min_length=1, max_length=1_024)

    @field_validator("operation_id")
    @classmethod
    def _operation_is_bounded_scalar_text(cls, value: str) -> str:
        return _bounded_scalar_text(
            value,
            "operation id",
            MAX_BASELINE_OPERATION_ID_BYTES,
        )

    @field_validator("reason")
    @classmethod
    def _reason_is_bounded_canonical_text(cls, value: str) -> str:
        if not governance_baseline_reason_is_admitted(value):
            raise ValueError("reason is outside its canonical text profile")
        return value


class GovernanceBaselinePointerResponse(ResponseModel):
    baseline_id: str = Field(
        pattern=r"^governance-baseline:[0-9a-f]{64}$",
    )
    version: int = Field(ge=1, le=MAX_SAFE_JSON_INTEGER)
    state_digest: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )


class GovernanceBaselineStateResponse(ResponseModel):
    repository: GovernanceRepositoryResponse
    api_version: str = Field(min_length=1, max_length=64)
    state_digest: str = Field(
        pattern=r"^[0-9a-f]{64}$",
    )
    rules: tuple[EffectiveGovernanceRuleResponse, ...] = Field(max_length=MAX_GOVERNANCE_RULES)


class GovernanceBaselineRecordResponse(ResponseModel):
    pointer: GovernanceBaselinePointerResponse
    supersedes: GovernanceBaselinePointerResponse | None
    state: GovernanceBaselineStateResponse
    observed_at: datetime
    approved_at: datetime
    actor: str = Field(min_length=1, max_length=256)
    reason: str = Field(min_length=1, max_length=1_024)
    operation_id: str = Field(min_length=1, max_length=256)
    audit_event_id: str = Field(
        pattern=r"^audit_[0-9a-f]{32}$",
    )
    authority: Literal["approved_expected_state"] = "approved_expected_state"

    @model_validator(mode="after")
    def _bind_record_identity(self) -> Self:
        predecessor = self.supersedes
        if (
            self.pointer.state_digest != self.state.state_digest
            or self.approved_at < self.observed_at
            or (self.pointer.version == 1) != (predecessor is None)
            or (predecessor is not None and predecessor.version + 1 != self.pointer.version)
        ):
            raise ValueError("governance baseline record identity is inconsistent")
        return self


class GovernanceBaselineReadResponse(ResponseModel):
    ok: Literal[True]
    state: Literal["active", "absent"]
    scope: GovernanceScopeResponse
    baseline: GovernanceBaselineRecordResponse | None

    @model_validator(mode="after")
    def _bind_state_to_record(self) -> Self:
        if (self.state == "active") != (self.baseline is not None):
            raise ValueError("governance baseline read shape is inconsistent")
        if self.baseline is not None and self.baseline.state.repository.scope != self.scope:
            raise ValueError("governance baseline read crosses repository scope")
        return self


class GovernanceBaselineApprovalResponse(ResponseModel):
    ok: Literal[True]
    state: Literal["accepted", "duplicate", "unchanged"]
    request_operation_id: str
    scope: GovernanceScopeResponse
    baseline: GovernanceBaselineRecordResponse

    @model_validator(mode="after")
    def _bind_scope_and_operation(self) -> Self:
        baseline_scope = self.baseline.state.repository.scope
        if baseline_scope != self.scope:
            raise ValueError("governance baseline approval crosses repository scope")
        if self.state != "unchanged" and self.baseline.operation_id != self.request_operation_id:
            raise ValueError("governance baseline approval operation is inconsistent")
        return self


type GovernanceBaselineErrorCode = Literal[
    "unauthenticated",
    "forbidden",
    "stale",
    "baseline_conflict",
    "operation_conflict",
    "overloaded",
    "unavailable",
]


class GovernanceBaselineErrorResponse(ResponseModel):
    ok: Literal[False]
    error: GovernanceBaselineErrorCode


def governance_baseline_read_response(
    outcome: GovernanceBaselineReadOutcome,
    scope: RepositoryScope,
) -> JSONResponse:
    response = GovernanceBaselineReadResponse.model_validate(
        {
            "ok": True,
            "state": outcome.state,
            "scope": scope,
            "baseline": (
                None
                if outcome.record is None
                else governance_baseline_record_projection(outcome.record)
            ),
        }
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=response.to_wire_mapping(),
        headers=_NO_STORE,
    )


def governance_baseline_approval_response(
    outcome: GovernanceBaselineApprovalOutcome,
    *,
    command: GovernanceBaselineCommand,
) -> JSONResponse:
    record = outcome.record
    if type(record) is not GovernanceBaselineRecord:
        raise RuntimeError("successful governance baseline approval omitted its record")
    _require_approval_result_matches_command(outcome, record=record, command=command)
    response = GovernanceBaselineApprovalResponse.model_validate(
        {
            "ok": True,
            "state": outcome.state,
            "request_operation_id": command.operation_id,
            "scope": command.scope,
            "baseline": governance_baseline_record_projection(record),
        }
    )
    return JSONResponse(
        status_code=(
            status.HTTP_201_CREATED if outcome.state == "accepted" else status.HTTP_200_OK
        ),
        content=response.to_wire_mapping(),
        headers=_NO_STORE,
    )


def governance_baseline_error_response(
    status_code: int,
    error: GovernanceBaselineErrorCode,
) -> JSONResponse:
    response = GovernanceBaselineErrorResponse(ok=False, error=error)
    headers = dict(_NO_STORE)
    if status_code == status.HTTP_401_UNAUTHORIZED:
        headers.update(WWW_AUTHENTICATE_HEADER)
    return JSONResponse(
        status_code=status_code,
        content=response.to_wire_mapping(),
        headers=headers,
    )


def governance_baseline_record_projection(
    record: GovernanceBaselineRecord,
) -> dict[str, object]:
    if type(record) is not GovernanceBaselineRecord:
        raise TypeError("governance baseline projection requires an exact record")
    return {
        "pointer": _pointer_projection(record.pointer),
        "supersedes": (
            None
            if record.command.expected_active is None
            else _pointer_projection(record.command.expected_active)
        ),
        "state": {
            **governance_state_projection(record.state),
            "state_digest": record.pointer.state_digest,
        },
        "observed_at": record.observed_at,
        "approved_at": record.approved_at,
        "actor": record.command.actor,
        "reason": record.command.reason,
        "operation_id": record.command.operation_id,
        "audit_event_id": record.audit_event_id,
        "authority": "approved_expected_state",
    }


def _pointer_projection(pointer: GovernanceBaselinePointer) -> dict[str, object]:
    return {
        "baseline_id": pointer.baseline_id,
        "version": pointer.version,
        "state_digest": pointer.state_digest,
    }


def _require_approval_result_matches_command(
    outcome: GovernanceBaselineApprovalOutcome,
    *,
    record: GovernanceBaselineRecord,
    command: GovernanceBaselineCommand,
) -> None:
    if outcome.state in {"accepted", "duplicate"}:
        matches = record.command == command
    elif outcome.state == "unchanged":
        matches = command.expected_active is not None and record.pointer == command.expected_active
    else:
        matches = False
    if not matches:
        raise RuntimeError("governance baseline approval contradicts its command")


def _bounded_scalar_text(
    value: str,
    name: str,
    maximum_bytes: int,
) -> str:
    if (
        "\0" in value
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError(f"{name} is outside its byte contract")
    return value

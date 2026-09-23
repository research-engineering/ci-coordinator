from datetime import datetime
from typing import Literal

from pydantic import Field

from ci_coordinator.api.http.model_contracts import (
    ProjectedResponseModel,
    RequestModel,
    ResponseModel,
)


class ActivityParameters(RequestModel):
    since: str = Field(min_length=20, max_length=27)
    until: str = Field(min_length=20, max_length=27)
    issuer: str | None = Field(default=None, max_length=2048)
    actor: str | None = Field(default=None, max_length=128)
    action: str | None = Field(default=None, max_length=64)
    limit: str = Field(default="50", pattern=r"^(?:[1-9][0-9]?|100)$")
    cursor: str | None = Field(default=None, max_length=1024)


class ActivityItemResponse(ProjectedResponseModel):
    sequence: int = Field(ge=1, le=9007199254740991)
    source: Literal["security", "business"]
    action: str = Field(max_length=64)
    outcome: Literal["committed", "denied", "attempted"]
    occurred_at: datetime
    actor: str | None = Field(max_length=128)
    issuer: str | None = Field(max_length=2048)
    subject: str | None = Field(max_length=512)
    operation_ref: str = Field(max_length=128)
    audit_event_id: str | None = Field(max_length=38)
    event_hash: str | None = Field(max_length=64)


class ActivityContextResponse(ResponseModel):
    source: Literal["security", "business"]
    issuer: str | None = Field(max_length=2048)
    installation_id: int | None = Field(ge=1, le=9007199254740991)
    repository_id: int | None = Field(ge=1, le=9007199254740991)


class ActivityPageResponse(ResponseModel):
    context: ActivityContextResponse
    items: tuple[ActivityItemResponse, ...] = Field(max_length=100)
    next_cursor: str | None = Field(max_length=1024)
    observed_at: datetime
    retention_seconds: int | None
    integrity: Literal["journal_transaction", "audit_reference_only"]


class ActivityErrorResponse(ResponseModel):
    ok: Literal[False] = False
    error: Literal["invalid_request", "unauthenticated", "forbidden", "unavailable"]

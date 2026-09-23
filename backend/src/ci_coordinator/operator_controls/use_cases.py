"""Application-facing monotonic control orchestration through typed ports."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from ci_coordinator.operator_controls.override import (
    ActiveOverride,
    OverrideAuditEvent,
    OverrideCommand,
)
from ci_coordinator.operator_controls.permissions import OperatorAuthorizer


@dataclass(frozen=True, slots=True)
class OverrideApplied:
    override: ActiveOverride


@dataclass(frozen=True, slots=True)
class OverrideDuplicate:
    override: ActiveOverride


@dataclass(frozen=True, slots=True)
class OverrideConflict:
    pass


@dataclass(frozen=True, slots=True)
class OverrideUnavailable:
    pass


type OverrideStoreResult = (
    OverrideApplied | OverrideDuplicate | OverrideConflict | OverrideUnavailable
)
type OverrideReplayResult = OverrideDuplicate | OverrideConflict | OverrideUnavailable | None


class OverrideStore(Protocol):
    async def resolve_operation(self, command: OverrideCommand) -> OverrideReplayResult: ...

    async def apply(
        self,
        override: ActiveOverride,
        audit_event: OverrideAuditEvent,
    ) -> OverrideStoreResult: ...

    async def record_rejection(self, audit_event: OverrideAuditEvent) -> bool: ...


@dataclass(frozen=True, slots=True)
class OverrideRejected:
    code: Literal["unauthorized", "expired"]


type OverrideResult = (
    OverrideApplied | OverrideDuplicate | OverrideConflict | OverrideUnavailable | OverrideRejected
)


class OperatorOverrideUseCase(Protocol):
    async def __call__(self, command: OverrideCommand) -> OverrideResult: ...


async def apply_override(
    command: OverrideCommand,
    *,
    authorizer: OperatorAuthorizer,
    store: OverrideStore,
    now: datetime,
) -> OverrideResult:
    if type(now) is not datetime or now.tzinfo is None:
        raise ValueError("override evaluation time must be timezone-aware")
    if not await authorizer.allows(actor=command.actor, action=command.kind, scope=command.scope):
        return await _record_rejection(command, "unauthorized", store, now)
    if command.expires_at is not None and command.expires_at <= now:
        replay = await store.resolve_operation(command)
        if replay is not None:
            return replay
        return await _record_rejection(command, "expired", store, now)
    override = ActiveOverride.create(command, now)
    return await store.apply(override, OverrideAuditEvent.applied(override))


async def _record_rejection(
    command: OverrideCommand,
    outcome: Literal["expired", "unauthorized"],
    store: OverrideStore,
    now: datetime,
) -> OverrideRejected | OverrideUnavailable:
    recorded = await store.record_rejection(OverrideAuditEvent.rejected(command, outcome, now))
    return OverrideRejected(outcome) if recorded else OverrideUnavailable()

"""Immutable validation-increasing override commands and records."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final, Literal

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object

type OverrideKind = Literal["force_full_ci", "disable_omission", "enable_omission"]
type OverrideAuditOutcome = Literal["applied", "expired", "unauthorized"]
_OVERRIDE_KINDS = frozenset[OverrideKind]({"force_full_ci", "disable_omission", "enable_omission"})
_OVERRIDE_ID = re.compile(r"override_[0-9a-f]{32}")
OPERATOR_OVERRIDE_APPLIED_AUDIT_EVENT_TYPE: Final = "operator_override_applied"


@dataclass(frozen=True, slots=True)
class OverrideCommand:
    kind: OverrideKind
    scope: RepositoryScope
    subject_id: str | None
    operation_id: str
    actor: str
    reason: str
    expires_at: datetime | None

    def __post_init__(self) -> None:
        if self.kind not in _OVERRIDE_KINDS:
            raise ValueError("override kind is not admitted")
        if type(self.scope) is not RepositoryScope:
            raise ValueError("override scope must be exact")
        if self.kind == "force_full_ci":
            _require_bounded_text(self.subject_id, "subject id")
        elif self.kind == "disable_omission" and self.subject_id is not None:
            raise ValueError("disable_omission must not identify a subject")
        elif self.kind == "enable_omission" and (
            type(self.subject_id) is not str or _OVERRIDE_ID.fullmatch(self.subject_id) is None
        ):
            raise ValueError("enable_omission must identify the exact disabled override")
        for value, name in (
            (self.operation_id, "operation id"),
            (self.actor, "actor"),
            (self.reason, "reason"),
        ):
            _require_bounded_text(value, name)
        if self.kind == "force_full_ci":
            if type(self.expires_at) is not datetime or self.expires_at.tzinfo is None:
                raise ValueError("force_full_ci expiry must be timezone-aware")
        elif self.expires_at is not None:
            raise ValueError("repository omission controls must be latched without an expiry")


@dataclass(frozen=True, slots=True)
class InvalidOverrideCommand:
    reason: Literal["invalid_override"] = "invalid_override"


def admit_override_command(
    *,
    kind: OverrideKind,
    scope: RepositoryScope,
    subject_id: str | None,
    operation_id: str,
    actor: str,
    reason: str,
    expires_at: datetime | None,
) -> OverrideCommand | InvalidOverrideCommand:
    try:
        return OverrideCommand(
            kind=kind,
            scope=scope,
            subject_id=subject_id,
            operation_id=operation_id,
            actor=actor,
            reason=reason,
            expires_at=expires_at,
        )
    except (TypeError, ValueError, UnicodeError):
        return InvalidOverrideCommand()


@dataclass(frozen=True, slots=True)
class ActiveOverride:
    override_id: str
    command: OverrideCommand
    applied_at: datetime

    @classmethod
    def create(cls, command: OverrideCommand, applied_at: datetime) -> ActiveOverride:
        if type(applied_at) is not datetime or applied_at.tzinfo is None:
            raise ValueError("override application time must be timezone-aware")
        if command.expires_at is not None and command.expires_at <= applied_at:
            raise ValueError("expired override cannot be applied")
        identity = _command_identity(command)
        return cls("override_" + hash_object(identity)[:32], command, applied_at)

    def applies_at(self, instant: datetime) -> bool:
        if type(instant) is not datetime or instant.tzinfo is None:
            raise ValueError("override evaluation time must be timezone-aware")
        if self.command.kind == "enable_omission" or instant < self.applied_at:
            return False
        return self.command.expires_at is None or instant < self.command.expires_at


@dataclass(frozen=True, slots=True)
class OverrideAuditEvent:
    event_id: str
    outcome: OverrideAuditOutcome
    command: OverrideCommand
    override_id: str | None
    occurred_at: datetime

    @classmethod
    def applied(cls, override: ActiveOverride) -> OverrideAuditEvent:
        return cls._from_command(
            override.command,
            outcome="applied",
            occurred_at=override.applied_at,
            override_id=override.override_id,
        )

    @classmethod
    def rejected(
        cls,
        command: OverrideCommand,
        outcome: Literal["expired", "unauthorized"],
        occurred_at: datetime,
    ) -> OverrideAuditEvent:
        return cls._from_command(
            command,
            outcome=outcome,
            occurred_at=occurred_at,
            override_id=None,
        )

    @classmethod
    def _from_command(
        cls,
        command: OverrideCommand,
        *,
        outcome: OverrideAuditOutcome,
        occurred_at: datetime,
        override_id: str | None,
    ) -> OverrideAuditEvent:
        if type(command) is not OverrideCommand:
            raise ValueError("override audit command must be exact")
        if type(occurred_at) is not datetime or occurred_at.tzinfo is None:
            raise ValueError("override audit time must be timezone-aware")
        identity = {
            "command": _command_identity(command),
            "outcome": outcome,
            "occurredAt": occurred_at.isoformat(),
            "overrideId": override_id,
        }
        return cls(
            event_id="override_event_" + hash_object(identity)[:32],
            outcome=outcome,
            command=command,
            override_id=override_id,
            occurred_at=occurred_at,
        )


def _command_identity(command: OverrideCommand) -> dict[str, object]:
    return {
        "kind": command.kind,
        "scope": {
            "installationId": command.scope.installation_id,
            "repositoryId": command.scope.repository_id,
        },
        "subjectId": command.subject_id,
        "operationId": command.operation_id,
        "actor": command.actor,
        "reason": command.reason,
        "expiresAt": None if command.expires_at is None else command.expires_at.isoformat(),
    }


def _require_bounded_text(value: object, name: str) -> None:
    if type(value) is not str or not value or len(value.encode("utf-8")) > 512:
        raise ValueError(f"override {name} must be bounded non-empty text")

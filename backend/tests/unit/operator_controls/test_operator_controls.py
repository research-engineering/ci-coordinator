from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls import (
    ControlPlaneScopeAuthorizer,
    InvalidOverrideCommand,
    OperatorAuthorizer,
    OverrideApplied,
    OverrideAuditEvent,
    OverrideCommand,
    OverrideConflict,
    OverrideDuplicate,
    OverrideRejected,
    OverrideStore,
    OverrideUnavailable,
    admit_override_command,
    apply_override,
)
from ci_coordinator.operator_controls.override import ActiveOverride, OverrideKind

NOW = datetime(2026, 7, 14, tzinfo=UTC)
SCOPE = RepositoryScope(1, 2)
ADMIN_ACTOR = "keycloak-human:v1:" + "a" * 64
BREAK_GLASS_ACTOR = "break-glass:v1:dev1"


class _Authorizer(OperatorAuthorizer):
    def __init__(self, allowed: bool) -> None:
        self._allowed = allowed

    async def allows(self, *, actor: str, action: object, scope: RepositoryScope) -> bool:
        del actor, action, scope
        return self._allowed


class _ActorAuthority:
    def is_administrator(self, actor: object) -> bool:
        return actor == ADMIN_ACTOR

    def is_break_glass(self, actor: object) -> bool:
        return actor == BREAK_GLASS_ACTOR


class _Store(OverrideStore):
    def __init__(self) -> None:
        self.saved: list[ActiveOverride] = []
        self.applied_audits: list[OverrideAuditEvent] = []
        self.rejected_audits: list[OverrideAuditEvent] = []
        self.replay_queries: list[OverrideCommand] = []
        self.replay_result: OverrideDuplicate | OverrideConflict | OverrideUnavailable | None = None
        self.audit_available = True

    async def resolve_operation(
        self, command: OverrideCommand
    ) -> OverrideDuplicate | OverrideConflict | OverrideUnavailable | None:
        self.replay_queries.append(command)
        return self.replay_result

    async def apply(
        self,
        override: ActiveOverride,
        audit_event: OverrideAuditEvent,
    ) -> OverrideApplied:
        self.saved.append(override)
        self.applied_audits.append(audit_event)
        return OverrideApplied(override)

    async def record_rejection(self, audit_event: OverrideAuditEvent) -> bool:
        if not self.audit_available:
            return False
        self.rejected_audits.append(audit_event)
        return True


def test_control_plane_scope_authority_separates_administration_and_emergency() -> None:
    authorizer = ControlPlaneScopeAuthorizer(
        actor_authority=_ActorAuthority(),
        allowed_scopes=frozenset({SCOPE}),
    )

    assert asyncio.run(authorizer.allows(actor=ADMIN_ACTOR, action="enable_omission", scope=SCOPE))
    assert asyncio.run(
        authorizer.allows(actor=BREAK_GLASS_ACTOR, action="disable_omission", scope=SCOPE)
    )
    assert not asyncio.run(
        authorizer.allows(actor=BREAK_GLASS_ACTOR, action="enable_omission", scope=SCOPE)
    )
    assert not asyncio.run(authorizer.allows_scope(actor=BREAK_GLASS_ACTOR, scope=SCOPE))


@pytest.mark.parametrize("actor", [ADMIN_ACTOR, BREAK_GLASS_ACTOR, "untrusted"])
async def test_empty_scope_authority_denies_all_actors_and_operations(actor: str) -> None:
    authorizer = ControlPlaneScopeAuthorizer(
        actor_authority=_ActorAuthority(), allowed_scopes=frozenset()
    )
    actions: tuple[OverrideKind, ...] = ("force_full_ci", "disable_omission", "enable_omission")
    for action in actions:
        assert not await authorizer.allows(actor=actor, action=action, scope=SCOPE)
    assert not await authorizer.allows_scope(actor=actor, scope=SCOPE)


@pytest.mark.parametrize(
    ("kind", "subject_id", "expires_at"),
    [
        ("force_full_ci", "run-1", None),
        ("disable_omission", "run-1", None),
        ("enable_omission", "not-an-override-id", None),
    ],
)
def test_override_admission_returns_a_typed_invalid_outcome(
    kind: Literal["force_full_ci", "disable_omission", "enable_omission"],
    subject_id: str | None,
    expires_at: datetime | None,
) -> None:
    admitted = admit_override_command(
        kind=kind,
        scope=SCOPE,
        subject_id=subject_id,
        operation_id="operation-1",
        actor="operator",
        reason="investigate failure",
        expires_at=expires_at,
    )
    assert admitted == InvalidOverrideCommand()


def test_authorized_override_is_active_only_before_expiry() -> None:
    store = _Store()
    result = asyncio.run(
        apply_override(_override_command(), authorizer=_Authorizer(True), store=store, now=NOW)
    )

    assert isinstance(result, OverrideApplied)
    assert result.override.applies_at(NOW) is True
    assert result.override.applies_at(NOW + timedelta(minutes=6)) is False
    assert store.applied_audits == [OverrideAuditEvent.applied(result.override)]


def test_unauthorized_or_expired_override_creates_no_effect() -> None:
    store = _Store()
    denied = asyncio.run(
        apply_override(_override_command(), authorizer=_Authorizer(False), store=store, now=NOW)
    )
    expired = asyncio.run(
        apply_override(
            _override_command(expires_at=NOW), authorizer=_Authorizer(True), store=store, now=NOW
        )
    )

    assert denied == OverrideRejected("unauthorized")
    assert expired == OverrideRejected("expired")
    assert store.saved == []
    assert [event.outcome for event in store.rejected_audits] == ["unauthorized", "expired"]
    assert [event.command for event in store.rejected_audits] == [
        _override_command(),
        _override_command(expires_at=NOW),
    ]
    assert store.replay_queries == [_override_command(expires_at=NOW)]


def test_authorized_retry_is_classified_before_expiry_from_retained_operation() -> None:
    command = _override_command()
    assert command.expires_at is not None
    retained = ActiveOverride.create(command, NOW)
    store = _Store()
    store.replay_result = OverrideDuplicate(retained)

    result = asyncio.run(
        apply_override(
            command,
            authorizer=_Authorizer(True),
            store=store,
            now=command.expires_at + timedelta(minutes=1),
        )
    )

    assert result == OverrideDuplicate(retained)
    assert store.saved == []
    assert store.rejected_audits == []


def test_expired_divergent_retry_preserves_operation_conflict() -> None:
    command = _override_command()
    assert command.expires_at is not None
    store = _Store()
    store.replay_result = OverrideConflict()

    result = asyncio.run(
        apply_override(
            command,
            authorizer=_Authorizer(True),
            store=store,
            now=command.expires_at + timedelta(minutes=1),
        )
    )

    assert result == OverrideConflict()
    assert store.rejected_audits == []


def test_override_command_subject_shape_is_kind_discriminated() -> None:
    with pytest.raises(ValueError, match="subject"):
        _override_command(subject_id=None)
    with pytest.raises(ValueError, match="must not identify"):
        _override_command(kind="disable_omission", subject_id="repository-control")

    command = _override_command(kind="disable_omission", subject_id=None)

    assert command.subject_id is None

    with pytest.raises(ValueError, match="exact disabled override"):
        _override_command(kind="enable_omission", subject_id="not-an-override")
    disabled = ActiveOverride.create(command, NOW)
    release = _override_command(
        kind="enable_omission",
        subject_id=disabled.override_id,
    )
    released = ActiveOverride.create(release, NOW + timedelta(seconds=1))
    assert released.applies_at(NOW + timedelta(minutes=1)) is False


def test_rejection_identity_includes_the_complete_scoped_command() -> None:
    first = _override_command()
    other_scope = OverrideCommand(
        kind=first.kind,
        scope=RepositoryScope(3, 4),
        subject_id=first.subject_id,
        operation_id=first.operation_id,
        actor=first.actor,
        reason=first.reason,
        expires_at=first.expires_at,
    )

    first_event = OverrideAuditEvent.rejected(first, "unauthorized", NOW)
    other_event = OverrideAuditEvent.rejected(other_scope, "unauthorized", NOW)

    assert first_event.command == first
    assert other_event.command == other_scope
    assert first_event.event_id != other_event.event_id


def test_override_is_not_reported_as_audited_when_rejection_audit_is_unavailable() -> None:
    store = _Store()
    store.audit_available = False

    result = asyncio.run(
        apply_override(_override_command(), authorizer=_Authorizer(False), store=store, now=NOW)
    )

    assert result == OverrideUnavailable()
    assert store.saved == []
    assert store.rejected_audits == []


def _override_command(
    expires_at: datetime | None = None,
    *,
    kind: Literal["force_full_ci", "disable_omission", "enable_omission"] = "force_full_ci",
    subject_id: str | None = "run-1",
) -> OverrideCommand:
    expiry = (
        expires_at
        if expires_at is not None
        else (NOW + timedelta(minutes=5) if kind == "force_full_ci" else None)
    )
    return OverrideCommand(
        kind,
        SCOPE,
        subject_id,
        "operation-1",
        "operator",
        "investigate failure",
        expiry,
    )

from __future__ import annotations

from typing import Literal, cast

import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls.auth import (
    ControlPlaneScopeAuthorizer,
    RepositoryAccessUnavailable,
)
from ci_coordinator.operator_controls.override import OverrideKind

SCOPE = RepositoryScope(1, 2)
ADMIN_ACTOR = "keycloak-human:v1:" + "a" * 64
BREAK_GLASS_ACTOR = "break-glass:v1:dev1"


class _ActorAuthority:
    def is_administrator(self, actor: object) -> bool:
        return actor == ADMIN_ACTOR

    def is_break_glass(self, actor: object) -> bool:
        return actor == BREAK_GLASS_ACTOR


class _RepositoryAccess:
    def __init__(self, result: bool | Exception) -> None:
        self.result = result
        self.calls: list[RepositoryScope] = []

    async def allows_repository(self, scope: RepositoryScope) -> bool:
        self.calls.append(scope)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.mark.parametrize("listed", [False, True])
@pytest.mark.parametrize("member", [False, True])
async def test_app_scope_requires_fresh_membership_even_for_static_scopes(
    listed: bool, member: bool
) -> None:
    reader = _RepositoryAccess(member)
    authorizer = ControlPlaneScopeAuthorizer(
        actor_authority=_ActorAuthority(),
        allowed_scopes=frozenset({SCOPE}) if listed else frozenset(),
        scope_mode="app",
        repository_access=reader,
    )
    for expected in (member, not member):
        reader.result = expected
        assert await authorizer.allows_scope(actor=ADMIN_ACTOR, scope=SCOPE) is expected
    assert reader.calls == [SCOPE, SCOPE]


@pytest.mark.parametrize("actor", [BREAK_GLASS_ACTOR, "untrusted"])
@pytest.mark.parametrize("listed", [False, True])
async def test_app_scope_never_promotes_non_admins_or_expands_emergency_access(
    actor: str, listed: bool
) -> None:
    reader = _RepositoryAccess(RepositoryAccessUnavailable())
    authorizer = ControlPlaneScopeAuthorizer(
        actor_authority=_ActorAuthority(),
        allowed_scopes=frozenset({SCOPE}) if listed else frozenset(),
        scope_mode="app",
        repository_access=reader,
    )
    assert not await authorizer.allows_scope(actor=actor, scope=SCOPE)
    actions: tuple[OverrideKind, ...] = ("force_full_ci", "disable_omission", "enable_omission")
    for action in actions:
        expected = actor == BREAK_GLASS_ACTOR and listed and action != "enable_omission"
        assert await authorizer.allows(actor=actor, action=action, scope=SCOPE) is expected
    assert reader.calls == []


@pytest.mark.parametrize("result", [RepositoryAccessUnavailable(), cast(bool, 1)])
async def test_unknown_or_non_boolean_membership_never_becomes_authority(
    result: bool | Exception,
) -> None:
    authorizer = ControlPlaneScopeAuthorizer(
        actor_authority=_ActorAuthority(),
        allowed_scopes=frozenset({SCOPE}),
        scope_mode="app",
        repository_access=_RepositoryAccess(result),
    )
    with pytest.raises(RepositoryAccessUnavailable):
        await authorizer.allows_scope(actor=ADMIN_ACTOR, scope=SCOPE)


@pytest.mark.parametrize(
    "mode,reader", [("app", None), ("restricted", _RepositoryAccess(True)), ("other", None)]
)
def test_mode_and_reader_must_be_admitted_together(
    mode: str, reader: _RepositoryAccess | None
) -> None:
    with pytest.raises(ValueError):
        ControlPlaneScopeAuthorizer(
            actor_authority=_ActorAuthority(),
            allowed_scopes=frozenset(),
            scope_mode=cast(Literal["restricted", "app"], mode),
            repository_access=reader,
        )

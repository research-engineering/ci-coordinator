import asyncio
from dataclasses import dataclass, replace
from datetime import timedelta

import pytest
from control_plane_http_support import NOW, human_principal

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import BreakGlassPrincipal, ControlPlanePrincipal
from ci_coordinator.control_plane_identity.activity import (
    ActivityForbidden,
    ActivityPrincipal,
    ActivityUnavailable,
    observe_login,
    observe_principal,
)
from ci_coordinator.control_plane_identity.activity_cursor import (
    ActivityCursorCodec,
    ActivityPosition,
)
from ci_coordinator.control_plane_identity.activity_query import (
    ActivityEntry,
    ActivityPage,
    ActivityQuery,
    ActivityReadService,
)


def _query() -> ActivityQuery:
    return ActivityQuery("business", NOW - timedelta(days=1), NOW, scope=RepositoryScope(1, 2))


@pytest.mark.parametrize("limit", [0, 101, True, -1])
def test_page_limits_cannot_expand_materialization(limit: int) -> None:
    with pytest.raises(ValueError):
        replace(_query(), limit=limit)


@pytest.mark.parametrize("days", [0, -1, 32])
def test_query_window_is_positive_and_bounded(days: int) -> None:
    with pytest.raises(ValueError):
        replace(_query(), since=NOW - timedelta(days=days))


@pytest.mark.parametrize("change", ["scope", "actor", "action", "limit", "since", "issuer"])
def test_cursor_is_bound_to_every_query_dimension(change: str) -> None:
    query = _query()
    principal = human_principal()
    codec = ActivityCursorCodec(b"a" * 32)
    position = ActivityPosition(50, 40, NOW + timedelta(minutes=5))
    token = codec.encode(query, principal, position)
    assert codec.decode(token, query, principal, now=NOW) == position
    alternatives = {
        "scope": replace(query, scope=RepositoryScope(1, 3)),
        "actor": replace(query, actor=principal.actor_id),
        "action": replace(query, action="config-epoch-activation/v1"),
        "limit": replace(query, limit=1),
        "since": replace(query, since=NOW - timedelta(days=2)),
        "issuer": ActivityQuery("security", query.since, query.until, issuer=principal.issuer),
    }
    with pytest.raises(ValueError):
        codec.decode(token, alternatives[change], principal, now=NOW)


@pytest.mark.parametrize("change", ["bytes", "key", "subject", "profile", "expiry"])
def test_cursor_rejects_tampering_or_authority_drift(change: str) -> None:
    query, principal = _query(), human_principal()
    codec = ActivityCursorCodec(b"a" * 32)
    token = codec.encode(query, principal, ActivityPosition(50, 40, NOW + timedelta(minutes=5)))
    if change == "bytes":
        token = ("B" if token[0] == "A" else "A") + token[1:]
    if change == "key":
        codec = ActivityCursorCodec(b"b" * 32)
    if change == "subject":
        principal = replace(principal, subject="other")
    if change == "profile":
        principal = replace(principal, authority_profile_digest="b" * 64)
    with pytest.raises(ValueError):
        codec.decode(
            token,
            query,
            principal,
            now=NOW + (timedelta(minutes=5) if change == "expiry" else timedelta()),
        )


@dataclass
class _Reader:
    calls: int = 0

    async def page(
        self, query: ActivityQuery, principal: ActivityPrincipal, cursor: str | None
    ) -> ActivityPage:
        self.calls += 1
        return ActivityPage((), None, NOW, None, "audit_reference_only")


@dataclass
class _Authorizer:
    allowed: bool = True
    issuer_allowed: bool = False

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        return self.allowed and scope == RepositoryScope(1, 2)

    async def allows_issuer(self, *, actor: str, issuer: str) -> bool:
        return self.issuer_allowed


@pytest.mark.parametrize(
    "principal",
    [
        human_principal(roles=frozenset({"read"})),
        BreakGlassPrincipal("emergency"),
        replace(human_principal(), expires_at=NOW),
    ],
)
def test_current_audit_role_is_required_before_storage(principal: ControlPlanePrincipal) -> None:
    reader = _Reader()
    result = asyncio.run(
        ActivityReadService(reader, _Authorizer()).page(_query(), principal, at=NOW)
    )
    assert isinstance(result, ActivityForbidden)
    assert reader.calls == 0


def test_scope_rechecked_for_export_and_continuation() -> None:
    async def scenario() -> None:
        reader, authorizer = _Reader(), _Authorizer()
        service = ActivityReadService(reader, authorizer)
        assert isinstance(await service.page(_query(), human_principal(), at=NOW), ActivityPage)
        authorizer.allowed = False
        assert isinstance(
            await service.page(_query(), human_principal(), at=NOW, cursor="previous", export=True),
            ActivityForbidden,
        )
        assert reader.calls == 1
        issuer_query = ActivityQuery(
            "security", _query().since, NOW, issuer=human_principal().issuer
        )
        assert isinstance(
            await service.page(issuer_query, human_principal(), at=NOW), ActivityForbidden
        )

    asyncio.run(scenario())


@dataclass
class _FailingObserver:
    cancelled: bool
    failure: type[Exception] = ActivityUnavailable

    async def login_diagnostic(self, action: str) -> None:
        if self.cancelled:
            raise asyncio.CancelledError
        raise self.failure("sensitive exception text")

    async def principal_diagnostic(self, principal: ActivityPrincipal, action: str) -> None:
        await self.login_diagnostic(action)


@pytest.mark.parametrize("principal_event", [False, True])
@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("failure", [ActivityUnavailable, RuntimeError, ValueError])
def test_optional_diagnostics_preserve_failure_and_cancellation(
    principal_event: bool, cancelled: bool, failure: type[Exception]
) -> None:
    observer = _FailingObserver(cancelled, failure)
    operation = (
        observe_principal(observer, human_principal(), "export")
        if principal_event
        else observe_login(observer, "login_rejected")
    )
    if cancelled:
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(operation)
    else:
        assert asyncio.run(operation) is None


@pytest.mark.parametrize("action", ["role_denied", "export", "arbitrary-request-success"])
def test_committed_outcome_cannot_be_inferred_from_diagnostic_or_unknown_action(
    action: str,
) -> None:
    with pytest.raises(ValueError):
        ActivityEntry(
            1,
            "security",
            action,
            "committed",
            NOW,
            human_principal().actor_id,
            human_principal().issuer,
            human_principal().subject,
            "00000000-0000-0000-0000-000000000001",
        )


def test_optional_export_diagnostic_failure_does_not_fail_authorized_page() -> None:
    reader = _Reader()
    service = ActivityReadService(reader, _Authorizer(), _FailingObserver(False))
    result = asyncio.run(service.page(_query(), human_principal(), at=NOW, export=True))
    assert isinstance(result, ActivityPage)
    assert reader.calls == 1

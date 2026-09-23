from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.workbench_read_models import (
    ReplayView,
    RepositoryDataSnapshot,
    RepositoryWorkbenchService,
    RepositoryWorkbenchSnapshot,
    TruncationView,
    WorkbenchForbidden,
    WorkbenchReadError,
    WorkbenchUnavailable,
)

SCOPE = RepositoryScope(1, 2)
NOW = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)


@dataclass
class _Authorizer:
    allowed: bool
    calls: int = 0

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        assert (actor, scope) == ("operator", SCOPE)
        self.calls += 1
        return self.allowed


@dataclass
class _Repository:
    snapshot: RepositoryDataSnapshot
    failure: bool = False
    calls: int = 0

    async def load(self, scope: RepositoryScope, *, limit: int) -> RepositoryDataSnapshot:
        assert (scope, limit) == (SCOPE, 10)
        self.calls += 1
        if self.failure:
            raise WorkbenchReadError("unavailable")
        return self.snapshot


@dataclass(frozen=True)
class _Verification:
    ready: bool
    reason: str
    verified_revision: int | None


@dataclass
class _Probe:
    result: _Verification
    calls: int = 0

    async def check(self) -> _Verification:
        self.calls += 1
        return self.result


def test_service_authorizes_before_reading_repository_state() -> None:
    authorizer = _Authorizer(False)
    repository = _Repository(_snapshot(3))
    probe = _Probe(_Verification(True, "ready", 3))
    service = RepositoryWorkbenchService(
        authorizer=authorizer,
        repository=repository,
        replay_probe=probe,
    )

    result = asyncio.run(service(actor="operator", scope=SCOPE, limit=10))

    assert isinstance(result, WorkbenchForbidden)
    assert (authorizer.calls, repository.calls, probe.calls) == (1, 0, 0)


def test_service_binds_replay_validity_to_the_snapshot_revision() -> None:
    service = RepositoryWorkbenchService(
        authorizer=_Authorizer(True),
        repository=_Repository(_snapshot(4)),
        replay_probe=_Probe(_Verification(False, "audit_verification_in_progress", 4)),
    )

    result = asyncio.run(service(actor="operator", scope=SCOPE, limit=10))

    assert result == RepositoryWorkbenchSnapshot(
        data=_snapshot(4),
        replay=ReplayView("valid", 4, 4, None),
    )


def test_service_never_promotes_an_unverified_snapshot_prefix() -> None:
    service = RepositoryWorkbenchService(
        authorizer=_Authorizer(True),
        repository=_Repository(_snapshot(5)),
        replay_probe=_Probe(_Verification(False, "audit_verification_in_progress", 4)),
    )

    result = asyncio.run(service(actor="operator", scope=SCOPE, limit=10))

    assert isinstance(result, RepositoryWorkbenchSnapshot)
    assert result.replay == ReplayView(
        "in_progress",
        5,
        4,
        "audit_verification_in_progress",
    )


def test_service_maps_persistence_failure_without_calling_replay() -> None:
    probe = _Probe(_Verification(True, "ready", 0))
    service = RepositoryWorkbenchService(
        authorizer=_Authorizer(True),
        repository=_Repository(_snapshot(0), failure=True),
        replay_probe=probe,
    )

    result = asyncio.run(service(actor="operator", scope=SCOPE, limit=10))

    assert isinstance(result, WorkbenchUnavailable)
    assert probe.calls == 0


def _snapshot(revision: int) -> RepositoryDataSnapshot:
    return RepositoryDataSnapshot(
        scope=SCOPE,
        observed_at=NOW,
        ledger_revision=revision,
        plans=(),
        runs=(),
        overrides=(),
        config_epochs=(),
        audit_events=(),
        truncated=TruncationView(False, False, False, False, False),
    )

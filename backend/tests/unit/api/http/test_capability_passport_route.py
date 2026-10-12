from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from control_plane_http_support import (
    ACTOR,
    StaticControlPlaneAuthenticator,
    StaticMutationAdmission,
    StaticRoleAdmission,
    human_principal,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import ASGITransport, AsyncClient

from ci_coordinator.api.http.app import CI_ECONOMICS_REQUEST_CONCURRENCY_LIMIT, create_app
from ci_coordinator.api.http.dependencies import (
    CapabilityPassportRouteDependencies,
    CiObservationRouteDependencies,
    HttpRouteDependencies,
    InvalidCredential,
)
from ci_coordinator.app.capability_passport import (
    CapabilityPassport,
    CapabilityPassportForbidden,
    CapabilityPassportResult,
    InstallationGrantFact,
    ReconciliationFact,
    TrustKeyFact,
)
from ci_coordinator.app.ci_observation import CiObservationUseCase
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import CONTROL_PLANE_ROLES, ControlPlaneRole
from ci_coordinator.identity_admission import JwksCacheState
from ci_coordinator.observability import BackgroundHealthState
from ci_coordinator.operator_controls.auth import RepositoryAccessUnavailable

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)
SCOPE = RepositoryScope(1, 2)
PATH = "/api/v1/workbench/repositories/1/2/capabilities"
OBSERVATION_STATUS = "/api/v2/economics/repositories/1/2/observation"
READ_ONLY: frozenset[ControlPlaneRole] = frozenset({"read"})


@dataclass
class _UseCase:
    result: CapabilityPassportResult
    calls: list[tuple[str, RepositoryScope]] = field(default_factory=list)

    async def __call__(self, *, actor: str, scope: RepositoryScope) -> CapabilityPassportResult:
        self.calls.append((actor, scope))
        return self.result


@dataclass
class _HeldUseCase(_UseCase):
    """Holds every call until released; signals once the query lane is full."""

    entered: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)

    async def __call__(self, *, actor: str, scope: RepositoryScope) -> CapabilityPassportResult:
        self.calls.append((actor, scope))
        if len(self.calls) == CI_ECONOMICS_REQUEST_CONCURRENCY_LIMIT:
            self.entered.set()
        await self.release.wait()
        return self.result


def test_passport_route_projects_distinct_owner_facts() -> None:
    # Kills: denial, unavailable observation and unknown webhook collapsing into one state.
    use_case = _UseCase(_passport())

    response = _client(use_case).get(PATH)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "ok": True,
        "schemaVersion": "capability-passport/v1",
        "scope": {"installationId": 1, "repositoryId": 2},
        "observedAt": "2026-10-11T12:00:00Z",
        "nextAction": "request_access",
        "installationGrant": {
            "status": "denied",
            "source": "github_app_installation",
            "observedAt": "2026-10-11T12:00:00Z",
            "reason": "inventory_grant_absent",
            "installation": None,
        },
        "providerLimits": {
            "status": "unknown",
            "source": "github_app_installation",
            "observedAt": "2026-10-11T12:00:00Z",
            "reason": "no_retained_limit_observation",
            "retryAfterSeconds": None,
        },
        "webhookObservation": {
            "status": "unknown",
            "source": "history_delivery_inbox",
            "reason": "receipt_clock_not_indexed",
        },
        "reconciliation": {
            "status": "unavailable",
            "source": "ci_observation_status",
            "observedAt": None,
            "reason": "store_unavailable",
            "enabled": None,
            "lanes": [],
            "gapsRecorded": None,
            "detailTruncatedUntil": None,
            "backgroundState": "degraded",
        },
        "actionsOidcKeys": {
            "status": "expired",
            "source": "github_actions_jwks_cache",
            "observedAt": "2026-10-11T12:00:00Z",
            "reason": "cache_expired",
            "keyCount": 2,
            "ageSeconds": 301,
        },
        "productionAdmissionKeys": {
            "status": "unknown",
            "source": "production_admission_authorities",
            "reason": "not_projected",
        },
    }
    assert use_case.calls == [(ACTOR, SCOPE)]


def test_passport_requires_audit_role_before_use_case() -> None:
    # Kills: a read-only operator receiving audit-owned observation freshness.
    use_case = _UseCase(_passport())
    read_only = _client(use_case, roles=READ_ONLY)
    unauthenticated = _client(use_case, authenticated=False)

    forbidden = read_only.get(PATH)
    missing = unauthenticated.get(PATH)

    assert (forbidden.status_code, forbidden.json()) == (403, {"ok": False, "error": "forbidden"})
    assert (missing.status_code, missing.json()) == (
        401,
        {"ok": False, "error": "unauthenticated"},
    )
    assert missing.headers["www-authenticate"] == "Bearer"
    assert use_case.calls == []


def test_scope_denial_and_access_failure_are_not_empty_passports() -> None:
    # Kills: scope denial or membership failure rendered as a successful empty passport.
    class _UnavailableAccess(_UseCase):
        async def __call__(self, *, actor: str, scope: RepositoryScope) -> CapabilityPassportResult:
            raise RepositoryAccessUnavailable("private provider diagnostic")

    forbidden = _client(_UseCase(CapabilityPassportForbidden())).get(PATH)
    unavailable = _client(_UnavailableAccess(_passport())).get(PATH)

    assert (forbidden.status_code, forbidden.json()) == (403, {"ok": False, "error": "forbidden"})
    assert (unavailable.status_code, unavailable.json()) == (
        503,
        {"ok": False, "error": "unavailable"},
    )
    assert unavailable.headers["cache-control"] == "no-store"


def test_passport_path_identity_is_bounded_before_use_case() -> None:
    use_case = _UseCase(_passport())

    response = _client(use_case).get("/api/v1/workbench/repositories/0/2/capabilities")

    assert (response.status_code, response.json()) == (422, {"code": "invalid_request"})
    assert use_case.calls == []


async def test_passport_shares_observation_query_bulkhead_without_queueing() -> None:
    # Kills: the passport admitted outside (or on a separate lane from) ci_economics_query,
    # letting observation reads enter while the shared lane is saturated.
    use_case = _HeldUseCase(_passport())
    observation = AsyncMock(spec=CiObservationUseCase)
    app = _app(use_case, observation=observation)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with asyncio.timeout(3):
            async with asyncio.TaskGroup() as group:
                held = [
                    group.create_task(client.get(PATH))
                    for _ in range(CI_ECONOMICS_REQUEST_CONCURRENCY_LIMIT)
                ]
                try:
                    await use_case.entered.wait()
                    excess_passport = await client.get(PATH)
                    excess_observation = await client.get(OBSERVATION_STATUS)
                finally:
                    use_case.release.set()
    overloaded = {"ok": False, "error": "unavailable"}
    assert (excess_passport.status_code, excess_passport.json()) == (503, overloaded)
    assert (excess_observation.status_code, excess_observation.json()) == (503, overloaded)
    assert excess_passport.headers["cache-control"] == "no-store"
    assert len(use_case.calls) == CI_ECONOMICS_REQUEST_CONCURRENCY_LIMIT
    observation.status.assert_not_awaited()
    assert all(request.result().status_code == 200 for request in held)


async def test_cancelled_passport_requests_release_query_admission() -> None:
    # Kills: a cancelled passport request retaining its ci_economics_query lease.
    use_case = _HeldUseCase(_passport())
    app = _app(use_case)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with asyncio.timeout(3):
            held = [
                asyncio.create_task(client.get(PATH))
                for _ in range(CI_ECONOMICS_REQUEST_CONCURRENCY_LIMIT)
            ]
            await use_case.entered.wait()
            for request in held:
                request.cancel()
            await asyncio.gather(*held, return_exceptions=True)
            use_case.release.set()
            response = await client.get(PATH)
    assert response.status_code == 200
    assert len(use_case.calls) == CI_ECONOMICS_REQUEST_CONCURRENCY_LIMIT + 1


def _passport() -> CapabilityPassport:
    return CapabilityPassport(
        scope=SCOPE,
        observed_at=NOW,
        installation_grant=InstallationGrantFact("denied", reason="inventory_grant_absent"),
        reconciliation=ReconciliationFact("unavailable"),
        background_reconciliation=BackgroundHealthState.DEGRADED,
        actions_oidc_keys=TrustKeyFact(JwksCacheState("expired", 2, 301)),
    )


def _client(
    use_case: _UseCase,
    *,
    authenticated: bool = True,
    roles: frozenset[ControlPlaneRole] = CONTROL_PLANE_ROLES,
) -> TestClient:
    return TestClient(_app(use_case, authenticated=authenticated, roles=roles))


def _app(
    use_case: _UseCase,
    *,
    authenticated: bool = True,
    roles: frozenset[ControlPlaneRole] = CONTROL_PLANE_ROLES,
    observation: CiObservationUseCase | None = None,
) -> FastAPI:
    principal = human_principal(roles=roles) if authenticated else InvalidCredential()
    authenticator = StaticControlPlaneAuthenticator(principal)
    return create_app(
        HttpRouteDependencies(
            capability_passport=CapabilityPassportRouteDependencies(
                authenticator=authenticator,
                role_admission=StaticRoleAdmission(),
                use_case=use_case,
            ),
            ci_observation=None
            if observation is None
            else CiObservationRouteDependencies(
                authenticator=authenticator,
                role_admission=StaticRoleAdmission(),
                mutation_admission=StaticMutationAdmission(True),
                use_case=observation,
            ),
        ),
        include_operator_ui=False,
        request_timeout_seconds=30,
    )

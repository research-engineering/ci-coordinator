from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Literal, cast

import pytest
from ci_economics.factories import ATTEMPT, NOW

from ci_coordinator.app.ci_economics_sources import (
    CiEconomicsSourceForbidden,
    CiEconomicsSourceService,
    CiEconomicsSourceUnavailable,
    ProviderSourceRegistrationAvailable,
)
from ci_coordinator.ci_economics.discovery import ProviderRunDiscoveryPage, RunDiscoveryWindow
from ci_coordinator.ci_economics.model import AttemptIdentity
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable, ProviderAttemptDeferred
from ci_coordinator.ci_economics.sources import (
    ProviderRunCollectionSource,
    ProviderSourceRegistrationResult,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

SOURCE = ProviderRunCollectionSource(ATTEMPT, NOW, "2026-03-10", "e" * 64)
WINDOW = RunDiscoveryWindow(NOW, NOW + timedelta(days=1))
PAGE = ProviderRunDiscoveryPage(ATTEMPT.scope, WINDOW, 1, 1, (SOURCE,), "exhausted")
type Operation = Literal["discovery", "registration"]


@dataclass
class Boundary:
    allowed: bool = True
    source: ProviderRunCollectionSource | ProviderAttemptDeferred | BaseException = SOURCE
    page: ProviderRunDiscoveryPage | ProviderAttemptDeferred | BaseException = PAGE
    outcome: ProviderSourceRegistrationResult | BaseException = "registered"
    calls: list[tuple[str, object]] = field(default_factory=list)

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        self.calls.append(("authorize", (actor, scope)))
        return self.allowed

    async def resolve_attempt(
        self, scope: RepositoryScope, workflow_run_id: int, run_attempt: int
    ) -> ProviderRunCollectionSource | ProviderAttemptDeferred:
        self.calls.append(("resolve", (scope, workflow_run_id, run_attempt)))
        if isinstance(self.source, BaseException):
            raise self.source
        return self.source

    async def discover_page(
        self, scope: RepositoryScope, window: RunDiscoveryWindow, *, page_number: int
    ) -> ProviderRunDiscoveryPage | ProviderAttemptDeferred:
        self.calls.append(("discover", (scope, window, page_number)))
        if isinstance(self.page, BaseException):
            raise self.page
        return self.page

    async def register_provider_source(
        self, source: ProviderRunCollectionSource
    ) -> ProviderSourceRegistrationResult:
        self.calls.append(("register", source))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    def service(self) -> CiEconomicsSourceService:
        return CiEconomicsSourceService(authorizer=self, resolver=self, registration=self)


async def invoke(boundary: Boundary, operation: Operation) -> object:
    service = boundary.service()
    if operation == "discovery":
        return await service.discover_page(
            actor="operator", scope=ATTEMPT.scope, window=WINDOW, page_number=1
        )
    return await service.register_attempt(
        actor="operator",
        scope=ATTEMPT.scope,
        workflow_run_id=ATTEMPT.workflow_run_id,
        run_attempt=ATTEMPT.run_attempt,
    )


@pytest.mark.parametrize("operation", ["discovery", "registration"])
async def test_forbidden_scope_never_reaches_provider_or_store(operation: Operation) -> None:
    boundary = Boundary(allowed=False)

    assert await invoke(boundary, operation) == CiEconomicsSourceForbidden()
    assert boundary.calls == [("authorize", ("operator", ATTEMPT.scope))]


async def test_discovery_preserves_the_page_without_registering_any_source() -> None:
    boundary = Boundary()

    assert await invoke(boundary, "discovery") is PAGE
    assert boundary.calls == [
        ("authorize", ("operator", ATTEMPT.scope)),
        ("discover", (ATTEMPT.scope, WINDOW, 1)),
    ]


@pytest.mark.parametrize(
    "outcome",
    [
        "registered",
        "replayed",
        "source_conflict",
        "outside_source_window",
        "capacity_reached",
    ],
)
async def test_registration_preserves_each_durable_outcome_and_exact_provenance(
    outcome: ProviderSourceRegistrationResult,
) -> None:
    boundary = Boundary(outcome=outcome)

    assert await invoke(boundary, "registration") == ProviderSourceRegistrationAvailable(
        SOURCE, outcome
    )
    assert boundary.calls == [
        ("authorize", ("operator", ATTEMPT.scope)),
        ("resolve", (ATTEMPT.scope, ATTEMPT.workflow_run_id, ATTEMPT.run_attempt)),
        ("register", SOURCE),
    ]


@pytest.mark.parametrize("operation", ["discovery", "registration"])
async def test_provider_deferral_is_preserved_without_a_durable_write(operation: Operation) -> None:
    deferred = ProviderAttemptDeferred("provider_unavailable")
    boundary = Boundary(source=deferred, page=deferred)

    assert await invoke(boundary, operation) is deferred
    assert [name for name, _ in boundary.calls] == [
        "authorize",
        "discover" if operation == "discovery" else "resolve",
    ]


@pytest.mark.parametrize(
    "attempt",
    [
        replace(ATTEMPT, scope=RepositoryScope(102, 202)),
        replace(ATTEMPT, scope=RepositoryScope(101, 203)),
        replace(ATTEMPT, workflow_run_id=304),
        replace(ATTEMPT, run_attempt=3),
    ],
    ids=["installation", "repository", "run", "attempt"],
)
async def test_provider_attempt_substitution_is_rejected_before_store(
    attempt: AttemptIdentity,
) -> None:
    boundary = Boundary(source=replace(SOURCE, attempt=attempt))

    assert await invoke(boundary, "registration") == ProviderAttemptDeferred(
        "provider_binding_mismatch"
    )
    assert [name for name, _ in boundary.calls] == ["authorize", "resolve"]


@pytest.mark.parametrize(
    "page",
    [
        ProviderRunDiscoveryPage(RepositoryScope(102, 202), WINDOW, 1, 0, (), "exhausted"),
        ProviderRunDiscoveryPage(RepositoryScope(101, 203), WINDOW, 1, 0, (), "exhausted"),
        ProviderRunDiscoveryPage(
            ATTEMPT.scope, RunDiscoveryWindow(NOW, NOW), 1, 0, (), "exhausted"
        ),
        ProviderRunDiscoveryPage(ATTEMPT.scope, WINDOW, 2, 100, (), "exhausted"),
    ],
    ids=["installation", "repository", "window", "page"],
)
async def test_application_rebinds_every_provider_page_operand(
    page: ProviderRunDiscoveryPage,
) -> None:
    boundary = Boundary(page=page)

    assert await invoke(boundary, "discovery") == ProviderAttemptDeferred(
        "provider_binding_mismatch"
    )
    assert [name for name, _ in boundary.calls] == ["authorize", "discover"]


async def test_store_unavailability_is_not_success_or_provider_failure() -> None:
    boundary = Boundary(outcome=CiEconomicsStoreUnavailable())

    assert await invoke(boundary, "registration") == CiEconomicsSourceUnavailable()
    assert boundary.calls[-1] == ("register", SOURCE)


@pytest.mark.parametrize("stage", ["source", "page", "outcome"])
@pytest.mark.parametrize("failure", [asyncio.CancelledError, RuntimeError])
async def test_cancellation_and_programmer_errors_are_not_relabelled(
    stage: str,
    failure: type[BaseException],
) -> None:
    boundary = Boundary()
    if stage == "source":
        boundary.source = failure()
    elif stage == "page":
        boundary.page = failure()
    else:
        boundary.outcome = failure()

    with pytest.raises(failure):
        await invoke(boundary, "discovery" if stage == "page" else "registration")
    expected = ["authorize", "discover" if stage == "page" else "resolve"]
    if stage == "outcome":
        expected.append("register")
    assert [name for name, _ in boundary.calls] == expected


@pytest.mark.parametrize("value", [0, -1, True, "1", None, MAX_SAFE_JSON_INTEGER + 1])
@pytest.mark.parametrize("operand", ["run", "attempt"])
async def test_invalid_attempt_input_cannot_reach_authorization(
    operand: str, value: object
) -> None:
    boundary = Boundary()
    with pytest.raises(ValueError):
        await boundary.service().register_attempt(
            actor="operator",
            scope=ATTEMPT.scope,
            workflow_run_id=cast(int, value) if operand == "run" else ATTEMPT.workflow_run_id,
            run_attempt=cast(int, value) if operand == "attempt" else ATTEMPT.run_attempt,
        )
    assert boundary.calls == []


@pytest.mark.parametrize("value", [0, -1, True, "1", None, 11])
async def test_invalid_page_cannot_reach_authorization(value: object) -> None:
    boundary = Boundary()
    with pytest.raises(ValueError):
        await boundary.service().discover_page(
            actor="operator",
            scope=ATTEMPT.scope,
            window=WINDOW,
            page_number=cast(int, value),
        )
    assert boundary.calls == []


@pytest.mark.parametrize("operation", ["discovery", "registration"])
async def test_scope_must_be_the_owner_value(operation: Operation) -> None:
    boundary = Boundary()
    with pytest.raises(TypeError, match="scope"):
        if operation == "discovery":
            await boundary.service().discover_page(
                actor="operator",
                scope=cast(RepositoryScope, object()),
                window=WINDOW,
                page_number=1,
            )
        else:
            await boundary.service().register_attempt(
                actor="operator",
                scope=cast(RepositoryScope, object()),
                workflow_run_id=303,
                run_attempt=2,
            )
    assert boundary.calls == []


async def test_window_must_be_the_owner_value() -> None:
    boundary = Boundary()
    with pytest.raises(TypeError, match="window"):
        await boundary.service().discover_page(
            actor="operator",
            scope=ATTEMPT.scope,
            window=cast(RunDiscoveryWindow, object()),
            page_number=1,
        )
    assert boundary.calls == []


def test_registration_projection_rejects_invalid_owner_values() -> None:
    with pytest.raises(TypeError, match="provenance"):
        ProviderSourceRegistrationAvailable(
            cast(ProviderRunCollectionSource, ATTEMPT), "registered"
        )
    with pytest.raises(ValueError, match="outcome"):
        ProviderSourceRegistrationAvailable(
            SOURCE, cast(ProviderSourceRegistrationResult, "unknown")
        )

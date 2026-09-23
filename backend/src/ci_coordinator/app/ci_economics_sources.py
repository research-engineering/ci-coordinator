from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ci_coordinator.app.ci_economics import CiEconomicsAuthorizer
from ci_coordinator.ci_economics.discovery import (
    MAX_DISCOVERY_PAGES,
    ProviderRunDiscoveryPage,
    RunDiscoveryWindow,
)
from ci_coordinator.ci_economics.ports import (
    CiEconomicsStoreUnavailable,
    ProviderAttemptDeferred,
    ProviderSourceRegistration,
    ProviderSourceResolver,
)
from ci_coordinator.ci_economics.sources import (
    ProviderRunCollectionSource,
    ProviderSourceRegistrationResult,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER


@dataclass(frozen=True, slots=True)
class CiEconomicsSourceForbidden:
    pass


@dataclass(frozen=True, slots=True)
class CiEconomicsSourceUnavailable:
    pass


@dataclass(frozen=True, slots=True)
class ProviderSourceRegistrationAvailable:
    source: ProviderRunCollectionSource
    outcome: ProviderSourceRegistrationResult

    def __post_init__(self) -> None:
        if type(self.source) is not ProviderRunCollectionSource:
            raise TypeError("source registration requires exact provider provenance")
        if self.outcome not in {
            "registered",
            "replayed",
            "source_conflict",
            "outside_source_window",
            "capacity_reached",
        }:
            raise ValueError("source registration requires a known outcome")


type SourceDiscoveryResult = (
    ProviderRunDiscoveryPage | ProviderAttemptDeferred | CiEconomicsSourceForbidden
)
type SourceRegistrationResult = (
    ProviderSourceRegistrationAvailable
    | ProviderAttemptDeferred
    | CiEconomicsSourceForbidden
    | CiEconomicsSourceUnavailable
)


class CiEconomicsSourceUseCase(Protocol):
    async def discover_page(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        window: RunDiscoveryWindow,
        page_number: int,
    ) -> SourceDiscoveryResult: ...

    async def register_attempt(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        workflow_run_id: int,
        run_attempt: int,
    ) -> SourceRegistrationResult: ...


class CiEconomicsSourceService:
    def __init__(
        self,
        *,
        authorizer: CiEconomicsAuthorizer,
        resolver: ProviderSourceResolver,
        registration: ProviderSourceRegistration,
    ) -> None:
        self._authorizer = authorizer
        self._resolver = resolver
        self._registration = registration

    async def discover_page(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        window: RunDiscoveryWindow,
        page_number: int,
    ) -> SourceDiscoveryResult:
        _require_scope(scope)
        if type(window) is not RunDiscoveryWindow:
            raise TypeError("source discovery requires an exact creation window")
        if type(page_number) is not int or not 1 <= page_number <= MAX_DISCOVERY_PAGES:
            raise ValueError("source discovery page exceeds its bound")
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return CiEconomicsSourceForbidden()
        result = await self._resolver.discover_page(scope, window, page_number=page_number)
        if isinstance(result, ProviderAttemptDeferred):
            return result
        if (
            type(result) is not ProviderRunDiscoveryPage
            or result.scope != scope
            or result.window != window
            or result.page_number != page_number
        ):
            return ProviderAttemptDeferred("provider_binding_mismatch")
        return result

    async def register_attempt(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        workflow_run_id: int,
        run_attempt: int,
    ) -> SourceRegistrationResult:
        _require_scope(scope)
        if any(
            type(value) is not int or not 1 <= value <= MAX_SAFE_JSON_INTEGER
            for value in (workflow_run_id, run_attempt)
        ):
            raise ValueError("source registration requires exact positive attempt IDs")
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return CiEconomicsSourceForbidden()
        source = await self._resolver.resolve_attempt(scope, workflow_run_id, run_attempt)
        if isinstance(source, ProviderAttemptDeferred):
            return source
        if (
            type(source) is not ProviderRunCollectionSource
            or source.attempt.scope != scope
            or source.attempt.workflow_run_id != workflow_run_id
            or source.attempt.run_attempt != run_attempt
        ):
            return ProviderAttemptDeferred("provider_binding_mismatch")
        try:
            outcome = await self._registration.register_provider_source(source)
        except CiEconomicsStoreUnavailable:
            return CiEconomicsSourceUnavailable()
        return ProviderSourceRegistrationAvailable(source, outcome)


def _require_scope(scope: RepositoryScope) -> None:
    if type(scope) is not RepositoryScope:
        raise TypeError("economics source operation requires an exact repository scope")

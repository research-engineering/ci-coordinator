import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from ci_coordinator.app.ci_economics import (
    CiEconomicsAuthorizer,
    CiEconomicsReadForbidden,
    CiEconomicsReadUnavailable,
)
from ci_coordinator.ci_economics.analytics_configuration import (
    ConfigurePurposeSettings,
    PurposeSettingsConflict,
    PurposeSettingsQuery,
    PurposeSettingsResult,
    PurposeSettingsSaved,
    PurposeSettingsSnapshot,
)
from ci_coordinator.ci_economics.archive_analytics_models import AnalyticsUnavailable
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls.auth import RepositoryAccessUnavailable

type PurposeAccessFailure = CiEconomicsReadForbidden | CiEconomicsReadUnavailable
PURPOSE_SETTINGS_DEADLINE_SECONDS = 20
type PurposeReadOutcome = PurposeSettingsSnapshot | AnalyticsUnavailable | PurposeAccessFailure
type PurposeWriteOutcome = PurposeSettingsResult | PurposeAccessFailure


class PurposeSettingsStore(Protocol):
    async def read_settings(
        self, query: PurposeSettingsQuery
    ) -> PurposeSettingsSnapshot | AnalyticsUnavailable: ...

    async def configure_settings(
        self, command: ConfigurePurposeSettings
    ) -> PurposeSettingsResult: ...


class PurposeSettingsUseCase(Protocol):
    async def read(self, *, actor: str, query: PurposeSettingsQuery) -> PurposeReadOutcome: ...

    async def configure(self, command: ConfigurePurposeSettings) -> PurposeWriteOutcome: ...


class PurposeSettingsService:
    def __init__(self, *, authorizer: CiEconomicsAuthorizer, store: PurposeSettingsStore) -> None:
        self._authorizer = authorizer
        self._store = store

    async def read(self, *, actor: str, query: PurposeSettingsQuery) -> PurposeReadOutcome:
        query = PurposeSettingsQuery.model_validate(query)
        result = await self._authorized(
            actor, query.scope, lambda: self._store.read_settings(query)
        )
        if type(result) is PurposeSettingsSnapshot:
            result = PurposeSettingsSnapshot.model_validate(result)
            if (result.scope, result.generation) != (query.scope, query.generation):
                return CiEconomicsReadUnavailable()
            return result
        if type(result) in {
            AnalyticsUnavailable,
            CiEconomicsReadForbidden,
            CiEconomicsReadUnavailable,
        }:
            return result
        return CiEconomicsReadUnavailable()

    async def configure(self, command: ConfigurePurposeSettings) -> PurposeWriteOutcome:
        command = ConfigurePurposeSettings.model_validate(command)
        result = await self._authorized(
            command.actor, command.scope, lambda: self._store.configure_settings(command)
        )
        if type(result) is PurposeSettingsSaved:
            if result.snapshot != command.successor() or type(result.replayed) is not bool:
                return CiEconomicsReadUnavailable()
            return result
        if type(result) in {
            PurposeSettingsConflict,
            CiEconomicsReadForbidden,
            CiEconomicsReadUnavailable,
        }:
            return result
        return CiEconomicsReadUnavailable()

    async def _authorized[T](
        self, actor: str, scope: RepositoryScope, operation: Callable[[], Awaitable[T]]
    ) -> T | PurposeAccessFailure:
        try:
            async with asyncio.timeout(PURPOSE_SETTINGS_DEADLINE_SECONDS):
                allowed = await self._authorizer.allows_scope(actor=actor, scope=scope)
                if allowed is False:
                    return CiEconomicsReadForbidden()
                if allowed is not True:
                    return CiEconomicsReadUnavailable()
                return await operation()
        except (
            RepositoryAccessUnavailable,
            CiEconomicsStoreUnavailable,
            TimeoutError,
            ValueError,
            TypeError,
        ):
            return CiEconomicsReadUnavailable()

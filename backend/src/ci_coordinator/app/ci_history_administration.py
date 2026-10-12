import asyncio
from collections.abc import Awaitable, Callable
from typing import Final, Protocol

from ci_coordinator.app.ci_economics import (
    CiEconomicsAuthorizer,
    CiEconomicsReadForbidden,
    CiEconomicsReadUnavailable,
)
from ci_coordinator.ci_economics.history_administration import (
    HistoryAdministrationStore,
    HistoryRepositoryCreationReader,
    HistoryStatus,
    RepositoryCreationObserved,
)
from ci_coordinator.ci_economics.history_commands import (
    ConfigureHistory,
    HistoryConfigurationConflict,
    HistoryConfigurationInvalid,
    HistoryConfigurationResult,
    HistoryConfigured,
    HistoryPopulationSourceRefused,
)
from ci_coordinator.ci_economics.history_gap_recovery import (
    HistoryGapRepairResult,
    RepairHistoryGaps,
)
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls.auth import RepositoryAccessUnavailable

HISTORY_ADMINISTRATION_DEADLINE_SECONDS: Final = 20
type HistoryAccessFailure = CiEconomicsReadForbidden | CiEconomicsReadUnavailable
type HistoryConfigurationOutcome = HistoryConfigurationResult | HistoryAccessFailure
type HistoryStatusOutcome = HistoryStatus | HistoryAccessFailure


class CiHistoryAdministrationUseCase(Protocol):
    async def configure(self, command: ConfigureHistory) -> HistoryConfigurationOutcome: ...

    async def status(self, *, actor: str, scope: RepositoryScope) -> HistoryStatusOutcome: ...

    async def repair(
        self, command: RepairHistoryGaps
    ) -> HistoryGapRepairResult | HistoryAccessFailure: ...


class CiHistoryAdministrationService:
    def __init__(
        self,
        *,
        authorizer: CiEconomicsAuthorizer,
        store: HistoryAdministrationStore,
        creation_reader: HistoryRepositoryCreationReader,
    ) -> None:
        self._authorizer = authorizer
        self._store = store
        self._creation_reader = creation_reader

    async def configure(self, command: ConfigureHistory) -> HistoryConfigurationOutcome:
        if type(command) is not ConfigureHistory:
            raise TypeError("history configuration requires an exact command")
        command = ConfigureHistory.model_validate(command)
        if command.population_bound_source is None:
            result = await self._authorized(
                command.actor, command.scope, lambda: self._store.configure_history(command)
            )
        else:
            result = await self._authorized(
                command.actor, command.scope, lambda: self._configure_catalog_bound(command)
            )
        if type(result) in {
            CiEconomicsReadForbidden,
            CiEconomicsReadUnavailable,
            HistoryConfigurationConflict,
            HistoryConfigurationInvalid,
            HistoryPopulationSourceRefused,
        }:
            return result
        if type(result) is HistoryConfigured and (
            result.snapshot.scope == command.scope
            and result.snapshot.configuration_revision == command.expected_revision + 1
            and result.snapshot.configuration == command.configuration
        ):
            return result
        return CiEconomicsReadUnavailable()

    async def _configure_catalog_bound(
        self, command: ConfigureHistory
    ) -> HistoryConfigurationResult | CiEconomicsReadUnavailable:
        # A retained receipt classifies replay or conflict without a provider read.
        receipt = await self._store.find_history_receipt(command)
        if receipt is not None:
            return receipt
        source = command.population_bound_source
        if source is None:
            raise ValueError("catalog population verification requires its asserted source")
        observed = await self._creation_reader.read_repository_created_at(command.scope)
        if type(observed) is not RepositoryCreationObserved or observed.scope != command.scope:
            return CiEconomicsReadUnavailable()
        if observed.created_at is None:
            return HistoryPopulationSourceRefused("creation_date_missing")
        if observed.created_at != source.created_at:
            return HistoryPopulationSourceRefused("creation_date_mismatch")
        return await self._store.configure_history(command, verified_creation=observed)

    async def status(self, *, actor: str, scope: RepositoryScope) -> HistoryStatusOutcome:
        result = await self._authorized(actor, scope, lambda: self._store.history_status(scope))
        if type(result) in {CiEconomicsReadForbidden, CiEconomicsReadUnavailable}:
            return result
        if type(result) is HistoryStatus and result.scope == scope:
            return result
        return CiEconomicsReadUnavailable()

    async def repair(
        self, command: RepairHistoryGaps
    ) -> HistoryGapRepairResult | HistoryAccessFailure:
        command = RepairHistoryGaps.model_validate(command)
        result = await self._authorized(
            command.actor, command.scope, lambda: self._store.repair_history_gaps(command)
        )
        if isinstance(result, (CiEconomicsReadForbidden, CiEconomicsReadUnavailable)):
            return result
        try:
            result = HistoryGapRepairResult.model_validate(result)
        except (TypeError, ValueError):
            return CiEconomicsReadUnavailable()
        if result.operation_id != command.operation_id or (
            result.receipt is not None and result.receipt.request != command.request
        ):
            return CiEconomicsReadUnavailable()
        return result

    async def _authorized[T](
        self, actor: str, scope: RepositoryScope, operation: Callable[[], Awaitable[T]]
    ) -> T | HistoryAccessFailure:
        if type(scope) is not RepositoryScope:
            raise TypeError("history administration requires an exact scope")
        try:
            async with asyncio.timeout(HISTORY_ADMINISTRATION_DEADLINE_SECONDS):
                allowed = await self._authorizer.allows_scope(actor=actor, scope=scope)
                if allowed is False:
                    return CiEconomicsReadForbidden()
                if allowed is not True:
                    return CiEconomicsReadUnavailable()
                return await operation()
        except (RepositoryAccessUnavailable, CiEconomicsStoreUnavailable, TimeoutError):
            return CiEconomicsReadUnavailable()

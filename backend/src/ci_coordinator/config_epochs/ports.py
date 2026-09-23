from __future__ import annotations

from typing import Protocol

from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.config_epochs.contracts import (
    ActiveConfigEpochSnapshot,
    ConfigEpochActivationResult,
    ConfigEpochOperationResolution,
    ConfigEpochRegistrationResult,
    ConfigEpochReplayCommand,
    PreparedConfigEpochActivation,
)
from ci_coordinator.config_epochs.queries import ConfigEpochStatus
from ci_coordinator.config_epochs.registration import (
    ConfigEpochRegistrationOperationResult,
    PreparedConfigEpochRegistration,
)


class ConfigEpochStoreUnavailable(RuntimeError):
    """The config epoch capability cannot complete a store operation."""


class ConfigEpochRepository(Protocol):
    async def register(self, draft: ValidatedEpochDraft) -> ConfigEpochRegistrationResult:
        pass

    async def register_operation(
        self,
        prepared: PreparedConfigEpochRegistration,
    ) -> ConfigEpochRegistrationOperationResult:
        pass

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None:
        pass

    async def load_epoch(
        self,
        scope: RepositoryScope,
        epoch_id: str,
    ) -> ValidatedEpochDraft | None:
        pass

    async def read_status(
        self,
        scope: RepositoryScope,
        *,
        after_epoch_id: str | None,
        limit: int,
    ) -> ConfigEpochStatus:
        pass

    async def resolve_operation(
        self,
        command: ConfigEpochReplayCommand,
    ) -> ConfigEpochOperationResolution:
        pass

    async def activate(
        self,
        prepared: PreparedConfigEpochActivation,
    ) -> ConfigEpochActivationResult:
        pass

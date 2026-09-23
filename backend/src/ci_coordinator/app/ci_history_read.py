import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from ci_coordinator.app.ci_economics import (
    CiEconomicsAuthorizer,
    CiEconomicsReadForbidden,
    CiEconomicsReadUnavailable,
)
from ci_coordinator.app.ci_history_administration import (
    HISTORY_ADMINISTRATION_DEADLINE_SECONDS,
    HistoryAccessFailure,
)
from ci_coordinator.ci_economics.history_read import (
    HistoryReadCursor,
    HistoryReadPage,
    HistoryReadQuery,
    HistoryReadRejected,
    HistoryReadStore,
)
from ci_coordinator.ci_economics.history_read_cursor import HistoryCursorCodec, history_query_digest
from ci_coordinator.ci_economics.history_retention_commands import (
    ApplyHistoryRetention,
    HistoryRetentionPreview,
    HistoryRetentionResult,
    HistoryRetentionSelection,
    HistoryRetentionStore,
)
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls.auth import RepositoryAccessUnavailable


class HistoryProductStore(HistoryReadStore, HistoryRetentionStore, Protocol):
    pass


@dataclass(frozen=True, slots=True)
class HistoryReadResult:
    page: HistoryReadPage
    next_cursor: str | None


class CiHistoryReadUseCase(Protocol):
    async def read(
        self, *, actor: str, query: HistoryReadQuery, cursor: str | None = None
    ) -> HistoryReadResult | HistoryReadRejected | HistoryAccessFailure: ...

    async def preview(
        self, *, actor: str, selection: HistoryRetentionSelection
    ) -> HistoryRetentionPreview | HistoryReadRejected | HistoryAccessFailure: ...

    async def apply(
        self, command: ApplyHistoryRetention
    ) -> HistoryRetentionResult | HistoryAccessFailure: ...


class CiHistoryReadService:
    def __init__(
        self,
        *,
        authorizer: CiEconomicsAuthorizer,
        store: HistoryProductStore,
        cursors: HistoryCursorCodec,
    ) -> None:
        self._authorizer = authorizer
        self._store = store
        self._cursors = cursors

    async def read(
        self, *, actor: str, query: HistoryReadQuery, cursor: str | None = None
    ) -> HistoryReadResult | HistoryReadRejected | HistoryAccessFailure:
        query = HistoryReadQuery.model_validate(query)

        async def operation() -> HistoryReadResult | HistoryReadRejected:
            try:
                position = (
                    None
                    if cursor is None
                    else self._cursors.decode(cursor, query=query, actor=actor)
                )
            except ValueError:
                return HistoryReadRejected("invalid_cursor")
            result = await self._store.read_history(query, position)
            if type(result) is HistoryReadRejected:
                return result
            result = HistoryReadPage.model_validate(result)
            if result.query != query:
                raise CiEconomicsStoreUnavailable("archive response crosses query scope")
            token = (
                None
                if result.next_key is None
                else self._cursors.encode(
                    HistoryReadCursor(
                        queryDigest=history_query_digest(query, actor),
                        configurationRevision=result.configuration_revision,
                        dataRevision=result.data_revision,
                        observedAt=result.observed_at if position is None else position.observed_at,
                        key=result.next_key,
                    )
                )
            )
            return HistoryReadResult(result, token)

        return await self._authorized(actor, query.scope, operation)

    async def preview(
        self, *, actor: str, selection: HistoryRetentionSelection
    ) -> HistoryRetentionPreview | HistoryReadRejected | HistoryAccessFailure:
        selection = HistoryRetentionSelection.model_validate(selection)

        async def operation() -> HistoryRetentionPreview | HistoryReadRejected:
            result = await self._store.preview_retention(selection)
            if result is None:
                return HistoryReadRejected("stale_cursor")
            result = HistoryRetentionPreview.model_validate(result)
            if result.selection != selection:
                raise CiEconomicsStoreUnavailable("retention preview crosses selection scope")
            return result

        return await self._authorized(actor, selection.scope, operation)

    async def apply(
        self, command: ApplyHistoryRetention
    ) -> HistoryRetentionResult | HistoryAccessFailure:
        command = ApplyHistoryRetention.model_validate(command)

        async def operation() -> HistoryRetentionResult:
            result = HistoryRetentionResult.model_validate(
                await self._store.apply_retention(command)
            )
            if result.operation_id != command.operation_id or (
                result.preview is not None
                and (
                    result.preview.selection != command.selection
                    or result.preview.review_digest != command.reviewed_digest
                    or result.data_revision != command.selection.data_revision + 1
                )
            ):
                raise CiEconomicsStoreUnavailable("retention receipt crosses command scope")
            return result

        return await self._authorized(command.actor, command.selection.scope, operation)

    async def _authorized[T](
        self, actor: str, scope: RepositoryScope, operation: Callable[[], Awaitable[T]]
    ) -> T | HistoryAccessFailure:
        try:
            async with asyncio.timeout(HISTORY_ADMINISTRATION_DEADLINE_SECONDS):
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

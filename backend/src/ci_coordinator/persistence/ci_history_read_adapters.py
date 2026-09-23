from ci_coordinator.ci_economics.history_read import (
    HistoryReadCursor,
    HistoryReadPage,
    HistoryReadQuery,
    HistoryReadRejected,
)
from ci_coordinator.ci_economics.history_retention_commands import (
    ApplyHistoryRetention,
    HistoryRetentionPreview,
    HistoryRetentionResult,
    HistoryRetentionSelection,
)
from ci_coordinator.persistence.ci_history_adapters import TransactionalHistoryStore
from ci_coordinator.persistence.ci_history_read_store import read_history
from ci_coordinator.persistence.ci_history_retention_store import (
    apply_history_retention,
    preview_history_retention,
)


class TransactionalHistoryReadStore(TransactionalHistoryStore):
    async def read_history(
        self, query: HistoryReadQuery, cursor: HistoryReadCursor | None
    ) -> HistoryReadPage | HistoryReadRejected:
        async with self._transaction() as transaction:
            return await read_history(transaction.history_connection, query, cursor)

    async def preview_retention(
        self, selection: HistoryRetentionSelection
    ) -> HistoryRetentionPreview | None:
        async with self._transaction() as transaction:
            return await preview_history_retention(transaction.history_connection, selection)

    async def apply_retention(self, command: ApplyHistoryRetention) -> HistoryRetentionResult:
        async with self._transaction() as transaction:
            result = await apply_history_retention(
                transaction.history_connection, transaction.history_audit, command
            )
            if result.outcome == "committed":
                await transaction.commit()
            return result

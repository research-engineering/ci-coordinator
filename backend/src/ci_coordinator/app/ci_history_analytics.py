import asyncio
from collections.abc import Callable
from typing import Protocol

from ci_coordinator.app.ci_economics import (
    CiEconomicsAuthorizer,
    CiEconomicsReadForbidden,
    CiEconomicsReadUnavailable,
)
from ci_coordinator.ci_economics.archive_analytics import summarize_archive
from ci_coordinator.ci_economics.archive_analytics_models import (
    AnalyticsQuery,
    AnalyticsReport,
    AnalyticsSnapshot,
    AnalyticsUnavailable,
    PurposeMapping,
)
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls.auth import RepositoryAccessUnavailable

ANALYTICS_DEADLINE_SECONDS = 20
type AnalyticsOutcome = (
    AnalyticsReport | AnalyticsUnavailable | CiEconomicsReadForbidden | CiEconomicsReadUnavailable
)


class HistoryAnalyticsStore(Protocol):
    async def read_analytics(
        self, query: AnalyticsQuery, mapping: PurposeMapping | None
    ) -> AnalyticsSnapshot | AnalyticsUnavailable: ...


class CiHistoryAnalyticsUseCase(Protocol):
    async def read(self, *, actor: str, query: AnalyticsQuery) -> AnalyticsOutcome: ...


class CiHistoryAnalyticsService:
    def __init__(
        self,
        *,
        authorizer: CiEconomicsAuthorizer,
        store: HistoryAnalyticsStore,
        purpose_mapping: Callable[[RepositoryScope, int], PurposeMapping | None] | None = None,
    ) -> None:
        self._authorizer = authorizer
        self._store = store
        self._purpose_mapping = purpose_mapping

    async def read(self, *, actor: str, query: AnalyticsQuery) -> AnalyticsOutcome:
        query = AnalyticsQuery.model_validate(query)
        try:
            async with asyncio.timeout(ANALYTICS_DEADLINE_SECONDS):
                allowed = await self._authorizer.allows_scope(actor=actor, scope=query.scope)
                if allowed is False:
                    return CiEconomicsReadForbidden()
                if allowed is not True:
                    return CiEconomicsReadUnavailable()
                mapping = (
                    None
                    if self._purpose_mapping is None
                    else self._purpose_mapping(query.scope, query.generation)
                )
                if mapping is not None:
                    mapping = PurposeMapping.model_validate(mapping)
                    if (mapping.installation_id, mapping.repository_id, mapping.generation) != (
                        query.installation_id,
                        query.repository_id,
                        query.generation,
                    ):
                        return CiEconomicsReadUnavailable()
                snapshot = await self._store.read_analytics(query, mapping)
                if type(snapshot) is AnalyticsUnavailable:
                    return AnalyticsUnavailable.model_validate(snapshot)
                if type(snapshot) is not AnalyticsSnapshot:
                    return CiEconomicsReadUnavailable()
                snapshot = AnalyticsSnapshot.model_validate(snapshot)
                if snapshot.query != query or (mapping is not None and snapshot.mapping != mapping):
                    return CiEconomicsReadUnavailable()
                return summarize_archive(snapshot)
        except (
            RepositoryAccessUnavailable,
            CiEconomicsStoreUnavailable,
            TimeoutError,
            ValueError,
            TypeError,
            OverflowError,
        ):
            return CiEconomicsReadUnavailable()

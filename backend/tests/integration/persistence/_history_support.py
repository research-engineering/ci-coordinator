from ci_economics.archive_factories import ARCHIVE_TIME, archived_statistics, history_dataset
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.ci_economics.discovery import ProviderObservationPage, ProviderRunDiscoveryPage
from ci_coordinator.ci_economics.history_commands import ConfigureHistory
from ci_coordinator.ci_economics.history_scan import HistoryClaim
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.persistence.ci_history_adapters import TransactionalHistoryStore
from ci_coordinator.persistence.ci_history_unit_of_work import PostgresHistoryUnitOfWork


def history_command() -> ConfigureHistory:
    dataset = history_dataset()
    return ConfigureHistory(
        installationId=dataset.scope.installation_id,
        repositoryId=dataset.scope.repository_id,
        expectedRevision=0,
        configuration=dataset.configuration,
        initialCreatedFrom=ARCHIVE_TIME.isoformat(),
        rescan=False,
        operationId="enable-history",
        actor="operator-1",
    )


def history_store(engine: AsyncEngine) -> TransactionalHistoryStore:
    return TransactionalHistoryStore(lambda: PostgresHistoryUnitOfWork(engine))


def history_page(claim: HistoryClaim) -> ProviderObservationPage:
    cursor = claim.state.checkpoint.cursor
    statistics = archived_statistics()
    return ProviderObservationPage(
        ProviderRunDiscoveryPage(
            cursor.scope,
            cursor.window,
            cursor.page_number,
            1,
            (
                ProviderRunCollectionSource(
                    statistics.attempt.to_attempt(), ARCHIVE_TIME, "2026-03-10", "b" * 64
                ),
            ),
            "exhausted",
        ),
        (statistics.workflow_id,),
    )

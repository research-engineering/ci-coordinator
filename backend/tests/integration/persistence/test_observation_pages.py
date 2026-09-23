import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import SQLAlchemyError
from tests.integration.persistence._ci_economics_support import provider_source

from ci_coordinator.ci_economics.discovery import ProviderObservationPage, ProviderRunDiscoveryPage
from ci_coordinator.ci_economics.observation import ObservationConfiguration
from ci_coordinator.ci_economics.observation_commands import (
    ConfigureObservation,
    ObservationCommitted,
)
from ci_coordinator.ci_economics.observation_scan import ObservationClaim
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.ci_observation_adapters import TransactionalObservationStore
from ci_coordinator.persistence.ci_observation_unit_of_work import PostgresObservationUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import ci_workflow_attempt_collections as collections

pytestmark = pytest.mark.persistence
_SCOPE = RepositoryScope(101, 202)


def _command() -> ConfigureObservation:
    return ConfigureObservation(
        _SCOPE, 0, ObservationConfiguration(True, (10,), 1), "enable", "operator-1"
    )


def _page(claim: ObservationClaim) -> ProviderObservationPage:
    return ProviderObservationPage(
        ProviderRunDiscoveryPage(
            _SCOPE,
            claim.cursor.window,
            claim.cursor.page_number,
            2,
            tuple(provider_source(run, claim.cursor.window.created_from) for run in (11, 22)),
            "exhausted",
        ),
        (10, 99),
    )


@pytest.mark.parametrize("pause_first", [False, True])
def test_page_registration_respects_committed_selector_revision_and_pause(
    runtime_postgres_database_url: str, pause_first: bool
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        adapter = TransactionalObservationStore(lambda: PostgresObservationUnitOfWork(engine))
        try:
            command = _command()
            configured = await adapter.configure_observation(command)
            assert isinstance(configured, ObservationCommitted)
            claimed = await adapter.claim_observation(worker_id="a" * 64)
            assert claimed is not None and claimed.claim.lane == "recent"
            page = _page(claimed.claim)
            if pause_first:
                paused = await adapter.configure_observation(
                    replace(
                        command,
                        expected_revision=1,
                        operation_id="pause",
                        configuration=replace(command.configuration, enabled=False),
                    )
                )
                assert isinstance(paused, ObservationCommitted)
            outcome = await adapter.record_observation_page(claimed.claim, page)
            assert outcome == ("claim_lost" if pause_first else "applied")
            assert await adapter.record_observation_page(claimed.claim, page) == "claim_lost"
            status = await adapter.observation_status(_SCOPE)
            recent = next(scan for scan in status.scans if scan.state.lane == "recent")
            assert recent.sources_registered == recent.pages_seen == int(not pause_first)
            assert recent.last_completed_through == (
                None if pause_first else claimed.claim.cursor.interval.created_through
            )
            async with engine.connect() as connection:
                subjects = tuple(await connection.scalars(select(collections.c.subject_id)))
                assert subjects == (() if pause_first else (page.page.sources[0].source_id,))
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_registration_failure_preserves_claim_cursor_and_allows_one_atomic_retry(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        adapter = TransactionalObservationStore(lambda: PostgresObservationUnitOfWork(engine))
        injected = False

        def reject_source(
            _connection: object, _cursor: object, statement: str, *_args: object
        ) -> None:
            nonlocal injected
            if statement.startswith("INSERT INTO ci_coordinator.ci_workflow_attempt_collections "):
                injected = True
                raise SQLAlchemyError("injected source-page failure")

        try:
            assert isinstance(await adapter.configure_observation(_command()), ObservationCommitted)
            claimed = await adapter.claim_observation(worker_id="a" * 64)
            assert claimed is not None
            page = _page(claimed.claim)
            event.listen(engine.sync_engine, "after_cursor_execute", reject_source)
            try:
                with pytest.raises(CiEconomicsStoreUnavailable):
                    await adapter.record_observation_page(claimed.claim, page)
            finally:
                event.remove(engine.sync_engine, "after_cursor_execute", reject_source)
            assert injected
            status = await adapter.observation_status(_SCOPE)
            prior = next(scan for scan in status.scans if scan.state.lane == claimed.claim.lane)
            assert prior.state.revision == claimed.claim.expected_revision
            assert prior.state.cursor == claimed.claim.cursor
            assert prior.state.lease == claimed.claim.lease
            assert prior.pages_seen == prior.sources_registered == 0
            async with engine.connect() as connection:
                assert await connection.scalar(select(func.count()).select_from(collections)) == 0
            assert await adapter.record_observation_page(claimed.claim, page) == "applied"
            assert await adapter.record_observation_page(claimed.claim, page) == "claim_lost"
            status = await adapter.observation_status(_SCOPE)
            applied = next(scan for scan in status.scans if scan.state.lane == claimed.claim.lane)
            assert applied.pages_seen == applied.sources_registered == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())

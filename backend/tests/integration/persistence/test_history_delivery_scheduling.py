import asyncio
from dataclasses import replace

import pytest
from sqlalchemy import func, insert, select

from ci_coordinator.ci_economics.history_commands import ConfigureHistory, HistoryConfigured
from ci_coordinator.ci_economics.history_configuration import MAX_HISTORY_DATASETS
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.github_ingestion import DeliveryClaimed
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    ci_history_datasets,
    ci_history_delivery_inbox,
    ci_history_rechecks,
)

from ._ci_economics_support import database_now
from ._history_support import history_command, history_store
from ._workflow_observation_support import ingest_workflow_observation, workflow_observation

pytestmark = pytest.mark.persistence


def test_scope_listing_is_ordered_active_only_and_does_not_authorize_a_later_write(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = history_store(engine)
        command = history_command()
        try:
            assert await store.list_delivery_scopes() == ()
            for repository_id in (303, 101, 202):
                selected = ConfigureHistory.model_validate(
                    {
                        **command.model_dump(),
                        "repositoryId": repository_id,
                        "operationId": f"configure-{repository_id}",
                        "configuration": {
                            **command.configuration.model_dump(),
                            "enabled": repository_id != 202,
                        },
                    }
                )
                assert isinstance(await store.configure_history(selected), HistoryConfigured)
            first, second = await store.list_delivery_scopes()
            assert (first, second) == (
                RepositoryScope(command.installation_id, 101),
                RepositoryScope(command.installation_id, 303),
            )
            original = workflow_observation("paused-after-list", await database_now(engine))
            observation = replace(
                original,
                repository=replace(original.repository, repository_id=first.repository_id),
            )
            assert isinstance(
                await ingest_workflow_observation(engine, observation), DeliveryClaimed
            )
            paused = ConfigureHistory.model_validate(
                {
                    **command.model_dump(),
                    "repositoryId": first.repository_id,
                    "expectedRevision": 1,
                    "operationId": "pause-after-list",
                    "initialCreatedFrom": None,
                    "configuration": {**command.configuration.model_dump(), "enabled": False},
                }
            )
            assert isinstance(await store.configure_history(paused), HistoryConfigured)
            result = await store.transfer_deliveries(first)
            assert result.status == "inactive" and result.transferred_count == 0
            assert await store.list_delivery_scopes() == (second,)
            async with engine.connect() as connection:
                assert (
                    await connection.scalar(
                        select(ci_history_delivery_inbox.c.delivered_generation)
                    )
                    == 0
                )
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_rechecks))
                    == 0
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_overflowing_scope_population_is_rejected_instead_of_silently_truncated(
    runtime_postgres_database_url: str,
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        store = history_store(runtime)
        try:
            command = history_command()
            assert isinstance(await store.configure_history(command), HistoryConfigured)
            async with admin.begin() as connection:
                original = dict(
                    (await connection.execute(select(ci_history_datasets))).mappings().one()
                )
                await connection.execute(
                    insert(ci_history_datasets),
                    [
                        {**original, "repository_id": 10000 + index}
                        for index in range(MAX_HISTORY_DATASETS - 1)
                    ],
                )
            assert len(await store.list_delivery_scopes()) == MAX_HISTORY_DATASETS
            async with admin.begin() as connection:
                await connection.execute(
                    insert(ci_history_datasets), {**original, "repository_id": 20000}
                )
            with pytest.raises(CiEconomicsStoreUnavailable):
                await store.list_delivery_scopes()
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())

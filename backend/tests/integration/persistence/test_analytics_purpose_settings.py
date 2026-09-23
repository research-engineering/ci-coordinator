import asyncio

import pytest
from ci_economics.purpose_configuration_factories import purpose_command, purpose_query
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.audit_replay import AuditAppendAppended, PreparedAuditEvent
from ci_coordinator.ci_economics.analytics_configuration import (
    PURPOSE_CONFIGURED_EVENT,
    PurposeSettingsConflict,
    PurposeSettingsQuery,
    PurposeSettingsSaved,
    PurposeSettingsSnapshot,
)
from ci_coordinator.ci_economics.archive_analytics_models import (
    AnalyticsSnapshot,
    AnalyticsUnavailable,
)
from ci_coordinator.ci_economics.history_commands import HistoryConfigured
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.analytics_purpose_store import (
    TransactionalPurposeSettingsStore,
    load_purpose_settings,
)
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.ci_history_analytics import TransactionalHistoryAnalyticsStore
from ci_coordinator.persistence.ci_history_unit_of_work import PostgresPurposeUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import (
    analytics_purpose_settings,
    audit_events,
    ci_history_datasets,
)

from ._history_support import history_command, history_store
from .test_history_analytics import _query, _record, _seed

pytestmark = pytest.mark.persistence


def test_cas_concurrency_durable_lost_response_replay_and_substitution(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = TransactionalPurposeSettingsStore(lambda: PostgresPurposeUnitOfWork(engine))
        try:
            assert isinstance(
                await history_store(engine).configure_history(history_command()), HistoryConfigured
            )
            original = purpose_command()
            alternative = purpose_command(operation_id="other-operation", entries=())
            outcomes = await asyncio.gather(
                store.configure_settings(original), store.configure_settings(alternative)
            )
            saved = [result for result in outcomes if isinstance(result, PurposeSettingsSaved)]
            assert len(saved) == 1
            assert outcomes.count(PurposeSettingsConflict("revision_conflict")) == 1
            winner = original if isinstance(outcomes[0], PurposeSettingsSaved) else alternative
            restarted = TransactionalPurposeSettingsStore(lambda: PostgresPurposeUnitOfWork(engine))
            assert await restarted.configure_settings(winner) == PurposeSettingsSaved(
                winner.successor(), True
            )
            assert await restarted.configure_settings(
                type(winner).model_validate(winner.model_copy(update={"actor": "other-actor"}))
            ) == PurposeSettingsConflict("operation_conflict")
            assert await restarted.configure_settings(
                type(winner).model_validate(winner.model_copy(update={"expected_revision": 1}))
            ) == PurposeSettingsConflict("operation_conflict")
            successor = purpose_command(expected_revision=1, operation_id="next", entries=())
            assert isinstance(await store.configure_settings(successor), PurposeSettingsSaved)
            assert await restarted.configure_settings(winner) == PurposeSettingsSaved(
                winner.successor(), True
            )
            assert await store.read_settings(purpose_query()) == successor.successor()
            async with engine.connect() as connection:
                rows = (
                    (
                        await connection.execute(
                            select(audit_events).where(
                                audit_events.c.event_type == PURPOSE_CONFIGURED_EVENT.encode()
                            )
                        )
                    )
                    .mappings()
                    .all()
                )
                assert len(rows) == 2
                assert {bytes(row["actor"]).decode() for row in rows} == {winner.actor}
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_audit_failure_rolls_back_settings_and_receipt(
    runtime_postgres_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = TransactionalPurposeSettingsStore(lambda: PostgresPurposeUnitOfWork(engine))
        original = _PostgresAuditEventRepository._append_pair_owned

        async def fail_after_append(
            self: _PostgresAuditEventRepository, event: PreparedAuditEvent, scope: RepositoryScope
        ) -> object:
            assert isinstance(await original(self, event, scope), AuditAppendAppended)
            raise ValueError("injected audit transaction failure")

        try:
            assert isinstance(
                await history_store(engine).configure_history(history_command()), HistoryConfigured
            )
            with monkeypatch.context() as patch:
                patch.setattr(
                    _PostgresAuditEventRepository, "_append_pair_owned", fail_after_append
                )
                with pytest.raises(CiEconomicsStoreUnavailable):
                    await store.configure_settings(purpose_command())
            async with engine.connect() as connection:
                assert not (await connection.execute(select(analytics_purpose_settings))).all()
                assert not (
                    await connection.execute(
                        select(audit_events).where(
                            audit_events.c.event_type == PURPOSE_CONFIGURED_EVENT.encode()
                        )
                    )
                ).all()
            assert await store.configure_settings(purpose_command()) == PurposeSettingsSaved(
                purpose_command().successor(), False
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_generation_change_rejects_old_replay_and_does_not_inherit_map(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = TransactionalPurposeSettingsStore(lambda: PostgresPurposeUnitOfWork(engine))
        try:
            assert isinstance(
                await history_store(engine).configure_history(history_command()), HistoryConfigured
            )
            assert isinstance(
                await store.configure_settings(purpose_command()), PurposeSettingsSaved
            )
            async with PostgresPurposeUnitOfWork(engine) as transaction:
                await transaction.history_connection.execute(
                    update(ci_history_datasets).values(
                        generation=2, data_revision=2, configuration_revision=2
                    )
                )
                await transaction.commit()
            assert await store.configure_settings(purpose_command()) == PurposeSettingsConflict(
                "generation_changed"
            )
            assert await store.read_settings(purpose_query()) == AnalyticsUnavailable(
                reason="generation_changed"
            )
            assert await store.read_settings(
                purpose_query(generation=2)
            ) == PurposeSettingsSnapshot(
                **purpose_query(generation=2).model_dump(), revision=0, mapping=None
            )
            assert isinstance(
                await store.configure_settings(
                    purpose_command(generation=2, operation_id="generation-two")
                ),
                PurposeSettingsSaved,
            )
            assert await store.read_settings(
                purpose_query(repository_id=999)
            ) == AnalyticsUnavailable(reason="dataset_unavailable")
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_missing_empty_and_hot_applied_named_classification(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = TransactionalPurposeSettingsStore(lambda: PostgresPurposeUnitOfWork(engine))
        analytics = TransactionalHistoryAnalyticsStore(lambda: PostgresPurposeUnitOfWork(engine))
        try:
            await _seed(engine, (_record(1),))
            assert await analytics.read_analytics(_query(purpose="lint")) == AnalyticsUnavailable(
                reason="purpose_mapping_unavailable"
            )
            empty = purpose_command(entries=())
            assert isinstance(await store.configure_settings(empty), PurposeSettingsSaved)
            no_match = await analytics.read_analytics(_query(purpose="lint"))
            assert isinstance(no_match, AnalyticsSnapshot)
            assert no_match.mapping is not None and no_match.buckets[0].selected.jobs == 0
            mapped = purpose_command(expected_revision=1, operation_id="named")
            assert isinstance(await store.configure_settings(mapped), PurposeSettingsSaved)
            selected = await analytics.read_analytics(_query(purpose="lint"))
            assert isinstance(selected, AnalyticsSnapshot)
            assert selected.mapping == mapped.successor().mapping
            assert selected.buckets[0].selected.jobs == 1
            assert selected.buckets[0].selected.unknown_purpose_jobs == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_mapping_change_during_aggregate_read_cannot_return_mixed_revision(
    runtime_postgres_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ci_coordinator.persistence.ci_history_analytics as adapter

    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        store = TransactionalPurposeSettingsStore(lambda: PostgresPurposeUnitOfWork(engine))
        calls = 0

        async def interleave(
            connection: AsyncConnection, query: PurposeSettingsQuery
        ) -> PurposeSettingsSnapshot:
            nonlocal calls
            calls += 1
            snapshot = await load_purpose_settings(connection, query)
            if calls == 1:
                assert isinstance(
                    await store.configure_settings(
                        purpose_command(expected_revision=1, operation_id="concurrent", entries=())
                    ),
                    PurposeSettingsSaved,
                )
            return snapshot

        try:
            await _seed(engine, (_record(1),))
            assert isinstance(
                await store.configure_settings(purpose_command()), PurposeSettingsSaved
            )
            # noinspection PyUnresolvedReferences
            monkeypatch.setattr(adapter, "load_purpose_settings", interleave)
            result = await TransactionalHistoryAnalyticsStore(
                lambda: PostgresPurposeUnitOfWork(engine)
            ).read_analytics(_query(purpose="lint"))
            assert result == AnalyticsUnavailable(reason="snapshot_changed")
            assert calls == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from ci_economics.archive_factories import (
    archived_detail,
    archived_statistics,
    history_dataset,
    history_scan,
)
from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection
from tests.integration.persistence._temporal_lock_support import wait_for_database_deadline

from ci_coordinator.ci_economics.archive_detail import (
    encode_archive_detail,
    validate_history_detail,
)
from ci_coordinator.ci_economics.archive_retention import (
    ArchiveDetailRetention,
    DetailPolicyReference,
    DetailRetentionPolicy,
)
from ci_coordinator.ci_economics.history_commands import ConfigureHistory, HistoryConfigured
from ci_coordinator.ci_economics.history_configuration import HistoryDataset, HistoryUsage
from ci_coordinator.ci_economics.history_scan import (
    HistoryClaim,
    HistoryScanState,
    current_history_claim,
)
from ci_coordinator.ci_economics.observation_scan import ObservationLease
from ci_coordinator.persistence import ci_history_completion
from ci_coordinator.persistence._schema_ci_history_archive import (
    ci_history_attempts,
    ci_history_details,
    ci_history_jobs,
)
from ci_coordinator.persistence._schema_ci_history_control import (
    ci_history_datasets,
    ci_history_scans,
)
from ci_coordinator.persistence.ci_history_adapters import TransactionalHistoryStore
from ci_coordinator.persistence.ci_history_codec import encode_archive_statistics
from ci_coordinator.persistence.ci_history_control_codec import (
    encode_history_dataset,
    encode_history_scan,
)
from ci_coordinator.persistence.ci_history_retention_codec import (
    decode_history_retention,
    encode_history_retention,
)
from ci_coordinator.persistence.ci_history_state_store import (
    history_database_time,
    load_history_dataset,
    load_history_scan,
    write_history_scan,
)
from ci_coordinator.persistence.connection import create_postgres_engine

from ._history_support import history_command, history_page, history_store

pytestmark = pytest.mark.persistence


def _command(
    *,
    operation_id: str,
    detail_retention: dict[str, object] | None = None,
    canonical_bytes: int | None = None,
) -> ConfigureHistory:
    base = history_command()
    configuration = base.configuration.model_dump(mode="json", by_alias=True)
    if detail_retention is not None:
        configuration["detailRetention"] = detail_retention
    if canonical_bytes is not None:
        quota = dict(configuration["quota"])
        quota["canonicalBytes"] = canonical_bytes
        configuration["quota"] = quota
    return ConfigureHistory.model_validate(
        {
            **base.model_dump(mode="json", by_alias=True),
            "configuration": configuration,
            "operationId": operation_id,
        }
    )


async def _claim_attempt(
    store: TransactionalHistoryStore, command: ConfigureHistory
) -> tuple[HistoryDataset, HistoryClaim]:
    configured = await store.configure_history(command)
    assert isinstance(configured, HistoryConfigured)
    page_claim = await store.claim_history(worker_id="a" * 64)
    assert page_claim is not None
    assert await store.record_history_page(page_claim[1], history_page(page_claim[1])) == "applied"
    attempt_claim = await store.claim_history(worker_id="a" * 64)
    assert attempt_claim is not None
    return attempt_claim


def test_detail_import_disabled_preserves_summary_only_statistics(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = history_store(runtime)
            command = _command(
                operation_id="disabled-detail",
                detail_retention={"mode": "disabled"},
            )
            dataset, claim = await _claim_attempt(store, command)
            statistics = archived_statistics()
            assert (
                await store.record_history_statistics(claim, statistics, detail=archived_detail())
                == "applied"
            )
            async with runtime.connect() as connection:
                row = (await connection.execute(select(ci_history_attempts))).mappings().one()
                assert row["detail_state"] == "not_imported"
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_details))
                    == 0
                )
                current = await load_history_dataset(connection, dataset.scope)
                assert current is not None and current.usage.canonical_bytes > 0
        finally:
            await runtime.dispose()

    asyncio.run(scenario())


def test_detail_replay_imports_first_sidecar_once_and_preserves_anchor(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = history_store(runtime)
            command = _command(operation_id="replay-detail", detail_retention={"mode": "disabled"})
            dataset, claim = await _claim_attempt(store, command)
            statistics = archived_statistics()
            detail = archived_detail()
            raw_detail = encode_archive_detail(detail)
            assert await store.record_history_statistics(claim, statistics) == "applied"
            async with runtime.connect() as connection:
                before = (await connection.execute(select(ci_history_attempts))).mappings().one()
                before_jobs = (await connection.execute(select(ci_history_jobs))).all()
                before_dataset = await load_history_dataset(connection, dataset.scope)
                assert before_dataset is not None
                assert decode_history_retention(before) == ArchiveDetailRetention()
                assert before_dataset.configuration.detail_retention is not None
                assert before_dataset.configuration.detail_retention.mode == "disabled"
                assert not (await connection.execute(select(ci_history_details))).all()
                assert before_dataset.usage == HistoryUsage(
                    attempts=1,
                    jobs=len(statistics.jobs),
                    gaps=0,
                    canonicalBytes=encode_archive_statistics(
                        statistics, generation=dataset.generation
                    ).canonical_bytes,
                )
            rescan = _command(operation_id="replay-detail-rescan")
            rescan = rescan.model_copy(
                update={"expected_revision": 1, "initial_created_from": None, "rescan": True}
            )
            enabled, replay_claim = await _claim_attempt(store, rescan)
            assert enabled.configuration.detail_retention is not None
            assert (
                enabled.configuration.detail_retention.to_policy()
                == DetailRetentionPolicy.default()
            )
            assert enabled.usage == before_dataset.usage
            async with runtime.connect() as connection:
                assert (
                    await connection.execute(select(ci_history_attempts))
                ).mappings().one() == before
                lower_bound = await history_database_time(connection)
            assert (
                await store.record_history_statistics(replay_claim, statistics, detail=detail)
                == "applied"
            )
            async with runtime.connect() as connection:
                after = (await connection.execute(select(ci_history_attempts))).mappings().one()
                after_dataset = await load_history_dataset(connection, dataset.scope)
                assert after_dataset is not None
                retention = decode_history_retention(after)
                anchor = retention.first_imported_at
                assert anchor is not None
                assert lower_bound <= anchor <= await history_database_time(connection)
                assert retention == ArchiveDetailRetention(
                    "retained",
                    anchor,
                    DetailRetentionPolicy.default(),
                    DetailPolicyReference("repository_override", 2),
                )
                assert {k: v for k, v in after.items() if not k.startswith("detail_")} == {
                    k: v for k, v in before.items() if not k.startswith("detail_")
                }
                assert (await connection.execute(select(ci_history_jobs))).all() == before_jobs
                child = (await connection.execute(select(ci_history_details))).mappings().one()
                assert dict(child) == {
                    "installation_id": statistics.attempt.installation_id,
                    "repository_id": statistics.attempt.repository_id,
                    "generation": dataset.generation,
                    "workflow_run_id": statistics.attempt.workflow_run_id,
                    "run_attempt": statistics.attempt.run_attempt,
                    "detail_canonical": raw_detail,
                }
                assert after_dataset.usage == HistoryUsage(
                    attempts=before_dataset.usage.attempts,
                    jobs=before_dataset.usage.jobs,
                    gaps=before_dataset.usage.gaps,
                    canonicalBytes=before_dataset.usage.canonical_bytes + len(raw_detail),
                )
                assert after_dataset.data_revision == before_dataset.data_revision + 1
                scan = await load_history_scan(connection, dataset.scope)
                assert scan is not None and scan.last_outcome == "replayed"
            third_rescan = rescan.model_copy(
                update={"expected_revision": 2, "operation_id": "replay-detail-third"}
            )
            _, third_claim = await _claim_attempt(store, third_rescan)
            async with runtime.connect() as connection:
                assert await history_database_time(connection) > anchor
            contradictory = archived_detail(conclusion="failure")
            assert validate_history_detail(statistics, contradictory) == contradictory
            different_bytes = encode_archive_detail(contradictory)
            assert different_bytes != raw_detail and len(different_bytes) == len(raw_detail)
            assert (
                await store.record_history_statistics(third_claim, statistics, detail=contradictory)
                == "applied"
            )
            async with runtime.connect() as connection:
                assert (
                    await connection.execute(select(ci_history_attempts))
                ).mappings().one() == after
                assert (
                    await connection.execute(select(ci_history_details))
                ).mappings().one() == child
                assert (await connection.execute(select(ci_history_jobs))).all() == before_jobs
                final_dataset = await load_history_dataset(connection, dataset.scope)
                assert final_dataset is not None
                assert final_dataset.usage == after_dataset.usage
                assert final_dataset.data_revision == after_dataset.data_revision
                scan = await load_history_scan(connection, dataset.scope)
                assert scan is not None and scan.last_outcome == "replayed"
        finally:
            await runtime.dispose()

    asyncio.run(scenario())


def test_detail_quota_refusal_rolls_back_statistics_detail_and_cursor(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        statistics = archived_statistics()
        detail = archived_detail()
        quota = (
            encode_archive_statistics(statistics, generation=1).canonical_bytes
            + len(encode_archive_detail(detail))
            - 1
        )
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = history_store(runtime)
            command = _command(operation_id="detail-quota", canonical_bytes=quota)
            _, claim = await _claim_attempt(store, command)
            assert (
                await store.record_history_statistics(claim, statistics, detail=detail)
                == "capacity_reached"
            )
            async with runtime.connect() as connection:
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_attempts))
                    == 0
                )
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_jobs)) == 0
                )
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_details))
                    == 0
                )
                current = await load_history_dataset(connection, command.scope)
                scan = await load_history_scan(connection, command.scope)
                assert current is not None and current.usage.canonical_bytes == 0
                assert scan is not None and scan.checkpoint == claim.state.checkpoint
        finally:
            await runtime.dispose()

    asyncio.run(scenario())


def test_detail_import_rolls_back_before_expired_terminal_lease_cas(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = history_store(runtime)
            dataset = history_dataset()
            async with admin.begin() as connection:
                await connection.execute(
                    insert(ci_history_datasets).values(**encode_history_dataset(dataset))
                )
                await connection.execute(
                    insert(ci_history_scans).values(
                        **encode_history_scan(history_scan(dataset, days=1))
                    )
                )
            page_claim = await store.claim_history(worker_id="a" * 64)
            assert page_claim is not None
            assert (
                await store.record_history_page(page_claim[1], history_page(page_claim[1]))
                == "applied"
            )
            claimed = await store.claim_history(worker_id="a" * 64)
            assert claimed is not None and claimed[1].state.lease is not None
            lease = claimed[1].state.lease
            async with admin.connect() as clock:
                shift = lease.expires_at - await history_database_time(clock) - timedelta(seconds=4)
            lease = replace(
                lease,
                acquired_at=lease.acquired_at - shift,
                expires_at=lease.expires_at - shift,
            )
            claim = HistoryClaim(
                replace(
                    claimed[1].state,
                    lease=lease,
                    next_attempt_at=claimed[1].state.next_attempt_at - shift,
                )
            )
            async with admin.begin() as connection:
                await connection.execute(
                    update(ci_history_scans)
                    .where(
                        ci_history_scans.c.installation_id == dataset.scope.installation_id,
                        ci_history_scans.c.repository_id == dataset.scope.repository_id,
                        ci_history_scans.c.lane == "backfill",
                    )
                    .values(**encode_history_scan(claim.state))
                )
            async with runtime.connect() as connection:
                stored = await load_history_scan(connection, dataset.scope)
                now = await history_database_time(connection)
                assert stored == claim.state
                assert (
                    dataset.configured_at
                    <= claim.state.checkpoint.cursor.cycle_started_at
                    <= claim.state.next_attempt_at
                    <= lease.acquired_at
                    <= now
                    < lease.expires_at
                )
                assert lease.expires_at - lease.acquired_at == timedelta(seconds=60)
                assert current_history_claim(dataset, claim.state, claim, now)
            real_write = write_history_scan
            intercepted = False

            async def delayed_terminal_cas(
                connection: AsyncConnection,
                prior: HistoryScanState,
                successor: HistoryScanState,
                *,
                live_lease: ObservationLease | None = None,
            ) -> None:
                nonlocal intercepted
                intercepted = True
                assert prior == claim.state and live_lease == lease
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_details))
                    == 1
                )
                await wait_for_database_deadline(admin, lease.expires_at)
                await real_write(connection, prior, successor, live_lease=live_lease)

            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(ci_history_completion, "write_history_scan", delayed_terminal_cas)
                result = await store.record_history_statistics(
                    claim, archived_statistics(), detail=archived_detail()
                )
            assert intercepted and result == "claim_lost"
            async with runtime.connect() as connection:
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_attempts))
                    == 0
                )
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_details))
                    == 0
                )
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())


def test_expired_detail_identity_is_terminal_and_rescan_does_not_resurrect(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            store = history_store(runtime)
            command = _command(
                operation_id="detail-expiry",
                detail_retention={
                    "mode": "days",
                    "days": 1,
                    "anchor": "first_successful_detail_import",
                },
            )
            _dataset, claim = await _claim_attempt(store, command)
            detail = archived_detail()
            assert (
                await store.record_history_statistics(claim, archived_statistics(), detail=detail)
                == "applied"
            )
            async with admin.begin() as connection:
                now = await history_database_time(connection)
                prior = ArchiveDetailRetention(
                    "retained",
                    now - timedelta(days=2),
                    DetailRetentionPolicy("days", 1),
                    DetailPolicyReference("repository_override", 1),
                )
                await connection.execute(
                    update(ci_history_attempts).values(**encode_history_retention(prior))
                )
            assert await store.expire_details() == 1
            async with runtime.connect() as connection:
                expired = (await connection.execute(select(ci_history_attempts))).mappings().one()
                anchor = expired["detail_first_imported_at"]
                assert expired["detail_state"] == "expired"
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_details))
                    == 0
                )
            rescan = _command(operation_id="detail-expiry-rescan")
            rescan = rescan.model_copy(
                update={"expected_revision": 1, "initial_created_from": None, "rescan": True}
            )
            _, replay_claim = await _claim_attempt(store, rescan)
            assert (
                await store.record_history_statistics(
                    replay_claim, archived_statistics(), detail=detail
                )
                == "applied"
            )
            async with runtime.connect() as connection:
                after = (await connection.execute(select(ci_history_attempts))).mappings().one()
                assert after["detail_state"] == "expired"
                assert after["detail_first_imported_at"] == anchor
                assert (
                    await connection.scalar(select(func.count()).select_from(ci_history_details))
                    == 0
                )
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())

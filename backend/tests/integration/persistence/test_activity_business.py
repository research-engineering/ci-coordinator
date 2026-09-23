import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest
from control_plane_http_support import human_principal
from sqlalchemy import func, select
from tests.integration.persistence.test_history_detail_cleanup import _seed_details
from tests.integration.persistence.test_history_retention_product import _selection

from ci_coordinator.ci_economics.history_retention_commands import (
    HISTORY_RETENTION_EVENT,
    ApplyHistoryRetention,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity.activity_cursor import ActivityCursorCodec
from ci_coordinator.control_plane_identity.activity_query import ActivityQuery
from ci_coordinator.persistence.activity_repository import PostgresActivityStore
from ci_coordinator.persistence.ci_history_read_adapters import TransactionalHistoryReadStore
from ci_coordinator.persistence.ci_history_state_store import history_database_time
from ci_coordinator.persistence.ci_history_unit_of_work import PostgresHistoryUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import activity_events, audit_events

pytestmark = pytest.mark.persistence


def test_retention_activity_projects_one_committed_pair_without_copy_or_cross_scope_read(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        admin = create_postgres_engine(postgres_database_url)
        runtime = create_postgres_engine(runtime_postgres_database_url)
        try:
            dataset = await _seed_details(admin, runtime, due=False)
            async with admin.connect() as connection:
                now = await history_database_time(connection)
            principal = replace(human_principal(), expires_at=now + timedelta(minutes=5))
            selection = _selection(dataset, now.isoformat())
            retention = TransactionalHistoryReadStore(lambda: PostgresHistoryUnitOfWork(runtime))
            preview = await retention.preview_retention(selection)
            assert preview is not None
            command = ApplyHistoryRetention(
                selection=selection,
                reviewedDigest=preview.review_digest,
                operationId="activity-retention",
                actor=principal.actor_id,
            )
            assert (await retention.apply_retention(command)).outcome == "committed"
            assert (await retention.apply_retention(command)).outcome == "replayed"
            async with admin.connect() as connection:
                before = (
                    await connection.execute(select(audit_events).order_by(audit_events.c.sequence))
                ).all()
            store = PostgresActivityStore(runtime, ActivityCursorCodec(b"a" * 32))
            query = ActivityQuery(
                "business",
                (now - timedelta(days=1)).replace(microsecond=0),
                (now + timedelta(minutes=1)).replace(microsecond=0),
                scope=dataset.scope,
                action=HISTORY_RETENTION_EVENT,
            )
            page = await store.page(query, principal, None)
            assert len(page.items) == 1
            entry = page.items[0]
            assert entry.action == HISTORY_RETENTION_EVENT and entry.outcome == "committed"
            assert entry.actor == principal.actor_id
            assert entry.issuer is None and entry.subject is None
            assert entry.operation_ref.startswith("sha256:")
            assert entry.audit_event_id is not None and entry.event_hash is not None
            assert page.integrity == "audit_reference_only"
            assert (
                await store.page(
                    replace(
                        query,
                        scope=RepositoryScope(
                            dataset.scope.installation_id, dataset.scope.repository_id + 1
                        ),
                    ),
                    principal,
                    None,
                )
            ).items == ()
            async with admin.connect() as connection:
                assert (
                    await connection.execute(select(audit_events).order_by(audit_events.c.sequence))
                ).all() == before
                assert (
                    await connection.scalar(select(func.count()).select_from(activity_events)) == 0
                )
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())

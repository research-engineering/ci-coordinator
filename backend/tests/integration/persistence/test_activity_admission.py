import asyncio

import pytest
from sqlalchemy import text

from ci_coordinator.control_plane_identity.activity import ActivityUnavailable
from ci_coordinator.control_plane_identity.activity_cursor import ActivityCursorCodec
from ci_coordinator.persistence.activity_repository import PostgresActivityStore
from ci_coordinator.persistence.connection import create_postgres_engine

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize(
    ("damage", "repair"),
    [
        (
            "ALTER TABLE ci_coordinator.activity_events ALTER COLUMN issuer DROP NOT NULL",
            "ALTER TABLE ci_coordinator.activity_events ALTER COLUMN issuer SET NOT NULL",
        ),
        (
            "DROP INDEX ci_coordinator.ix_activity_retention",
            "CREATE INDEX ix_activity_retention ON ci_coordinator.activity_events "
            "(retain_until, sequence)",
        ),
        (
            "ALTER SEQUENCE ci_coordinator.activity_events_sequence_seq INCREMENT BY 2",
            "ALTER SEQUENCE ci_coordinator.activity_events_sequence_seq INCREMENT BY 1",
        ),
        (
            "GRANT UPDATE ON ci_coordinator.activity_events TO ci_coordinator_runtime_test",
            "REVOKE UPDATE ON ci_coordinator.activity_events FROM ci_coordinator_runtime_test",
        ),
        (
            "REVOKE INSERT ON ci_coordinator.activity_events FROM ci_coordinator_runtime_test",
            "GRANT INSERT ON ci_coordinator.activity_events TO ci_coordinator_runtime_test",
        ),
        (
            "REVOKE USAGE ON SEQUENCE ci_coordinator.activity_events_sequence_seq "
            "FROM ci_coordinator_runtime_test",
            "GRANT USAGE ON SEQUENCE ci_coordinator.activity_events_sequence_seq "
            "TO ci_coordinator_runtime_test",
        ),
        (
            "GRANT UPDATE ON SEQUENCE ci_coordinator.activity_events_sequence_seq "
            "TO ci_coordinator_runtime_test",
            "REVOKE UPDATE ON SEQUENCE ci_coordinator.activity_events_sequence_seq "
            "FROM ci_coordinator_runtime_test",
        ),
    ],
)
def test_current_runtime_rejects_activity_schema_or_acl_loss(
    postgres_database_url: str, runtime_postgres_database_url: str, damage: str, repair: str
) -> None:
    async def scenario() -> None:
        runtime = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        store = PostgresActivityStore(runtime, ActivityCursorCodec(b"a" * 32))
        try:
            await store.cleanup()
            async with admin.begin() as connection:
                await connection.execute(text(damage))
            try:
                with pytest.raises(ActivityUnavailable):
                    await store.cleanup()
                with pytest.raises(ActivityUnavailable):
                    await store.login_diagnostic("login_rejected")
            finally:
                async with admin.begin() as connection:
                    await connection.execute(text(repair))
            await store.cleanup()
        finally:
            await runtime.dispose()
            await admin.dispose()

    asyncio.run(scenario())

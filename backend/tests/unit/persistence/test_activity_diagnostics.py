import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal, cast
from unittest.mock import AsyncMock, Mock, call

import pytest
from control_plane_http_support import human_principal
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ci_coordinator.control_plane_identity.activity import ActivityUnavailable
from ci_coordinator.control_plane_identity.activity_cursor import ActivityCursorCodec
from ci_coordinator.persistence import activity_repository, activity_write
from ci_coordinator.persistence.activity_repository import PostgresActivityStore


@pytest.mark.parametrize("action", [None, "role_denied", "export"])
def test_diagnostic_kind_selects_only_its_cleanup_and_ordering(
    monkeypatch: pytest.MonkeyPatch, action: Literal["role_denied", "export"] | None
) -> None:
    connection = AsyncMock(spec=AsyncConnection)

    @asynccontextmanager
    async def transaction(*_args: object) -> AsyncIterator[AsyncConnection]:
        yield cast(AsyncConnection, connection)

    monkeypatch.setattr(activity_repository, "_identity_transaction", transaction)
    operations = Mock()
    for name in (
        "lock_activity",
        "cleanup_activity",
        "cleanup_diagnostic_buckets",
        "record_diagnostic_count",
        "record_principal_diagnostic",
    ):
        operation = AsyncMock()
        operations.attach_mock(operation, name)
        monkeypatch.setattr(activity_repository, name, operation)
    store = PostgresActivityStore(cast(AsyncEngine, object()), ActivityCursorCodec(b"k" * 32))
    principal = human_principal()
    if action is None:
        asyncio.run(store.login_diagnostic("login_rejected"))
        expected = [
            call.cleanup_diagnostic_buckets(connection),
            call.record_diagnostic_count(connection, "login_rejected"),
        ]
    else:
        asyncio.run(store.principal_diagnostic(principal, action))
        expected = [
            call.lock_activity(connection),
            call.cleanup_activity(connection),
            call.record_principal_diagnostic(connection, principal, action),
        ]
    assert operations.mock_calls == expected
    assert [str(args.args[0]) for args in connection.execute.await_args_list] == [
        "SET LOCAL statement_timeout = '250ms'",
        "SET LOCAL lock_timeout = '100ms'",
    ]


@pytest.mark.parametrize("cancelled", [False, True])
def test_failed_anonymous_counter_releases_its_diagnostic_slot(
    monkeypatch: pytest.MonkeyPatch, cancelled: bool
) -> None:
    connection = AsyncMock(spec=AsyncConnection)

    @asynccontextmanager
    async def transaction(*_args: object) -> AsyncIterator[AsyncConnection]:
        yield cast(AsyncConnection, connection)

    monkeypatch.setattr(activity_repository, "_identity_transaction", transaction)
    monkeypatch.setattr(activity_repository, "cleanup_diagnostic_buckets", AsyncMock())
    error = asyncio.CancelledError() if cancelled else SQLAlchemyError("counter unavailable")
    counter = AsyncMock(side_effect=error)
    monkeypatch.setattr(activity_repository, "record_diagnostic_count", counter)
    store = PostgresActivityStore(cast(AsyncEngine, object()), ActivityCursorCodec(b"k" * 32))

    async def scenario() -> None:
        for _ in range(3):
            with pytest.raises(asyncio.CancelledError if cancelled else ActivityUnavailable):
                await store.login_diagnostic("login_unavailable")

    asyncio.run(scenario())
    assert counter.await_count == 3


def test_full_cleanup_reuses_bucket_cleanup_without_changing_event_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = AsyncMock(spec=AsyncConnection)
    connection.scalar.return_value = 128
    buckets = AsyncMock()
    monkeypatch.setattr(activity_write, "cleanup_diagnostic_buckets", buckets)
    assert asyncio.run(activity_write.cleanup_activity(cast(AsyncConnection, connection))) == 128
    buckets.assert_awaited_once_with(connection)

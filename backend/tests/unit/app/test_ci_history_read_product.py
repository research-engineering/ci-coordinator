import asyncio
from unittest.mock import AsyncMock

import pytest
from ci_economics.archive_factories import ARCHIVE_TIME

from ci_coordinator.app.ci_economics import CiEconomicsReadForbidden, CiEconomicsReadUnavailable
from ci_coordinator.app.ci_history_read import (
    CiHistoryReadService,
    HistoryProductStore,
    HistoryReadResult,
)
from ci_coordinator.ci_economics.history_read import (
    HistoryReadPage,
    HistoryReadQuery,
    HistoryReadRejected,
)
from ci_coordinator.ci_economics.history_read_cursor import HistoryCursorCodec
from ci_coordinator.ci_economics.history_retention_commands import (
    ApplyHistoryRetention,
    HistoryRetentionSelection,
)


def _query(repository: int = 202) -> HistoryReadQuery:
    return HistoryReadQuery(
        installationId=101, repositoryId=repository, generation=1, kind="records"
    )


@pytest.mark.parametrize("allowed", [False, None, 0, 1, "yes"])
async def test_access_denied_or_unknown_never_reads_archive(allowed: object) -> None:
    authorizer, store = AsyncMock(), AsyncMock(spec=HistoryProductStore)
    authorizer.allows_scope.return_value = allowed
    service = CiHistoryReadService(
        authorizer=authorizer,
        store=store,
        cursors=HistoryCursorCodec(b"test-only-archive-cursor-key-value"),
    )
    result = await service.read(actor="operator", query=_query())
    assert type(result) is (
        CiEconomicsReadForbidden if allowed is False else CiEconomicsReadUnavailable
    )
    assert not store.mock_calls
    authorizer.allows_scope.assert_awaited_once_with(actor="operator", scope=_query().scope)


async def test_store_cannot_return_another_repository_or_forged_cursor() -> None:
    authorizer, store = AsyncMock(), AsyncMock(spec=HistoryProductStore)
    authorizer.allows_scope.return_value = True
    store.read_history.return_value = HistoryReadPage(
        query=_query(203), configurationRevision=1, dataRevision=1, observedAt=ARCHIVE_TIME
    )
    service = CiHistoryReadService(
        authorizer=authorizer,
        store=store,
        cursors=HistoryCursorCodec(b"test-only-archive-cursor-key-value"),
    )
    assert isinstance(
        await service.read(actor="operator", query=_query()), CiEconomicsReadUnavailable
    )
    store.reset_mock()
    assert await service.read(
        actor="operator", query=_query(), cursor="forged"
    ) == HistoryReadRejected("invalid_cursor")
    assert not store.mock_calls


async def test_retained_read_does_not_request_upstream_source_or_invent_activity() -> None:
    authorizer, store = AsyncMock(), AsyncMock(spec=HistoryProductStore)
    authorizer.allows_scope.return_value = True
    page = HistoryReadPage(
        query=_query(), configurationRevision=1, dataRevision=1, observedAt=ARCHIVE_TIME
    )
    store.read_history.return_value = page
    service = CiHistoryReadService(
        authorizer=authorizer,
        store=store,
        cursors=HistoryCursorCodec(b"test-only-archive-cursor-key-value"),
    )
    result = await service.read(actor="operator", query=_query())
    assert result == HistoryReadResult(page, None)
    store.read_history.assert_awaited_once_with(_query(), None)


async def test_cancellation_propagates_to_store_without_detached_read() -> None:
    entered, cancelled = asyncio.Event(), asyncio.Event()
    authorizer, store = AsyncMock(), AsyncMock(spec=HistoryProductStore)
    authorizer.allows_scope.return_value = True

    async def held(*_args: object) -> None:
        entered.set()
        try:
            await asyncio.Future[None]()
        finally:
            cancelled.set()

    store.read_history.side_effect = held
    service = CiHistoryReadService(
        authorizer=authorizer,
        store=store,
        cursors=HistoryCursorCodec(b"test-only-archive-cursor-key-value"),
    )
    async with asyncio.timeout(3):
        task = asyncio.create_task(service.read(actor="operator", query=_query()))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await cancelled.wait()


@pytest.mark.parametrize("allowed", [False, None, 1])
async def test_retention_reads_and_writes_recheck_exact_repository_authority(
    allowed: object,
) -> None:
    authorizer, store = AsyncMock(), AsyncMock(spec=HistoryProductStore)
    authorizer.allows_scope.return_value = allowed
    service = CiHistoryReadService(
        authorizer=authorizer,
        store=store,
        cursors=HistoryCursorCodec(b"test-only-archive-cursor-key-value"),
    )
    selection = HistoryRetentionSelection.model_validate(
        {
            "installationId": 101,
            "repositoryId": 202,
            "generation": 1,
            "configurationRevision": 1,
            "dataRevision": 1,
            "defaultRevision": 1,
            "importedThrough": ARCHIVE_TIME.isoformat(),
            "action": "erase_details",
            "keys": [{"workflowRunId": 303, "runAttempt": 1}],
        }
    )
    result = await service.preview(actor="operator", selection=selection)
    expected = CiEconomicsReadForbidden if allowed is False else CiEconomicsReadUnavailable
    assert type(result) is expected
    applied = await service.apply(
        ApplyHistoryRetention(
            selection=selection, reviewedDigest="a" * 64, operationId="apply", actor="operator"
        )
    )
    assert type(applied) is expected and not store.mock_calls

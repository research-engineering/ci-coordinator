import asyncio
from unittest.mock import AsyncMock

import pytest
from ci_economics.purpose_configuration_factories import purpose_command, purpose_query

from ci_coordinator.app.analytics_configuration import PurposeSettingsService, PurposeSettingsStore
from ci_coordinator.app.ci_economics import (
    CiEconomicsAuthorizer,
    CiEconomicsReadForbidden,
    CiEconomicsReadUnavailable,
)
from ci_coordinator.ci_economics.analytics_configuration import PurposeSettingsSaved
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable


@pytest.mark.parametrize("allowed", [False, None, 1])
def test_denied_scope_cannot_read_write_or_replay(allowed: object) -> None:
    authorizer = AsyncMock(spec=CiEconomicsAuthorizer)
    authorizer.allows_scope.return_value = allowed
    store = AsyncMock(spec=PurposeSettingsStore)
    service = PurposeSettingsService(authorizer=authorizer, store=store)
    expected = CiEconomicsReadForbidden if allowed is False else CiEconomicsReadUnavailable
    assert isinstance(
        asyncio.run(service.read(actor="operator-1", query=purpose_query())), expected
    )
    assert isinstance(asyncio.run(service.configure(purpose_command())), expected)
    store.read_settings.assert_not_awaited()
    store.configure_settings.assert_not_awaited()


def test_current_authorization_is_rechecked_before_duplicate_and_foreign_snapshot_rejected() -> (
    None
):
    authorizer = AsyncMock(spec=CiEconomicsAuthorizer)
    authorizer.allows_scope.return_value = True
    store = AsyncMock(spec=PurposeSettingsStore)
    command = purpose_command()
    store.configure_settings.return_value = PurposeSettingsSaved(command.successor(), True)
    service = PurposeSettingsService(authorizer=authorizer, store=store)
    assert asyncio.run(service.configure(command)) == PurposeSettingsSaved(
        command.successor(), True
    )
    authorizer.allows_scope.return_value = False
    assert isinstance(asyncio.run(service.configure(command)), CiEconomicsReadForbidden)
    store.configure_settings.assert_awaited_once()
    authorizer.allows_scope.return_value = True
    store.read_settings.return_value = purpose_command(repository_id=999).successor()
    assert isinstance(
        asyncio.run(service.read(actor=command.actor, query=purpose_query())),
        CiEconomicsReadUnavailable,
    )
    store.configure_settings.return_value = PurposeSettingsSaved(
        purpose_command(entries=()).successor(), False
    )
    assert isinstance(asyncio.run(service.configure(command)), CiEconomicsReadUnavailable)


@pytest.mark.parametrize("error", [TimeoutError(), CiEconomicsStoreUnavailable("private failure")])
def test_store_timeout_or_unknown_commit_is_not_success(error: Exception) -> None:
    authorizer = AsyncMock(spec=CiEconomicsAuthorizer)
    authorizer.allows_scope.return_value = True
    store = AsyncMock(spec=PurposeSettingsStore)
    store.configure_settings.side_effect = error
    assert isinstance(
        asyncio.run(
            PurposeSettingsService(authorizer=authorizer, store=store).configure(purpose_command())
        ),
        CiEconomicsReadUnavailable,
    )


def test_authorization_deadline_prevents_entering_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ci_coordinator.app.analytics_configuration as application

    async def scenario() -> None:
        authorizer = AsyncMock(spec=CiEconomicsAuthorizer)
        waiting = asyncio.Event()

        async def wait(**_kwargs: object) -> bool:
            await waiting.wait()
            return True

        authorizer.allows_scope.side_effect = wait
        store = AsyncMock(spec=PurposeSettingsStore)
        monkeypatch.setattr(application, "PURPOSE_SETTINGS_DEADLINE_SECONDS", 0)
        result = await PurposeSettingsService(authorizer=authorizer, store=store).configure(
            purpose_command()
        )
        assert isinstance(result, CiEconomicsReadUnavailable)
        store.configure_settings.assert_not_awaited()

    asyncio.run(scenario())

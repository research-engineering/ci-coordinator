from __future__ import annotations

import asyncio

import pytest
from config_epoch_support import CONFIG_SOURCE, admitted_config_epoch
from repository_activation_support import ACTOR, SCOPE

from ci_coordinator.app.config_queries import (
    ConfigQueryForbidden,
    ConfigQueryNotFound,
    ConfigQueryService,
    ConfigQueryUnavailable,
    ConfigSourceAvailable,
    ConfigStatusAvailable,
)
from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.config_epochs import (
    ConfigEpochPage,
    ConfigEpochStatus,
    ConfigEpochStoreUnavailable,
)


class _Authorizer:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, RepositoryScope]] = []

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        self.calls.append((actor, scope))
        return self.allowed


class _Store:
    def __init__(
        self,
        *,
        unavailable: bool = False,
        status_scope: RepositoryScope = SCOPE,
    ) -> None:
        self.unavailable = unavailable
        self.status_scope = status_scope
        self.status_calls: list[tuple[RepositoryScope, str | None, int]] = []
        self.source_calls: list[tuple[RepositoryScope, str]] = []
        self.draft: ValidatedEpochDraft | None = admitted_config_epoch()

    async def read_status(
        self,
        scope: RepositoryScope,
        *,
        after_epoch_id: str | None,
        limit: int,
    ) -> ConfigEpochStatus:
        self.status_calls.append((scope, after_epoch_id, limit))
        if self.unavailable:
            raise ConfigEpochStoreUnavailable
        return ConfigEpochStatus(self.status_scope, None, ConfigEpochPage((), None))

    async def load_epoch(
        self,
        scope: RepositoryScope,
        epoch_id: str,
    ) -> ValidatedEpochDraft | None:
        self.source_calls.append((scope, epoch_id))
        if self.unavailable:
            raise ConfigEpochStoreUnavailable
        return self.draft


def test_status_authorizes_before_its_bounded_store_read() -> None:
    authorizer = _Authorizer(True)
    store = _Store()
    service = ConfigQueryService(authorizer=authorizer, store=store)

    result = asyncio.run(service.status(actor=ACTOR, scope=SCOPE, after_epoch_id=None, limit=50))

    assert isinstance(result, ConfigStatusAvailable)
    assert result.value.scope == SCOPE
    assert authorizer.calls == [(ACTOR, SCOPE)]
    assert store.status_calls == [(SCOPE, None, 50)]


def test_forbidden_status_and_source_never_reach_storage() -> None:
    authorizer = _Authorizer(False)
    store = _Store()
    service = ConfigQueryService(authorizer=authorizer, store=store)

    status_result = asyncio.run(
        service.status(actor=ACTOR, scope=SCOPE, after_epoch_id=None, limit=1)
    )
    source_result = asyncio.run(service.source(actor=ACTOR, scope=SCOPE, epoch_id="a" * 64))

    assert isinstance(status_result, ConfigQueryForbidden)
    assert isinstance(source_result, ConfigQueryForbidden)
    assert store.status_calls == []
    assert store.source_calls == []


def test_source_distinguishes_exact_value_missing_value_and_unavailability() -> None:
    available_store = _Store()
    available_service = ConfigQueryService(
        authorizer=_Authorizer(True),
        store=available_store,
    )
    available = asyncio.run(
        available_service.source(
            actor=ACTOR,
            scope=SCOPE,
            epoch_id=available_store.draft.epoch_id if available_store.draft else "a" * 64,
        )
    )
    missing_store = _Store()
    missing_store.draft = None
    missing = asyncio.run(
        ConfigQueryService(authorizer=_Authorizer(True), store=missing_store).source(
            actor=ACTOR,
            scope=SCOPE,
            epoch_id="a" * 64,
        )
    )
    unavailable = asyncio.run(
        ConfigQueryService(authorizer=_Authorizer(True), store=_Store(unavailable=True)).source(
            actor=ACTOR,
            scope=SCOPE,
            epoch_id="a" * 64,
        )
    )

    assert isinstance(available, ConfigSourceAvailable)
    assert isinstance(missing, ConfigQueryNotFound)
    assert isinstance(unavailable, ConfigQueryUnavailable)


def test_cross_scope_or_wrong_epoch_store_results_fail_closed() -> None:
    foreign_scope = RepositoryScope(7, 8)
    foreign_status = asyncio.run(
        ConfigQueryService(
            authorizer=_Authorizer(True),
            store=_Store(status_scope=foreign_scope),
        ).status(actor=ACTOR, scope=SCOPE, after_epoch_id=None, limit=1)
    )
    wrong_epoch_store = _Store()
    assert wrong_epoch_store.draft is not None
    wrong_epoch = asyncio.run(
        ConfigQueryService(
            authorizer=_Authorizer(True),
            store=wrong_epoch_store,
        ).source(actor=ACTOR, scope=SCOPE, epoch_id="f" * 64)
    )
    foreign_source = CONFIG_SOURCE.replace(
        b'"installationId":1,"repositoryId":2',
        b'"installationId":7,"repositoryId":8',
    )
    foreign_source_store = _Store()
    foreign_source_store.draft = admitted_config_epoch(source=foreign_source)
    foreign_draft = asyncio.run(
        ConfigQueryService(
            authorizer=_Authorizer(True),
            store=foreign_source_store,
        ).source(
            actor=ACTOR,
            scope=SCOPE,
            epoch_id=foreign_source_store.draft.epoch_id,
        )
    )

    assert isinstance(foreign_status, ConfigQueryUnavailable)
    assert isinstance(wrong_epoch, ConfigQueryUnavailable)
    assert isinstance(foreign_draft, ConfigQueryUnavailable)


@pytest.mark.parametrize(
    ("after_epoch_id", "limit"),
    [("A" * 64, 1), (None, 0), (None, 101), (None, True)],
)
def test_invalid_status_queries_fail_before_authorization(
    after_epoch_id: str | None,
    limit: int,
) -> None:
    authorizer = _Authorizer(True)
    store = _Store()
    service = ConfigQueryService(authorizer=authorizer, store=store)

    with pytest.raises(ValueError):
        asyncio.run(
            service.status(
                actor=ACTOR,
                scope=SCOPE,
                after_epoch_id=after_epoch_id,
                limit=limit,
            )
        )

    assert authorizer.calls == []
    assert store.status_calls == []

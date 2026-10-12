from __future__ import annotations

import asyncio

import pytest
from config_epoch_support import CONFIG_SOURCE, admitted_config_epoch
from planning_core._epoch_support import admitted_dynamic_epoch
from repository_activation_support import ACTOR, SCOPE

from ci_coordinator.app import config_queries
from ci_coordinator.app.config_queries import (
    ConfigPreviewAvailable,
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
from ci_coordinator.proposal_review import SemanticDiffLimitExceeded, compare_policy_drafts


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


class _EpochStore:
    def __init__(self, *drafts: ValidatedEpochDraft, unavailable: bool = False) -> None:
        self.drafts = {draft.epoch_id: draft for draft in drafts}
        self.unavailable = unavailable
        self.load_calls: list[tuple[RepositoryScope, str]] = []

    async def read_status(
        self,
        scope: RepositoryScope,
        *,
        after_epoch_id: str | None,
        limit: int,
    ) -> ConfigEpochStatus:
        raise AssertionError("preview must not read status")

    async def load_epoch(
        self,
        scope: RepositoryScope,
        epoch_id: str,
    ) -> ValidatedEpochDraft | None:
        self.load_calls.append((scope, epoch_id))
        if self.unavailable:
            raise ConfigEpochStoreUnavailable
        return self.drafts.get(epoch_id)


def _preview(
    store: _EpochStore,
    base_epoch_id: str,
    target_epoch_id: str,
    *,
    scope: RepositoryScope = SCOPE,
    allowed: bool = True,
) -> object:
    return asyncio.run(
        ConfigQueryService(authorizer=_Authorizer(allowed), store=store).preview(
            actor=ACTOR,
            scope=scope,
            base_epoch_id=base_epoch_id,
            target_epoch_id=target_epoch_id,
        )
    )


def test_preview_projects_the_review_diff_and_rollback_coverage_of_two_epochs() -> None:
    active = admitted_dynamic_epoch(default_depth="smoke")
    weaker = admitted_dynamic_epoch(default_depth="smoke", full_depth="targeted")
    store = _EpochStore(active, weaker)

    result = _preview(store, active.epoch_id, weaker.epoch_id, scope=active.scope)

    # Kills mutants that swap operands, drop the coverage owner or recompute a second diff.
    assert isinstance(result, ConfigPreviewAvailable)
    expected_diff = compare_policy_drafts(active, weaker)
    assert result.value.coverage_relation == "less"
    assert result.value.diff == expected_diff
    assert result.value.diff is not None and result.value.diff.changed_pointers
    assert store.load_calls == [
        (active.scope, active.epoch_id),
        (active.scope, weaker.epoch_id),
    ]


def test_preview_pointers_name_schema_fields_without_user_authored_values() -> None:
    base = admitted_config_epoch()
    renamed = admitted_config_epoch(
        source=CONFIG_SOURCE.replace(b'"name":"main"', b'"name":"secret-release-rule"')
    )
    store = _EpochStore(base, renamed)

    result = _preview(store, base.epoch_id, renamed.epoch_id)

    # Provisional D2/D3 disclosure boundary: arrays collapse to their schema pointer.
    assert isinstance(result, ConfigPreviewAvailable)
    assert result.value.diff is not None
    assert result.value.diff.changed_pointers == ("/repository/rules",)
    assert all(b"secret" not in pointer.encode() for pointer in result.value.diff.changed_pointers)


def test_preview_of_one_epoch_against_itself_is_an_empty_equal_comparison() -> None:
    draft = admitted_config_epoch()

    result = _preview(_EpochStore(draft), draft.epoch_id, draft.epoch_id)

    assert isinstance(result, ConfigPreviewAvailable)
    assert result.value.coverage_relation == "equal"
    assert result.value.diff is not None
    assert result.value.diff.changed_pointers == ()


def test_preview_keeps_coverage_when_the_exact_diff_exceeds_its_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = admitted_dynamic_epoch(default_depth="smoke")
    stronger = admitted_dynamic_epoch(
        default_depth="standard",
        full_depth="exhaustive",
        omit_allowed=False,
    )

    def _over_limit(*_: object) -> object:
        raise SemanticDiffLimitExceeded("semantic diff pointer limit exceeded")

    monkeypatch.setattr(config_queries, "compare_policy_drafts", _over_limit)

    result = _preview(
        _EpochStore(active, stronger), active.epoch_id, stronger.epoch_id, scope=active.scope
    )

    assert isinstance(result, ConfigPreviewAvailable)
    assert result.value.diff is None
    assert result.value.coverage_relation == "greater"


def test_preview_fails_closed_for_forbidden_missing_unavailable_and_foreign_epochs() -> None:
    draft = admitted_config_epoch()
    forbidden_store = _EpochStore(draft)
    foreign_source = CONFIG_SOURCE.replace(
        b'"installationId":1,"repositoryId":2',
        b'"installationId":7,"repositoryId":8',
    )
    foreign = admitted_config_epoch(source=foreign_source)

    forbidden = _preview(forbidden_store, draft.epoch_id, draft.epoch_id, allowed=False)
    missing_base = _preview(_EpochStore(draft), "a" * 64, draft.epoch_id)
    missing_target = _preview(_EpochStore(draft), draft.epoch_id, "a" * 64)
    unavailable = _preview(_EpochStore(draft, unavailable=True), draft.epoch_id, draft.epoch_id)
    cross_scope = _preview(_EpochStore(draft, foreign), draft.epoch_id, foreign.epoch_id)

    assert isinstance(forbidden, ConfigQueryForbidden)
    assert forbidden_store.load_calls == []
    assert isinstance(missing_base, ConfigQueryNotFound)
    assert isinstance(missing_target, ConfigQueryNotFound)
    assert isinstance(unavailable, ConfigQueryUnavailable)
    assert isinstance(cross_scope, ConfigQueryUnavailable)


@pytest.mark.parametrize(
    ("base_epoch_id", "target_epoch_id"),
    [("A" * 64, "a" * 64), ("a" * 64, "a" * 63), ("a" * 64, None)],
)
def test_invalid_preview_identities_fail_before_authorization(
    base_epoch_id: str,
    target_epoch_id: str,
) -> None:
    authorizer = _Authorizer(True)
    store = _EpochStore()

    with pytest.raises(ValueError):
        asyncio.run(
            ConfigQueryService(authorizer=authorizer, store=store).preview(
                actor=ACTOR,
                scope=SCOPE,
                base_epoch_id=base_epoch_id,
                target_epoch_id=target_epoch_id,
            )
        )

    assert authorizer.calls == []
    assert store.load_calls == []

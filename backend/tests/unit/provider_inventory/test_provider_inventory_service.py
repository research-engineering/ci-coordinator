from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Literal

import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import FixedClock
from ci_coordinator.provider_inventory import (
    ControlPlaneProviderInventoryAuthorizer,
    InstallationCatalog,
    InstallationPageReadResult,
    InstallationSummary,
    InventoryMode,
    ProviderInstallationPage,
    ProviderInstallationReader,
    ProviderInventoryForbidden,
    ProviderInventoryIneligible,
    ProviderInventoryService,
    ProviderInventoryUnavailable,
    ProviderRepositoryPage,
    ProviderRepositoryReader,
    RepositoryPage,
    RepositorySummary,
)
from ci_coordinator.provider_inventory.model import AccountType, InstallationState

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
ADMIN_ACTOR = "keycloak-human:v1:" + "a" * 64


@dataclass
class _Installations:
    results: dict[int, InstallationSummary | ProviderInventoryUnavailable]
    calls: list[int] = field(default_factory=list)
    page_calls: list[tuple[int, int]] = field(default_factory=list)

    async def list_installations(self, *, page: int, per_page: int) -> InstallationPageReadResult:
        self.page_calls.append((page, per_page))
        entries = tuple(
            value
            for _, value in sorted(self.results.items())
            if isinstance(value, InstallationSummary)
        )
        start = (page - 1) * per_page
        return ProviderInstallationPage(
            page, per_page, start + per_page < len(entries), entries[start : start + per_page]
        )

    async def get_installation(
        self,
        installation_id: int,
    ) -> InstallationSummary | ProviderInventoryUnavailable:
        self.calls.append(installation_id)
        return self.results[installation_id]


@dataclass
class _Repositories:
    result: ProviderRepositoryPage | ProviderInventoryUnavailable
    calls: list[tuple[int, int, int]] = field(default_factory=list)

    async def list_repositories(
        self,
        installation_id: int,
        *,
        page: int,
        per_page: int,
    ) -> ProviderRepositoryPage | ProviderInventoryUnavailable:
        self.calls.append((installation_id, page, per_page))
        return self.result


def test_empty_inventory_grant_is_complete_without_provider_io() -> None:
    installations = _Installations({})
    repositories = _Repositories(_page())
    service = _service(installations, repositories, installation_ids=frozenset())

    result = asyncio.run(service.list_installations(actor=ADMIN_ACTOR))

    assert result == InstallationCatalog(NOW, True, (), ())
    assert installations.calls == []
    assert repositories.calls == []


def test_wrong_actor_is_forbidden_before_provider_io() -> None:
    installations = _Installations({1: _installation()})
    repositories = _Repositories(_page())
    service = _service(installations, repositories)

    catalog = asyncio.run(service.list_installations(actor="other"))
    page = asyncio.run(
        service.list_repositories(
            actor="other",
            installation_id=1,
            page=1,
            per_page=100,
        )
    )

    assert isinstance(catalog, ProviderInventoryForbidden)
    assert isinstance(page, ProviderInventoryForbidden)
    assert installations.calls == []
    assert repositories.calls == []


def test_installation_catalog_preserves_independent_partial_failures() -> None:
    installations = _Installations(
        {
            1: _installation(installation_id=1, login="example"),
            2: ProviderInventoryUnavailable("rate_limited", 30),
        }
    )
    service = _service(
        installations,
        _Repositories(_page()),
        installation_ids=frozenset({1, 2}),
    )

    result = asyncio.run(service.list_installations(actor=ADMIN_ACTOR))

    assert isinstance(result, InstallationCatalog)
    assert result.complete is False
    assert [item.account_login for item in result.installations] == ["example"]
    assert [(item.installation_id, item.reason) for item in result.failures] == [
        (2, "rate_limited")
    ]


def test_installation_catalog_reads_and_projects_canonical_identity_order() -> None:
    installations = _Installations(
        {
            2: _installation(installation_id=2, login="two"),
            17: _installation(installation_id=17, login="seventeen"),
            33: _installation(installation_id=33, login="thirty-three"),
        }
    )
    service = _service(
        installations,
        _Repositories(_page()),
        installation_ids=frozenset({33, 2, 17}),
    )

    result = asyncio.run(service.list_installations(actor=ADMIN_ACTOR))

    assert isinstance(result, InstallationCatalog)
    assert installations.calls == [2, 17, 33]
    assert [item.installation_id for item in result.installations] == [2, 17, 33]


def test_repository_page_marks_only_exact_workbench_scopes() -> None:
    repositories = _Repositories(
        ProviderRepositoryPage(
            page=1,
            per_page=100,
            total_count=2,
            has_next_page=False,
            repositories=(_repository(10), _repository(11)),
        )
    )
    service = _service(
        _Installations({1: _installation()}),
        repositories,
        scopes=frozenset({RepositoryScope(1, 11), RepositoryScope(2, 10)}),
    )

    result = asyncio.run(
        service.list_repositories(
            actor=ADMIN_ACTOR,
            installation_id=1,
            page=1,
            per_page=100,
        )
    )

    assert isinstance(result, RepositoryPage)
    authorization = [
        (item.scope.repository_id, item.workbench_authorized) for item in result.repositories
    ]
    assert authorization == [
        (10, False),
        (11, True),
    ]
    assert repositories.calls == [(1, 1, 100)]


@pytest.mark.parametrize("substitution", ["owner", "installation", "page", "per-page"])
def test_repository_page_rejects_each_foreign_binding(substitution: str) -> None:
    page = _page()
    if substitution == "owner":
        page = replace(page, repositories=(_repository(owner_id=999),))
    elif substitution == "installation":
        page = replace(page, repositories=(replace(_repository(), scope=RepositoryScope(2, 10)),))
    elif substitution == "page":
        page = replace(page, page=2, total_count=101)
    else:
        page = replace(page, per_page=30)
    repositories = _Repositories(page)
    service = _service(_Installations({1: _installation()}), repositories)

    result = asyncio.run(
        service.list_repositories(
            actor=ADMIN_ACTOR,
            installation_id=1,
            page=1,
            per_page=100,
        )
    )

    assert result == ProviderInventoryUnavailable("provider_binding_mismatch")
    assert repositories.calls == [(1, 1, 100)]


def test_installation_catalog_propagates_provider_cancellation() -> None:
    class _CancelledInstallations(_Installations):
        async def get_installation(
            self,
            installation_id: int,
        ) -> InstallationSummary | ProviderInventoryUnavailable:
            raise asyncio.CancelledError

    service = _service(_CancelledInstallations({}), _Repositories(_page()))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.list_installations(actor=ADMIN_ACTOR))


def test_repository_page_rejects_next_page_beyond_the_request_range() -> None:
    with pytest.raises(ValueError, match="request range"):
        ProviderRepositoryPage(
            page=10_000,
            per_page=1,
            total_count=10_001,
            has_next_page=True,
            repositories=(_repository(),),
        )


def test_installation_catalog_enforces_its_global_item_bound() -> None:
    installations = tuple(
        _installation(installation_id=value, login=f"organization-{value}")
        for value in range(1, 66)
    )

    with pytest.raises(ValueError, match="installation bound"):
        InstallationCatalog(NOW, True, installations, ())


@pytest.mark.parametrize(
    ("account_type", "state", "reason"),
    [
        ("Organization", "suspended", "suspended"),
        ("User", "unsupported", "unsupported_account_type"),
    ],
)
def test_ineligible_installation_stops_before_repository_io(
    account_type: AccountType,
    state: InstallationState,
    reason: str,
) -> None:
    installation = _installation(account_type=account_type, state=state)
    repositories = _Repositories(_page())
    service = _service(_Installations({1: installation}), repositories)

    result = asyncio.run(
        service.list_repositories(
            actor=ADMIN_ACTOR,
            installation_id=1,
            page=1,
            per_page=100,
        )
    )

    assert isinstance(result, ProviderInventoryIneligible)
    assert result.reason == reason
    assert repositories.calls == []


@pytest.mark.parametrize("mode", ["restricted", "app"])
def test_catalog_pages_preserve_population_without_granting_commands(mode: InventoryMode) -> None:
    installations = _Installations(
        {value: _installation(installation_id=value) for value in (1, 2, 3)}
    )
    repositories = _Repositories(_page())
    service = _service(
        installations,
        repositories,
        inventory_mode=mode,
        installation_ids=frozenset({1, 2, 3}) if mode == "restricted" else frozenset(),
        scopes=frozenset(),
    )
    first = asyncio.run(service.list_installations(actor=ADMIN_ACTOR, page=1, per_page=2))
    last = asyncio.run(service.list_installations(actor=ADMIN_ACTOR, page=2, per_page=2))
    assert isinstance(first, InstallationCatalog) and isinstance(last, InstallationCatalog)
    assert ([item.installation_id for item in first.installations], first.has_next_page) == (
        [1, 2],
        True,
    )
    assert ([item.installation_id for item in last.installations], last.has_next_page) == (
        [3],
        False,
    )
    repository_page = asyncio.run(
        service.list_repositories(actor=ADMIN_ACTOR, installation_id=1, page=1, per_page=100)
    )
    assert isinstance(repository_page, RepositoryPage)
    assert not any(item.workbench_authorized for item in repository_page.repositories)


def test_app_mode_denies_actor_and_foreign_installation_before_repository_io() -> None:
    installations = _Installations({999: ProviderInventoryUnavailable("not_found")})
    repositories = _Repositories(_page())
    service = _service(
        installations, repositories, inventory_mode="app", installation_ids=frozenset()
    )
    assert isinstance(
        asyncio.run(service.list_installations(actor="other")), ProviderInventoryForbidden
    )
    assert installations.page_calls == []
    assert asyncio.run(
        service.list_repositories(actor=ADMIN_ACTOR, installation_id=999, page=1, per_page=100)
    ) == ProviderInventoryUnavailable("not_found")
    assert repositories.calls == []


@pytest.mark.parametrize("disabled,archived", [(False, False), (False, True), (True, False)])
def test_app_scope_projects_admitted_membership_without_per_repository_io(
    disabled: bool, archived: bool
) -> None:
    installations = _Installations({1: _installation()})
    repositories = _Repositories(
        replace(
            _page(),
            repositories=(replace(_repository(), disabled=disabled, archived=archived),),
        )
    )
    service = _service(
        installations,
        repositories,
        inventory_mode="app",
        scope_mode="app",
        installation_ids=frozenset(),
        scopes=frozenset(),
    )
    result = asyncio.run(
        service.list_repositories(
            actor=ADMIN_ACTOR,
            installation_id=1,
            page=1,
            per_page=100,
        )
    )
    assert isinstance(result, RepositoryPage)
    assert result.repositories[0].workbench_authorized is not disabled
    assert installations.calls == [1]
    assert repositories.calls == [(1, 1, 100)]


def test_app_scope_never_grants_inventory_to_non_administrators() -> None:
    authorizer = ControlPlaneProviderInventoryAuthorizer(
        inventory_installation_ids=frozenset(),
        workbench_scopes=frozenset(),
        inventory_mode="app",
        scope_mode="app",
    )
    assert not authorizer.permits_all_workbench_repositories("other", 1)
    with pytest.raises(ValueError):
        ControlPlaneProviderInventoryAuthorizer(
            inventory_installation_ids=frozenset(),
            workbench_scopes=frozenset(),
            inventory_mode="restricted",
            scope_mode="app",
        )


def _service(
    installations: ProviderInstallationReader,
    repositories: ProviderRepositoryReader,
    *,
    installation_ids: frozenset[int] = frozenset({1}),
    scopes: frozenset[RepositoryScope] = frozenset({RepositoryScope(1, 10)}),
    inventory_mode: InventoryMode = "restricted",
    scope_mode: Literal["restricted", "app"] = "restricted",
) -> ProviderInventoryService:
    return ProviderInventoryService(
        authorizer=ControlPlaneProviderInventoryAuthorizer(
            inventory_installation_ids=installation_ids,
            inventory_mode=inventory_mode,
            scope_mode=scope_mode,
            workbench_scopes=scopes,
        ),
        installations=installations,
        repositories=repositories,
        clock=FixedClock(NOW),
    )


def _installation(
    *,
    installation_id: int = 1,
    login: str = "example",
    account_type: AccountType = "Organization",
    state: InstallationState = "active",
) -> InstallationSummary:
    return InstallationSummary(
        installation_id=installation_id,
        account_id=100 + installation_id,
        account_login=login,
        account_type=account_type,
        repository_selection="selected",
        state=state,
    )


def _repository(repository_id: int = 10, *, owner_id: int = 101) -> RepositorySummary:
    return RepositorySummary(
        scope=RepositoryScope(1, repository_id),
        node_id=f"R_{repository_id}",
        owner_id=owner_id,
        owner_login="example",
        name=f"repo-{repository_id}",
        full_name=f"example/repo-{repository_id}",
        visibility="private",
        default_branch="main",
        archived=False,
        disabled=False,
        fork=False,
    )


def _page() -> ProviderRepositoryPage:
    return ProviderRepositoryPage(1, 100, 1, False, (_repository(),))

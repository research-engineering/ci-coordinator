"""Bounded GitHub provider for exact self-hosted runner capacity evidence."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from typing import Final

from ci_coordinator.integrations.github._routes import (
    GitHubPage,
    GitHubRepository,
    organization_runner_group_runners_path,
    organization_runner_groups_path,
)
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import (
    GitHubIncomplete,
    GitHubOutcome,
    GitHubQueryParameter,
    GitHubSuccess,
    GitHubUnavailable,
)
from ci_coordinator.integrations.github.reconciliation_observer_pagination import (
    next_page_number,
    pagination_count_matches,
    terminal_pagination,
)
from ci_coordinator.integrations.github.request_admission import (
    get_request_matches as _request_matches,
)
from ci_coordinator.integrations.github.runner_capacity_decoding import (
    DecodedPage,
    decode_runner_group_page,
    decode_runner_page,
)
from ci_coordinator.integrations.github.runner_client import RunnerClient
from ci_coordinator.integrations.github.transport import InstallationTransportFactory
from ci_coordinator.kernel import Clock
from ci_coordinator.plan_issuance import PlanRequest
from ci_coordinator.runner_capacity import (
    CapacityClassSelector,
    RunnerSnapshot,
    VisibleRunnerGroup,
    project_runner_snapshot,
)

RUNNER_CAPACITY_PAGE_SIZE: Final = 100


@dataclass(frozen=True, slots=True)
class RunnerCapacityProviderLimits:
    max_pages: int = 128
    max_groups: int = 64
    max_unique_runners: int = 4_096
    max_runner_occurrences: int = 8_192
    max_total_response_bytes: int = 8_388_608
    deadline_seconds: int = 10
    freshness_ttl_seconds: int = 15

    def __post_init__(self) -> None:
        values = (
            self.max_pages,
            self.max_groups,
            self.max_unique_runners,
            self.max_runner_occurrences,
            self.max_total_response_bytes,
            self.deadline_seconds,
            self.freshness_ttl_seconds,
        )
        if any(type(value) is not int or value < 1 for value in values):
            raise ValueError("runner capacity provider limits must be positive integers")
        if self.max_runner_occurrences < self.max_unique_runners:
            raise ValueError("runner occurrence bound cannot be below the unique-runner bound")


DEFAULT_RUNNER_CAPACITY_PROVIDER_LIMITS: Final = RunnerCapacityProviderLimits()


@dataclass(slots=True)
class _ReadBudget:
    limits: RunnerCapacityProviderLimits
    pages: int = 0
    response_bytes: int = 0
    runner_occurrences: int = 0

    def admits_another_page(self) -> bool:
        return self.pages < self.limits.max_pages

    def consume_page(self, body: bytes, *, runner_items: int) -> bool:
        if (
            type(body) is not bytes
            or type(runner_items) is not int
            or runner_items < 0
            or self.pages >= self.limits.max_pages
            or self.response_bytes > self.limits.max_total_response_bytes - len(body)
            or self.runner_occurrences > self.limits.max_runner_occurrences - runner_items
        ):
            return False
        self.pages += 1
        self.response_bytes += len(body)
        self.runner_occurrences += runner_items
        return True


class GitHubRunnerSnapshotProvider:
    """Capture one bounded request-local observation without changing coverage."""

    def __init__(
        self,
        transport_factory: InstallationTransportFactory,
        *,
        clock: Clock,
        limits: RunnerCapacityProviderLimits = DEFAULT_RUNNER_CAPACITY_PROVIDER_LIMITS,
    ) -> None:
        if type(limits) is not RunnerCapacityProviderLimits:
            raise TypeError("runner capacity provider requires exact limits")
        self._transport_factory = transport_factory
        self._clock = clock
        self._limits = limits

    async def capture(
        self,
        request: PlanRequest,
        selectors: tuple[CapacityClassSelector, ...],
        /,
    ) -> RunnerSnapshot | None:
        if type(request) is not PlanRequest:
            raise TypeError("runner capacity requires an exact plan request")
        if type(selectors) is not tuple or any(
            type(selector) is not CapacityClassSelector for selector in selectors
        ):
            raise TypeError("runner capacity requires exact class selectors")
        if not selectors:
            return None
        observed_at = self._clock.now()
        deadline = asyncio.timeout(self._limits.deadline_seconds)
        try:
            async with deadline:
                return await self._capture(request, selectors, observed_at=observed_at)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            if not deadline.expired():
                raise
            return None

    async def _capture(
        self,
        request: PlanRequest,
        selectors: tuple[CapacityClassSelector, ...],
        *,
        observed_at: datetime,
    ) -> RunnerSnapshot | None:
        repository = GitHubRepository(request.owner, request.repository)
        client = RunnerClient(
            self._transport_factory.for_installation(request.installation_id),
            api_version=GITHUB_API_VERSION,
        )
        budget = _ReadBudget(self._limits)
        repository_path = f"{repository.path}/actions/runners"
        repository_runners = await _load_pages(
            fetch=lambda page: client.list_self_hosted_runners(repository, page=page),
            expected_operation="runner.list_self_hosted_runners",
            expected_path=repository_path,
            required_query=(),
            decode=decode_runner_page,
            item_identity=lambda runner: runner.runner_id,
            max_items=self._limits.max_unique_runners,
            budget=budget,
            count_runner_occurrences=True,
        )
        if repository_runners is None:
            return None

        visible_query = (GitHubQueryParameter("visible_to_repository", repository.name),)
        groups = await _load_pages(
            fetch=lambda page: client.list_visible_self_hosted_runner_groups(
                repository,
                page=page,
            ),
            expected_operation="runner.list_visible_self_hosted_runner_groups",
            expected_path=organization_runner_groups_path(repository.owner),
            required_query=visible_query,
            decode=decode_runner_group_page,
            item_identity=lambda group: group.group_id,
            max_items=self._limits.max_groups,
            budget=budget,
            count_runner_occurrences=False,
        )
        if groups is None:
            return None

        visible_groups: list[VisibleRunnerGroup] = []
        for group in groups:
            runners = await _load_pages(
                fetch=partial(
                    _list_group_runner_page,
                    client,
                    repository.owner,
                    group.group_id,
                ),
                expected_operation="runner.list_group_self_hosted_runners",
                expected_path=organization_runner_group_runners_path(
                    repository.owner,
                    group.group_id,
                ),
                required_query=(),
                decode=decode_runner_page,
                item_identity=lambda runner: runner.runner_id,
                max_items=self._limits.max_unique_runners,
                budget=budget,
                count_runner_occurrences=True,
            )
            if runners is None:
                return None
            visible_groups.append(
                VisibleRunnerGroup(
                    group_id=group.group_id,
                    name=group.name,
                    restricted_to_workflows=group.restricted_to_workflows,
                    runners=runners,
                )
            )

        unique_runner_ids = {
            runner.runner_id
            for runner in (
                *repository_runners,
                *(runner for group in visible_groups for runner in group.runners),
            )
        }
        if len(unique_runner_ids) > self._limits.max_unique_runners:
            return None
        return project_runner_snapshot(
            selectors=selectors,
            repository_runners=repository_runners,
            groups=tuple(visible_groups),
            observed_at=observed_at,
            freshness_ttl_seconds=self._limits.freshness_ttl_seconds,
        )


async def _list_group_runner_page(
    client: RunnerClient,
    organization: str,
    group_id: int,
    page: GitHubPage,
) -> GitHubOutcome:
    return await client.list_group_self_hosted_runners(
        organization,
        group_id,
        page=page,
    )


async def _load_pages[T](
    *,
    fetch: Callable[[GitHubPage], Awaitable[GitHubOutcome]],
    expected_operation: str,
    expected_path: str,
    required_query: tuple[GitHubQueryParameter, ...],
    decode: Callable[[bytes], DecodedPage[T] | None],
    item_identity: Callable[[T], int],
    max_items: int,
    budget: _ReadBudget,
    count_runner_occurrences: bool,
) -> tuple[T, ...] | None:
    page_number = 1
    expected_total: int | None = None
    items: list[T] = []
    identities: set[int] = set()
    while True:
        if not budget.admits_another_page():
            return None
        page = GitHubPage(page_number, RUNNER_CAPACITY_PAGE_SIZE)
        outcome = await fetch(page)
        expected_query = (*required_query, *page.query())
        if isinstance(outcome, GitHubUnavailable):
            return None
        if not isinstance(outcome, (GitHubSuccess, GitHubIncomplete)) or not _request_matches(
            outcome.request,
            operation=expected_operation,
            path=expected_path,
            query=expected_query,
            api_version=GITHUB_API_VERSION,
        ):
            return None

        decoded = decode(outcome.response.body)
        runner_items = len(decoded.items) if decoded is not None and count_runner_occurrences else 0
        if decoded is None or not budget.consume_page(
            outcome.response.body,
            runner_items=runner_items,
        ):
            return None
        if decoded.total_count > max_items:
            return None
        if expected_total is None:
            expected_total = decoded.total_count
        elif decoded.total_count != expected_total:
            return None
        if not pagination_count_matches(outcome.response.pagination, expected_total):
            return None

        for item in decoded.items:
            identity = item_identity(item)
            if identity in identities:
                return None
            identities.add(identity)
            items.append(item)
        if len(items) > expected_total:
            return None

        if isinstance(outcome, GitHubSuccess):
            if not terminal_pagination(outcome.response.pagination) or len(items) != expected_total:
                return None
            return tuple(sorted(items, key=item_identity))
        if len(items) >= expected_total:
            return None
        next_page = next_page_number(
            outcome.response.pagination,
            expected_path=expected_path,
            current_page=page_number,
            page_size=RUNNER_CAPACITY_PAGE_SIZE,
            required_query=required_query,
        )
        if next_page is None:
            return None
        page_number = next_page

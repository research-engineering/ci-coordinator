from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import (
    GitHubPaginationEvidence,
    GitHubRequest,
    GitHubResponse,
    GitHubTransportResult,
)
from ci_coordinator.integrations.github.runner_capacity import (
    GitHubRunnerSnapshotProvider,
    RunnerCapacityProviderLimits,
)
from ci_coordinator.integrations.github.transport import GitHubTransport
from ci_coordinator.kernel import FixedClock
from ci_coordinator.plan_issuance import PlanRequest
from ci_coordinator.runner_capacity import CapacityClassSelector

NOW = datetime(2026, 9, 5, tzinfo=UTC)


class _QueueTransport:
    def __init__(self, responses: tuple[GitHubTransportResult | BaseException, ...]) -> None:
        self._responses = list(responses)
        self.requests: list[GitHubRequest] = []

    async def send(self, request: GitHubRequest) -> GitHubTransportResult:
        self.requests.append(request)
        response = self._responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class _Factory:
    def __init__(self, transport: GitHubTransport) -> None:
        self._transport = transport

    def for_installation(self, installation_id: int) -> GitHubTransport:
        del installation_id
        return self._transport


class _FailingFactory:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def for_installation(self, installation_id: int) -> GitHubTransport:
        del installation_id
        raise self._error


class _BlockingTransport:
    async def send(self, request: GitHubRequest) -> GitHubTransportResult:
        await asyncio.Event().wait()
        raise AssertionError(f"unreachable response for {request.operation}")


class _AdvancingClock:
    def __init__(self) -> None:
        self._calls = 0

    def now(self) -> datetime:
        value = NOW + timedelta(seconds=self._calls)
        self._calls += 1
        return value


def _runner(
    runner_id: int,
    label: str,
    *,
    busy: bool = False,
    extra_labels: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "id": runner_id,
        "status": "online",
        "busy": busy,
        "labels": [{"name": label}, *(extra_labels or [])],
    }


def _group(group_id: int, name: str, *, restricted: bool) -> dict[str, object]:
    return {
        "id": group_id,
        "name": name,
        "restricted_to_workflows": restricted,
    }


def _runner_page(total_count: int, *runners: dict[str, object]) -> dict[str, object]:
    return {"total_count": total_count, "runners": list(runners)}


def _group_page(total_count: int, *groups: dict[str, object]) -> dict[str, object]:
    return {"total_count": total_count, "runner_groups": list(groups)}


def _response(
    value: object,
    *,
    status: int = 200,
    next_page: str | None = None,
    include_item_count: bool = True,
) -> GitHubResponse:
    body = value if type(value) is bytes else json.dumps(value, separators=(",", ":")).encode()
    total_count = value.get("total_count") if type(value) is dict else None
    return GitHubResponse(
        status=status,
        api_version=GITHUB_API_VERSION,
        headers=(),
        body=body,
        pagination=GitHubPaginationEvidence(
            complete=next_page is None,
            pages_observed=1,
            item_count=(total_count if include_item_count and type(total_count) is int else None),
            next_page=next_page,
            termination="exhausted" if next_page is None else "next_page",
        ),
    )


def test_provider_counts_only_free_eligible_unrestricted_runners() -> None:
    transport = _QueueTransport(
        (
            _response(_runner_page(1, _runner(1, "dev"))),
            _response(
                _group_page(
                    2,
                    _group(10, "Dev", restricted=False),
                    _group(11, "Restricted", restricted=True),
                )
            ),
            _response(_runner_page(1, _runner(2, "dev"))),
            _response(_runner_page(1, _runner(3, "dev"))),
        )
    )
    provider = GitHubRunnerSnapshotProvider(_Factory(transport), clock=FixedClock(NOW))

    snapshot = asyncio.run(
        provider.capture(
            _request(),
            (CapacityClassSelector("dev", ("dev",), None),),
        )
    )

    assert snapshot is not None
    assert snapshot.free_slots_for("dev") == 2
    assert [request.operation for request in transport.requests] == [
        "runner.list_self_hosted_runners",
        "runner.list_visible_self_hosted_runner_groups",
        "runner.list_group_self_hosted_runners",
        "runner.list_group_self_hosted_runners",
    ]


def test_provider_admits_exact_sequential_pages() -> None:
    repository_path = "/repos/example/target/actions/runners"
    transport = _QueueTransport(
        (
            _response(
                _runner_page(2, _runner(1, "dev")),
                next_page=f"https://api.github.com{repository_path}?page=2&per_page=100",
            ),
            _response(_runner_page(2, _runner(2, "dev", busy=True))),
            _response(_group_page(0)),
        )
    )
    provider = GitHubRunnerSnapshotProvider(_Factory(transport), clock=FixedClock(NOW))

    snapshot = asyncio.run(
        provider.capture(
            _request(),
            (CapacityClassSelector("dev", ("dev",), None),),
        )
    )

    assert snapshot is not None
    assert snapshot.free_slots_for("dev") == 1
    assert tuple(parameter.value for parameter in transport.requests[1].query) == ("2", "100")
    assert tuple(parameter.value for parameter in transport.requests[2].query) == (
        "target",
        "1",
        "100",
    )


def test_provider_admits_exact_visible_group_pagination() -> None:
    group_path = "/orgs/example/actions/runner-groups"
    transport = _QueueTransport(
        (
            _response(_runner_page(0)),
            _response(
                _group_page(2, _group(1, "One", restricted=False)),
                next_page=(
                    f"https://api.github.com{group_path}"
                    "?visible_to_repository=target&page=2&per_page=100"
                ),
            ),
            _response(_group_page(2, _group(2, "Two", restricted=False))),
            _response(_runner_page(0)),
            _response(_runner_page(0)),
        )
    )
    provider = GitHubRunnerSnapshotProvider(_Factory(transport), clock=FixedClock(NOW))

    snapshot = asyncio.run(
        provider.capture(
            _request(),
            (CapacityClassSelector("dev", ("dev",), None),),
        )
    )

    assert snapshot is not None
    assert tuple(parameter.value for parameter in transport.requests[2].query) == (
        "target",
        "2",
        "100",
    )


def test_provider_admits_exact_group_runner_pagination() -> None:
    runner_path = "/orgs/example/actions/runner-groups/1/runners"
    transport = _QueueTransport(
        (
            _response(_runner_page(0)),
            _response(_group_page(1, _group(1, "Build", restricted=False))),
            _response(
                _runner_page(2, _runner(1, "dev")),
                next_page=f"https://api.github.com{runner_path}?page=2&per_page=100",
            ),
            _response(_runner_page(2, _runner(2, "dev", busy=True))),
        )
    )
    provider = GitHubRunnerSnapshotProvider(_Factory(transport), clock=FixedClock(NOW))

    snapshot = asyncio.run(
        provider.capture(
            _request(),
            (CapacityClassSelector("build", ("dev",), "Build"),),
        )
    )

    assert snapshot is not None
    assert snapshot.free_slots_for("build") == 1
    assert tuple(parameter.value for parameter in transport.requests[3].query) == ("2", "100")


def test_provider_rejects_ambiguous_organization_not_found() -> None:
    transport = _QueueTransport(
        (
            _response(_runner_page(1, _runner(1, "dev"))),
            _response({}, status=404),
        )
    )
    provider = GitHubRunnerSnapshotProvider(_Factory(transport), clock=FixedClock(NOW))

    assert (
        asyncio.run(
            provider.capture(
                _request(),
                (CapacityClassSelector("dev", ("dev",), None),),
            )
        )
        is None
    )


def test_snapshot_time_is_the_start_of_multi_source_observation() -> None:
    transport = _QueueTransport(
        (
            _response(_runner_page(0)),
            _response(_group_page(0)),
        )
    )
    provider = GitHubRunnerSnapshotProvider(_Factory(transport), clock=_AdvancingClock())

    snapshot = asyncio.run(
        provider.capture(
            _request(),
            (CapacityClassSelector("dev", ("dev",), None),),
        )
    )

    assert snapshot is not None
    assert snapshot.observed_at == NOW


@pytest.mark.parametrize(
    "responses",
    [
        (_response(b"not-json"),),
        (
            _response(
                _runner_page(2, _runner(1, "dev")),
                next_page=(
                    "https://api.github.com/repos/example/other/actions/runners?page=2&per_page=100"
                ),
            ),
            _response(_runner_page(2, _runner(2, "dev"))),
            _response(_group_page(0)),
        ),
        (
            _response(
                _runner_page(2, _runner(1, "dev")),
                next_page=(
                    "https://api.github.com/repos/example/target/actions/runners?page=2&per_page=100"
                ),
            ),
            _response(
                _runner_page(3, _runner(2, "dev")),
                include_item_count=False,
            ),
            _response(_group_page(0)),
        ),
        (
            _response(
                _runner_page(2, _runner(1, "dev")),
                next_page=(
                    "https://api.github.com/repos/example/target/actions/runners?page=3&per_page=100"
                ),
            ),
        ),
        (
            _response(_runner_page(0)),
            _response(
                _group_page(2, _group(1, "One", restricted=False)),
                next_page=(
                    "https://api.github.com/orgs/example/actions/runner-groups"
                    "?visible_to_repository=other&page=2&per_page=100"
                ),
            ),
        ),
        (
            _response(
                _runner_page(
                    1,
                    _runner(1, "dev", extra_labels=[{"name": "DEV"}]),
                )
            ),
        ),
        (_response(_runner_page(1, _runner(1, "d\u00e9v"))),),
    ],
    ids=[
        "malformed-json",
        "foreign-next-link",
        "unstable-total",
        "skipped-page",
        "foreign-required-query",
        "duplicate-label",
        "non-ascii-label",
    ],
)
def test_provider_rejects_incomplete_or_malformed_runner_evidence(
    responses: tuple[GitHubResponse, ...],
) -> None:
    provider = GitHubRunnerSnapshotProvider(
        _Factory(_QueueTransport(responses)),
        clock=FixedClock(NOW),
    )

    assert (
        asyncio.run(
            provider.capture(
                _request(),
                (CapacityClassSelector("dev", ("dev",), None),),
            )
        )
        is None
    )


@pytest.mark.parametrize(
    ("limits", "responses", "expected_request_count"),
    [
        (
            RunnerCapacityProviderLimits(max_pages=1),
            (
                _response(
                    _runner_page(2, _runner(1, "dev")),
                    next_page=(
                        "https://api.github.com/repos/example/target/actions/runners"
                        "?page=2&per_page=100"
                    ),
                ),
                _response(_runner_page(2, _runner(2, "dev"))),
                _response(_group_page(0)),
            ),
            1,
        ),
        (
            RunnerCapacityProviderLimits(max_total_response_bytes=1),
            (_response(_runner_page(0)), _response(_group_page(0))),
            1,
        ),
        (
            RunnerCapacityProviderLimits(max_groups=1),
            (
                _response(_runner_page(0)),
                _response(
                    _group_page(
                        2,
                        _group(1, "One", restricted=False),
                        _group(2, "Two", restricted=False),
                    )
                ),
                _response(_runner_page(0)),
                _response(_runner_page(0)),
            ),
            2,
        ),
        (
            RunnerCapacityProviderLimits(max_unique_runners=1),
            (
                _response(_runner_page(2, _runner(1, "dev"), _runner(2, "dev"))),
                _response(_group_page(0)),
            ),
            1,
        ),
        (
            RunnerCapacityProviderLimits(max_unique_runners=1, max_runner_occurrences=1),
            (
                _response(_runner_page(1, _runner(1, "dev"))),
                _response(_group_page(1, _group(1, "One", restricted=False))),
                _response(_runner_page(1, _runner(1, "dev"))),
            ),
            3,
        ),
    ],
    ids=["pages", "bytes", "groups", "unique-runners", "runner-occurrences"],
)
def test_provider_withholds_capacity_when_a_read_bound_is_exceeded(
    limits: RunnerCapacityProviderLimits,
    responses: tuple[GitHubResponse, ...],
    expected_request_count: int,
) -> None:
    transport = _QueueTransport(responses)
    provider = GitHubRunnerSnapshotProvider(
        _Factory(transport),
        clock=FixedClock(NOW),
        limits=limits,
    )

    assert (
        asyncio.run(
            provider.capture(
                _request(),
                (CapacityClassSelector("dev", ("dev",), None),),
            )
        )
        is None
    )
    assert len(transport.requests) == expected_request_count


def test_provider_total_deadline_withholds_capacity() -> None:
    provider = GitHubRunnerSnapshotProvider(
        _Factory(_BlockingTransport()),
        clock=FixedClock(NOW),
        limits=RunnerCapacityProviderLimits(deadline_seconds=1),
    )

    assert (
        asyncio.run(
            provider.capture(
                _request(),
                (CapacityClassSelector("dev", ("dev",), None),),
            )
        )
        is None
    )


def test_provider_propagates_cancellation() -> None:
    provider = GitHubRunnerSnapshotProvider(
        _Factory(_QueueTransport((asyncio.CancelledError(),))),
        clock=FixedClock(NOW),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            provider.capture(
                _request(),
                (CapacityClassSelector("dev", ("dev",), None),),
            )
        )


def test_provider_propagates_unexpected_failures_to_the_application_boundary() -> None:
    provider = GitHubRunnerSnapshotProvider(
        _Factory(_QueueTransport((RuntimeError("unexpected provider defect"),))),
        clock=FixedClock(NOW),
    )

    with pytest.raises(RuntimeError, match="unexpected provider defect"):
        asyncio.run(
            provider.capture(
                _request(),
                (CapacityClassSelector("dev", ("dev",), None),),
            )
        )


def test_provider_propagates_nested_timeout_outside_transport_outcome_algebra() -> None:
    provider = GitHubRunnerSnapshotProvider(
        _FailingFactory(TimeoutError("unexpected provider timeout")),
        clock=FixedClock(NOW),
    )

    with pytest.raises(TimeoutError, match="unexpected provider timeout"):
        asyncio.run(
            provider.capture(
                _request(),
                (CapacityClassSelector("dev", ("dev",), None),),
            )
        )


def test_empty_selector_set_performs_no_provider_io() -> None:
    transport = _QueueTransport(())
    provider = GitHubRunnerSnapshotProvider(_Factory(transport), clock=FixedClock(NOW))

    assert asyncio.run(provider.capture(_request(), ())) is None
    assert transport.requests == []


def _request() -> PlanRequest:
    return PlanRequest(
        "dynamic-ci-plan-request/v2",
        "request-1",
        100,
        200,
        "example",
        "target",
        "pull_request",
        "refs/pull/42/merge",
        "a" * 40,
        "b" * 40,
        7001,
        1,
        42,
        execution_sha="c" * 40,
    )

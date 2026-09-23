import asyncio
import json
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from ci_economics.budget_factories import budget_command
from ci_economics.report_factories import stored_report
from control_plane_http_support import (
    StaticControlPlaneAuthenticator,
    StaticMutationAdmission,
    StaticRoleAdmission,
    human_principal,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import ASGITransport, AsyncClient

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    CiEconomicsBudgetRouteDependencies,
    CiEconomicsRouteDependencies,
    HttpRouteDependencies,
    InvalidCredential,
)
from ci_coordinator.api.http.routers.ci_economics_budgets import BUDGET_CONFIGURATION_PATH
from ci_coordinator.app.ci_economics import (
    CiEconomicsReadForbidden,
    CiEconomicsReadUnavailable,
    CiEconomicsReadUseCase,
)
from ci_coordinator.app.ci_economics_budgets import CiEconomicsBudgetUseCase
from ci_coordinator.ci_economics.budget_commands import BudgetPolicyCommitted, BudgetPolicyConflict
from ci_coordinator.ci_economics.budget_signal import BudgetSignal, BudgetSignalPage

_POLICIES = "/api/v2/economics/repositories/101/202/budget-policies"
_SIGNALS = "/api/v2/economics/repositories/101/202/budget-signals"
_COMMAND = budget_command()
_BODY = {
    "installationId": 101,
    "repositoryId": 202,
    "policyKey": _COMMAND.policy_key,
    "expectedRevision": 0,
    "configuration": _COMMAND.configuration.canonical_mapping(),
    "operationId": _COMMAND.operation_id,
}


def _app(
    use_case: AsyncMock,
    *,
    authenticated: bool = True,
    integrity: bool = True,
    timeout: int = 30,
    reads: AsyncMock | None = None,
) -> FastAPI:
    return create_app(
        HttpRouteDependencies(
            ci_economics_budgets=CiEconomicsBudgetRouteDependencies(
                authenticator=StaticControlPlaneAuthenticator(
                    human_principal() if authenticated else InvalidCredential()
                ),
                role_admission=StaticRoleAdmission(),
                mutation_admission=StaticMutationAdmission(integrity),
                use_case=use_case,
            ),
            ci_economics=CiEconomicsRouteDependencies(
                authenticator=StaticControlPlaneAuthenticator(human_principal()),
                role_admission=StaticRoleAdmission(),
                use_case=reads,
            )
            if reads is not None
            else None,
        ),
        include_operator_ui=False,
        request_timeout_seconds=timeout,
    )


@pytest.mark.parametrize("replayed", [False, True])
def test_configuration_binds_authenticated_actor_and_exact_revision(replayed: bool) -> None:
    use_case = AsyncMock(spec=CiEconomicsBudgetUseCase)
    use_case.configure.return_value = BudgetPolicyCommitted(_COMMAND.next_policy, replayed)
    with TestClient(_app(use_case)) as client:
        response = client.post(BUDGET_CONFIGURATION_PATH, json=_BODY)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "schemaVersion": "ci-economics-budget-mutation/v1",
        "operationId": _COMMAND.operation_id,
        "outcome": "replayed" if replayed else "committed",
        "policy": _COMMAND.next_policy.canonical_mapping(),
    }
    use_case.configure.assert_awaited_once_with(replace(_COMMAND, actor=human_principal().actor_id))


@pytest.mark.parametrize("reason", ["revision_conflict", "operation_conflict", "capacity_reached"])
def test_policy_conflicts_never_become_success(reason: str) -> None:
    from typing import Literal, cast

    use_case = AsyncMock(spec=CiEconomicsBudgetUseCase)
    use_case.configure.return_value = BudgetPolicyConflict(
        cast(Literal["revision_conflict", "operation_conflict", "capacity_reached"], reason)
    )
    with TestClient(_app(use_case)) as client:
        response = client.post(BUDGET_CONFIGURATION_PATH, json=_BODY)
    assert response.status_code == 409 and response.json()["outcome"] == reason
    assert response.json()["policy"] is None


@pytest.mark.parametrize("path", [_POLICIES, _SIGNALS])
@pytest.mark.parametrize(
    "failure,code", [(CiEconomicsReadForbidden(), 403), (CiEconomicsReadUnavailable(), 503)]
)
def test_private_reads_preserve_explicit_failure(path: str, failure: object, code: int) -> None:
    use_case = AsyncMock(spec=CiEconomicsBudgetUseCase)
    use_case.list_policies.return_value = failure
    use_case.list_signals.return_value = failure
    with TestClient(_app(use_case)) as client:
        response = client.get(path)
    assert response.status_code == code and response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "method,path", [("GET", _POLICIES), ("GET", _SIGNALS), ("POST", BUDGET_CONFIGURATION_PATH)]
)
def test_unauthenticated_requests_never_reach_budget_storage(method: str, path: str) -> None:
    use_case = AsyncMock(spec=CiEconomicsBudgetUseCase)
    with TestClient(_app(use_case, authenticated=False)) as client:
        response = client.request(method, path, json=_BODY if method == "POST" else None)
    assert response.status_code == 401
    assert response.headers["www-authenticate"]
    assert not use_case.mock_calls


def test_mutation_integrity_is_checked_before_configuration() -> None:
    use_case = AsyncMock(spec=CiEconomicsBudgetUseCase)
    with TestClient(_app(use_case, integrity=False)) as client:
        response = client.post(BUDGET_CONFIGURATION_PATH, json=_BODY)
    assert response.status_code == 403 and not use_case.mock_calls


@pytest.mark.parametrize(
    "content,headers,code",
    [
        (b"{}", {"content-type": "text/plain"}, 400),
        (b"{}", {"content-type": "application/json", "content-encoding": "gzip"}, 400),
        (b'{"operationId":"a","operationId":"b"}', {"content-type": "application/json"}, 400),
        (
            b'{"configuration":{"enabled":true,"enabled":false}}',
            {"content-type": "application/json"},
            400,
        ),
        (b"{" * 50, {"content-type": "application/json"}, 400),
        (b" " * 2049, {"content-type": "application/json"}, 413),
    ],
)
def test_raw_json_admission_precedes_budget_processing(
    content: bytes, headers: dict[str, str], code: int
) -> None:
    use_case = AsyncMock(spec=CiEconomicsBudgetUseCase)
    with TestClient(_app(use_case)) as client:
        response = client.post(BUDGET_CONFIGURATION_PATH, content=content, headers=headers)
    assert response.status_code == code and not use_case.mock_calls


@pytest.mark.parametrize("field", tuple(_BODY))
def test_configuration_does_not_invent_missing_required_fields(field: str) -> None:
    use_case = AsyncMock(spec=CiEconomicsBudgetUseCase)
    with TestClient(_app(use_case)) as client:
        response = client.post(
            BUDGET_CONFIGURATION_PATH,
            json={key: value for key, value in _BODY.items() if key != field},
        )
    assert response.status_code == 422 and not use_case.mock_calls


@pytest.mark.parametrize(
    "query", ["?limit=1&limit=2", "?revision=1", "?unexpected=x", "?policyKey=x&policyKey=y"]
)
def test_signal_query_is_closed_and_unambiguous(query: str) -> None:
    use_case = AsyncMock(spec=CiEconomicsBudgetUseCase)
    with TestClient(_app(use_case)) as client:
        response = client.get(_SIGNALS + query)
    assert response.status_code == 400 and not use_case.mock_calls


def test_catalog_projections_preserve_signal_snapshot_and_do_not_copy_reports() -> None:
    use_case = AsyncMock(spec=CiEconomicsBudgetUseCase)
    signal = BudgetSignal.evaluate(_COMMAND.next_policy, stored_report())
    use_case.list_policies.return_value = (_COMMAND.next_policy,)
    use_case.list_signals.return_value = BudgetSignalPage((signal,), signal.signal_id)
    with TestClient(_app(use_case)) as client:
        policies = client.get(_POLICIES)
        signals = client.get(
            _SIGNALS + "?limit=1&policyKey=backend-cpu&revision=1&outcome=within_budget"
        )
    assert policies.status_code == signals.status_code == 200
    assert policies.json()["policies"] == [_COMMAND.next_policy.canonical_mapping()]
    expected_signal = signal.canonical_mapping()
    expected_signal["receivedAt"] = signal.received_at.isoformat().replace("+00:00", "Z")
    expected_signal["retainUntil"] = signal.retain_until.isoformat().replace("+00:00", "Z")
    assert signals.json()["items"] == [expected_signal]
    assert signals.json()["nextCursor"] == signal.signal_id
    assert "producerClaimHash" not in json.dumps(signals.json())


@pytest.mark.parametrize("mutation", [False, True])
def test_connected_budget_routes_share_owned_no_queue_admission(mutation: bool) -> None:
    async def scenario() -> None:
        use_case = AsyncMock(spec=CiEconomicsBudgetUseCase)
        reads = AsyncMock(spec=CiEconomicsReadUseCase)
        entered, release = asyncio.Event(), asyncio.Event()
        calls = 0
        capacity = 2 if mutation else 4

        async def blocked(*_: object, **__: object) -> object:
            nonlocal calls
            calls += 1
            if calls == capacity:
                entered.set()
            await release.wait()
            return (
                BudgetPolicyCommitted(_COMMAND.next_policy, False)
                if mutation
                else CiEconomicsReadUnavailable()
            )

        use_case.configure.side_effect = blocked
        use_case.list_policies.side_effect = blocked
        use_case.list_signals.side_effect = blocked
        reads.list_attempts.side_effect = blocked
        paths = (
            [BUDGET_CONFIGURATION_PATH] * 2
            if mutation
            else [
                "/api/v1/economics/repositories/101/202/attempts",
                _POLICIES,
                _SIGNALS,
                _POLICIES,
            ]
        )
        async with AsyncClient(
            transport=ASGITransport(app=_app(use_case, reads=reads)), base_url="https://test"
        ) as client:
            tasks = [
                asyncio.create_task(
                    client.request(
                        "POST" if mutation else "GET",
                        path,
                        json=_BODY if mutation else None,
                    )
                )
                for path in paths
            ]
            try:
                await asyncio.wait_for(entered.wait(), 5)
                for path in [BUDGET_CONFIGURATION_PATH] if mutation else [_POLICIES, _SIGNALS]:
                    async with asyncio.timeout(2):
                        response = await client.request(
                            "POST" if mutation else "GET", path, json=_BODY if mutation else None
                        )
                    assert response.status_code == 503
                    assert response.json() == {
                        "ok": False,
                        "error": "overloaded" if mutation else "unavailable",
                    }
                    assert calls == capacity
            finally:
                release.set()
                await asyncio.gather(*tasks)
            await client.request(
                "POST" if mutation else "GET", paths[-1], json=_BODY if mutation else None
            )
            assert calls == capacity + 1

    asyncio.run(scenario())


@pytest.mark.parametrize("termination", ["deadline", "cancellation"])
@pytest.mark.parametrize(
    "method,path,operation",
    [
        ("POST", BUDGET_CONFIGURATION_PATH, "configure"),
        ("GET", _POLICIES, "list_policies"),
        ("GET", _SIGNALS, "list_signals"),
    ],
)
def test_budget_request_termination_releases_capacity(
    method: str,
    path: str,
    operation: str,
    termination: str,
) -> None:
    async def scenario() -> None:
        use_case = AsyncMock(spec=CiEconomicsBudgetUseCase)
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def blocked(*_: object, **__: object) -> object:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
            raise AssertionError("cancelled request must not resume")

        operation_mock = getattr(use_case, operation)
        operation_mock.side_effect = blocked
        async with AsyncClient(
            transport=ASGITransport(app=_app(use_case, timeout=1)), base_url="https://test"
        ) as client:
            task = asyncio.create_task(
                client.request(method, path, json=_BODY if method == "POST" else None)
            )
            await asyncio.wait_for(entered.wait(), 2)
            if termination == "cancellation":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                response = await asyncio.wait_for(task, 3)
                assert response.status_code == 503
                assert response.json() == {"ok": False, "error": "unavailable"}
            assert cancelled.is_set()
            expected_result = (
                BudgetPolicyCommitted(_COMMAND.next_policy, False)
                if method == "POST"
                else ()
                if operation == "list_policies"
                else BudgetSignalPage((), None)
            )
            capacity = 2 if method == "POST" else 4
            resumed, release = asyncio.Event(), asyncio.Event()
            resumed_count = 0

            async def hold_capacity(*_: object, **__: object) -> object:
                nonlocal resumed_count
                resumed_count += 1
                if resumed_count == capacity:
                    resumed.set()
                await release.wait()
                return expected_result

            operation_mock.side_effect = hold_capacity
            tasks = [
                asyncio.create_task(
                    client.request(method, path, json=_BODY if method == "POST" else None)
                )
                for _ in range(capacity)
            ]
            try:
                await asyncio.wait_for(resumed.wait(), 2)
            finally:
                release.set()
                results = await asyncio.gather(*tasks)
            assert [result.status_code for result in results] == [200] * capacity
            assert operation_mock.await_count == 1 + capacity

    asyncio.run(scenario())

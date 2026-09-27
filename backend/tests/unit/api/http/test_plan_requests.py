from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import cast

import pytest
from fastapi.testclient import TestClient
from package_b_support import (
    assert_deterministic_plan,
    make_input,
    make_native_execution_projection,
    make_policy,
)
from prometheus_support import prometheus_samples
from starlette.middleware.errors import ServerErrorMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    ActionsAuthenticationResult,
    AuthenticationDependencyUnavailable,
    ForbiddenIdentity,
    HttpRouteDependencies,
    InvalidCredential,
    ObservabilityRouteDependencies,
    PlanRouteDependencies,
)
from ci_coordinator.api.http.routers.plan_requests import (
    MAX_PLAN_REQUEST_BODY_BYTES,
    PLAN_REQUEST_PATH,
)
from ci_coordinator.app import DynamicPlanCommand, DynamicPlanUseCase
from ci_coordinator.identity_admission import TrustedActionsRun
from ci_coordinator.observability import ReadinessStatus, RuntimeMetrics, StructuredEventLogger
from ci_coordinator.observability.request_observation import HttpRequestObservationMiddleware
from ci_coordinator.plan_issuance import (
    AuthenticatedRunBinding,
    FullCiExecution,
    IssuanceConflict,
    IssuanceRejected,
    Issued,
    IssuedPlanRecord,
    PlanRequest,
    RepositoryBinding,
    SelectedExecution,
    SignedPlanEnvelope,
    SignedPlanPayload,
)
from ci_coordinator.planning_core import plan
from ci_coordinator.repo_context import DiffFileChangeInput
from ci_coordinator.verification_core import verify

NOW = datetime(2026, 7, 14, tzinfo=UTC)
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


def _plan_counts(metrics: RuntimeMetrics) -> dict[str, float]:
    return {
        dict(labels)["result"]: value
        for (name, labels), value in prometheus_samples(metrics).items()
        if name == "ci_coordinator_plan_requests_total" and value
    }


async def _post_asgi(app: ASGIApp, send: Send) -> None:
    body = json.dumps(_wire_request()).encode()
    scope: Scope = {
        "type": "http",
        "method": "POST",
        "path": PLAN_REQUEST_PATH,
        "http_version": "1.1",
        "scheme": "http",
        "query_string": b"",
        "headers": [
            (b"authorization", b"Bearer test-token"),
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    }

    async def receive() -> Message:
        return {"type": "http.request", "body": body, "more_body": False}

    await app(scope, receive, send)


@pytest.mark.parametrize("sink_mode", ["shared", "separate", "plan_only", "http_only", "disabled"])
@pytest.mark.parametrize("duplicate", [False, True])
def test_plan_terminal_counter_uses_only_its_configured_sink(
    sink_mode: str,
    duplicate: bool,
) -> None:
    plan_metrics = RuntimeMetrics()
    http_metrics = plan_metrics if sink_mode == "shared" else RuntimeMetrics()
    use_case = _RecordingUseCase(replace(_issued(_plan_request()), duplicate=duplicate))
    app = create_app(
        HttpRouteDependencies(
            plan=PlanRouteDependencies(
                _Authenticator(_trusted_identity()),
                use_case,
                None if sink_mode in {"http_only", "disabled"} else plan_metrics,
            ),
            observability=None
            if sink_mode in {"plan_only", "disabled"}
            else ObservabilityRouteDependencies(readiness=_ready, metrics=http_metrics),
        )
    )
    response = TestClient(app).post(
        PLAN_REQUEST_PATH,
        json=_wire_request(),
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    assert len(use_case.commands) == 1
    assert _plan_counts(plan_metrics) == (
        {} if sink_mode in {"http_only", "disabled"} else {"issued": 1}
    )
    if sink_mode != "shared":
        assert _plan_counts(http_metrics) == {}
    if sink_mode not in {"http_only", "disabled"}:
        assert (
            prometheus_samples(plan_metrics)[
                (
                    "ci_coordinator_signed_envelopes_total",
                    (("result", "duplicate" if duplicate else "created"),),
                )
            ]
            == 1
        )


@pytest.mark.parametrize("cut", ["start", "body", "cleanup"])
@pytest.mark.parametrize("cancel", [False, True])
def test_issued_plan_send_boundary_is_not_durable_issuance_or_cleanup(
    cut: str,
    cancel: bool,
) -> None:
    async def scenario() -> None:
        output = StringIO()
        logger = logging.Logger("send-boundary")
        logger.addHandler(logging.StreamHandler(output))
        metrics = RuntimeMetrics()
        use_case = _RecordingUseCase(_issued(_plan_request()))
        app = create_app(
            HttpRouteDependencies(
                plan=PlanRouteDependencies(_Authenticator(_trusted_identity()), use_case, metrics),
                observability=ObservabilityRouteDependencies(
                    readiness=_ready,
                    metrics=metrics,
                    request_logger=StructuredEventLogger(logger),
                ),
            )
        )
        attempts: list[str] = []
        failure = (
            asyncio.CancelledError("request response deadline expired")
            if cancel
            else (RuntimeError("private send failure"))
        )
        if cut == "cleanup":
            stack = app.build_middleware_stack()
            assert isinstance(stack, ServerErrorMiddleware)
            observer = stack.app
            assert isinstance(observer, HttpRequestObservationMiddleware)
            inner = observer._app

            async def cleanup(scope: Scope, receive: Receive, send: Send) -> None:
                await inner(scope, receive, send)
                raise failure

            observer._app = cleanup
            app.middleware_stack = stack

        async def send(message: Message) -> None:
            attempts.append(message["type"])
            if (cut == "start" and message["type"] == "http.response.start") or (
                cut == "body" and message["type"] == "http.response.body"
            ):
                raise failure

        with pytest.raises(type(failure)) as caught:
            await _post_asgi(app, send)
        assert caught.value is failure
        assert attempts == (
            ["http.response.start"]
            if cut == "start"
            else ["http.response.start", "http.response.body"]
        )
        expected = "issued" if cut == "cleanup" else "cancelled" if cancel else "response_failed"
        assert _plan_counts(metrics) == {expected: 1}
        assert len(use_case.commands) == 1
        samples = prometheus_samples(metrics)
        assert samples[("ci_coordinator_signed_envelopes_total", (("result", "created"),))] == 1
        assert samples[
            (
                "ci_coordinator_http_request_duration_seconds_count",
                (("method", "POST"), ("route", PLAN_REQUEST_PATH), ("status_class", "2xx")),
            )
        ] == (1 if cut == "cleanup" else 0)
        record = json.loads(output.getvalue())
        assert record["statusCode"] == (None if cut == "start" else 200)
        assert record["responseCompleted"] is (cut == "cleanup")
        assert record["termination"] == (
            "completed" if cut == "cleanup" else "cancelled" if cancel else "exception"
        )
        assert "private send failure" not in output.getvalue()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("identity", "outcome", "result", "status"),
    [
        (InvalidCredential(), None, "unauthenticated", 401),
        (ForbiddenIdentity(), None, "forbidden", 403),
        (AuthenticationDependencyUnavailable(), None, "dependency_unavailable", 503),
        (None, "conflict", "conflict", 409),
        (None, IssuanceRejected("private"), "issuance_unavailable", 503),
    ],
)
def test_typed_plan_exclusions_and_dependency_carveout_keep_one_terminal_result(
    identity: ActionsAuthenticationResult | None,
    outcome: str | IssuanceRejected | None,
    result: str,
    status: int,
) -> None:
    metrics = RuntimeMetrics()
    issued = _issued(_plan_request())
    use_case = _RecordingUseCase(
        IssuanceConflict(issued.record, issued.record)
        if outcome == "conflict"
        else outcome
        if isinstance(outcome, IssuanceRejected)
        else issued
    )
    app = create_app(
        PlanRouteDependencies(
            _Authenticator(identity or _trusted_identity()),
            use_case,
            metrics,
        )
    )
    response = TestClient(app).post(
        PLAN_REQUEST_PATH,
        json=_wire_request(),
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == status
    assert _plan_counts(metrics) == {result: 1}
    assert len(use_case.commands) == (0 if identity is not None else 1)


@pytest.mark.parametrize("fault", ["healthy", "cancelled", "send_failed"])
def test_conflict_exclusion_survives_response_start_failure(fault: str) -> None:
    async def scenario() -> None:
        request = _plan_request()
        identity = _trusted_identity()
        attempted = _issued(request).record
        existing = _issued(replace(request, request_id="earlier-request")).record
        assert existing.idempotency_key == attempted.idempotency_key
        assert existing.request_hash != attempted.request_hash
        use_case = _RecordingUseCase(IssuanceConflict(existing, attempted))
        authenticator = _Authenticator(identity)
        metrics = RuntimeMetrics()
        app = create_app(
            HttpRouteDependencies(
                plan=PlanRouteDependencies(authenticator, use_case, metrics),
                observability=ObservabilityRouteDependencies(readiness=_ready, metrics=metrics),
            ),
            include_operator_ui=False,
            request_timeout_seconds=30,
        )
        attempts: list[Message] = []
        entered = asyncio.Event()
        cancellation: list[asyncio.CancelledError] = []
        send_failure = RuntimeError("conflict response send failure")

        async def send(message: Message) -> None:
            attempts.append(message)
            if message["type"] == "http.response.start":
                assert message["status"] == 409
                if fault == "send_failed":
                    raise send_failure
                if fault == "cancelled":
                    entered.set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError as exc:
                        cancellation.append(exc)
                        raise

        if fault == "cancelled":
            task = asyncio.create_task(_post_asgi(app, send))
            start_wait = asyncio.create_task(entered.wait())
            try:
                done, _ = await asyncio.wait(
                    {task, start_wait}, timeout=5, return_when=asyncio.FIRST_COMPLETED
                )
                if task in done:
                    await task
                    pytest.fail("request completed before the response-start barrier")
                assert start_wait in done, "response-start barrier was not reached"
                task.cancel("external request cancellation")
                with pytest.raises(asyncio.CancelledError) as caught:
                    await task
                assert len(cancellation) == 1
                assert caught.value is cancellation[0]
            finally:
                for owned in (task, start_wait):
                    if not owned.done():
                        owned.cancel()
                await asyncio.gather(task, start_wait, return_exceptions=True)
        elif fault == "send_failed":
            with pytest.raises(RuntimeError) as caught_failure:
                await _post_asgi(app, send)
            assert caught_failure.value is send_failure
        else:
            await _post_asgi(app, send)

        assert [message["type"] for message in attempts] == (
            ["http.response.start", "http.response.body"]
            if fault == "healthy"
            else ["http.response.start"]
        )
        if fault == "healthy":
            assert json.loads(attempts[1]["body"]) == {"code": "plan_request_conflict"}
            assert not attempts[1].get("more_body", False)
        assert authenticator.calls == 1
        assert authenticator.authorizations == ["Bearer test-token"]
        assert authenticator.plan_requests == [request]
        assert use_case.commands == [DynamicPlanCommand(request, identity)]
        samples = prometheus_samples(metrics)
        assert {
            dict(labels)["result"]: value
            for (name, labels), value in samples.items()
            if name == "ci_coordinator_plan_requests_total"
        } == {
            "cancelled": 0,
            "conflict": 1,
            "dependency_unavailable": 0,
            "forbidden": 0,
            "invalid": 0,
            "internal_error": 0,
            "issuance_unavailable": 0,
            "issued": 0,
            "response_failed": 0,
            "timed_out": 0,
            "unauthenticated": 0,
        }
        assert samples[("ci_coordinator_signed_envelopes_total", (("result", "created"),))] == 0
        assert samples[("ci_coordinator_signed_envelopes_total", (("result", "duplicate"),))] == 0
        success_labels = (("method", "POST"), ("route", PLAN_REQUEST_PATH), ("status_class", "2xx"))
        assert samples[("ci_coordinator_http_request_duration_seconds_count", success_labels)] == 0
        assert samples[("ci_coordinator_http_request_duration_seconds_sum", success_labels)] == 0

    asyncio.run(scenario())


def test_post_issuance_model_failure_is_not_a_success(monkeypatch: pytest.MonkeyPatch) -> None:
    from ci_coordinator.api.http.routers import plan_requests

    metrics = RuntimeMetrics()
    use_case = _RecordingUseCase(_issued(_plan_request()))
    monkeypatch.setattr(plan_requests, "_serialize_envelope", lambda _: {})
    response = TestClient(
        create_app(
            PlanRouteDependencies(
                _Authenticator(_trusted_identity()),
                use_case,
                metrics,
            )
        )
    ).post(
        PLAN_REQUEST_PATH,
        json=_wire_request(),
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 500
    assert _plan_counts(metrics) == {"internal_error": 1}
    assert len(use_case.commands) == 1
    assert (
        prometheus_samples(metrics)[
            (
                "ci_coordinator_signed_envelopes_total",
                (("result", "created"),),
            )
        ]
        == 1
    )


def test_completed_selected_envelope_is_good_like_full_ci() -> None:
    source = make_input(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy()
    verified = verify(source, policy, assert_deterministic_plan(plan(source, policy)))
    execution = SelectedExecution.project(verified, make_native_execution_projection(verified))
    full_ci = _issued(_plan_request())
    payload = replace(
        full_ci.record.envelope.payload,
        plan_id=verified.execution_plan_id,
        verified_plan_id=verified.execution_plan_id,
        production_admission_receipt_id="production_admission_" + "1" * 32,
        execution=execution,
        verifier_version="fixture-v1",
        fallback_reason=None,
    )
    envelope = replace(full_ci.record.envelope, payload=payload)
    issued = Issued(IssuedPlanRecord.create("test-key", _plan_request(), envelope), False)
    metrics = RuntimeMetrics()
    use_case = _RecordingUseCase(issued)
    response = TestClient(
        create_app(
            PlanRouteDependencies(
                _Authenticator(_trusted_identity()),
                use_case,
                metrics,
            )
        )
    ).post(
        PLAN_REQUEST_PATH,
        json=_wire_request(),
        headers={"Authorization": "Bearer test-token"},
    )
    assert response.status_code == 200
    assert response.json()["payload"]["execution"]["mode"] == "selected"
    assert _plan_counts(metrics) == {"issued": 1}
    assert len(use_case.commands) == 1


@pytest.mark.parametrize("authenticated", [False, True])
@pytest.mark.parametrize("cause", ["cancelled", "downstream_timeout", "owned_timeout"])
def test_plan_population_requires_auth_or_the_explicit_dependency_carveout(
    authenticated: bool,
    cause: str,
) -> None:
    async def scenario() -> None:
        metrics = RuntimeMetrics()
        calls = 0
        fault_entered = asyncio.Event()

        async def fault() -> None:
            if cause == "cancelled":
                fault_entered.set()
                await asyncio.Event().wait()
            if cause == "downstream_timeout":
                raise TimeoutError("not the request owner's timeout")
            await asyncio.Event().wait()

        class Authenticator:
            async def authenticate(
                self,
                authorization: str | None,
                plan_request: PlanRequest,
            ) -> ActionsAuthenticationResult:
                assert authorization == "Bearer test-token"
                assert plan_request == _plan_request()
                if not authenticated:
                    await fault()
                return _trusted_identity()

        class UseCase:
            async def request_dynamic_plan(
                self,
                command: DynamicPlanCommand,
            ) -> Issued:
                nonlocal calls
                calls += 1
                assert command == DynamicPlanCommand(_plan_request(), _trusted_identity())
                await fault()
                raise AssertionError("fault must terminate")

        app = create_app(
            PlanRouteDependencies(Authenticator(), UseCase(), metrics),
            request_timeout_seconds=1 if cause == "owned_timeout" else 30,
        )
        sent: list[Message] = []

        async def send(message: Message) -> None:
            sent.append(message)

        if cause == "cancelled":
            task = asyncio.create_task(_post_asgi(app, send))
            try:
                await fault_entered.wait()
                task.cancel("request response deadline expired")
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            assert sent == []
        else:
            await _post_asgi(app, send)
            assert sent[0]["status"] == (503 if cause == "owned_timeout" else 500)
            assert len([m for m in sent if m["type"] == "http.response.start"]) == 1
        assert calls == int(authenticated)
        assert _plan_counts(metrics) == (
            {
                {
                    "cancelled": "cancelled",
                    "downstream_timeout": "internal_error",
                    "owned_timeout": "timed_out",
                }[cause]: 1
            }
            if authenticated
            else {}
        )

    asyncio.run(scenario())


class _RecordingUseCase:
    def __init__(self, outcome: Issued | IssuanceRejected | IssuanceConflict) -> None:
        self.commands: list[DynamicPlanCommand] = []
        self._outcome = outcome

    async def request_dynamic_plan(
        self,
        command: DynamicPlanCommand,
    ) -> Issued | IssuanceRejected | IssuanceConflict:
        self.commands.append(command)
        return self._outcome


class _UnsupportedOutcomeUseCase:
    async def request_dynamic_plan(self, command: DynamicPlanCommand) -> object:
        del command
        return object()


class _TimeoutUseCase:
    async def request_dynamic_plan(
        self,
        command: DynamicPlanCommand,
    ) -> Issued | IssuanceRejected | IssuanceConflict:
        del command
        raise TimeoutError("sensitive downstream timeout")


class _Authenticator:
    def __init__(self, identity: ActionsAuthenticationResult) -> None:
        self._identity = identity
        self.calls = 0
        self.authorizations: list[str | None] = []
        self.plan_requests: list[PlanRequest] = []

    async def authenticate(
        self,
        authorization: str | None,
        plan_request: PlanRequest,
    ) -> ActionsAuthenticationResult:
        self.calls += 1
        self.authorizations.append(authorization)
        self.plan_requests.append(plan_request)
        return self._identity


async def _ready() -> ReadinessStatus:
    return ReadinessStatus(True, ())


def test_plan_route_uses_a_typed_command_and_returns_the_signed_envelope() -> None:
    request = _plan_request()
    use_case = _RecordingUseCase(_issued(request))
    authenticator = _Authenticator(_trusted_identity())
    client = TestClient(
        create_app(PlanRouteDependencies(authenticator, use_case)),
        headers={"Authorization": "Bearer test-token"},
    )

    response = client.post(PLAN_REQUEST_PATH, json=_wire_request())

    assert response.status_code == 200
    assert response.json()["payload"]["request"]["pullRequestNumber"] == 42
    assert response.json()["payload"]["authenticatedRun"]["runId"] == 7001
    assert authenticator.authorizations == ["Bearer test-token"]
    assert authenticator.plan_requests == [request]
    assert use_case.commands == [DynamicPlanCommand(request, _trusted_identity())]


def test_plan_route_projects_issued_envelopes_into_runtime_metrics() -> None:
    metrics = RuntimeMetrics()
    dependencies = PlanRouteDependencies(
        _Authenticator(_trusted_identity()),
        _RecordingUseCase(_issued(_plan_request())),
        metrics,
    )

    response = TestClient(
        create_app(dependencies), headers={"Authorization": "Bearer test-token"}
    ).post(
        PLAN_REQUEST_PATH,
        json=_wire_request(),
    )

    assert response.status_code == 200
    samples = prometheus_samples(metrics)
    assert samples[("ci_coordinator_plan_requests_total", (("result", "issued"),))] == 1
    assert samples[("ci_coordinator_signed_envelopes_total", (("result", "created"),))] == 1


def test_issued_plan_log_correlates_http_provider_and_durable_plan_identities() -> None:
    output = StringIO()
    logger = logging.Logger("plan-observation-test")
    logger.addHandler(logging.StreamHandler(output))
    metrics = RuntimeMetrics()
    plan = PlanRouteDependencies(
        _Authenticator(_trusted_identity()),
        _RecordingUseCase(_issued(_plan_request())),
        metrics,
    )
    client = TestClient(
        create_app(
            HttpRouteDependencies(
                plan=plan,
                observability=ObservabilityRouteDependencies(
                    readiness=_ready,
                    metrics=metrics,
                    request_logger=StructuredEventLogger(logger),
                ),
            )
        ),
        headers={"Authorization": "Bearer test-token"},
    )

    response = client.post(PLAN_REQUEST_PATH, json=_wire_request())

    record = json.loads(output.getvalue())
    assert response.status_code == 200
    assert record["correlationId"] == response.headers["x-correlation-id"]
    assert record["issuedPlanRecordId"].startswith("issued_plan_")
    assert record["planId"] == response.json()["payload"]["planId"]
    assert record["repositoryId"] == 200
    assert record["workflowRunId"] == 7001
    assert record["runAttempt"] == 1
    assert "test-token" not in output.getvalue()


def test_plan_route_rejects_invalid_transport_before_the_use_case() -> None:
    use_case = _RecordingUseCase(_issued(_plan_request()))
    client = _client(use_case, _trusted_identity())

    unknown_field = client.post(PLAN_REQUEST_PATH, json={**_wire_request(), "unknown": True})
    missing_pull_request = client.post(
        PLAN_REQUEST_PATH,
        json={key: value for key, value in _wire_request().items() if key != "pullRequestNumber"},
    )

    assert unknown_field.status_code == 422
    assert unknown_field.json() == {"code": "invalid_request"}
    assert missing_pull_request.status_code == 400
    assert missing_pull_request.json() == {"code": "invalid_plan_request"}
    assert use_case.commands == []


@pytest.mark.parametrize(
    ("failure", "expected_status", "expected_body"),
    [
        (InvalidCredential(), 401, {"code": "unauthenticated"}),
        (ForbiddenIdentity(), 403, {"code": "forbidden"}),
        (AuthenticationDependencyUnavailable(), 503, {"code": "plan_unavailable"}),
    ],
)
def test_plan_route_preserves_the_redacted_authentication_failure_algebra(
    failure: InvalidCredential | ForbiddenIdentity | AuthenticationDependencyUnavailable,
    expected_status: int,
    expected_body: dict[str, str],
) -> None:
    use_case = _RecordingUseCase(_issued(_plan_request()))

    response = _client(use_case, failure).post(
        PLAN_REQUEST_PATH,
        json=_wire_request(),
    )

    assert response.status_code == expected_status
    assert response.json() == expected_body
    assert response.headers["cache-control"] == "no-store"
    assert use_case.commands == []
    assert "oidc" not in response.text


def test_plan_route_redacts_domain_unavailability() -> None:
    request = _plan_request()
    denied_use_case = _RecordingUseCase(IssuanceRejected("internal request mismatch"))

    unavailable = _client(denied_use_case, _trusted_identity()).post(
        PLAN_REQUEST_PATH,
        json=_wire_request(),
    )

    assert denied_use_case.commands == [DynamicPlanCommand(request, _trusted_identity())]
    assert unavailable.status_code == 503
    assert unavailable.json() == {"code": "plan_unavailable"}


def test_plan_route_treats_an_unknown_use_case_outcome_as_an_internal_error() -> None:
    metrics = RuntimeMetrics()
    dependencies = PlanRouteDependencies(
        _Authenticator(_trusted_identity()),
        cast(DynamicPlanUseCase, _UnsupportedOutcomeUseCase()),
        metrics,
    )

    response = TestClient(
        create_app(dependencies),
        headers={"Authorization": "Bearer test-token"},
        raise_server_exceptions=False,
    ).post(PLAN_REQUEST_PATH, json=_wire_request())

    assert response.status_code == 500
    assert response.json() == {"code": "internal_error"}
    assert response.headers["cache-control"] == "no-store"
    assert (
        prometheus_samples(metrics)[
            (
                "ci_coordinator_plan_requests_total",
                (("result", "issuance_unavailable"),),
            )
        ]
        == 0
    )
    assert _plan_counts(metrics) == {"internal_error": 1}


def test_downstream_timeout_error_uses_the_generic_internal_error_contract() -> None:
    response = TestClient(
        create_app(
            PlanRouteDependencies(
                _Authenticator(_trusted_identity()),
                _TimeoutUseCase(),
            )
        ),
        headers={"Authorization": "Bearer test-token"},
        raise_server_exceptions=False,
    ).post(PLAN_REQUEST_PATH, json=_wire_request())

    assert response.status_code == 500
    assert response.json() == {"code": "internal_error"}
    assert response.headers["cache-control"] == "no-store"
    assert "sensitive downstream timeout" not in response.text


def test_plan_route_openapi_exposes_one_typed_plan_operation() -> None:
    app = create_app(
        PlanRouteDependencies(
            _Authenticator(_trusted_identity()),
            _RecordingUseCase(_issued(_plan_request())),
        )
    )

    operation = app.openapi()["paths"][PLAN_REQUEST_PATH]["post"]

    assert operation["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/SignedPlanEnvelopeBody"
    }
    assert set(operation["responses"]).issuperset(
        {"200", "400", "401", "403", "409", "413", "422", "500", "503"}
    )
    assert operation["security"] == [{"GitHubActionsOIDC": []}]
    assert (
        operation["responses"]["401"]["headers"]["WWW-Authenticate"]["schema"]["type"] == "string"
    )


def test_plan_route_rejects_duplicate_authorization_fields_before_authentication() -> None:
    use_case = _RecordingUseCase(_issued(_plan_request()))
    authenticator = _Authenticator(_trusted_identity())
    client = TestClient(create_app(PlanRouteDependencies(authenticator, use_case)))

    response = client.post(
        PLAN_REQUEST_PATH,
        json=_wire_request(),
        headers=[
            ("Authorization", "Bearer valid"),
            ("Authorization", "Bearer forged"),
        ],
    )

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store"
    assert authenticator.calls == 0
    assert use_case.commands == []


def test_plan_route_rejects_an_oversized_raw_body_before_authentication() -> None:
    use_case = _RecordingUseCase(_issued(_plan_request()))
    authenticator = _Authenticator(_trusted_identity())
    client = TestClient(create_app(PlanRouteDependencies(authenticator, use_case)))

    response = client.post(
        PLAN_REQUEST_PATH,
        content=b"x" * (MAX_PLAN_REQUEST_BODY_BYTES + 1),
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 413
    assert response.json() == {"code": "request_too_large"}
    assert authenticator.calls == 0
    assert authenticator.authorizations == []
    assert authenticator.plan_requests == []
    assert use_case.commands == []


def _client(
    use_case: _RecordingUseCase,
    identity: ActionsAuthenticationResult,
) -> TestClient:
    return TestClient(
        create_app(PlanRouteDependencies(_Authenticator(identity), use_case)),
        headers={"Authorization": "Bearer test-token"},
    )


def _plan_request() -> PlanRequest:
    return PlanRequest(
        "dynamic-ci-plan-request/v2",
        "request-1",
        100,
        200,
        "example-org",
        "ci-coordinator",
        "pull_request",
        "refs/pull/42/merge",
        BASE_SHA,
        HEAD_SHA,
        7001,
        1,
        42,
        execution_sha="c" * 40,
    )


def _wire_request() -> dict[str, object]:
    return _plan_request().identity_mapping()


def _trusted_identity() -> TrustedActionsRun:
    return TrustedActionsRun(
        "https://token.actions.githubusercontent.com",
        "ci-coordinator",
        "example-org/ci-coordinator",
        200,
        "refs/pull/42/merge",
        7001,
        1,
        "pull_request",
        "workflow@ref",
        None,
        None,
        None,
        None,
        NOW,
        execution_sha="c" * 40,
    )


def _issued(request: PlanRequest) -> Issued:
    plan_id = "fallback_plan_test"
    identity = _trusted_identity()
    payload = SignedPlanPayload(
        "dynamic-ci-signed-plan-payload/v2",
        plan_id,
        RepositoryBinding(100, 200, "example-org", "ci-coordinator"),
        request,
        AuthenticatedRunBinding(
            identity.issuer,
            identity.audience,
            identity.repository,
            identity.repository_id,
            identity.ref,
            identity.run_id,
            identity.run_attempt,
            identity.event_name,
            identity.workflow_ref,
            identity.workflow_sha,
            identity.job_workflow_ref,
            identity.job_workflow_sha,
            identity.check_run_id,
            identity.verified_at,
            identity.verifier_version,
            identity.claim_hash,
            execution_sha=identity.execution_sha,
        ),
        None,
        None,
        FullCiExecution(mode="full-ci", reason="test_fallback"),
        None,
        "test_fallback",
    )
    envelope = SignedPlanEnvelope(
        "dynamic-ci-signed-plan-envelope/v1",
        "test-key",
        "Ed25519",
        NOW,
        NOW + timedelta(seconds=60),
        payload,
        "signature",
    )
    return Issued(IssuedPlanRecord.create("test-key", request, envelope), False)

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from io import StringIO
from typing import cast

import pytest
from fastapi.testclient import TestClient
from prometheus_support import prometheus_samples

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
from ci_coordinator.plan_issuance import (
    AuthenticatedRunBinding,
    FullCiExecution,
    IssuanceConflict,
    IssuanceRejected,
    Issued,
    IssuedPlanRecord,
    PlanRequest,
    RepositoryBinding,
    SignedPlanEnvelope,
    SignedPlanPayload,
)

NOW = datetime(2026, 7, 14, tzinfo=UTC)
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


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

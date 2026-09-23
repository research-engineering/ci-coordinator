from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

import pytest
from ci_economics.factories import ATTEMPT, NOW, job, recorded_measurements, retained_snapshot
from control_plane_http_support import (
    ACTOR,
    StaticControlPlaneAuthenticator,
    StaticRoleAdmission,
    human_principal,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    CiEconomicsRouteDependencies,
    HttpRouteDependencies,
    InvalidCredential,
)
from ci_coordinator.api.http.routers.ci_economics import (
    CI_ECONOMICS_ATTEMPTS_PATH,
    CI_ECONOMICS_JOBS_PATH,
    CI_ECONOMICS_MEASUREMENTS_PATH,
)
from ci_coordinator.app.ci_economics import (
    AttemptJobsAvailable,
    AttemptJobsResult,
    AttemptMeasurementsResult,
    AttemptSummariesAvailable,
    AttemptSummariesResult,
    CiEconomicsReadForbidden,
    CiEconomicsReadNotFound,
    CiEconomicsReadUnavailable,
    ProviderSourcePageResult,
)
from ci_coordinator.ci_economics import (
    AttemptIdentity,
    AttemptSummary,
    AttemptSummaryPage,
    RunnerIdentity,
    derive_attempt_economics,
    encode_attempt_cursor,
    load_bundled_ci_economics_profile,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import ControlPlaneRole


@dataclass
class _UseCase:
    summaries: AttemptSummariesResult
    jobs: AttemptJobsResult
    calls: list[tuple[str, object]] = field(default_factory=list)
    measurements: AttemptMeasurementsResult = field(default_factory=CiEconomicsReadNotFound)

    async def list_provider_sources(
        self, *, actor: str, scope: RepositoryScope, after_cursor: str | None, limit: int
    ) -> ProviderSourcePageResult:
        raise AssertionError("unexpected source catalog read")

    async def load_measurements(
        self, *, actor: str, attempt: AttemptIdentity
    ) -> AttemptMeasurementsResult:
        self.calls.append(("measurements", (actor, attempt)))
        return self.measurements

    async def list_attempts(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        after_cursor: str | None,
        limit: int,
    ) -> AttemptSummariesResult:
        self.calls.append(("list", (actor, scope, after_cursor, limit)))
        return self.summaries

    async def load_attempt_jobs(
        self,
        *,
        actor: str,
        attempt: AttemptIdentity,
        after_job_id: int | None,
        limit: int,
    ) -> AttemptJobsResult:
        self.calls.append(("jobs", (actor, attempt, after_job_id, limit)))
        return self.jobs


_MEASUREMENTS_URL = (
    f"/api/v2/economics/repositories/101/202/attempts/303/2/measurements?headSha={'b' * 40}"
)


@pytest.mark.parametrize("independent", (False, True))
def test_measurement_projection_keeps_source_semantics_and_units(independent: bool) -> None:
    use_case = _use_case()
    use_case.measurements = recorded_measurements(independent=independent)
    response = _client(use_case).get(_MEASUREMENTS_URL)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["schemaVersion"] == "ci-economics-measurements/v2"
    assert body["definitionVersion"] == "ci-economics-measurement/v1"
    assert body["queue"] == {
        "quality": "unknown",
        "knownValueMs": None,
        "knownJobCount": 0,
        "totalJobCount": 1,
        "reasonCode": "workflow_job_queue_evidence_incomplete",
    }
    assert body["runnerOccupancy"]["knownValueMs"] == 120_000
    assert body["attemptWall"] == {
        "quality": "unknown",
        "knownValueMs": None,
        "knownJobCount": 0,
        "totalJobCount": 1,
        "reasonCode": "attempt_wall_evidence_incomplete",
    }
    assert body["recordedAt"] == "2026-09-04T10:04:00Z"
    assert body["retainUntil"] == "2026-09-05T10:00:00Z"
    assert body["source"]["attempt"] == {
        "installationId": 101,
        "repositoryId": 202,
        "workflowRunId": 303,
        "runAttempt": 2,
        "headSha": "b" * 40,
    }
    if independent:
        assert body["source"]["sourceKind"] == "provider_run"
        assert body["source"]["runCreatedAt"] == "2026-09-04T10:00:00Z"
        assert body["source"]["providerApiVersion"] == "2026-03-10"
        assert "contractHash" not in body["source"]
        assert "plannedRoute" not in body["source"]
    else:
        assert body["source"]["sourceKind"] == "reconciliation"
        assert body["source"]["plannedRoute"] == "unknown"
        assert len(body["source"]["contractHash"]) == 64
    assert "cpu" not in body and "jobs" not in body and "savings" not in body
    assert use_case.calls == [("measurements", (ACTOR, ATTEMPT))]


@pytest.mark.parametrize(
    ("result", "code", "error"),
    [
        (CiEconomicsReadForbidden(), 403, "forbidden"),
        (CiEconomicsReadNotFound(), 404, "not_found"),
        (CiEconomicsReadUnavailable(), 503, "unavailable"),
    ],
)
def test_measurement_read_maps_closed_errors_without_cache(
    result: AttemptMeasurementsResult, code: int, error: str
) -> None:
    use_case = _use_case()
    use_case.measurements = result
    response = _client(use_case).get(_MEASUREMENTS_URL)
    assert response.status_code == code
    assert response.json() == {"ok": False, "error": error}
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("authenticated", (False, True))
def test_measurement_authority_precedes_application(authenticated: bool) -> None:
    use_case = _use_case()
    response = _client(use_case, authenticated=authenticated, roles=frozenset({"configure"})).get(
        _MEASUREMENTS_URL
    )
    assert response.status_code == (403 if authenticated else 401)
    assert use_case.calls == []
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "url",
    [
        _MEASUREMENTS_URL.replace("/101/", "/0/"),
        _MEASUREMENTS_URL.replace("/202/", "/9007199254740992/"),
        _MEASUREMENTS_URL.replace("/303/", "/-1/"),
        _MEASUREMENTS_URL.replace("/2/", "/false/"),
        _MEASUREMENTS_URL.replace("b" * 40, "B" * 40),
        _MEASUREMENTS_URL.split("?")[0],
    ],
)
def test_measurement_identity_validation_rejects_before_use_case(url: str) -> None:
    use_case = _use_case()
    assert _client(use_case).get(url).status_code == 422
    assert use_case.calls == []


def test_measurement_openapi_discriminates_source_without_optional_legacy_hash() -> None:
    schema = _app(_use_case()).openapi()
    operation = schema["paths"][CI_ECONOMICS_MEASUREMENTS_PATH]["get"]
    assert set(operation["responses"]) >= {"200", "401", "403", "404", "422", "503"}
    assert operation["security"] == [{"ControlPlaneBearer": []}, {"ControlPlaneSession": []}]
    models = schema["components"]["schemas"]
    source = models["EconomicsMeasurementsResponse"]["properties"]["source"]
    assert source["discriminator"]["propertyName"] == "sourceKind"
    assert len(source["oneOf"]) == 2
    for variant in source["oneOf"]:
        model = models[variant["$ref"].rsplit("/", 1)[-1]]
        assert "sourceKind" in model["properties"] and "sourceKind" in model["required"]
        assert "source_kind" not in model["properties"]
    assert "contractHash" in models["EconomicsReconciliationSourceResponse"]["required"]
    assert "contractHash" not in models["EconomicsProviderSourceResponse"]["properties"]


def test_ci_economics_attempt_page_is_authenticated_scoped_and_bounded() -> None:
    use_case = _use_case()

    response = _client(use_case).get("/api/v1/economics/repositories/101/202/attempts?limit=7")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "schemaVersion": "ci-economics-attempt-page/v1",
        "ok": True,
        "items": [
            {
                "subjectId": "c" * 64,
                "attempt": {
                    "installationId": 101,
                    "repositoryId": 202,
                    "workflowRunId": 303,
                    "runAttempt": 2,
                    "headSha": "b" * 40,
                },
                "contractHash": "d" * 64,
                "plannedRoute": "selected",
                "jobCount": 1,
                "snapshotDigest": "e" * 64,
                "recordedAt": "2026-09-04T10:00:00Z",
            }
        ],
        "nextCursor": None,
    }
    assert use_case.calls == [("list", (ACTOR, ATTEMPT.scope, None, 7))]


def test_ci_economics_job_page_projects_measurements_without_rederiving_them() -> None:
    use_case = _use_case()

    response = _client(use_case).get(
        f"/api/v1/economics/repositories/101/202/attempts/303/2/jobs?headSha={'b' * 40}&limit=1"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schemaVersion"] == "ci-economics-attempt-jobs/v1"
    assert body["definitionVersion"] == "ci-economics-measurement/v1"
    assert body["queue"] == {
        "quality": "exact",
        "knownValueMs": 60_000,
        "knownJobCount": 1,
        "totalJobCount": 1,
        "reasonCode": None,
    }
    assert body["runnerOccupancy"]["knownValueMs"] == 120_000
    assert body["jobs"][0]["providerJobId"] == 404
    assert use_case.calls == [("jobs", (ACTOR, ATTEMPT, None, 1))]


@pytest.mark.parametrize("missing_metadata", (False, True))
def test_ci_economics_job_page_preserves_complete_nested_projection(
    missing_metadata: bool,
) -> None:
    source_job = job(
        name="projected-job",
        created_at=None if missing_metadata else NOW,
        started_at=None if missing_metadata else NOW + timedelta(minutes=1),
        completed_at=None if missing_metadata else NOW + timedelta(minutes=3),
        labels=() if missing_metadata else ("arm64", "linux"),
        runner=(
            RunnerIdentity(None, None, None, None)
            if missing_metadata
            else RunnerIdentity(11, "runner-a", 12, "build-pool")
        ),
        delivery_id=None,
    )
    economics = derive_attempt_economics(retained_snapshot(source_job), ())
    use_case = _use_case(jobs=AttemptJobsAvailable(economics, (source_job,), 404))

    response = _client(use_case).get(
        f"/api/v1/economics/repositories/101/202/attempts/303/2/jobs?headSha={'b' * 40}&limit=1"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["jobs"] == [
        {
            "providerJobId": 404,
            "name": "projected-job",
            "conclusion": "success",
            "timing": {
                "createdAt": None if missing_metadata else "2026-09-04T10:00:00Z",
                "startedAt": None if missing_metadata else "2026-09-04T10:01:00Z",
                "completedAt": None if missing_metadata else "2026-09-04T10:03:00Z",
            },
            "labels": [] if missing_metadata else ["arm64", "linux"],
            "runner": {
                "runnerId": None if missing_metadata else 11,
                "runnerName": None if missing_metadata else "runner-a",
                "runnerGroupId": None if missing_metadata else 12,
                "runnerGroupName": None if missing_metadata else "build-pool",
            },
            "semanticHash": source_job.semantic_hash,
        }
    ]
    assert body["runnerOccupancy"] == {
        "quality": "unknown" if missing_metadata else "exact",
        "knownValueMs": None if missing_metadata else 120_000,
        "knownJobCount": 0 if missing_metadata else 1,
        "totalJobCount": 1,
        "reasonCode": "provider_job_occupancy_evidence_incomplete" if missing_metadata else None,
    }
    assert body["nextJobId"] == 404
    assert use_case.calls == [("jobs", (ACTOR, ATTEMPT, None, 1))]


def test_ci_economics_authentication_and_not_found_fail_closed() -> None:
    unauthenticated_use_case = _use_case()
    unauthenticated = _client(unauthenticated_use_case, authenticated=False).get(
        "/api/v1/economics/repositories/101/202/attempts"
    )
    missing_use_case = _use_case(jobs=CiEconomicsReadNotFound())
    missing = _client(missing_use_case).get(
        f"/api/v1/economics/repositories/101/202/attempts/303/2/jobs?headSha={'b' * 40}"
    )

    assert (unauthenticated.status_code, unauthenticated.json()) == (
        401,
        {"ok": False, "error": "unauthenticated"},
    )
    assert unauthenticated.headers["www-authenticate"] == "Bearer"
    assert unauthenticated.headers["cache-control"] == "no-store"
    assert unauthenticated_use_case.calls == []
    assert (missing.status_code, missing.json()) == (
        404,
        {"ok": False, "error": "not_found"},
    )


def test_ci_economics_requires_audit_role_before_scope_authorization() -> None:
    use_case = _use_case()
    read_only_roles: frozenset[ControlPlaneRole] = frozenset({"read"})

    response = _client(use_case, roles=read_only_roles).get(
        "/api/v1/economics/repositories/101/202/attempts"
    )

    assert (response.status_code, response.json()) == (
        403,
        {"ok": False, "error": "forbidden"},
    )
    assert response.headers["cache-control"] == "no-store"
    assert use_case.calls == []


def test_ci_economics_forwards_only_admitted_cursor_bounds() -> None:
    use_case = _use_case()
    cursor = encode_attempt_cursor(NOW, "c" * 64)

    attempts = _client(use_case).get(
        "/api/v1/economics/repositories/101/202/attempts",
        params={"afterCursor": cursor, "limit": 100},
    )
    jobs = _client(use_case).get(
        "/api/v1/economics/repositories/101/202/attempts/303/2/jobs",
        params={"afterJobId": 403, "limit": 100, "headSha": "b" * 40},
    )

    assert attempts.status_code == 200
    assert jobs.status_code == 200
    assert use_case.calls == [
        ("list", (ACTOR, ATTEMPT.scope, cursor, 100)),
        ("jobs", (ACTOR, ATTEMPT, 403, 100)),
    ]


def test_ci_economics_rejects_page_bounds_before_use_case() -> None:
    use_case = _use_case()

    attempts = _client(use_case).get("/api/v1/economics/repositories/101/202/attempts?limit=101")
    jobs = _client(use_case).get(
        "/api/v1/economics/repositories/101/202/attempts/303/2/jobs",
        params={"afterJobId": 0, "headSha": "b" * 40},
    )

    assert (attempts.status_code, attempts.json()) == (422, {"code": "invalid_request"})
    assert (jobs.status_code, jobs.json()) == (422, {"code": "invalid_request"})
    assert use_case.calls == []


def test_ci_economics_rejects_a_noncanonical_calendar_cursor_before_use_case() -> None:
    use_case = _use_case()
    invalid_cursor = f"2026-99-99T10:00:00.000000Z.{'a' * 64}"

    response = _client(use_case).get(
        "/api/v1/economics/repositories/101/202/attempts",
        params={"afterCursor": invalid_cursor},
    )

    assert (response.status_code, response.json()) == (422, {"code": "invalid_request"})
    assert use_case.calls == []


def test_ci_economics_openapi_closes_both_route_shapes() -> None:
    document = _app(_use_case()).openapi()

    attempts = document["paths"][CI_ECONOMICS_ATTEMPTS_PATH]["get"]
    jobs = document["paths"][CI_ECONOMICS_JOBS_PATH]["get"]
    assert attempts["operationId"] == "list_repository_ci_economics_attempts"
    assert set(attempts["responses"]) == {"200", "401", "403", "422", "500", "503"}
    assert jobs["operationId"] == "get_ci_economics_attempt_jobs"
    assert set(jobs["responses"]) == {"200", "401", "403", "404", "422", "500", "503"}
    assert attempts["security"] == [{"ControlPlaneBearer": []}, {"ControlPlaneSession": []}]
    assert jobs["security"] == attempts["security"]


def test_ci_economics_profile_is_an_exact_projection_of_openapi() -> None:
    document = _app(_use_case()).openapi()
    profile = load_bundled_ci_economics_profile()

    for expected in profile.http_operations:
        operation = document["paths"][expected.path][expected.method.lower()]
        parameters = {item["name"]: item for item in operation["parameters"]}
        assert operation["operationId"] == expected.operation_id
        assert operation["security"] == [
            {"ControlPlaneBearer": []},
            {"ControlPlaneSession": []},
        ]
        assert parameters["limit"]["schema"]["default"] == expected.default_page_size
        assert parameters["limit"]["schema"]["maximum"] == expected.maximum_page_size
        assert expected.cursor_parameter in parameters
        if expected.identity_parameter is not None:
            assert parameters[expected.identity_parameter]["required"] is True


def _client(
    use_case: _UseCase,
    *,
    authenticated: bool = True,
    roles: frozenset[ControlPlaneRole] | None = None,
) -> TestClient:
    return TestClient(_app(use_case, authenticated=authenticated, roles=roles))


def _app(
    use_case: _UseCase,
    *,
    authenticated: bool = True,
    roles: frozenset[ControlPlaneRole] | None = None,
) -> FastAPI:
    principal = (
        (human_principal() if roles is None else human_principal(roles=roles))
        if authenticated
        else InvalidCredential()
    )
    return create_app(
        HttpRouteDependencies(
            ci_economics=CiEconomicsRouteDependencies(
                authenticator=StaticControlPlaneAuthenticator(principal),
                role_admission=StaticRoleAdmission(),
                use_case=use_case,
            )
        )
    )


def _use_case(
    *,
    jobs: AttemptJobsResult | None = None,
) -> _UseCase:
    snapshot_job = job(created_at=None, delivery_id=None)
    economics = derive_attempt_economics(retained_snapshot(snapshot_job), (job(),))
    summary = AttemptSummary(
        subject_id="c" * 64,
        attempt=ATTEMPT,
        contract_hash="d" * 64,
        planned_route="selected",
        job_count=1,
        snapshot_digest="e" * 64,
        recorded_at=NOW,
    )
    return _UseCase(
        summaries=AttemptSummariesAvailable(AttemptSummaryPage((summary,), None)),
        jobs=jobs or AttemptJobsAvailable(economics, economics.snapshot.jobs, None),
    )

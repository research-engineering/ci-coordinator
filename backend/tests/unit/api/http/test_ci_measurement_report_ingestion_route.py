from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import pytest
from ci_economics.report_ingestion_support import IDENTITY, REPORT, IngestionBoundary
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx2 import ASGITransport, AsyncClient

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    ActionsAuthenticationResult,
    AuthenticationDependencyUnavailable,
    ForbiddenIdentity,
    HttpRouteDependencies,
    InvalidCredential,
    MeasurementReportIngestionRouteDependencies,
)
from ci_coordinator.api.http.routers.ci_measurement_report_ingestion import REPORT_INGESTION_PATH
from ci_coordinator.ci_economics.ports import MeasurementReportWriteResult
from ci_coordinator.ci_economics.reports import MAX_REPORT_BYTES

BODY: dict[str, object] = {
    "repository": IDENTITY.repository,
    "ref": IDENTITY.ref,
    "eventName": IDENTITY.event_name,
    "executionSha": IDENTITY.execution_sha,
    "jobPage": 2,
    "report": REPORT.canonical_mapping(),
}
HEADERS = {"Authorization": "Bearer test-credential"}


@dataclass
class Authenticator:
    result: ActionsAuthenticationResult = IDENTITY
    calls: list[tuple[object, ...]] = field(default_factory=list)
    entered: asyncio.Event | None = None
    release: asyncio.Event | None = None

    async def authenticate_run(
        self,
        authorization: str | None,
        *,
        repository: str,
        repository_id: int,
        ref: str,
        run_id: int,
        run_attempt: int,
        event_name: str,
        execution_sha: str,
    ) -> ActionsAuthenticationResult:
        self.calls.append(
            (
                authorization,
                repository,
                repository_id,
                ref,
                run_id,
                run_attempt,
                event_name,
                execution_sha,
            )
        )
        if self.entered is not None and len(self.calls) == 2:
            self.entered.set()
        if self.release is not None:
            await self.release.wait()
        return self.result


def app(boundary: IngestionBoundary, authenticator: Authenticator, *, timeout: int = 5) -> FastAPI:
    return create_app(
        HttpRouteDependencies(
            ci_measurement_report_ingestion=MeasurementReportIngestionRouteDependencies(
                authenticator=authenticator,
                use_case=boundary.service(),
            )
        ),
        request_timeout_seconds=timeout,
        include_operator_ui=False,
    )


@pytest.mark.parametrize(
    "outcome,code",
    [
        ("recorded", 201),
        ("replayed", 200),
        ("report_conflict", 409),
        ("source_unavailable", 409),
        ("outside_retention", 410),
        ("capacity_reached", 429),
    ],
)
def test_response_preserves_report_identity_and_closed_durable_outcomes(
    outcome: MeasurementReportWriteResult,
    code: int,
) -> None:
    boundary, authenticator = IngestionBoundary(result=outcome), Authenticator()
    with TestClient(app(boundary, authenticator)) as client:
        response = client.post(REPORT_INGESTION_PATH, json=BODY, headers=HEADERS)
    assert response.status_code == code
    assert response.headers["cache-control"] == "no-store"
    assert authenticator.calls == [
        (
            "Bearer test-credential",
            "acme/service",
            202,
            "refs/pull/42/merge",
            303,
            2,
            "pull_request",
            "c" * 40,
        )
    ]
    assert boundary.calls[1] == ("bind", (REPORT, "acme/service", 2))
    if code < 300:
        assert response.json() == {
            "schemaVersion": "ci-economics-report-receipt/v1",
            "ok": True,
            "status": outcome,
            "reportId": REPORT.report_id,
            "reportDigest": REPORT.report_digest,
        }
    else:
        assert response.json() == {"ok": False, "error": outcome}


@pytest.mark.parametrize(
    "result,code,error",
    [
        (InvalidCredential(), 401, "unauthenticated"),
        (ForbiddenIdentity(), 403, "forbidden"),
        (AuthenticationDependencyUnavailable(), 503, "unavailable"),
    ],
)
def test_failed_authentication_cannot_reach_any_provider_or_storage(
    result: ActionsAuthenticationResult,
    code: int,
    error: str,
) -> None:
    boundary = IngestionBoundary()
    with TestClient(app(boundary, Authenticator(result))) as client:
        response = client.post(REPORT_INGESTION_PATH, json=BODY, headers=HEADERS)
    assert (response.status_code, response.json()) == (code, {"ok": False, "error": error})
    assert not boundary.calls
    if code == 401:
        assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.parametrize(
    "headers", [[], [("Authorization", "Bearer a"), ("authorization", "Bearer b")]]
)
def test_missing_or_ambiguous_bearer_is_rejected(headers: list[tuple[str, str]]) -> None:
    boundary, authenticator = IngestionBoundary(), Authenticator()
    with TestClient(app(boundary, authenticator)) as client:
        response = client.post(REPORT_INGESTION_PATH, json=BODY, headers=headers)
    assert response.status_code == 401
    assert not authenticator.calls and not boundary.calls


@pytest.mark.parametrize(
    "field,value",
    [
        ("repository", "acme/service/extra"),
        ("ref", ""),
        ("eventName", 42),
        ("executionSha", "c" * 39),
        ("executionSha", "C" * 40),
        ("jobPage", 0),
        ("jobPage", 21),
        ("jobPage", True),
        ("report", {}),
        ("extra", "secret-must-not-echo"),
    ],
)
def test_strict_envelope_is_rejected_before_authentication(field: str, value: object) -> None:
    boundary, authenticator = IngestionBoundary(), Authenticator()
    with TestClient(app(boundary, authenticator)) as client:
        response = client.post(REPORT_INGESTION_PATH, json={**BODY, field: value}, headers=HEADERS)
    assert response.status_code == 422
    assert "secret-must-not-echo" not in response.text
    assert not authenticator.calls and not boundary.calls


@pytest.mark.parametrize(
    "content,headers,code",
    [
        (b'{"jobPage":1,"jobPage":2}', {"Content-Type": "application/json"}, 400),
        (b"[" * 151, {"Content-Type": "application/json"}, 400),
        (b"{}", {"Content-Type": "text/plain"}, 400),
        (b"{}", {"Content-Type": "application/json", "Content-Encoding": "gzip"}, 400),
        (b"x" * (MAX_REPORT_BYTES + 1), {"Content-Type": "application/json"}, 413),
    ],
    ids=(
        "duplicate-key",
        "excessive-nesting",
        "wrong-media-type",
        "encoded-body",
        "oversized-body",
    ),
)
def test_raw_request_bounds_precede_framework_parsing(
    content: bytes,
    headers: dict[str, str],
    code: int,
) -> None:
    boundary, authenticator = IngestionBoundary(), Authenticator()
    with TestClient(app(boundary, authenticator)) as client:
        response = client.post(
            REPORT_INGESTION_PATH, content=content, headers={**HEADERS, **headers}
        )
    assert response.status_code == code
    assert not authenticator.calls and not boundary.calls


async def test_ingestion_has_no_waiting_queue_and_releases_slots() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    boundary, authenticator = IngestionBoundary(), Authenticator(entered=entered, release=release)
    async with AsyncClient(
        transport=ASGITransport(app(boundary, authenticator)), base_url="http://test"
    ) as client:
        requests = [
            asyncio.create_task(client.post(REPORT_INGESTION_PATH, json=BODY, headers=HEADERS))
            for _ in range(2)
        ]
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            refused = await client.post(REPORT_INGESTION_PATH, json=BODY, headers=HEADERS)
            assert refused.status_code == 503
            assert refused.json() == {"ok": False, "error": "overloaded"}
            assert len(authenticator.calls) == 2 and not boundary.calls
        finally:
            release.set()
            results = await asyncio.gather(*requests)
        assert all(result.status_code == 201 for result in results)
        assert (
            await client.post(REPORT_INGESTION_PATH, json=BODY, headers=HEADERS)
        ).status_code == 201


async def test_whole_request_deadline_covers_authentication() -> None:
    boundary, authenticator = IngestionBoundary(), Authenticator(release=asyncio.Event())
    async with AsyncClient(
        transport=ASGITransport(app(boundary, authenticator, timeout=1)), base_url="http://test"
    ) as client:
        response = await client.post(
            REPORT_INGESTION_PATH,
            content=json.dumps(BODY),
            headers={**HEADERS, "Content-Type": "application/json"},
        )
    assert response.status_code == 503
    assert response.json() == {"ok": False, "error": "unavailable"}
    assert not boundary.calls

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from email.message import Message
from http.client import responses
from io import BytesIO
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import HTTPSHandler, Request
from urllib.response import addinfourl

import pytest
from ci_economics.report_ingestion_support import REPORT

from ci_coordinator.api.http.ci_measurement_report_submission import MeasurementReportSubmission
from ci_coordinator.ci_economics.report_ingestion import measurement_report_audience
from ci_coordinator.target_artifacts.resources import ci_measurement_reporter as producer


class _HttpResponse(addinfourl):
    def __init__(self, request: Request, content: bytes, status: int) -> None:
        headers = Message()
        headers["Content-Type"] = "application/json"
        headers["Location"] = "https://foreign.example/collect"
        super().__init__(BytesIO(content), headers, request.full_url, status)
        self.msg = responses[status]


@dataclass
class HttpTransport:
    status: int = 200
    content: bytes = b'{"ok":true}'
    calls: list[Request] = field(default_factory=list)


@pytest.fixture
def http_transport(monkeypatch: pytest.MonkeyPatch) -> HttpTransport:
    transport = HttpTransport()

    def https_open(_handler: HTTPSHandler, request: Request) -> _HttpResponse:
        transport.calls.append(request)
        assert request.full_url == "https://coordinator.example/report"
        assert request.host == "coordinator.example" and not request.has_proxy()
        assert request.timeout == 5
        return _HttpResponse(request, transport.content, transport.status)

    monkeypatch.setattr(HTTPSHandler, "https_open", https_open)
    monkeypatch.setenv("https_proxy", "http://foreign.example:8080")
    monkeypatch.setenv("HTTPS_PROXY", "http://foreign.example:8080")
    monkeypatch.setenv("no_proxy", "")
    monkeypatch.setenv("NO_PROXY", "")
    return transport


@pytest.mark.parametrize("payload", [None, {"sample": "baseline"}])
@pytest.mark.parametrize("status", [200, 201])
def test_request_preserves_headers_body_deadline_and_disables_ambient_proxy(
    http_transport: HttpTransport, payload: dict[str, object] | None, status: int
) -> None:
    http_transport.status = status
    assert producer._request(
        "https://coordinator.example/report", "credential", payload=payload
    ) == {"ok": True}
    assert len(http_transport.calls) == 1
    request = http_transport.calls[0]
    assert request.get_header("Authorization") == "Bearer credential"
    assert request.get_header("Accept") == "application/json"
    assert request.get_header("X-github-api-version") == producer.API_VERSION
    assert request.get_method() == ("POST" if payload is not None else "GET")
    assert request.data == (None if payload is None else json.dumps(payload).encode())
    assert request.get_header("Content-type") == (None if payload is None else "application/json")


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 401, 503])
def test_request_does_not_follow_redirects_or_treat_failures_as_reports(
    http_transport: HttpTransport, status: int
) -> None:
    http_transport.status = status
    with pytest.raises(HTTPError) as rejected:
        producer._request("https://coordinator.example/report", "credential")
    assert rejected.value.code == status
    assert rejected.value.closed
    assert len(http_transport.calls) == 1


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"x" * 1_048_577, id="oversize"),
        pytest.param(b'{"ok":true,"ok":false}', id="duplicate"),
        pytest.param(b"[]", id="array"),
        pytest.param(b"{", id="malformed"),
    ],
)
def test_request_rejects_oversize_or_malformed_http_response(
    http_transport: HttpTransport, content: bytes
) -> None:
    http_transport.content = content
    with pytest.raises(ValueError):
        producer._request("https://coordinator.example/report", "credential")
    assert len(http_transport.calls) == 1


def _environment() -> dict[str, str]:
    return {
        "GITHUB_REPOSITORY": "acme/service",
        "GITHUB_REPOSITORY_ID": "202",
        "GITHUB_RUN_ID": "303",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_REF": "refs/pull/42/merge",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_SHA": "c" * 40,
        "CI_REPORT_GITHUB_TOKEN": "repository-secret",
        "ACTIONS_ID_TOKEN_REQUEST_URL": "https://pipelines.actions.githubusercontent.com/idtoken?api-version=2.0",
        "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "mint-secret",
    }


def _input() -> dict[str, object]:
    return {
        "endpoint": "https://coordinator.example",
        "audience": "ci-coordinator",
        "installationId": 101,
        "sampleKey": REPORT.sample_key,
        "producerDigest": REPORT.producer_digest,
        "workload": REPORT.workload.canonical_mapping(),
        "reportedAt": REPORT.canonical_mapping()["reportedAt"],
        "commandExitCode": REPORT.command_exit_code,
        "measurements": [item.canonical_mapping() for item in REPORT.measurements],
    }


class Network:
    def __init__(self, *, found_page: int | None = 2) -> None:
        self.calls: list[tuple[str, str, dict[str, object] | None]] = []
        hint = base64.urlsafe_b64encode(b'{"check_run_id":"405"}').decode().rstrip("=")
        self.token = f"unsigned.{hint}.lookup-only"
        self.found_page = found_page

    def request(
        self, url: str, token: str, *, payload: dict[str, object] | None = None
    ) -> dict[str, object]:
        self.calls.append((url, token, payload))
        if urlsplit(url).hostname == "pipelines.actions.githubusercontent.com":
            return {"value": self.token}
        if url.endswith("/attempts/2"):
            return {"id": 303, "run_attempt": 2, "head_sha": "b" * 40, "repository": {"id": 202}}
        if "/jobs?" in url:
            page = int(parse_qs(urlsplit(url).query)["page"][0])
            if page == self.found_page:
                return {
                    "jobs": [
                        {
                            "id": 404,
                            "run_id": 303,
                            "head_sha": "b" * 40,
                            "check_run_url": "https://api.github.com/repos/acme/service/check-runs/405",
                        }
                    ]
                }
            return {"jobs": [{"id": 1_000 + index} for index in range(100)]}
        assert url == "https://coordinator.example/api/v2/economics/reports"
        return {
            "schemaVersion": "ci-economics-report-receipt/v1",
            "ok": True,
            "status": "recorded",
            "reportId": REPORT.report_id,
            "reportDigest": REPORT.report_digest,
        }


def test_upload_builds_receiver_admitted_payload_without_claiming_local_jwt_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = Network()
    monkeypatch.setattr(producer, "_request", network.request)
    receipt = producer._upload(_input(), _environment())
    assert receipt["reportId"] == REPORT.report_id
    assert receipt["reportDigest"] == REPORT.report_digest
    assert len(json.dumps(receipt)) < 512
    assert len(network.calls) == 5
    assert parse_qs(urlsplit(network.calls[0][0]).query)["audience"] == [
        measurement_report_audience("ci-coordinator")
    ]
    assert network.calls[0][1] == "mint-secret"
    assert all(
        token == "repository-secret" and urlsplit(url).hostname == "api.github.com"
        for url, token, _ in network.calls[1:-1]
    )
    url, token, payload = network.calls[-1]
    assert token == network.token and url == "https://coordinator.example/api/v2/economics/reports"
    body = MeasurementReportSubmission.model_validate(payload)
    assert body.report.to_report() == REPORT
    assert body.job_page == 2 and body.execution_sha == "c" * 40
    serialized = json.dumps(payload)
    assert "secret" not in serialized and network.token not in serialized


@pytest.mark.parametrize(
    "mutation",
    [
        {"schemaVersion": "other"},
        {"ok": 1},
        {"ok": False},
        {"status": "unknown"},
        {"reportId": "0" * 64},
        {"reportDigest": "0" * 64},
        {"unowned": "private-secret"},
    ],
)
def test_receipt_requires_exact_closed_request_binding(
    mutation: dict[str, object], monkeypatch: pytest.MonkeyPatch
) -> None:
    network = Network()

    def request(
        url: str, token: str, *, payload: dict[str, object] | None = None
    ) -> dict[str, object]:
        response = network.request(url, token, payload=payload)
        return {**response, **mutation} if payload is not None else response

    monkeypatch.setattr(producer, "_request", request)
    with pytest.raises(ValueError, match="receipt"):
        producer._upload(_input(), _environment())


def test_job_lookup_never_walks_an_unbounded_population(monkeypatch: pytest.MonkeyPatch) -> None:
    network = Network(found_page=None)
    monkeypatch.setattr(producer, "_request", network.request)
    with pytest.raises(ValueError, match="within bound"):
        producer._upload(_input(), _environment())
    assert len(network.calls) == 22
    assert all(payload is None for _, _, payload in network.calls)


@pytest.mark.parametrize(
    "oidc_url",
    [
        "http://pipelines.actions.githubusercontent.com/idtoken?a=1",
        "https://pipelines.actions.githubusercontent.com.attacker.example/idtoken?a=1",
        "https://user@pipelines.actions.githubusercontent.com/idtoken?a=1",
        "https://pipelines.actions.githubusercontent.com:444/idtoken?a=1",
        "https://pipelines.actions.githubusercontent.com/idtoken?audience=other",
    ],
)
def test_mint_credential_cannot_leave_the_admitted_endpoint(
    oidc_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = Network()
    monkeypatch.setattr(producer, "_request", network.request)
    with pytest.raises(ValueError):
        producer._upload(_input(), {**_environment(), "ACTIONS_ID_TOKEN_REQUEST_URL": oidc_url})
    assert not network.calls


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://coordinator.example",
        "file:///tmp/x",
        "https://u:p@coordinator.example",
        "https://coordinator.example/path",
        "https://coordinator.example/?token=x",
    ],
)
def test_bad_coordinator_endpoint_is_rejected_before_credentials(
    endpoint: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    network = Network()
    monkeypatch.setattr(producer, "_request", network.request)
    with pytest.raises(ValueError):
        producer._upload({**_input(), "endpoint": endpoint}, _environment())
    assert not network.calls

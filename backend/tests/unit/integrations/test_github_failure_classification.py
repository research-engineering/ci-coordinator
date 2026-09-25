from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Never

import pytest

from ci_coordinator.integrations.github import (
    AppIdentityClient,
    GitHubHeader,
    GitHubIncomplete,
    GitHubPaginationEvidence,
    GitHubRateLimitEvidence,
    GitHubRequest,
    GitHubResponse,
    GitHubSuccess,
    GitHubUnavailable,
    _client,
)
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import GitHubOutcome, GitHubTransportResult
from ci_coordinator.integrations.github.governance_observation import (
    _unavailable as governance_unavailable,
)
from ci_coordinator.integrations.github.provider_inventory import (
    _unavailable as inventory_unavailable,
)

_MESSAGE = "You have exceeded a secondary rate limit"


@dataclass
class _Transport:
    response: GitHubResponse
    calls: int = 0

    async def send(self, _request: GitHubRequest) -> GitHubTransportResult:
        self.calls += 1
        return self.response


def _response(
    status: int = 403,
    *,
    version: str | None = GITHUB_API_VERSION,
    body: bytes = b"{}",
    headers: tuple[GitHubHeader, ...] = (),
    rate_limit: GitHubRateLimitEvidence | None = None,
) -> GitHubResponse:
    return GitHubResponse(
        status, version, headers, body, GitHubPaginationEvidence.not_paginated(), rate_limit
    )


def _classify(response: GitHubResponse) -> GitHubOutcome:
    transport = _Transport(response)
    result = asyncio.run(
        AppIdentityClient(transport, api_version=GITHUB_API_VERSION).get_installation(77)
    )
    assert transport.calls == 1
    return result


@pytest.mark.parametrize("secondary", [False, True])
@pytest.mark.parametrize("version", [GITHUB_API_VERSION, None, "2022-11-27"])
@pytest.mark.parametrize(
    "status", [200, 204, 299, 400, 401, 403, 404, 408, 429, 500, 501, 502, 503, 504, 505, 599]
)
def test_status_version_and_body_matrix_preserves_exact_failure_precedence(
    status: int, version: str | None, secondary: bool
) -> None:
    body = json.dumps({"message": _MESSAGE if secondary else "Resource not accessible"}).encode()
    response = _response(status, version=version, body=body)
    result = _classify(response)
    if version == GITHUB_API_VERSION and 200 <= status < 300:
        assert isinstance(result, GitHubSuccess)
        assert result.response is response
        return
    assert isinstance(result, GitHubUnavailable)
    if version is None and status not in {500, 502, 503, 504}:
        expected = "missing_api_version_provenance"
    elif version not in {None, GITHUB_API_VERSION}:
        expected = "api_version_provenance_mismatch"
    elif status == 429 or (status == 403 and secondary):
        expected = "rate_limited"
    else:
        expected = {403: "forbidden", 404: "not_found", 408: "timeout", 504: "timeout"}.get(
            status, "non_success"
        )
    assert result.failure.kind == expected
    assert result.failure.response is response
    assert body.decode() not in result.failure.message
    if status in {500, 502, 503, 504} and version is None:
        assert inventory_unavailable(result).reason == "unavailable"
        assert governance_unavailable(result).reason == "unavailable"


@pytest.mark.parametrize("status", [200, 403, 429, 500, 502, 503, 504])
@pytest.mark.parametrize(
    "values", [("",), (GITHUB_API_VERSION, GITHUB_API_VERSION), (GITHUB_API_VERSION, "wrong")]
)
def test_present_or_ambiguous_version_header_cannot_use_missing_header_exception(
    status: int, values: tuple[str, ...]
) -> None:
    response = _response(
        status,
        version=None,
        headers=tuple(GitHubHeader("X-GitHub-Api-Version-Selected", value) for value in values),
    )
    result = _classify(response)
    assert isinstance(result, GitHubUnavailable)
    assert result.failure.kind == "missing_api_version_provenance"
    assert result.failure.response is response


@pytest.mark.parametrize(
    ("status", "evidence"),
    [
        (403, GitHubRateLimitEvidence(None, 0, None, None)),
        (403, GitHubRateLimitEvidence(None, 12, None, "60")),
        (429, None),
    ],
)
def test_header_rate_controls_precede_body_decoding(
    monkeypatch: pytest.MonkeyPatch, status: int, evidence: GitHubRateLimitEvidence | None
) -> None:
    def unexpected_body(_body: bytes) -> Never:
        raise AssertionError("existing status/header evidence must precede error-body parsing")

    monkeypatch.setattr(_client, "has_secondary_rate_limit_message", unexpected_body)
    result = _classify(_response(status, body=b"not JSON", rate_limit=evidence))
    assert isinstance(result, GitHubUnavailable)
    assert result.failure.kind == "rate_limited"
    assert result.failure.rate_limit is evidence


@pytest.mark.parametrize(
    "document",
    [
        {"message": _MESSAGE},
        {"message": _MESSAGE + "."},
        {
            "message": _MESSAGE + ". Please wait a few minutes before you try again.",
            "documentation_url": "https://docs.github.com/rest/using-the-rest-api/rate-limits-for-the-rest-api",
            "status": "403",
        },
    ],
)
def test_documented_secondary_message_shape_is_redacted_without_invented_delay(
    document: dict[str, object],
) -> None:
    body = json.dumps(document).encode()
    result = _classify(
        _response(body=body, rate_limit=GitHubRateLimitEvidence(5000, 12, None, None))
    )
    assert isinstance(result, GitHubUnavailable)
    assert result.failure.kind == "rate_limited"
    assert result.failure.message == "GitHub operation returned HTTP 403"
    for projected in (inventory_unavailable(result), governance_unavailable(result)):
        assert projected.reason == "rate_limited"
        assert projected.retry_after_seconds is None
        assert _MESSAGE not in repr(projected)


@pytest.mark.parametrize(
    "document",
    [
        None,
        [],
        {},
        {"message": False},
        {"message": 403},
        {"message": {"message": _MESSAGE}},
        {"message": "Not " + _MESSAGE},
        {"message": _MESSAGE + "s"},
        {"message": _MESSAGE.lower()},
        {"message": _MESSAGE + ".\nprovider private text"},
        {
            "message": "forbidden",
            "documentation_url": "https://docs.github.com/#secondary-rate-limits",
        },
        {"message": _MESSAGE, "documentation_url": {}},
        {"message": _MESSAGE, "documentation_url": "bad\ntext"},
        {"message": _MESSAGE, "status": 403},
        {"message": _MESSAGE, "status": "404"},
        {"message": _MESSAGE, "errors": []},
        {"message": _MESSAGE, "errors": [[[[[]]]]]},
        {"message": _MESSAGE, "documentation_url": "x" * 1_025},
    ],
)
def test_unadmitted_error_shapes_remain_forbidden(document: object) -> None:
    result = _classify(_response(body=json.dumps(document).encode()))
    assert isinstance(result, GitHubUnavailable)
    assert result.failure.kind == "forbidden"


@pytest.mark.parametrize(
    "body",
    [
        b"{",
        b"\xff",
        b'{"message":"You have exceeded a secondary rate limit","message":"forbidden"}',
        b'{"message":"You have exceeded a secondary rate limit","status":"403","status":"403"}',
        b'{"message":"You have exceeded a secondary rate limit. \\ud800"}',
        b'{"message": NaN}',
    ],
)
def test_non_strict_json_cannot_supply_message_evidence(body: bytes) -> None:
    result = _classify(_response(body=body))
    assert isinstance(result, GitHubUnavailable)
    assert result.failure.kind == "forbidden"


@pytest.mark.parametrize("extra", [0, 1])
def test_secondary_error_body_and_message_limits_are_exact(extra: int) -> None:
    message = _MESSAGE + ". " + "x" * (1_024 + extra - len(_MESSAGE) - 2)
    result = _classify(_response(body=json.dumps({"message": message}).encode()))
    assert isinstance(result, GitHubUnavailable)
    assert result.failure.kind == ("rate_limited" if extra == 0 else "forbidden")
    body = json.dumps({"message": _MESSAGE}).encode()
    padded = body + b" " * (4_096 + extra - len(body))
    result = _classify(_response(body=padded))
    assert isinstance(result, GitHubUnavailable)
    assert result.failure.kind == ("rate_limited" if extra == 0 else "forbidden")


@pytest.mark.parametrize(
    "body",
    [
        b"{}" + b" " * 4_095,
        b'{"message":"You have exceeded a secondary rate limit","extra":[[[0]]]}',
        b'{"message":"You have exceeded a secondary rate limit","extra":[' + b"0," * 16 + b"0]}",
    ],
    ids=["bytes", "depth", "nodes"],
)
def test_error_resource_preflight_precedes_host_object_construction(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    positive = _classify(_response(body=json.dumps({"message": _MESSAGE}).encode()))
    assert isinstance(positive, GitHubUnavailable)
    assert positive.failure.kind == "rate_limited"

    def unexpected_load(*_args: object, **_kwargs: object) -> Never:
        raise AssertionError("resource refusal must precede JSON object construction")

    monkeypatch.setattr("ci_coordinator.kernel.strict_json.json.loads", unexpected_load)
    result = _classify(_response(body=body))
    assert isinstance(result, GitHubUnavailable)
    assert result.failure.kind == "forbidden"


def test_version_bound_success_and_incomplete_outcomes_do_not_parse_error_messages() -> None:
    response = _response(200, body=b"not JSON")
    assert isinstance(_classify(response), GitHubSuccess)
    incomplete = GitHubResponse(
        200,
        GITHUB_API_VERSION,
        (),
        response.body,
        GitHubPaginationEvidence(False, 1, 100, "next", "next_page"),
    )
    result = _classify(incomplete)
    assert isinstance(result, GitHubIncomplete)
    assert result.response is incomplete


def test_missing_configured_version_still_stops_before_transport() -> None:
    transport = _Transport(_response(503, version=None))
    result = asyncio.run(AppIdentityClient(transport, api_version=None).get_installation(77))
    assert isinstance(result, GitHubUnavailable)
    assert result.failure.kind == "missing_api_version_provenance"
    assert transport.calls == 0


def test_cancellation_is_not_a_redacted_provider_failure() -> None:
    class CancelledTransport:
        async def send(self, _request: GitHubRequest) -> GitHubTransportResult:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            AppIdentityClient(
                CancelledTransport(), api_version=GITHUB_API_VERSION
            ).get_installation(77)
        )

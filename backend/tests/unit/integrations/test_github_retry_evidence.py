from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone

import httpx2 as httpx
import pytest
from cryptography.hazmat.primitives import serialization

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_observation import GovernanceObservationUnavailable
from ci_coordinator.integrations.github import (
    GitHubAppTransportFactory,
    GitHubGovernanceObservationReader,
    GitHubHeader,
    GitHubPaginationEvidence,
    GitHubProviderInventory,
    GitHubRateLimitEvidence,
    GitHubRequest,
    GitHubResponse,
    GitHubSuccess,
    GitHubTransportFailure,
    GitHubUnavailable,
)
from ci_coordinator.integrations.github._client import GitHubProtocolClient
from ci_coordinator.integrations.github._rate_limits import retry_after_seconds
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import GitHubFailure
from ci_coordinator.integrations.github.governance_observation import (
    _unavailable as governance_unavailable,
)
from ci_coordinator.integrations.github.provider_inventory import (
    _unavailable as inventory_unavailable,
)
from ci_coordinator.provider_inventory import ProviderInventoryUnavailable

from ._github_app_transport_support import NOW, _private_key, _token_response

_REQUEST = GitHubRequest(
    "app_identity.get_installation", "GET", "/app/installations/77", GITHUB_API_VERSION
)


def _reset(offset: int) -> str:
    return str(int(NOW.timestamp()) + offset)


def _failure(
    *,
    remaining: int | None = 0,
    reset: str | None = None,
    retry: str | None = None,
    received: datetime | None = NOW,
    headers: tuple[GitHubHeader, ...] = (),
) -> GitHubFailure:
    evidence = GitHubRateLimitEvidence(5000, remaining, reset, retry)
    response = GitHubResponse(
        403,
        GITHUB_API_VERSION,
        headers,
        b"provider-private-error",
        GitHubPaginationEvidence.not_paginated(),
        evidence,
        received,
    )
    return GitHubFailure("rate_limited", _REQUEST, "rate limited", response, evidence)


def _assert_projection(failure: GitHubFailure, expected: int | None) -> None:
    assert retry_after_seconds(failure) == expected
    for projected in (
        inventory_unavailable(GitHubUnavailable(failure)),
        governance_unavailable(GitHubUnavailable(failure)),
    ):
        assert projected.reason == "rate_limited"
        assert projected.retry_after_seconds == expected
        assert "provider-private-error" not in repr(projected)


@pytest.mark.parametrize("offset", [-60, 0, 1, 60, 3_600, 3_601])
@pytest.mark.parametrize("microseconds", [0, 250_000, 999_999])
def test_primary_reset_rounds_up_without_shortening_an_unrepresentable_wait(
    offset: int, microseconds: int
) -> None:
    received = NOW.replace(microsecond=microseconds)
    expected = max(0, offset) if offset <= 3_600 else None
    _assert_projection(_failure(reset=_reset(offset), received=received), expected)


@pytest.mark.parametrize(
    ("remaining", "reset", "retry", "received", "expected"),
    [
        (0, _reset(90), "30", NOW, 90),
        (0, _reset(30), "90", NOW, 90),
        (0, _reset(30), "3601", NOW, None),
        (0, _reset(3_601), "30", NOW, None),
        (0, _reset(30), "bad", NOW, None),
        (0, "bad", "30", NOW, None),
        (0, _reset(30), "30", None, None),
        (0, _reset(30), None, None, None),
        (0, None, "30", None, 30),
        (0, None, None, NOW, None),
        (12, _reset(7_200), "30", NOW, 30),
        (12, _reset(60), None, NOW, None),
        (None, _reset(60), None, NOW, None),
        (0, _reset(30), None, NOW.astimezone(timezone(timedelta(hours=5))), 30),
    ],
)
def test_only_applicable_explicit_constraints_contribute_to_retry_metadata(
    remaining: int | None,
    reset: str | None,
    retry: str | None,
    received: datetime | None,
    expected: int | None,
) -> None:
    _assert_projection(
        _failure(remaining=remaining, reset=reset, retry=retry, received=received), expected
    )


@pytest.mark.parametrize(
    ("retry", "expected"),
    [
        ("0", 0),
        ("0030", 30),
        ("3600", 3600),
        ("3601", None),
        ("9999", None),
        ("10000", None),
        ("", None),
        (" 30", None),
        ("-1", None),
        ("+30", None),
        ("1.5", None),
        ("\u0661", None),
        ("Wed, 01 Jan 2026 00:00:00 GMT", None),
    ],
)
def test_explicit_retry_seconds_keep_existing_grammar(retry: str, expected: int | None) -> None:
    _assert_projection(_failure(retry=retry), expected)


@pytest.mark.parametrize(
    "reset",
    [
        "",
        "-1",
        "+1",
        " 1",
        "1.0",
        "1_000",
        "\u0661",
        "2026-01-01T00:00:00Z",
        "253402300800",
        "9" * 5_000,
    ],
)
def test_malformed_or_out_of_domain_reset_cannot_yield_a_shorter_retry(reset: str) -> None:
    _assert_projection(_failure(reset=reset, retry="30"), None)


@pytest.mark.parametrize("name", ["retry-after", "x-ratelimit-remaining", "x-ratelimit-reset"])
@pytest.mark.parametrize("conflicting", [False, True])
def test_duplicate_header_field_lines_make_timing_unknown(name: str, conflicting: bool) -> None:
    value = {"retry-after": "30", "x-ratelimit-remaining": "0", "x-ratelimit-reset": _reset(60)}[
        name
    ]
    headers = (
        GitHubHeader(name, value),
        GitHubHeader(name.upper(), "999999999999" if conflicting else value),
    )
    _assert_projection(_failure(reset=_reset(60), retry="30", headers=headers), None)


def test_legacy_response_without_receipt_time_keeps_reset_unknown() -> None:
    evidence = GitHubRateLimitEvidence(5000, 0, _reset(60), None)
    legacy = GitHubResponse(
        403, GITHUB_API_VERSION, (), b"{}", GitHubPaginationEvidence.not_paginated(), evidence
    )
    assert legacy.received_at is None
    failure = GitHubFailure("rate_limited", _REQUEST, "rate limited", legacy, evidence)
    _assert_projection(failure, None)
    _assert_projection(replace(failure, response=None), None)
    assert retry_after_seconds(replace(failure, kind="forbidden")) is None
    with pytest.raises(ValueError, match="aware datetime"):
        replace(legacy, received_at=NOW.replace(tzinfo=None))


@dataclass
class _Clock:
    instant: datetime = NOW
    reads: list[datetime] = field(default_factory=list)

    def now(self) -> datetime:
        self.reads.append(self.instant)
        return self.instant


@pytest.mark.parametrize("adapter", ["inventory", "governance"])
@pytest.mark.parametrize("status", [403, 429])
@pytest.mark.parametrize("duplicate_reset", [False, True])
def test_real_transport_binds_retry_to_post_exchange_clock_for_both_adapters(
    adapter: str, status: int, duplicate_reset: bool
) -> None:
    clock = _Clock()
    received = NOW + timedelta(seconds=60, microseconds=250_000)
    calls: list[str] = []
    body_reads: list[bool] = []

    class Body(httpx.AsyncByteStream):
        async def __aiter__(self) -> AsyncIterator[bytes]:
            clock.instant = received
            body_reads.append(True)
            yield b'{"message":"provider-private-error"}'

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        assert request.url.path == (
            "/app/installations/77" if adapter == "inventory" else "/repositories/501"
        )
        headers = [
            ("x-github-api-version-selected", GITHUB_API_VERSION),
            ("x-ratelimit-remaining", "0"),
            ("x-ratelimit-reset", _reset(120)),
            ("retry-after", "30"),
            ("date", "Wed, 01 Jan 2040 00:00:00 GMT"),
        ]
        if duplicate_reset:
            headers.append(("X-RateLimit-Reset", _reset(7_200)))
        return httpx.Response(status, headers=headers, stream=Body())

    async def scenario() -> None:
        pem = (
            _private_key()
            .private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            .decode()
        )
        factory = GitHubAppTransportFactory(
            app_id="12345", private_key_pem=pem, clock=clock, transport=httpx.MockTransport(handler)
        )
        try:
            result = (
                await GitHubProviderInventory(factory).get_installation(77)
                if adapter == "inventory"
                else await GitHubGovernanceObservationReader(factory).read(
                    scope=RepositoryScope(77, 501)
                )
            )
        finally:
            await factory.aclose()
        assert isinstance(result, ProviderInventoryUnavailable | GovernanceObservationUnavailable)
        assert result.reason == "rate_limited"
        assert result.retry_after_seconds == (None if duplicate_reset else 60)
        assert "provider-private-error" not in repr(result)

    asyncio.run(scenario())
    assert body_reads == [True]
    assert clock.reads[-1] == received
    assert calls == (
        ["/app/installations/77"]
        if adapter == "inventory"
        else ["/app/installations/77/access_tokens", "/repositories/501"]
    )


@pytest.mark.parametrize("status", [200, 503])
@pytest.mark.parametrize(
    "versions", [(), (GITHUB_API_VERSION,), ("",), ("wrong",), (GITHUB_API_VERSION, "wrong")]
)
def test_transport_version_cardinality_and_success_path_need_no_receipt_clock(
    status: int, versions: tuple[str, ...]
) -> None:
    class UnusedClock:
        def now(self) -> datetime:
            raise AssertionError("success and 5xx must not sample the new receipt clock")

    async def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            headers=[("X-GitHub-Api-Version-Selected", value) for value in versions],
            content=b"{}",
        )

    async def scenario() -> None:
        factory = GitHubAppTransportFactory(
            app_id="12345",
            private_key_pem="unused on reviewer plane",
            clock=UnusedClock(),
            transport=httpx.MockTransport(handler),
        )
        request = GitHubRequest("reviewer_attestation.get_user", "GET", "/user", GITHUB_API_VERSION)
        try:
            transport = factory.for_reviewer("ephemeral-token")
            result = await GitHubProtocolClient(transport, api_version=GITHUB_API_VERSION)._send(
                request
            )
            rejected = await transport.send(replace(request, operation="foreign.operation"))
        finally:
            await factory.aclose()
        assert isinstance(rejected, GitHubTransportFailure)
        if status == 200 and versions == (GITHUB_API_VERSION,):
            assert isinstance(result, GitHubSuccess)
            response = result.response
        else:
            assert isinstance(result, GitHubUnavailable)
            expected = (
                "api_version_provenance_mismatch"
                if versions in {("",), ("wrong",)}
                else "non_success"
                if status == 503 and versions in {(), (GITHUB_API_VERSION,)}
                else "missing_api_version_provenance"
            )
            assert result.failure.kind == expected
            retained_response = result.failure.response
            assert retained_response is not None
            response = retained_response
        assert response.received_at is None
        assert (
            tuple(
                h.value
                for h in response.headers
                if h.name.casefold() == "x-github-api-version-selected"
            )
            == versions
        )

    asyncio.run(scenario())

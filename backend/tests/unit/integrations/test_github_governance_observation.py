from __future__ import annotations

import asyncio
import json

import httpx2 as httpx
import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_observation import (
    GovernanceFailureReason,
    GovernanceObservationUnavailable,
    GovernanceReadResult,
    GovernanceState,
)
from ci_coordinator.integrations.github import (
    GitHubAppTransportFactory,
    GitHubGovernanceObservationReader,
)
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION

from ._github_app_transport_support import _factory, _private_key, _token_response

SCOPE = RepositoryScope(77, 501)
RULES_PATH = "/repos/example/repo/rules/branches/master"


def test_reader_binds_terminal_traversal_to_stable_numeric_repository_identity() -> None:
    requests: list[str] = []
    rule = _rule("required_status_checks", extension={"provider_addition": True})

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        return _provider_response(request, rules=(rule,))

    result = asyncio.run(_read(_factory(_private_key(), httpx.MockTransport(handler))))

    assert isinstance(result, GovernanceState)
    assert result.repository.scope == SCOPE
    assert result.repository.owner_id == 101
    assert result.rules[0].canonical_text == json.dumps(
        rule,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert requests == [
        "/app/installations/77/access_tokens",
        "/repositories/501",
        RULES_PATH,
        "/repositories/501",
    ]


@pytest.mark.parametrize("path", [RULES_PATH, "/repositories/501/rules/branches/master"])
def test_reader_follows_only_exact_sequential_pagination_and_sorts_provider_order(
    path: str,
) -> None:
    first = _rule("z-rule")
    second = _rule("a-rule")

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == RULES_PATH and request.url.params["page"] == "1":
            return _response(
                _json([first]),
                link=(f'<https://api.github.com{path}?page=2&per_page=100>; rel="next"'),
            )
        return _provider_response(request, rules=(second,))

    result = asyncio.run(_read(_factory(_private_key(), httpx.MockTransport(handler))))

    assert isinstance(result, GovernanceState)
    assert [rule.rule_type for rule in result.rules] == ["a-rule", "z-rule"]


@pytest.mark.parametrize(
    ("mutation", "expected_reason"),
    [
        ("identity", "provider_binding_mismatch"),
        ("duplicate", "malformed_provider_response"),
        ("depth", "observation_limit_exceeded"),
        ("bad-next", "malformed_provider_response"),
    ],
)
def test_reader_rejects_binding_pagination_and_resource_countermodels(
    mutation: str,
    expected_reason: GovernanceFailureReason,
) -> None:
    repository_reads = 0
    rule = _rule("required_status_checks")

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal repository_reads
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        if request.url.path == "/repositories/501":
            repository_reads += 1
            owner = "other" if mutation == "identity" and repository_reads == 2 else "example"
            return _repository_response(owner=owner)
        if request.url.path == RULES_PATH:
            if mutation == "duplicate":
                return _response(_json([rule, rule]))
            if mutation == "depth":
                nested: object = "leaf"
                for _ in range(18):
                    nested = [nested]
                return _response(_json([_rule("deep", extension={"nested": nested})]))
            if mutation == "bad-next":
                return _response(
                    _json([rule]),
                    link=(f'<https://api.github.com{RULES_PATH}?page=3&per_page=100>; rel="next"'),
                )
            return _response(_json([rule]))
        raise AssertionError(f"unexpected request: {request.url}")

    result = asyncio.run(_read(_factory(_private_key(), httpx.MockTransport(handler))))

    assert result == GovernanceObservationUnavailable(expected_reason)


@pytest.mark.parametrize(
    ("status", "headers", "expected"),
    [
        (
            429,
            {"retry-after": "20"},
            GovernanceObservationUnavailable("rate_limited", 20),
        ),
        (404, {}, GovernanceObservationUnavailable("not_found")),
    ],
)
def test_reader_preserves_second_repository_read_failure(
    status: int,
    headers: dict[str, str],
    expected: GovernanceObservationUnavailable,
) -> None:
    repository_reads = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal repository_reads
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        if request.url.path == "/repositories/501":
            repository_reads += 1
            if repository_reads == 2:
                return httpx.Response(
                    status,
                    headers={
                        "x-github-api-version-selected": GITHUB_API_VERSION,
                        **headers,
                    },
                    json={"message": "provider diagnostic"},
                )
            return _repository_response()
        if request.url.path == RULES_PATH:
            return _response(_json([_rule("required_status_checks")]))
        raise AssertionError(f"unexpected request: {request.url}")

    result = asyncio.run(_read(_factory(_private_key(), httpx.MockTransport(handler))))

    assert result == expected
    assert "provider diagnostic" not in repr(result)


@pytest.mark.parametrize(
    ("owner", "name", "default_branch"),
    [
        ("example", "repo", "a" * 513),
        (
            "\N{LATIN SMALL LETTER E WITH ACUTE}" * 256,
            "\N{LATIN SMALL LETTER E WITH ACUTE}" * 256,
            "\N{LATIN SMALL LETTER E WITH ACUTE}" * 256,
        ),
    ],
)
def test_reader_rejects_provider_identity_that_cannot_form_a_bounded_rule_path(
    owner: str,
    name: str,
    default_branch: str,
) -> None:
    requests: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path.endswith("/access_tokens"):
            return _token_response()
        if request.url.path == "/repositories/501":
            return _repository_response(
                owner=owner,
                name=name,
                default_branch=default_branch,
            )
        raise AssertionError(f"unexpected request: {request.url}")

    result = asyncio.run(_read(_factory(_private_key(), httpx.MockTransport(handler))))

    assert result == GovernanceObservationUnavailable("malformed_provider_response")
    assert requests == ["/app/installations/77/access_tokens", "/repositories/501"]


async def _read(factory: GitHubAppTransportFactory) -> GovernanceReadResult:
    try:
        return await GitHubGovernanceObservationReader(factory).read(scope=SCOPE)
    finally:
        await factory.aclose()


def _provider_response(
    request: httpx.Request,
    *,
    rules: tuple[dict[str, object], ...],
) -> httpx.Response:
    if request.url.path.endswith("/access_tokens"):
        return _token_response()
    if request.url.path == "/repositories/501":
        return _repository_response()
    if request.url.path == RULES_PATH:
        return _response(_json(list(rules)))
    raise AssertionError(f"unexpected request: {request.url}")


def _repository_response(
    *,
    owner: str = "example",
    name: str = "repo",
    default_branch: str = "master",
) -> httpx.Response:
    return _response(
        _json(
            {
                "default_branch": default_branch,
                "full_name": f"{owner}/{name}",
                "id": 501,
                "name": name,
                "owner": {"id": 101, "login": owner},
            }
        )
    )


def _rule(
    rule_type: str,
    *,
    ruleset_id: int = 41,
    extension: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "parameters": {},
        "ruleset_id": ruleset_id,
        "ruleset_source": "example/repo",
        "ruleset_source_type": "Repository",
        "type": rule_type,
        **({} if extension is None else extension),
    }


def _response(body: bytes, *, link: str | None = None) -> httpx.Response:
    headers = {
        "content-type": "application/json",
        "x-github-api-version-selected": GITHUB_API_VERSION,
    }
    if link is not None:
        headers["link"] = link
    return httpx.Response(200, headers=headers, content=body)


def _json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

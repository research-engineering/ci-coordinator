from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from schemathesis.core.failures import FailureGroup
from schemathesis.core.result import Ok
from schemathesis.core.transport import Response
from schemathesis.python import asgi

from .harness import Harness, schemathesis_config
from .oracles import validate_response
from .profile import (
    HEADERS,
    OPERATIONS,
    REQUEST_TIMEOUT_SECONDS,
    STATUS_PATH,
    VALIDATION_PATH,
    WORKBENCH_PATH,
    campaign_from_environment,
)
from .test_controls import positive_case


def test_schema_is_raw_bound_and_exactly_three_operations(harness: Harness) -> None:
    results = list(harness.schema.get_all_operations())
    assert all(isinstance(result, Ok) for result in results)
    operations = [result.ok() for result in results if isinstance(result, Ok)]
    assert {(operation.method.upper(), operation.path) for operation in operations} == set(
        OPERATIONS
    )
    assert len(operations) == 3
    assert all(operation.app is harness.app for operation in operations)
    assert harness.schema.raw_schema == harness.app.openapi()
    assert harness.ports.starts == harness.ports.stops == 0
    assert not harness.app.dependency_overrides


def test_each_example_resets_ports_cookies_roles_and_lifespan(harness: Harness) -> None:
    initial_headers = dict(HEADERS)
    with harness.example() as first:
        first.cookies.set("leftover", "synthetic")
        case = positive_case(harness, WORKBENCH_PATH)
        case.call(session=first, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        assert harness.ports.workbench_calls
        assert harness.ports.starts == 1
        assert harness.ports.stops == 0
    assert harness.ports.stops == 1
    with harness.example() as second:
        assert second is not first
        assert not second.cookies
        assert not harness.ports.workbench_calls
        assert not harness.ports.authentications
        case = positive_case(harness, VALIDATION_PATH)
        response = case.call(session=second, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        validate_response(case, response, harness.ports)
    assert harness.ports.starts == harness.ports.stops == 2
    assert dict(HEADERS) == initial_headers


def test_session_close_alone_does_not_end_registry_lifespan(harness: Harness) -> None:
    try:
        with asgi.get_client(cast(asgi.ASGIApp, harness.app)):
            assert harness.ports.starts == 1
        assert harness.ports.stops == 0
    finally:
        asgi.shutdown_lifespans()
    assert harness.ports.stops == 1
    asgi.shutdown_lifespans()
    assert harness.ports.stops == 1


def test_exception_still_stops_lifespan(harness: Harness) -> None:
    with pytest.raises(RuntimeError, match="controlled example failure"), harness.example():
        raise RuntimeError("controlled example failure")
    assert harness.ports.starts == harness.ports.stops == 1


@pytest.mark.parametrize("mode,examples", [("fast", 10), ("deep", 60)])
def test_profile_preserves_finite_generation_and_shrinking(mode: str, examples: int) -> None:
    campaign = campaign_from_environment({"CI_COORDINATOR_API_CAMPAIGN": mode})
    config = schemathesis_config(campaign)
    assert campaign.max_examples == examples
    assert config.seed == 20260919
    assert len(OPERATIONS) == 3


@pytest.mark.parametrize(
    "environment",
    [
        {"CI_COORDINATOR_API_CAMPAIGN": "unbounded"},
        {"CI_COORDINATOR_API_SEED": "-1"},
        {"CI_COORDINATOR_API_SEED": "4294967296"},
        {"CI_COORDINATOR_API_SEED": "1.0"},
        {"CI_COORDINATOR_API_SEED": " 1"},
        {"CI_COORDINATOR_API_SEED": ""},
    ],
)
def test_invalid_campaign_is_not_silently_replaced(environment: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="CI_COORDINATOR_API_"):
        campaign_from_environment(environment)


@pytest.mark.parametrize(
    "mutation,failure",
    [
        ("body", "JsonSchemaError"),
        ("status", "UndefinedStatusCode"),
        ("media", "MalformedMediaType"),
    ],
)
def test_response_oracle_rejects_controlled_violation(
    harness: Harness, mutation: str, failure: str
) -> None:
    with harness.example() as client:
        case = positive_case(harness, WORKBENCH_PATH)
        healthy = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        validate_response(case, healthy, harness.ports)
        headers = dict(healthy.headers)
        original_payload = healthy.json()
        payload = deepcopy(original_payload)
        if mutation == "body":
            payload["configEpochs"][0]["active"] = "not-a-boolean"
        if mutation == "media":
            headers["content-type"] = ["not-a-media-type"]
        corrupted = Response(
            status_code=299 if mutation == "status" else healthy.status_code,
            headers=headers,
            content=json.dumps(payload).encode(),
            request=healthy.request,
            elapsed=healthy.elapsed,
            verify=healthy.verify,
        )
        with pytest.raises(FailureGroup) as caught:
            validate_response(case, corrupted, harness.ports)
        assert failure in {type(error).__name__ for error in caught.value.exceptions}
        assert healthy.json() == original_payload == json.loads(healthy.content)
        validate_response(case, healthy, harness.ports)


@pytest.mark.parametrize("path", [path for _, path in OPERATIONS])
def test_auth_response_oracle_rejects_wrong_body_only(harness: Harness, path: str) -> None:
    with harness.example() as client:
        case = positive_case(harness, path)
        healthy = case.call(session=client, timeout=REQUEST_TIMEOUT_SECONDS)
        assert healthy.status_code == 401
        validate_response(case, healthy, harness.ports)
        original_payload = healthy.json()
        payload = deepcopy(original_payload)
        payload["ok"] = True
        corrupted = Response(
            status_code=401,
            headers=healthy.headers,
            content=json.dumps(payload).encode(),
            request=healthy.request,
            elapsed=healthy.elapsed,
            verify=healthy.verify,
        )
        with pytest.raises(FailureGroup) as caught:
            validate_response(case, corrupted, harness.ports)
        assert "JsonSchemaError" in {type(error).__name__ for error in caught.value.exceptions}
        assert healthy.json() == original_payload == json.loads(healthy.content)
        validate_response(case, healthy, harness.ports)


def test_exact_case_replay_repeats_oracle_failure_then_healthy_control(harness: Harness) -> None:
    case = positive_case(harness, WORKBENCH_PATH)
    serialized = json.dumps({"path_parameters": case.path_parameters, "query": case.query})
    for candidate in (case, harness.schema[WORKBENCH_PATH]["GET"].Case(**json.loads(serialized))):
        with harness.example() as client:
            healthy = candidate.call(
                session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS
            )
            validate_response(candidate, healthy, harness.ports)
            headers = {**healthy.headers, "cache-control": ["public"]}
            corrupted = Response(
                status_code=200,
                headers=headers,
                content=healthy.content,
                request=healthy.request,
                elapsed=healthy.elapsed,
                verify=healthy.verify,
            )
            with pytest.raises(AssertionError, match="Missing no-store"):
                validate_response(candidate, corrupted, harness.ports)
            validate_response(candidate, healthy, harness.ports)


def test_contextual_oracle_rejects_spurious_config_422(harness: Harness) -> None:
    with harness.example() as client:
        case = positive_case(harness, VALIDATION_PATH)
        healthy = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        validate_response(case, healthy, harness.ports)
        corrupted = Response(
            status_code=422,
            headers=healthy.headers,
            content=b'{"ok":false,"error":"invalid_config","diagnostics":[]}',
            request=healthy.request,
            elapsed=healthy.elapsed,
            verify=healthy.verify,
        )
        with pytest.raises(AssertionError, match="Admitted policy was rejected"):
            validate_response(case, corrupted, harness.ports)
        validate_response(case, healthy, harness.ports)


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("path", [WORKBENCH_PATH, STATUS_PATH])
def test_trusted_read_oracle_rejects_schema_valid_auth_refusal(
    harness: Harness, path: str, status: int
) -> None:
    with harness.example() as client:
        case = positive_case(harness, path)
        healthy = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        validate_response(case, healthy, harness.ports, trusted_machine=True)
        body: dict[str, object] = {
            "ok": False,
            "error": "unauthenticated" if status == 401 else "forbidden",
        }
        if path == STATUS_PATH:
            body["diagnostics"] = []
        corrupted = Response(
            status_code=status,
            headers={**healthy.headers, "www-authenticate": ["Bearer"]},
            content=json.dumps(body).encode(),
            request=healthy.request,
            elapsed=healthy.elapsed,
            verify=healthy.verify,
        )
        with pytest.raises(AssertionError, match=r"Trusted (machine|read)"):
            validate_response(case, corrupted, harness.ports, trusted_machine=True)
        validate_response(case, healthy, harness.ports, trusted_machine=True)


def test_main_and_implicit_auth_sessions_ignore_ambient_credentials(
    harness: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("NETRC", str(tmp_path / "not-a-test-credential-source"))
    monkeypatch.setenv("HTTP_PROXY", "http://not-a-test-provider.invalid")
    with harness.example() as client:
        assert client.trust_env is False
        case = positive_case(harness, WORKBENCH_PATH)
        response = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        validate_response(case, response, harness.ports, trusted_machine=True)
        assert None in harness.ports.authentications
        assert len(harness.ports.authentications) >= 3

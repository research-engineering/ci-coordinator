from __future__ import annotations

import json
from dataclasses import replace
from hashlib import sha256
from typing import Any

import pytest
from config_epoch_support import CONFIG_SOURCE
from schemathesis import Case

from ci_coordinator.config_control import RepositoryScope

from .harness import Harness
from .oracles import assert_no_effects, validate_response
from .ports import NOW
from .profile import (
    HEADERS,
    OPERATIONS,
    REQUEST_TIMEOUT_SECONDS,
    STATUS_PATH,
    VALIDATION_PATH,
    WORKBENCH_PATH,
)

INVALID_TOKEN = "syntactically-valid-but-unrecognized-machine-token"


def positive_case(harness: Harness, path: str) -> Case[Any]:
    if path == VALIDATION_PATH:
        return harness.schema[path]["POST"].Case(
            body={
                "schemaVersion": "ci-config-epoch-validation/v1",
                "sourceFormat": "json",
                "source": CONFIG_SOURCE.decode(),
            },
            media_type="application/json",
        )
    return harness.schema[path]["GET"].Case(
        path_parameters={"installation_id": 1, "repository_id": 2}, query={"limit": 1}
    )


@pytest.mark.parametrize("path", [path for _, path in OPERATIONS])
def test_positive_controls_reach_real_routes(harness: Harness, path: str) -> None:
    with harness.example() as client:
        case = positive_case(harness, path)
        response = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        assert response.status_code == 200
        validate_response(case, response, harness.ports)
        ports = harness.ports
        if path == WORKBENCH_PATH:
            assert ports.workbench_calls == [(ports.principal.actor_id, RepositoryScope(1, 2), 1)]
            assert response.json()["configEpochs"][0]["active"] is True
        elif path == STATUS_PATH:
            assert ports.status_calls == [
                (ports.principal.actor_id, RepositoryScope(1, 2), None, 1)
            ]
            assert len(response.json()["epochs"]) == 1
        else:
            assert ports.policy_calls == [(CONFIG_SOURCE, "json")]
            assert (
                response.json()["sourceHash"]
                == sha256(b"ci-policy-source/v1\0json\0" + CONFIG_SOURCE).hexdigest()
            )
        # The built-in ignored_auth check must have reached the same app without credentials.
        assert None in ports.authentications
        assert len(ports.authentications) >= 3


@pytest.mark.parametrize("path", [path for _, path in OPERATIONS])
@pytest.mark.parametrize("token", [None, INVALID_TOKEN], ids=["missing", "invalid-machine"])
def test_authentication_probes_are_typed_and_effect_free(
    harness: Harness, path: str, token: str | None
) -> None:
    with harness.example() as client:
        case = positive_case(harness, path)
        headers = {} if token is None else {"Authorization": f"Bearer {token}"}
        response = case.call(session=client, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        assert response.status_code == 401
        validate_response(case, response, harness.ports)
        assert harness.ports.machine_tokens == ([] if token is None else [token])
        assert_no_effects(harness.ports)


@pytest.mark.parametrize("path", [path for _, path in OPERATIONS])
def test_real_role_guard_refuses_missing_role(harness: Harness, path: str) -> None:
    with harness.example() as client:
        harness.ports.principal = replace(harness.ports.principal, roles=frozenset())
        case = positive_case(harness, path)
        response = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        assert response.status_code == 403
        expected: dict[str, object] = {"ok": False, "error": "forbidden"}
        if path != WORKBENCH_PATH:
            expected["diagnostics"] = []
        assert response.json() == expected
        validate_response(case, response, harness.ports)
        assert_no_effects(harness.ports)


def test_real_role_guard_refuses_expired_identity(harness: Harness) -> None:
    with harness.example() as client:
        harness.ports.principal = replace(harness.ports.principal, expires_at=NOW)
        case = positive_case(harness, WORKBENCH_PATH)
        response = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        assert response.status_code == 401
        validate_response(case, response, harness.ports)


@pytest.mark.parametrize(
    "extra", [{"Origin": "https://untrusted.example"}, {"Cookie": "session=unexpected"}]
)
def test_real_mutation_guard_refuses_browser_context(
    harness: Harness, extra: dict[str, str]
) -> None:
    with harness.example() as client:
        case = positive_case(harness, VALIDATION_PATH)
        response = case.call(
            session=client, headers={**HEADERS, **extra}, timeout=REQUEST_TIMEOUT_SECONDS
        )
        assert response.status_code == 403
        validate_response(case, response, harness.ports)
        assert_no_effects(harness.ports)


@pytest.mark.parametrize(
    "path,field,value",
    [
        (WORKBENCH_PATH, "installation_id", 0),
        (WORKBENCH_PATH, "repository_id", 9_007_199_254_740_992),
        (STATUS_PATH, "repository_id", 0),
        (STATUS_PATH, "installation_id", 9_007_199_254_740_992),
    ],
)
def test_invalid_scope_never_reaches_port(
    harness: Harness, path: str, field: str, value: int
) -> None:
    with harness.example() as client:
        case = positive_case(harness, path)
        case.path_parameters[field] = value
        response = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        assert response.status_code == 422
        validate_response(case, response, harness.ports)
        assert_no_effects(harness.ports)


@pytest.mark.parametrize("path,limit", [(WORKBENCH_PATH, 20), (STATUS_PATH, 100)])
def test_upper_scope_and_query_boundaries_remain_admitted(
    harness: Harness, path: str, limit: int
) -> None:
    with harness.example() as client:
        case = positive_case(harness, path)
        case.path_parameters = {
            "installation_id": 9_007_199_254_740_991,
            "repository_id": 9_007_199_254_740_991,
        }
        case.query = {"limit": limit}
        response = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        assert response.status_code == 200
        validate_response(case, response, harness.ports)


@pytest.mark.parametrize(
    "path,query",
    [
        (WORKBENCH_PATH, {"limit": "0"}),
        (WORKBENCH_PATH, {"limit": "21"}),
        (STATUS_PATH, {"limit": "0"}),
        (STATUS_PATH, {"limit": "101"}),
        (STATUS_PATH, {"afterEpochId": "a" * 63}),
        (STATUS_PATH, {"afterEpochId": "a" * 65}),
        (STATUS_PATH, {"afterEpochId": "G" * 64}),
    ],
)
def test_invalid_query_never_reaches_port(
    harness: Harness, path: str, query: dict[str, str]
) -> None:
    with harness.example() as client:
        case = positive_case(harness, path)
        case.query = query
        response = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        assert response.status_code == 422
        validate_response(case, response, harness.ports)


@pytest.mark.parametrize("source", ["{}", "{broken", "schemaVersion: invalid"])
def test_invalid_embedded_policy_has_diagnostics(harness: Harness, source: str) -> None:
    with harness.example() as client:
        case = positive_case(harness, VALIDATION_PATH)
        assert isinstance(case.body, dict)
        case.body["source"] = source
        response = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        assert response.status_code == 422
        validate_response(case, response, harness.ports)
        assert response.json()["diagnostics"]
        assert harness.ports.policy_calls == [(source.encode(), "json")]


@pytest.mark.parametrize(
    "field,value",
    [
        ("schemaVersion", "unknown"),
        ("sourceFormat", "yaml"),
        ("source", ""),
        ("source", 1),
        ("unknown", True),
    ],
)
def test_invalid_envelope_is_not_policy_rejection(
    harness: Harness, field: str, value: str | int
) -> None:
    with harness.example() as client:
        case = positive_case(harness, VALIDATION_PATH)
        assert isinstance(case.body, dict)
        case.body[field] = value
        response = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        assert response.status_code == 422
        validate_response(case, response, harness.ports)
        assert_no_effects(harness.ports)


def test_utf8_byte_limit_is_not_character_limit(harness: Harness) -> None:
    with harness.example() as client:
        case = positive_case(harness, VALIDATION_PATH)
        source = "\u00e9" * 1_048_577
        assert len(source) < 2_097_152 < len(source.encode())
        assert isinstance(case.body, dict)
        case.body["source"] = source
        response = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        assert response.status_code == 422
        validate_response(case, response, harness.ports)
        assert_no_effects(harness.ports)


def test_policy_oracle_uses_wire_json_not_the_generator_body_representation(
    harness: Harness,
) -> None:
    with harness.example() as client:
        case = positive_case(harness, VALIDATION_PATH)
        case.body = json.dumps(case.body).encode("utf-8")
        response = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        assert response.status_code == 200
        validate_response(case, response, harness.ports)

from __future__ import annotations

import json
from copy import deepcopy

import pytest
from schemathesis.core.transport import Response

from ci_coordinator.config_control import RepositoryScope

from .harness import Harness
from .oracles import assert_no_effects, validate_response
from .profile import HEADERS, REQUEST_TIMEOUT_SECONDS, STATUS_PATH, VALIDATION_PATH, WORKBENCH_PATH
from .test_controls import positive_case


def test_trusted_post_rejects_schema_valid_401_before_any_port_call(harness: Harness) -> None:
    with harness.example() as client:
        case = positive_case(harness, VALIDATION_PATH)
        healthy = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        validate_response(case, healthy, harness.ports, trusted_machine=True)
        harness.ports.reset()
        corrupted = Response(
            status_code=401,
            headers={**healthy.headers, "www-authenticate": ["Bearer"]},
            content=b'{"ok":false,"error":"unauthenticated","diagnostics":[]}',
            request=healthy.request,
            elapsed=healthy.elapsed,
            verify=healthy.verify,
        )
        assert_no_effects(harness.ports)
        with pytest.raises(AssertionError, match="Trusted machine was not authenticated"):
            validate_response(case, corrupted, harness.ports, trusted_machine=True)
        healthy = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        validate_response(case, healthy, harness.ports, trusted_machine=True)


@pytest.mark.parametrize("path", [WORKBENCH_PATH, STATUS_PATH])
@pytest.mark.parametrize(
    "query",
    [{}, {"limit": "7.0"}, {"limit": ["3", "7"]}, {"afterEpochId": "0" * 64}],
)
def test_read_binding_uses_wire_coercion_defaults_and_last_query_value(
    harness: Harness, path: str, query: dict[str, str | list[str]]
) -> None:
    with harness.example() as client:
        case = positive_case(harness, path)
        case.path_parameters = {"installation_id": "17.0", "repository_id": "29"}
        case.query = query
        response = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        assert response.status_code == 200
        validate_response(case, response, harness.ports, trusted_machine=True)


@pytest.mark.parametrize("path", [WORKBENCH_PATH, STATUS_PATH])
@pytest.mark.parametrize("mutation", ["constant", "swap", "actor", "limit", "duplicate"])
def test_read_oracle_rejects_consistent_but_wrong_port_and_response(
    harness: Harness, path: str, mutation: str
) -> None:
    with harness.example() as client:
        case = positive_case(harness, path)
        case.path_parameters = {"installation_id": 17, "repository_id": 29}
        case.query = {"limit": 7}
        healthy = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        ports = harness.ports
        validate_response(case, healthy, ports, trusted_machine=True)
        original_workbench = list(ports.workbench_calls)
        original_status = list(ports.status_calls)
        scope = {
            "constant": RepositoryScope(1, 2),
            "swap": RepositoryScope(29, 17),
        }.get(mutation, RepositoryScope(17, 29))
        actor = "wrong-actor" if mutation == "actor" else ports.principal.actor_id
        limit = 8 if mutation == "limit" else 7
        copies = 2 if mutation == "duplicate" else 1
        body = deepcopy(healthy.json())
        if path == WORKBENCH_PATH:
            ports.workbench_calls[:] = [(actor, scope, limit)] * copies
            body["scope"] = {
                "installationId": scope.installation_id,
                "repositoryId": scope.repository_id,
            }
        else:
            ports.status_calls[:] = [(actor, scope, None, limit)] * copies
            body["installationId"] = scope.installation_id
            body["repositoryId"] = scope.repository_id
        corrupted = Response(
            status_code=200,
            headers=healthy.headers,
            content=json.dumps(body).encode(),
            request=healthy.request,
            elapsed=healthy.elapsed,
            verify=healthy.verify,
        )
        with pytest.raises(AssertionError, match="request binding changed"):
            validate_response(case, corrupted, ports, trusted_machine=True)
        ports.workbench_calls[:] = original_workbench
        ports.status_calls[:] = original_status
        assert healthy.json() == json.loads(healthy.content)
        validate_response(case, healthy, ports, trusted_machine=True)


@pytest.mark.parametrize("replacement", [None, "0" * 63 + "1"])
def test_status_oracle_rejects_cursor_change_even_when_page_is_unchanged(
    harness: Harness, replacement: str | None
) -> None:
    with harness.example() as client:
        case = positive_case(harness, STATUS_PATH)
        case.query = {"limit": 7, "afterEpochId": "0" * 64}
        healthy = case.call(session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS)
        ports = harness.ports
        validate_response(case, healthy, ports, trusted_machine=True)
        actor, scope, cursor, limit = ports.status_calls[0]
        assert all(
            replacement is None or item["epochId"] > replacement
            for item in healthy.json()["epochs"]
        )
        ports.status_calls[:] = [(actor, scope, replacement, limit)]
        with pytest.raises(AssertionError, match="Status request binding changed"):
            validate_response(case, healthy, ports, trusted_machine=True)
        ports.status_calls[:] = [(actor, scope, cursor, limit)]
        validate_response(case, healthy, ports, trusted_machine=True)

from __future__ import annotations

import json
from typing import Any, cast
from urllib.parse import parse_qs, unquote, urlsplit

from pydantic import TypeAdapter, ValidationError
from schemathesis import Case
from schemathesis.checks import CheckFunction
from schemathesis.core.transport import Response
from schemathesis.specs.openapi.checks import positive_data_acceptance

from ci_coordinator.api.http.config_lifecycle_contracts import ConfigEpochValidationBody
from ci_coordinator.api.http.routers.config_management import MAX_CONFIG_REGISTRATION_BODY_BYTES
from ci_coordinator.config_control import RepositoryScope, admit_policy_document

from .ports import Ports
from .profile import HEADERS, REQUEST_TIMEOUT_SECONDS, STATUS_PATH, VALIDATION_PATH, WORKBENCH_PATH


def assert_no_effects(ports: Ports) -> None:
    assert not ports.workbench_calls
    assert not ports.status_calls
    assert not ports.policy_calls
    assert not ports.scope_calls
    assert not ports.forbidden_calls


def validate_response(
    case: Case[Any], response: Response, ports: Ports, *, trusted_machine: bool = False
) -> None:
    if trusted_machine:
        assert response.status_code != 401, "Trusted machine was not authenticated"
    if trusted_machine and case.method == "GET":
        assert response.status_code not in {401, 403}, "Trusted read was denied"
        if case.meta is None or case.meta.generation.mode.is_positive:
            assert response.status_code == 200, "Admitted read was rejected"
    # Only embedded policy admission replaces the schema-positive => accepted implication.
    excluded = (
        [cast(CheckFunction, positive_data_acceptance)] if case.path == VALIDATION_PATH else []
    )
    case.validate_response(
        response,
        excluded_checks=excluded,
        headers=dict(HEADERS),
        transport_kwargs={"timeout": REQUEST_TIMEOUT_SECONDS},
    )
    assert response.headers.get("cache-control") == ["no-store"], "Missing no-store"
    assert not ports.forbidden_calls
    body = response.json()
    if response.status_code == 401:
        expected: dict[str, object] = {"ok": False, "error": "unauthenticated"}
        if case.path != WORKBENCH_PATH:
            expected["diagnostics"] = []
        assert body == expected, "Authentication response body changed"
        assert response.headers.get("www-authenticate") == ["Bearer"]
        assert_no_effects(ports)
        return
    if case.path == VALIDATION_PATH:
        _validate_policy_response(response, ports)
    elif response.status_code == 200:
        _assert_read_request_binding(case, response, ports)
        if case.path == WORKBENCH_PATH:
            actor, scope, limit = ports.workbench_calls[0]
            assert body["scope"] == {
                "installationId": scope.installation_id,
                "repositoryId": scope.repository_id,
            }
            assert body["ok"] is True
            assert body["replay"]["status"] == "valid"
            assert len(body["configEpochs"]) == 1 <= limit
            assert body["configEpochs"][0]["epochId"] == ports.draft.epoch_id
        else:
            assert case.path == STATUS_PATH
            actor, scope, cursor, limit = ports.status_calls[0]
            assert (body["installationId"], body["repositoryId"]) == (
                scope.installation_id,
                scope.repository_id,
            )
            assert body["active"] == {"epochId": ports.draft.epoch_id, "revision": 1}
            assert len(body["epochs"]) <= limit
            assert all(cursor is None or item["epochId"] > cursor for item in body["epochs"])
        assert actor == ports.principal.actor_id
    elif response.status_code == 422:
        assert body == {"code": "invalid_request"}
        assert_no_effects(ports)
    assert all(
        secret not in response.text for secret in ("sourceBytes", "bearerToken", "leaseToken")
    )


def _assert_read_request_binding(case: Case[Any], response: Response, ports: Ports) -> None:
    # The serialized request is independent of both the port ledger and its echoed response.
    request_url = response.request.url
    assert request_url is not None
    url = urlsplit(request_url)
    parameters = {
        template: unquote(value)
        for template, value in zip(case.path.split("/"), url.path.split("/"), strict=True)
        if template.startswith("{")
    }
    integer = TypeAdapter(int)
    scope = RepositoryScope(
        integer.validate_python(parameters["{installation_id}"]),
        integer.validate_python(parameters["{repository_id}"]),
    )
    query = parse_qs(url.query, keep_blank_values=True)
    default_limit = "10" if case.path == WORKBENCH_PATH else "50"
    limit = integer.validate_python(query.get("limit", [default_limit])[-1])
    actor = ports.principal.actor_id
    assert not ports.policy_calls and not ports.scope_calls
    if case.path == WORKBENCH_PATH:
        assert ports.workbench_calls == [(actor, scope, limit)], "Workbench request binding changed"
        assert not ports.status_calls
    else:
        cursor = query["afterEpochId"][-1] if "afterEpochId" in query else None
        assert ports.status_calls == [(actor, scope, cursor, limit)], (
            "Status request binding changed"
        )
        assert not ports.workbench_calls


def _validate_policy_response(response: Response, ports: Ports) -> None:
    wire_body = response.request.body
    if isinstance(wire_body, str):
        wire_body = wire_body.encode("utf-8")
    if isinstance(wire_body, bytes) and len(wire_body) > MAX_CONFIG_REGISTRATION_BODY_BYTES:
        assert response.status_code == 413
        assert response.json() == {"ok": False, "error": "invalid_config", "diagnostics": []}
        assert_no_effects(ports)
        return
    try:
        decoded = json.loads(wire_body) if isinstance(wire_body, bytes) else None
    except ValueError:
        decoded = None
    try:
        envelope = ConfigEpochValidationBody.model_validate(decoded)
    except ValidationError:
        assert response.status_code == 422, "Invalid envelope was accepted"
        assert response.json() == {"code": "invalid_request"}
        assert_no_effects(ports)
        return
    request_headers = response.request.headers
    if (
        request_headers.get("Content-Type") != "application/json"
        or "Origin" in request_headers
        or "Cookie" in request_headers
        or "configure" not in ports.principal.roles
    ):
        assert response.status_code == 403
        assert response.json() == {"ok": False, "error": "forbidden", "diagnostics": []}
        assert_no_effects(ports)
        return
    source = envelope.source.encode("utf-8")
    admitted = admit_policy_document(source, envelope.source_format)
    assert ports.policy_calls == [(source, envelope.source_format)]
    if isinstance(admitted, tuple):
        assert response.status_code == 422, "Invalid embedded policy was accepted"
        assert response.json() == {
            "ok": False,
            "error": "invalid_config",
            "diagnostics": [
                {
                    "code": item.code,
                    "phase": item.phase,
                    "ruleId": item.rule_id,
                    "instancePointer": item.instance_pointer,
                }
                for item in admitted
            ],
        }
        assert not ports.scope_calls
    else:
        assert response.status_code == 200, "Admitted policy was rejected"
        body = response.json()
        assert body["ok"] is True
        assert body["epochId"] == admitted.epoch_id
        assert body["sourceHash"] == admitted.source_hash
        assert (body["installationId"], body["repositoryId"]) == (
            admitted.scope.installation_id,
            admitted.scope.repository_id,
        )
        assert ports.scope_calls == [(ports.principal.actor_id, admitted.scope)]

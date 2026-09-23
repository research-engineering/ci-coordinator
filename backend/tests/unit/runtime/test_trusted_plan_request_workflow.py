from __future__ import annotations

import base64
import gzip
import hashlib
import json
from pathlib import Path

import pytest
from trusted_plan_request_support import (
    load_full_check_workflow,
    load_trusted_request_workflow,
    run_trusted_request_script,
    valid_request_environment,
)

_ACTION = "actions/github-script@3a2844b7e9c422d3c10d287c895573f7108da1b3"
_OUTPUTS = {"reason", *(f"plan_chunk_{index}" for index in range(7))}


def test_trusted_plan_request_workflow_has_one_bounded_oidc_authority() -> None:
    workflow = load_trusted_request_workflow()

    assert workflow["permissions"] == {}
    assert set(workflow) == {"name", "on", "permissions", "jobs"}
    trigger = workflow["on"]
    assert type(trigger) is dict
    call = trigger["workflow_call"]
    assert type(call) is dict
    assert set(call["inputs"]) == {"installation_id", "plan_url"}
    assert set(call["outputs"]) == _OUTPUTS

    jobs = workflow["jobs"]
    assert type(jobs) is dict and set(jobs) == {"request"}
    request = jobs["request"]
    assert type(request) is dict
    assert request["permissions"] == {"id-token": "write"}
    assert request["runs-on"] == "ubuntu-24.04"
    assert request["timeout-minutes"] == 2
    assert set(request["outputs"]) == _OUTPUTS
    assert len(request["steps"]) == 1
    step = request["steps"][0]
    assert step["uses"] == _ACTION
    assert step["with"]["github-token"] == "unused"
    assert "${{" not in step["with"]["github-token"]
    assert "actions/checkout" not in json.dumps(workflow)
    assert "ACTIONS_ID_TOKEN_REQUEST" not in step["with"]["script"]


def test_full_check_executes_the_real_requester_action_entrypoint() -> None:
    workflow = load_full_check_workflow()
    jobs = workflow["jobs"]
    assert type(jobs) is dict

    requester = jobs["trusted-plan-request-entrypoint"]
    assert requester == {
        "name": "Trusted plan requester entrypoint",
        "permissions": {"id-token": "write"},
        "uses": "$/.github/workflows/trusted-plan-request.yml",
        "with": {"installation_id": "1", "plan_url": "invalid"},
    }
    gate = jobs["pull-request-gate"]
    assert type(gate) is dict
    assert "trusted-plan-request-entrypoint" in gate["needs"]
    steps = gate["steps"]
    assert type(steps) is list and len(steps) == 1
    environment = steps[0]["env"]
    assert environment["TRUSTED_PLAN_REQUEST_RESULT"] == (
        "${{ needs.trusted-plan-request-entrypoint.result }}"
    )
    assert environment["TRUSTED_PLAN_REQUEST_REASON"] == (
        "${{ needs.trusted-plan-request-entrypoint.outputs.reason }}"
    )
    script = steps[0]["run"]
    assert 'test "${TRUSTED_PLAN_REQUEST_RESULT}" = success' in script
    assert 'test "${TRUSTED_PLAN_REQUEST_REASON}" = plan_url_invalid' in script


def test_trusted_plan_request_maps_exact_identity_and_bounded_response(
    tmp_path: Path,
) -> None:
    plan = b'{"schemaVersion":"dynamic-ci-signed-plan-envelope/v1","signature":"abc"}\n'
    evidence = run_trusted_request_script(
        tmp_path,
        valid_request_environment(),
        fetch_scenario={"status": 200, "body": plan.decode()},
    )

    assert evidence["oidcAudiences"] == ["https://coordinator.example/api/v1/dynamic-ci/plan"]
    assert evidence["secrets"] == ["signed-oidc-token"]
    calls = evidence["fetchCalls"]
    assert type(calls) is list and len(calls) == 1
    call = calls[0]
    assert call == {
        "authorization": "Bearer signed-oidc-token",
        "body": json.dumps(
            {
                "schemaVersion": "dynamic-ci-plan-request/v2",
                "requestId": "200:300:1",
                "installationId": 100,
                "repositoryId": 200,
                "owner": "example",
                "repository": "target",
                "eventName": "pull_request",
                "ref": "refs/pull/42/merge",
                "baseSha": "a" * 40,
                "headSha": "b" * 40,
                "executionSha": "c" * 40,
                "workflowRunId": 300,
                "runAttempt": 1,
                "pullRequestNumber": 42,
                "mergeGroupHeadRef": None,
            },
            separators=(",", ":"),
        ),
        "contentType": "application/json",
        "method": "POST",
        "redirect": "error",
        "url": "https://coordinator.example/api/v1/dynamic-ci/plan",
    }
    outputs = evidence["outputs"]
    assert type(outputs) is dict and set(outputs) == _OUTPUTS
    assert outputs["reason"] == "signed_plan_available"
    encoded = "".join(str(outputs[f"plan_chunk_{index}"]) for index in range(7))
    padding = "=" * (-len(encoded) % 4)
    assert gzip.decompress(base64.urlsafe_b64decode(encoded + padding)) == plan
    assert "signed-oidc-token" not in json.dumps(outputs)


@pytest.mark.parametrize(
    ("overrides", "oidc_error", "token", "scenario", "reason", "oidc_calls", "fetch_calls"),
    [
        (
            {"CI_PLAN_URL": "http://coordinator.example/plan"},
            False,
            "token",
            {},
            "plan_url_invalid",
            0,
            0,
        ),
        (
            {"CI_EVENT": "workflow_dispatch"},
            False,
            "token",
            {},
            "event_not_supported_by_dynamic_plan",
            0,
            0,
        ),
        (
            {"CI_INSTALLATION_ID": ""},
            False,
            "token",
            {},
            "plan_identity_unavailable",
            0,
            0,
        ),
        ({}, True, "token", {}, "oidc_token_request_failed", 1, 0),
        ({}, False, "", {}, "oidc_token_response_invalid", 1, 0),
        ({}, False, "token", {"kind": "throw"}, "coordinator_plan_request_failed", 1, 1),
        ({}, False, "token", {"status": 503}, "coordinator_plan_request_failed", 1, 1),
        ({}, False, "token", {"body": "[]"}, "coordinator_plan_response_invalid", 1, 1),
        (
            {},
            False,
            "token",
            {"headers": {"content-length": "1048577"}, "body": "{}"},
            "coordinator_plan_response_invalid",
            1,
            1,
        ),
    ],
    ids=(
        "non-https-url",
        "unsupported-event",
        "missing-identity",
        "oidc-failure",
        "invalid-oidc-token",
        "plan-transport",
        "plan-status",
        "plan-object",
        "declared-plan-bound",
    ),
)
def test_trusted_plan_request_emits_closed_fallback_outputs(
    tmp_path: Path,
    overrides: dict[str, str],
    oidc_error: bool,
    token: str,
    scenario: dict[str, object],
    reason: str,
    oidc_calls: int,
    fetch_calls: int,
) -> None:
    evidence = run_trusted_request_script(
        tmp_path,
        {**valid_request_environment(), **overrides},
        oidc_token=token,
        oidc_error=oidc_error,
        fetch_scenario=scenario,
    )

    outputs = evidence["outputs"]
    oidc_audiences = evidence["oidcAudiences"]
    fetch_calls_value = evidence["fetchCalls"]
    assert type(outputs) is dict and set(outputs) == _OUTPUTS
    assert type(oidc_audiences) is list
    assert type(fetch_calls_value) is list
    assert outputs == {
        "reason": reason,
        **{f"plan_chunk_{index}": "" for index in range(7)},
    }
    assert len(oidc_audiences) == oidc_calls
    assert len(fetch_calls_value) == fetch_calls


def test_trusted_plan_request_rejects_a_response_that_cannot_fit_job_outputs(
    tmp_path: Path,
) -> None:
    incompressible = b"".join(
        hashlib.sha256(index.to_bytes(4, "big")).digest() for index in range(12_500)
    )
    body = json.dumps({"payload": base64.b64encode(incompressible).decode()})

    evidence = run_trusted_request_script(
        tmp_path,
        valid_request_environment(),
        fetch_scenario={"status": 200, "body": body},
    )

    assert evidence["outputs"] == {
        "reason": "coordinator_plan_transport_too_large",
        **{f"plan_chunk_{index}": "" for index in range(7)},
    }

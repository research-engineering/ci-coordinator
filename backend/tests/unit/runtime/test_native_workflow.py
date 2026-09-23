from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

from ci_coordinator.execution_orchestration import CONTROL_INVOCATION_JOB_ID
from ci_coordinator.repo_context import (
    DefaultBranchWorkflow,
    ProviderWorkflowInventory,
    parse_workflow_capability,
)
from ci_coordinator.target_artifacts.resources import ci_measurement_reporter

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_FIXTURE_ROOT = _REPOSITORY_ROOT / "fixtures/native-target-repository"
_WORKFLOW_PATH = _FIXTURE_ROOT / ".github/workflows/full-check.yml"
_REGISTRY_PATH = _FIXTURE_ROOT / ".ci-coordinator/execution-registry.v1.json"
_CONTROL_BUNDLE_PATH = _FIXTURE_ROOT / ".ci-coordinator/ci-coordinator.cjs"
_REQUIREMENTS_PATH = _REPOSITORY_ROOT / "docs/specs/ci-coordinator-runtime/requirements.v1.json"
_SOURCE_SCHEMA_PATH = (
    _REPOSITORY_ROOT / "docs/specs/ci-coordinator-runtime/target-artifacts-source.schema.v1.json"
)
_REVISION = "a" * 40
_ADOPTION_FLOORS = (
    pytest.param(
        _FIXTURE_ROOT / ".github/workflows/native-full-check.yml",
        "printf 'native lint fixture\\n'",
        "printf 'native test fixture\\n'",
        id="native-job-set",
    ),
    pytest.param(
        _REPOSITORY_ROOT / "fixtures/target-repository/.github/workflows/native-full-check.yml",
        ".ci-coordinator/run full-ci",
        None,
        id="witness-shards",
    ),
)


@pytest.mark.parametrize(("workflow_path", "first_command", "second_command"), _ADOPTION_FLOORS)
def test_adoption_floor_is_independent_from_remote_workflow_resolution(
    workflow_path: Path,
    first_command: str,
    second_command: str | None,
) -> None:
    source = workflow_path.read_text(encoding="utf-8")
    workflow = _read_workflow_path(workflow_path)
    triggers = _mapping(workflow["on"])
    jobs = _mapping(workflow["jobs"])

    assert workflow["name"] == "Native Full Check"
    assert set(triggers) == {"merge_group", "pull_request", "push", "workflow_dispatch"}
    assert _mapping(triggers["merge_group"])["types"] == ["checks_requested"]
    assert workflow["permissions"] == {"contents": "read"}
    assert jobs and all("uses" not in _mapping(job) for job in jobs.values())
    assert "example-org/" not in source
    assert "/.github/workflows/" not in source
    assert "CI_COORDINATOR" not in source
    assert "plan-request" not in source
    assert first_command in source
    assert second_command is None or second_command in source


def test_runtime_requirements_and_schema_name_the_same_control_topology() -> None:
    requirements = _mapping(json.loads(_REQUIREMENTS_PATH.read_bytes()))
    invariants = {
        str(requirement["requirementId"]): str(requirement["invariant"])
        for requirement in _sequence(requirements["requirements"])
    }
    runtime026 = invariants["REQ-CI-RUNTIME-026"]
    runtime027 = invariants["REQ-CI-RUNTIME-027"]
    schema = _mapping(json.loads(_SOURCE_SCHEMA_PATH.read_bytes()))
    workflow_schema = _mapping(_mapping(schema["$defs"])["workflow"])
    required_fields = set(_string_sequence(workflow_schema["required"]))

    assert "one fixed credential-free invocation-classifier job" in runtime026
    assert (
        "one exact remote plan-request job whose complete dependency set equals that classifier"
        in runtime026
    )
    assert "one plan job whose complete dependency set equals that request job" in runtime026
    assert (
        "registered classifier, request, plan, execution, kind-specific fallback, and gate roles"
        in runtime026
    )
    assert "dependency-free plan job" not in runtime026
    assert {"planRequestJobId", "planRequestWorkflowRef", "planJobId"} <= required_fields
    assert "separately triggered target-local FullCI workflow" in runtime026
    assert "separately triggered target-local FullCI workflow" not in runtime027


def test_native_workflow_preserves_static_jobs_and_has_one_fail_closed_gate() -> None:
    source = _WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = _read_workflow()
    jobs = _mapping(workflow["jobs"])
    registry = _mapping(json.loads(_REGISTRY_PATH.read_bytes()))
    workflow_binding = _sequence(_mapping(registry)["workflows"])[0]

    assert workflow["name"] == "Full Check"
    assert set(_mapping(workflow["on"])) == {
        "merge_group",
        "pull_request",
        "push",
        "workflow_dispatch",
    }
    assert set(jobs) == {
        CONTROL_INVOCATION_JOB_ID,
        "full-check-gate",
        "lint",
        "plan",
        "plan-request",
        "test",
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow_binding["executionKind"] == "native-job-set"
    assert workflow_binding["fallbackJobId"] is None
    assert workflow_binding["workflowPath"] == ".github/workflows/full-check.yml"
    assert [item["jobId"] for item in _sequence(workflow_binding["executionJobs"])] == [
        "lint",
        "test",
    ]

    invocation = _mapping(jobs[CONTROL_INVOCATION_JOB_ID])
    assert invocation["permissions"] == {}
    assert invocation["outputs"] == {"direct": "${{ steps.classify.outputs.direct }}"}
    assert invocation["runs-on"] == "ubuntu-24.04"
    assert invocation["timeout-minutes"] == 1
    invocation_step = _mapping(_sequence(invocation["steps"])[0])
    assert _mapping(invocation_step["env"]) == {
        "CI_CALLER_WORKFLOW_REF": "${{ github.workflow_ref }}",
        "CI_DEFINING_WORKFLOW_REF": "${{ job.workflow_ref }}",
    }
    request_job = _mapping(jobs["plan-request"])
    assert request_job["if"] == (
        "${{ needs['ci-invocation'].outputs.direct == 'true' && "
        "(github.event_name == 'pull_request' || github.event_name == 'push' || "
        "github.event_name == 'merge_group') }}"
    )
    assert request_job["needs"] == CONTROL_INVOCATION_JOB_ID
    assert request_job["uses"] == workflow_binding["planRequestWorkflowRef"]
    assert request_job["permissions"] == {"id-token": "write"}
    assert "steps" not in request_job
    plan = _mapping(jobs["plan"])
    assert plan["needs"] == "plan-request"
    plan_steps = _sequence(plan["steps"])
    assert _mapping(plan_steps[0]["with"])["ref"] == "${{ job.workflow_sha }}"
    plan_environment = _mapping(plan_steps[3]["env"])
    assert plan_environment["CI_WORKFLOW_REF"] == "${{ job.workflow_ref }}"
    assert plan_environment["CI_WORKFLOW_SHA"] == "${{ job.workflow_sha }}"
    assert _mapping(jobs["lint"])["needs"] == "plan"
    assert set(_string_sequence(_mapping(jobs["test"])["needs"])) == {"lint", "plan"}
    for job_id in ("lint", "test"):
        job = _mapping(jobs[job_id])
        condition = str(job["if"])
        assert "needs.plan.result != 'success'" in condition
        assert "needs.plan.outputs.fallback != 'false'" in condition
        assert "fromJSON(needs.plan.outputs.selected_jobs || '[]')" in condition
        assert f"'{job_id}'" in condition
        assert _mapping(job["permissions"]) == {"contents": "read"}

    gate = _mapping(jobs["full-check-gate"])
    assert gate["name"] == "Full Check"
    assert set(_string_sequence(gate["needs"])) == {"lint", "plan", "test"}
    assert "always()" in str(gate["if"])
    gate_steps = _sequence(gate["steps"])
    assert gate_steps[0] == {
        "name": "Reject cancelled workflow",
        "if": "${{ cancelled() }}",
        "run": "exit 1",
    }
    assert _mapping(gate_steps[1]["with"]) == {
        "persist-credentials": False,
        "ref": "${{ job.workflow_sha }}",
    }
    assert gate_steps[2]["uses"] == ("actions/setup-node@820762786026740c76f36085b0efc47a31fe5020")
    assert _mapping(gate_steps[2]["with"]) == {"node-version": "24.21.0"}
    gate_environment = _mapping(gate_steps[3]["env"])
    assert "CI_FULL_CI_RESULT" not in gate_environment
    assert gate_environment["CI_WORKFLOW_REF"] == "${{ job.workflow_ref }}"
    assert gate_steps[3]["run"] == "node .ci-coordinator/ci-coordinator.cjs validate-gate"
    assert ".ci-coordinator/run" not in source
    assert "CI_COORDINATOR_SELECTED_CHECK_COMMAND" not in source


@pytest.mark.parametrize(
    ("caller_ref", "defining_ref", "expected"),
    (
        (
            "Owner/Repo/.github/workflows/full-check.yml@refs/heads/main",
            "Owner/Repo/.github/workflows/full-check.yml@refs/heads/main",
            "direct=true\n",
        ),
        (
            "owner/repo/.github/workflows/full-check.yml@refs/heads/main",
            "Owner/Repo/.github/workflows/full-check.yml@refs/heads/main",
            "direct=false\n",
        ),
        (
            "Owner/Repo/.github/workflows/full-check.yml@refs/heads/main",
            "Owner/Repo/.github/workflows/full-check.yml@refs/heads/main@caller.yml",
            "direct=false\n",
        ),
        ("", "", "direct=false\n"),
    ),
    ids=("exact", "case-difference", "prefix-only", "empty"),
)
def test_invocation_classifier_uses_non_empty_case_sensitive_equality(
    caller_ref: str,
    defining_ref: str,
    expected: str,
    tmp_path: Path,
) -> None:
    jobs = _mapping(_read_workflow()["jobs"])
    invocation = _mapping(jobs[CONTROL_INVOCATION_JOB_ID])
    script = str(_mapping(_sequence(invocation["steps"])[0])["run"])
    output = tmp_path / "github-output"
    environment = {
        **os.environ,
        "CI_CALLER_WORKFLOW_REF": caller_ref,
        "CI_DEFINING_WORKFLOW_REF": defining_ref,
        "GITHUB_OUTPUT": str(output),
    }

    subprocess.run(["bash", "-c", script], check=True, env=environment)

    assert output.read_text(encoding="utf-8") == expected


def test_native_workflow_matches_exact_revision_provider_inventory() -> None:
    content = _WORKFLOW_PATH.read_bytes()
    relative_path = ".github/workflows/full-check.yml"
    capability = parse_workflow_capability(
        content,
        path=relative_path,
        revision_sha=_REVISION,
    )

    assert capability is not None
    inventory = ProviderWorkflowInventory(
        revision_sha=_REVISION,
        default_branch_workflows=(DefaultBranchWorkflow(1, relative_path, True),),
        revision_capabilities=(capability,),
    )
    assert inventory.admits_exact_job_topology(
        workflow_path=relative_path,
        expected=(
            (CONTROL_INVOCATION_JOB_ID, ()),
            ("full-check-gate", ("lint", "plan", "test")),
            ("lint", ("plan",)),
            ("plan", ("plan-request",)),
            ("plan-request", (CONTROL_INVOCATION_JOB_ID,)),
            ("test", ("lint", "plan")),
        ),
    )
    assert inventory.admits_static_gate(
        workflow_path=relative_path,
        job_id="full-check-gate",
        job_name="Full Check",
        required_dependencies=("lint", "plan", "test"),
    )
    assert inventory.admits_control_plane(
        workflow_path=relative_path,
        execution_kind="native-job-set",
        invocation_job_id=CONTROL_INVOCATION_JOB_ID,
        plan_request_job_id="plan-request",
        plan_request_workflow_ref=(
            "example-org/ci-coordinator/.github/workflows/trusted-plan-request.yml@" + "1" * 40
        ),
        plan_job_id="plan",
        fallback_job_id=None,
        gate_job_id="full-check-gate",
        execution_job_ids=("lint", "test"),
    )


@pytest.mark.parametrize(
    ("overrides", "admitted"),
    (
        ({}, True),
        (
            {
                "CI_SELECTED_JOBS_JSON": '["lint","test"]',
                "CI_STATIC_JOB_RESULTS_JSON": '{"lint":"success","test":"success"}',
            },
            True,
        ),
        (
            {
                "CI_PLAN_RESULT": "failure",
                "CI_PLAN_VALID": "",
                "CI_FALLBACK": "",
                "CI_SELECTED_JOBS_JSON": "[]",
                "CI_STATIC_JOB_RESULTS_JSON": '{"lint":"success","test":"success"}',
            },
            True,
        ),
        (
            {
                "CI_SELECTED_JOBS_JSON": '["test"]',
                "CI_STATIC_JOB_RESULTS_JSON": '{"lint":"skipped","test":"success"}',
            },
            False,
        ),
        (
            {
                "CI_PLAN_RESULT": "failure",
                "CI_PLAN_VALID": "",
                "CI_FALLBACK": "",
                "CI_SELECTED_JOBS_JSON": "[]",
                "CI_STATIC_JOB_RESULTS_JSON": '{"lint":"failure","test":"skipped"}',
            },
            False,
        ),
        ({"CI_FULL_CI_RESULT": "skipped"}, False),
        (
            {
                "CI_SELECTED_JOBS_JSON": '["foreign"]',
                "CI_STATIC_JOB_RESULTS_JSON": '{"lint":"skipped","test":"skipped"}',
            },
            False,
        ),
        (
            {
                "CI_STATIC_JOB_RESULTS_JSON": '{"lint":"cancelled","test":"skipped"}',
            },
            False,
        ),
    ),
)
@pytest.mark.parametrize("with_reporter", [False, True])
def test_native_gate_admits_only_closed_selected_or_complete_fallback(
    overrides: dict[str, str],
    admitted: bool,
    with_reporter: bool,
) -> None:
    command = ["node", str(_CONTROL_BUNDLE_PATH), "validate-gate"]
    if with_reporter:
        command = [sys.executable, "-I", ci_measurement_reporter.__file__, "--", *command]
    completed = subprocess.run(
        command,
        cwd=_REPOSITORY_ROOT,
        env={**os.environ, **_gate_environment(), **overrides},
        check=False,
        text=True,
        capture_output=True,
        timeout=20,
    )

    assert (completed.returncode == 0) is admitted


def test_native_workflow_shell_steps_are_syntactically_valid() -> None:
    for job in _mapping(_read_workflow()["jobs"]).values():
        steps = _mapping(job).get("steps")
        if steps is None:
            continue
        for step in _sequence(steps):
            script = step.get("run")
            if isinstance(script, str):
                subprocess.run(
                    ["bash", "-n"],
                    check=True,
                    input=script,
                    text=True,
                    capture_output=True,
                )


def _read_workflow() -> dict[str, Any]:
    return _read_workflow_path(_WORKFLOW_PATH)


def _read_workflow_path(path: Path) -> dict[str, Any]:
    parser = YAML(typ="safe")
    return _mapping(parser.load(path.read_text(encoding="utf-8")))


def _gate_environment() -> dict[str, str]:
    return {
        "CI_PLAN_RESULT": "success",
        "CI_PLAN_VALID": "true",
        "CI_FALLBACK": "false",
        "CI_SELECTED_JOBS_JSON": '["lint"]',
        "CI_STATIC_JOB_RESULTS_JSON": '{"lint":"success","test":"skipped"}',
        "CI_TARGET_REGISTRY_PATH": str(_REGISTRY_PATH),
        "CI_OWNER": "example",
        "CI_REPO": "target",
        "CI_WORKFLOW_PATH": ".github/workflows/full-check.yml",
        "CI_WORKFLOW_REF": ("example/target/.github/workflows/full-check.yml@refs/heads/master"),
    }


def _mapping(value: object) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _sequence(value: object) -> list[dict[str, Any]]:
    assert isinstance(value, list)
    assert all(isinstance(item, dict) for item in value)
    return value


def _string_sequence(value: object) -> list[str]:
    assert isinstance(value, list)
    assert all(isinstance(item, str) for item in value)
    return value

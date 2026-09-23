from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from package_b_support import PLAN_REQUEST_JOB_ID, PLAN_REQUEST_WORKFLOW_REF
from ruamel.yaml import YAML

from ci_coordinator.execution_orchestration import CONTROL_INVOCATION_JOB_ID
from ci_coordinator.kernel import canonical_json
from ci_coordinator.repo_context import (
    ProviderWorkflowInventory,
    parse_workflow_capability,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_BOOTSTRAP_PATH = (
    _REPOSITORY_ROOT / "fixtures/target-repository/.github/workflows/ci-coordinator-bootstrap.yml"
)
_BOOTSTRAP_CONTRACT_PATH = _REPOSITORY_ROOT / "docs/bootstrap-workflow-contract.md"
_CONTROL_PATH = _REPOSITORY_ROOT / "fixtures/target-repository/.ci-coordinator/ci-coordinator.cjs"
_PLAN_CONSUMER_SOURCE_PATH = (
    _REPOSITORY_ROOT / "backend/src/ci_coordinator/target_artifacts/control_source/consume_plan.cjs"
)
_REGISTRY_PATH = (
    _REPOSITORY_ROOT / "fixtures/target-repository/.ci-coordinator/execution-registry.v1.json"
)


def test_bootstrap_workflow_is_a_stable_fail_closed_gate() -> None:
    source = _BOOTSTRAP_PATH.read_text(encoding="utf-8")
    workflow = _read_workflow(_BOOTSTRAP_PATH)
    triggers = _mapping(workflow["on"])
    jobs = _mapping(workflow["jobs"])
    registry = json.loads(_REGISTRY_PATH.read_bytes())
    profile_bindings = _sequence(_mapping(registry)["profiles"])
    selected_job_ids = {str(binding["jobId"]) for binding in profile_bindings}

    assert workflow["name"] == "Dynamic CI Bootstrap"
    assert set(triggers) == {"merge_group", "pull_request", "push", "workflow_dispatch"}
    assert _mapping(triggers["merge_group"])["types"] == ["checks_requested"]
    assert workflow["permissions"] == {"contents": "read"}
    for trigger in triggers.values():
        if isinstance(trigger, dict):
            assert not set(trigger).intersection(
                {"branches", "branches-ignore", "paths", "paths-ignore", "tags", "tags-ignore"}
            )

    forbidden_fragments = (
        "${{ secrets.",
        "actions: write",
        "checks: write",
        "continue-on-error: true",
        "pull-requests: write",
        "|| true",
    )
    assert all(fragment not in source for fragment in forbidden_fragments)
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
    plan_request = _mapping(jobs[PLAN_REQUEST_JOB_ID])
    assert plan_request["if"] == (
        "${{ needs['ci-invocation'].outputs.direct == 'true' && "
        "(github.event_name == 'pull_request' || github.event_name == 'push' || "
        "github.event_name == 'merge_group') }}"
    )
    assert plan_request["needs"] == CONTROL_INVOCATION_JOB_ID
    assert _mapping(plan_request["permissions"]) == {"id-token": "write"}
    assert plan_request["uses"] == PLAN_REQUEST_WORKFLOW_REF
    assert _mapping(plan_request["with"]) == {
        "installation_id": (
            "${{ vars.CI_COORDINATOR_INSTALLATION_ID || github.event.installation.id }}"
        ),
        "plan_url": "${{ vars.CI_COORDINATOR_PLAN_URL }}",
    }
    assert not {"env", "runs-on", "steps"}.intersection(plan_request)
    plan = _mapping(jobs["plan"])
    assert plan["needs"] == "plan-request"
    assert plan["if"] == "${{ !cancelled() }}"
    assert _mapping(plan["permissions"]) == {"contents": "read"}
    assert "id-token" not in _mapping(plan["permissions"])
    assert source.count("id-token: write") == 1
    assert all(
        _mapping(_mapping(jobs[job_id])["permissions"]) == {"contents": "read"}
        for job_id in selected_job_ids
    )
    assert _mapping(_mapping(jobs["full-ci"])["permissions"]) == {"contents": "read"}
    assert all(
        _mapping(job)["runs-on"] == "ubuntu-24.04"
        for job_id, job in jobs.items()
        if job_id != PLAN_REQUEST_JOB_ID
    )
    plan_steps = _sequence(plan["steps"])
    checkout = next(
        step for step in plan_steps if step.get("name") == "Check out execution authority"
    )
    assert _mapping(checkout["with"])["ref"] == "${{ job.workflow_sha }}"
    setup_node = next(step for step in plan_steps if step.get("name") == "Set up Node.js")
    assert setup_node["uses"] == ("actions/setup-node@820762786026740c76f36085b0efc47a31fe5020")
    assert _mapping(setup_node["with"])["node-version"] == "24.21.0"
    consume = next(
        step for step in plan_steps if step.get("name") == "Consume coordinator response"
    )
    assert consume["run"] == "node .ci-coordinator/ci-coordinator.cjs consume-plan"
    assert set(_mapping(consume["env"])) == {
        "CI_COORDINATOR_PLAN_REASON",
        *(f"CI_COORDINATOR_PLAN_CHUNK_{index}" for index in range(7)),
        "PLAN_PATH",
    }
    assert _mapping(consume["env"])["CI_COORDINATOR_PLAN_REASON"] == (
        "${{ needs['plan-request'].outputs.reason }}"
    )
    for index in range(7):
        assert _mapping(consume["env"])[f"CI_COORDINATOR_PLAN_CHUNK_{index}"] == (
            f"${{{{ needs['plan-request'].outputs.plan_chunk_{index} }}}}"
        )
    validate = next(step for step in plan_steps if step.get("id") == "validate")
    assert json.loads(_mapping(validate["env"])["CI_STATIC_JOB_IDS_JSON"]) == sorted(
        selected_job_ids
    )
    assert _mapping(validate["env"])["CI_COORDINATOR_PLAN_TRUST_ROOT_PATH"] == (
        ".ci-coordinator/plan-trust-root.v1.json"
    )
    assert _mapping(validate["env"])["CI_COORDINATOR_PLAN_OIDC_AUDIENCE"] == (
        "${{ vars.CI_COORDINATOR_PLAN_URL }}"
    )
    assert "CI_COORDINATOR_PLAN_KEY_ID" not in source
    assert "CI_COORDINATOR_PLAN_PUBLIC_KEY" not in source
    assert _mapping(validate["env"])["CI_WORKFLOW_REF"] == "${{ job.workflow_ref }}"
    assert _mapping(validate["env"])["CI_WORKFLOW_SHA"] == "${{ job.workflow_sha }}"

    gate = _mapping(jobs["bootstrap-gate"])
    assert gate["name"] == "Dynamic CI Bootstrap"
    assert set(_string_sequence(gate["needs"])) == {"plan", "full-ci", *selected_job_ids}
    assert "always()" in str(gate["if"])
    assert _mapping(gate["permissions"]) == {"contents": "read"}
    gate_steps = _sequence(gate["steps"])
    assert gate_steps[0] == {
        "name": "Reject cancelled workflow",
        "if": "${{ cancelled() }}",
        "run": "exit 1",
    }
    gate_checkout = gate_steps[1]
    assert gate_checkout["uses"] == checkout["uses"]
    assert _mapping(gate_checkout["with"]) == {
        "persist-credentials": False,
        "ref": "${{ job.workflow_sha }}",
    }
    gate_setup_node = gate_steps[2]
    assert gate_setup_node["uses"] == setup_node["uses"]
    assert _mapping(gate_setup_node["with"]) == {"node-version": "24.21.0"}
    gate_step = gate_steps[3]
    assert gate_step["run"] == "node .ci-coordinator/ci-coordinator.cjs validate-gate"
    assert set(_mapping(gate_step["env"])) == {
        "CI_FALLBACK",
        "CI_FULL_CI_RESULT",
        "CI_OWNER",
        "CI_PLAN_RESULT",
        "CI_PLAN_VALID",
        "CI_REPO",
        "CI_SELECTED_JOBS_JSON",
        "CI_STATIC_JOB_RESULTS_JSON",
        "CI_TARGET_REGISTRY_PATH",
        "CI_WORKFLOW_PATH",
        "CI_WORKFLOW_REF",
    }
    assert _mapping(gate_step["env"])["CI_WORKFLOW_REF"] == "${{ job.workflow_ref }}"

    full_ci = _mapping(jobs["full-ci"])
    assert full_ci["name"] == "ci/full-ci/ed96dc58121d215d5431"
    assert full_ci["needs"] == "plan"
    assert "needs.plan.outputs.plan_valid != 'true'" in str(full_ci["if"])
    assert "needs.plan.outputs.fallback != 'false'" in str(full_ci["if"])
    for job_id in selected_job_ids:
        selected = _mapping(jobs[job_id])
        assert selected["name"] == "${{ matrix.provider_job_name }}"
        assert selected["needs"] == "plan"
        assert "needs.plan.outputs.plan_valid == 'true'" in str(selected["if"])
        assert "needs.plan.outputs.fallback == 'false'" in str(selected["if"])
        assert "fromJSON(needs.plan.outputs.selected_jobs || '[]')" in str(selected["if"])
        assert f"'{job_id}'" in str(selected["if"])
        strategy = _mapping(selected["strategy"])
        assert strategy["matrix"] == f"${{{{ fromJSON(needs.plan.outputs.matrices)['{job_id}'] }}}}"
        assert strategy["max-parallel"] == (
            f"${{{{ fromJSON(needs.plan.outputs.max_parallel_by_job)['{job_id}'] }}}}"
        )

    postgres = _mapping(jobs["selected-python-postgres"])
    service = _mapping(_mapping(postgres["services"])["postgres"])
    assert service["image"] == (
        "postgres:18.6-trixie@sha256:"
        "86c951e05bf56c93d95d397747fb8820ac76cc3bedb78f43abd83eedbe3666ae"
    )


def test_bootstrap_executes_only_the_fixed_repository_entrypoint() -> None:
    source = _BOOTSTRAP_PATH.read_text(encoding="utf-8")

    assert "CI_COORDINATOR_SELECTED_CHECK_COMMAND" not in source
    assert "CI_COORDINATOR_FULL_CI_COMMAND" not in source
    assert "bash -euo pipefail -c" not in source
    assert source.count(".ci-coordinator/run witness") == 2
    assert source.count(".ci-coordinator/run full-ci") == 1
    assert "CI_COORDINATOR_SHARD_JSON: ${{ matrix.shard_json }}" in source


def test_bootstrap_workflow_matches_the_control_plane_admission_contract() -> None:
    revision = "a" * 40
    workflow_path = ".github/workflows/ci-coordinator-bootstrap.yml"
    capability = parse_workflow_capability(
        _BOOTSTRAP_PATH.read_bytes(),
        path=workflow_path,
        revision_sha=revision,
    )
    assert capability is not None
    inventory = ProviderWorkflowInventory(
        revision_sha=revision,
        default_branch_workflows=(),
        revision_capabilities=(capability,),
    )

    assert inventory.admits_control_plane(
        workflow_path=workflow_path,
        execution_kind="witness-shards",
        invocation_job_id=CONTROL_INVOCATION_JOB_ID,
        plan_request_job_id=PLAN_REQUEST_JOB_ID,
        plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
        plan_job_id="plan",
        fallback_job_id="full-ci",
        gate_job_id="bootstrap-gate",
        execution_job_ids=(
            "selected-python-linux",
            "selected-python-postgres",
        ),
    )


def test_external_control_bundle_is_syntactically_valid_node_code() -> None:
    subprocess.run(
        ["node", "--check", str(_CONTROL_PATH)],
        cwd=_REPOSITORY_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )


@pytest.mark.parametrize(
    "arguments",
    ((), ("unknown",), ("consume-plan", "extra")),
    ids=("missing", "unknown", "extra"),
)
def test_external_control_bundle_rejects_invalid_command_grammar(
    arguments: tuple[str, ...],
) -> None:
    completed = subprocess.run(
        ["node", str(_CONTROL_PATH), *arguments],
        cwd=_REPOSITORY_ROOT,
        check=False,
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_plan_consumer_enforces_the_bounded_seven_chunk_transport() -> None:
    source = _PLAN_CONSUMER_SOURCE_PATH.read_text(encoding="utf-8")

    assert "const CHUNK_COUNT = 7;" in source
    assert "const MAX_CHUNK_CHARACTERS = 65_536;" in source
    assert "const MAX_COMPRESSED_BYTES = 344_064;" in source
    assert "const MAX_PLAN_BYTES = 1_048_576;" in source
    assert "plan_transport_chunks_invalid" in source
    assert 'compressed.toString("base64url") !== encoded' in source


def test_bootstrap_contract_uses_only_signed_fallback_coordinates() -> None:
    contract = _BOOTSTRAP_CONTRACT_PATH.read_text(encoding="utf-8")

    assert "resolution.status" not in contract
    assert all(
        coordinate in contract
        for coordinate in (
            "verifiedPlanId",
            "productionAdmissionReceiptId",
            "verifierVersion",
            "fallbackReason",
            "execution.mode",
            "targetRegistryHash",
            "providerSignal",
        )
    )


def test_bootstrap_gate_admits_exactly_one_successful_execution_path() -> None:
    common = _gate_environment()
    cases: tuple[tuple[dict[str, str], bool], ...] = (
        ({}, True),
        (
            {
                "CI_FALLBACK": "true",
                "CI_SELECTED_JOBS_JSON": "[]",
                "CI_STATIC_JOB_RESULTS_JSON": (
                    '{"selected-python-linux":"skipped","selected-python-postgres":"skipped"}'
                ),
                "CI_FULL_CI_RESULT": "success",
            },
            True,
        ),
        (
            {
                "CI_PLAN_RESULT": "failure",
                "CI_PLAN_VALID": "",
                "CI_FALLBACK": "",
                "CI_SELECTED_JOBS_JSON": "",
                "CI_STATIC_JOB_RESULTS_JSON": (
                    '{"selected-python-linux":"skipped","selected-python-postgres":"skipped"}'
                ),
                "CI_FULL_CI_RESULT": "success",
            },
            True,
        ),
        (
            {
                "CI_FULL_CI_RESULT": "success",
            },
            False,
        ),
        (
            {
                "CI_STATIC_JOB_RESULTS_JSON": (
                    '{"selected-python-linux":"failure","selected-python-postgres":"skipped"}'
                ),
            },
            False,
        ),
        (
            {
                "CI_PLAN_RESULT": "failure",
                "CI_PLAN_VALID": "",
                "CI_FALLBACK": "",
                "CI_SELECTED_JOBS_JSON": "[]",
                "CI_STATIC_JOB_RESULTS_JSON": (
                    '{"selected-python-linux":"skipped","selected-python-postgres":"skipped"}'
                ),
                "CI_FULL_CI_RESULT": "failure",
            },
            False,
        ),
        (
            {
                "CI_SELECTED_JOBS_JSON": "[]",
            },
            False,
        ),
        (
            {
                "CI_SELECTED_JOBS_JSON": ('["selected-python-linux","selected-python-postgres"]'),
                "CI_STATIC_JOB_RESULTS_JSON": (
                    '{"selected-python-linux":"success","selected-python-postgres":"success"}'
                ),
            },
            True,
        ),
        (
            {
                "CI_SELECTED_JOBS_JSON": ('["selected-python-postgres","selected-python-linux"]'),
                "CI_STATIC_JOB_RESULTS_JSON": (
                    '{"selected-python-linux":"success","selected-python-postgres":"success"}'
                ),
            },
            False,
        ),
        (
            {
                "CI_STATIC_JOB_RESULTS_JSON": (
                    '{"selected-python-linux":"success","selected-python-postgres":"success"}'
                ),
            },
            False,
        ),
        (
            {
                "CI_STATIC_JOB_RESULTS_JSON": '{"selected-python-linux":"success"}',
            },
            False,
        ),
        (
            {
                "CI_STATIC_JOB_RESULTS_JSON": (
                    '{"foreign-job":"skipped","selected-python-linux":"success",'
                    '"selected-python-postgres":"skipped"}'
                ),
            },
            False,
        ),
        (
            {
                "CI_STATIC_JOB_RESULTS_JSON": (
                    '{"selected-python-linux":"neutral","selected-python-postgres":"skipped"}'
                ),
            },
            False,
        ),
        (
            {
                "CI_SELECTED_JOBS_JSON": "not-json",
            },
            False,
        ),
        (
            {
                "CI_SELECTED_JOBS_JSON": '["foreign-job"]',
                "CI_STATIC_JOB_RESULTS_JSON": (
                    '{"selected-python-linux":"skipped","selected-python-postgres":"skipped"}'
                ),
            },
            False,
        ),
        (
            {
                "CI_FALLBACK": "true",
                "CI_SELECTED_JOBS_JSON": '["selected-python-linux"]',
                "CI_STATIC_JOB_RESULTS_JSON": (
                    '{"selected-python-linux":"skipped","selected-python-postgres":"skipped"}'
                ),
                "CI_FULL_CI_RESULT": "success",
            },
            False,
        ),
    )
    for overrides, admitted in cases:
        completed = subprocess.run(
            ["node", str(_CONTROL_PATH), "validate-gate"],
            cwd=_REPOSITORY_ROOT,
            env={**os.environ, **common, **overrides},
            check=False,
            text=True,
            capture_output=True,
        )
        assert (completed.returncode == 0) is admitted


def test_bootstrap_gate_rejects_selected_mode_without_a_required_job(
    tmp_path: Path,
) -> None:
    registry = json.loads(_REGISTRY_PATH.read_bytes())
    workflow = _mapping(_sequence(_mapping(registry)["workflows"])[0])
    workflow["requiredJobIds"] = ["selected-python-postgres"]
    registry_path = tmp_path / "execution-registry.v1.json"
    registry_path.write_bytes(canonical_json(registry) + b"\n")

    completed = subprocess.run(
        ["node", str(_CONTROL_PATH), "validate-gate"],
        cwd=_REPOSITORY_ROOT,
        env={
            **os.environ,
            **_gate_environment(),
            "CI_TARGET_REGISTRY_PATH": str(registry_path),
        },
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode != 0


def test_gate_validator_is_execution_kind_and_workflow_path_neutral(
    tmp_path: Path,
) -> None:
    registry = json.loads(_REGISTRY_PATH.read_bytes())
    workflow_path = ".github/workflows/native-ci.yml"
    for workflow in _sequence(_mapping(registry)["workflows"]):
        workflow["executionKind"] = "native-job-set"
        workflow["fallbackJobId"] = None
        workflow["workflowPath"] = workflow_path
    for profile in _sequence(_mapping(registry)["profiles"]):
        profile["executionKind"] = "native-job-set"
        profile["workflowPath"] = workflow_path
    first_workflow = _sequence(_mapping(registry)["workflows"])[0]
    first_profile = _sequence(_mapping(registry)["profiles"])[0]
    second_path = ".github/workflows/release.yml"
    _mapping(registry)["workflows"] = sorted(
        (
            first_workflow,
            {
                **first_workflow,
                "executionJobs": [{"jobId": "release-check", "needs": ["plan"]}],
                "gateJobId": "release-gate",
                "workflowPath": second_path,
            },
        ),
        key=lambda item: str(item["workflowPath"]),
    )
    _mapping(registry)["profiles"] = sorted(
        (
            *_sequence(_mapping(registry)["profiles"]),
            {
                **first_profile,
                "jobId": "release-check",
                "profileId": "release-profile",
                "workflowPath": second_path,
            },
        ),
        key=lambda item: str(item["profileId"]),
    )
    adapter_files = _sequence(_mapping(registry)["adapterFiles"])
    workflow_binding = next(
        binding
        for binding in adapter_files
        if str(binding["path"]).startswith(".github/workflows/")
    )
    _mapping(registry)["adapterFiles"] = sorted(
        (
            *(
                binding
                for binding in adapter_files
                if not str(binding["path"]).startswith(".github/workflows/")
            ),
            {**workflow_binding, "path": workflow_path},
            {"path": second_path, "sha256": "0" * 64},
        ),
        key=lambda item: str(item["path"]),
    )
    registry_path = tmp_path / "execution-registry.v1.json"
    registry_path.write_bytes(canonical_json(registry) + b"\n")
    environment = {
        **_gate_environment(),
        "CI_TARGET_REGISTRY_PATH": str(registry_path),
        "CI_WORKFLOW_PATH": workflow_path,
        "CI_WORKFLOW_REF": f"example/target/{workflow_path}@refs/heads/master",
        "CI_FULL_CI_RESULT": "",
    }

    completed = subprocess.run(
        ["node", str(_CONTROL_PATH), "validate-gate"],
        cwd=_REPOSITORY_ROOT,
        env={**os.environ, **environment},
        check=False,
        text=True,
        capture_output=True,
    )

    assert completed.returncode == 0
    fallback = {
        **environment,
        "CI_PLAN_RESULT": "failure",
        "CI_PLAN_VALID": "",
        "CI_FALLBACK": "",
        "CI_SELECTED_JOBS_JSON": "[]",
        "CI_STATIC_JOB_RESULTS_JSON": (
            '{"selected-python-linux":"success","selected-python-postgres":"success"}'
        ),
        "CI_FULL_CI_RESULT": "",
    }
    native_fallback = subprocess.run(
        ["node", str(_CONTROL_PATH), "validate-gate"],
        cwd=_REPOSITORY_ROOT,
        env={**os.environ, **fallback},
        check=False,
        text=True,
        capture_output=True,
    )
    sharded_fallback_shape = subprocess.run(
        ["node", str(_CONTROL_PATH), "validate-gate"],
        cwd=_REPOSITORY_ROOT,
        env={
            **os.environ,
            **fallback,
            "CI_STATIC_JOB_RESULTS_JSON": (
                '{"selected-python-linux":"skipped","selected-python-postgres":"skipped"}'
            ),
            "CI_FULL_CI_RESULT": "success",
        },
        check=False,
        text=True,
        capture_output=True,
    )

    assert native_fallback.returncode == 0
    assert sharded_fallback_shape.returncode != 0

    fake_native_full_ci = subprocess.run(
        ["node", str(_CONTROL_PATH), "validate-gate"],
        cwd=_REPOSITORY_ROOT,
        env={
            **os.environ,
            **fallback,
            "CI_FULL_CI_RESULT": "skipped",
        },
        check=False,
        text=True,
        capture_output=True,
    )
    assert fake_native_full_ci.returncode != 0


def test_every_bootstrap_shell_step_is_syntactically_valid() -> None:
    workflow = _read_workflow(_BOOTSTRAP_PATH)
    for job in _mapping(workflow["jobs"]).values():
        for step in _sequence(_mapping(job).get("steps", [])):
            script = step.get("run")
            if isinstance(script, str):
                subprocess.run(
                    ["bash", "-n"],
                    check=True,
                    input=script,
                    text=True,
                    capture_output=True,
                )


def _read_workflow(path: Path) -> dict[str, Any]:
    parser = YAML(typ="safe")
    return _mapping(parser.load(path.read_text(encoding="utf-8")))


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


def _gate_environment() -> dict[str, str]:
    return {
        "CI_PLAN_RESULT": "success",
        "CI_PLAN_VALID": "true",
        "CI_FALLBACK": "false",
        "CI_SELECTED_JOBS_JSON": '["selected-python-linux"]',
        "CI_STATIC_JOB_RESULTS_JSON": (
            '{"selected-python-linux":"success","selected-python-postgres":"skipped"}'
        ),
        "CI_FULL_CI_RESULT": "skipped",
        "CI_TARGET_REGISTRY_PATH": (
            "fixtures/target-repository/.ci-coordinator/execution-registry.v1.json"
        ),
        "CI_OWNER": "example",
        "CI_REPO": "target",
        "CI_WORKFLOW_PATH": ".github/workflows/ci-coordinator-bootstrap.yml",
        "CI_WORKFLOW_REF": (
            "example/target/.github/workflows/ci-coordinator-bootstrap.yml@refs/heads/master"
        ),
    }

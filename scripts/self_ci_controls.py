from __future__ import annotations

import copy
import io
import json

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.nodes import ScalarNode
from ruamel.yaml.resolver import VersionedResolver
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from ci_coordinator.repo_context.workflow_control_plane import (
    admitted_control_job_projection_hashes,
    control_job_projection_hash,
)
from scripts.proofkit_common import JsonObject, as_array, as_object
from scripts.self_ci_source import (
    REQUESTER_ASSERTION_ID,
    REQUESTER_ID,
    WORKFLOW_PATH,
    NativeSource,
    needs,
    workflow_value,
)

PLAN_ID = "plan"
REQUEST_ID = "plan-request"
COORDINATED_GATE_ID = "coordinated-checks-gate"
COORDINATED_GATE_NAME = "Coordinated Checks"
LOCAL_REQUESTER = "$/.github/workflows/trusted-plan-request.yml"


def render_workflow(native: NativeSource, control_template: bytes, node_version: str) -> bytes:
    template = workflow_value(control_template, ".github/workflows/full-check.yml")
    template_jobs = as_object(template["jobs"], "control template jobs")
    execution_jobs = {
        job_id: _native_job(job_id, job, native.workflow)
        for job_id, job in sorted(native.jobs.items())
    }
    reason_job = {
        "name": "Native requester rejection contract",
        "needs": [REQUESTER_ID],
        "runs-on": "ubuntu-24.04",
        "timeout-minutes": 2,
        "permissions": {},
        "steps": [
            {
                "name": "Require the native rejection reason",
                "env": {"TRUSTED_PLAN_REQUEST_REASON": native.requester_reason_expression},
                "run": (
                    "set -euo pipefail\n"
                    'test "${TRUSTED_PLAN_REQUEST_REASON}" = ' + native.requester_reason + "\n"
                ),
            }
        ],
    }
    execution_jobs[REQUESTER_ASSERTION_ID] = _native_job(REQUESTER_ASSERTION_ID, reason_job, {})
    execution_ids = tuple(sorted(execution_jobs))
    invocation = copy.deepcopy(as_object(template_jobs["ci-invocation"], "invocation template"))
    request = copy.deepcopy(as_object(template_jobs[REQUEST_ID], "request template"))
    request["uses"] = LOCAL_REQUESTER
    request["if"] = (
        "${{ needs['ci-invocation'].outputs.direct == 'true' && github.event_name == 'push' }}"
    )
    plan = copy.deepcopy(as_object(template_jobs[PLAN_ID], "plan template"))
    gate = copy.deepcopy(as_object(template_jobs["full-check-gate"], "gate template"))
    gate["name"] = COORDINATED_GATE_NAME
    gate["needs"] = sorted((PLAN_ID, *execution_ids))
    for job in (plan, gate):
        for raw_step in as_array(job["steps"], "control steps"):
            step = as_object(raw_step, "control step")
            if str(step.get("uses", "")).startswith("actions/setup-node@"):
                as_object(step["with"], "Node setup")["node-version"] = node_version
            environment = as_object(step.get("env", {}), "control environment")
            if "CI_WORKFLOW_PATH" in environment:
                environment["CI_WORKFLOW_PATH"] = WORKFLOW_PATH
            if "CI_STATIC_JOB_IDS_JSON" in environment:
                environment["CI_STATIC_JOB_IDS_JSON"] = json.dumps(
                    execution_ids, separators=(",", ":")
                )
            if "CI_STATIC_JOB_RESULTS_JSON" in environment:
                environment["CI_STATIC_JOB_RESULTS_JSON"] = (
                    "{"
                    + ",".join(
                        f"{json.dumps(job_id)}:\"${{{{ needs['{job_id}'].result }}}}\""
                        for job_id in execution_ids
                    )
                    + "}"
                )
    jobs = {
        "ci-invocation": invocation,
        REQUEST_ID: request,
        PLAN_ID: plan,
        **dict(sorted(execution_jobs.items())),
        COORDINATED_GATE_ID: gate,
    }
    workflow: JsonObject = {
        "name": COORDINATED_GATE_NAME,
        "on": {
            "push": {"branches": ["ci-qualification/**"]},
            "workflow_dispatch": {"inputs": copy.deepcopy(native.proof_inputs)},
        },
        "permissions": {"contents": "read"},
        "concurrency": {
            "group": "coordinated-checks-${{ github.repository }}-${{ github.ref }}",
            "cancel-in-progress": False,
        },
        "jobs": jobs,
    }
    admitted = admitted_control_job_projection_hashes(
        workflow_path=WORKFLOW_PATH,
        workflow_triggers=("push", "workflow_dispatch"),
        execution_kind="native-job-set",
        execution_job_ids=execution_ids,
        invocation_job_id="ci-invocation",
        plan_request_job_id=REQUEST_ID,
        plan_request_workflow_ref=LOCAL_REQUESTER,
        plan_job_id=PLAN_ID,
        gate_job_id=COORDINATED_GATE_ID,
        fallback_job_id=None,
    )
    observed = tuple(
        control_job_projection_hash(job, workflow=workflow)
        for job in (invocation, request, plan, gate)
    )
    if admitted is None or observed not in admitted:
        raise ValueError("generated control jobs differ from the runtime-owned control contract")
    return (
        "# Generated by python -m scripts.self_ci_generate --write; edit native owners.\n"
        + dump_workflow_yaml(workflow)
    ).encode()


def dump_workflow_yaml(workflow: JsonObject) -> str:
    yaml = YAML()
    yaml.default_flow_style = False
    yaml.width = 1000
    output = io.StringIO()
    resolver = VersionedResolver(version=(1, 1))
    projected = as_object(_quote_ambiguous_strings(workflow, resolver), "workflow YAML")
    _document_requester_permissions(projected)
    yaml.dump(projected, output)
    return output.getvalue()


def _document_requester_permissions(workflow: JsonObject) -> None:
    jobs = workflow.get("jobs")
    if not isinstance(jobs, dict):
        return
    comments = {
        REQUEST_ID: "Delegate GitHub OIDC to the target-free coordinator plan requester.",
        REQUESTER_ID: (
            "Match the callee's OIDC authority; "
            "the invalid URL must reject before token acquisition."
        ),
    }
    for job_id, comment in comments.items():
        job = jobs.get(job_id)
        if (
            not isinstance(job, dict)
            or job.get("uses") != LOCAL_REQUESTER
            or job.get("permissions") != {"id-token": "write"}
            or (
                job_id == REQUESTER_ID
                and job.get("with") != {"installation_id": "1", "plan_url": "invalid"}
            )
        ):
            continue
        permissions = CommentedMap({"id-token": "write"})
        permissions.yaml_add_eol_comment(comment, key="id-token")
        job["permissions"] = permissions


def _quote_ambiguous_strings(value: object, resolver: VersionedResolver) -> object:
    if isinstance(value, str):
        if str(resolver.resolve(ScalarNode, value, (True, False))) != "tag:yaml.org,2002:str":
            return DoubleQuotedScalarString(value)
        return value
    if isinstance(value, dict):
        return {
            _quote_ambiguous_strings(key, resolver): _quote_ambiguous_strings(item, resolver)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_quote_ambiguous_strings(item, resolver) for item in value]
    return value


def _native_job(job_id: str, source_job: JsonObject, workflow: JsonObject) -> JsonObject:
    job = copy.deepcopy(source_job)
    native_needs = needs(job)
    dependencies = sorted({PLAN_ID, *native_needs})
    job["needs"] = dependencies
    ready = " && ".join(f"needs['{dependency}'].result == 'success'" for dependency in native_needs)
    selected = (
        "needs.plan.result != 'success' || needs.plan.outputs.plan_valid != 'true' || "
        "needs.plan.outputs.fallback != 'false' || "
        f"contains(fromJSON(needs.plan.outputs.selected_jobs || '[]'), '{job_id}')"
    )
    job["if"] = "${{ always() && " + (ready + " && " if ready else "") + "(" + selected + ") }}"
    if "steps" in job:
        environment = {
            **as_object(workflow.get("env", {}), "source environment"),
            **as_object(job.get("env", {}), "job environment"),
        }
        if environment:
            job["env"] = environment
    return job

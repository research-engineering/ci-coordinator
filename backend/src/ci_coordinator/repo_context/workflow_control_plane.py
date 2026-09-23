"""Canonical safety projection for the generated target control plane."""

from __future__ import annotations

import json
import math
import re
from typing import Final

from ci_coordinator.kernel import hash_object

PLAN_CONSUME_COMMAND: Final = "node .ci-coordinator/ci-coordinator.cjs consume-plan"
PLAN_VALIDATE_COMMAND: Final = "node .ci-coordinator/ci-coordinator.cjs validate-plan"
GATE_CANCEL_COMMAND: Final = "exit 1"
GATE_VALIDATE_COMMAND: Final = "node .ci-coordinator/ci-coordinator.cjs validate-gate"

_CHECKOUT_ACTION: Final = "actions/checkout@<immutable-git-object>"
_SETUP_NODE_ACTION: Final = "actions/setup-node@<immutable-git-object>"
_GIT_OBJECT_ACTION: Final = re.compile(r"(?P<action>actions/(?:checkout|setup-node))@[0-9a-f]{40}")
_DOT_PROPERTY: Final = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_OMITTED_JOB_KEYS: Final = frozenset({"name", "needs"})
_OMITTED_STEP_KEYS: Final = frozenset({"name"})
_DYNAMIC_PLAN_EVENTS: Final[tuple[str, ...]] = (
    "pull_request",
    "push",
    "merge_group",
)

PLAN_TRANSPORT_CHUNK_COUNT: Final = 7
PLAN_CONSUME_ENVIRONMENT_KEYS: Final = tuple(
    sorted(
        {
            "CI_COORDINATOR_PLAN_REASON",
            "PLAN_PATH",
            *(f"CI_COORDINATOR_PLAN_CHUNK_{index}" for index in range(PLAN_TRANSPORT_CHUNK_COUNT)),
        }
    )
)
PLAN_VALIDATE_ENVIRONMENT_KEYS: Final = tuple(
    sorted(
        {
            "CI_BASE_SHA",
            "CI_COORDINATOR_PLAN_OIDC_AUDIENCE",
            "CI_COORDINATOR_PLAN_TRUST_ROOT_PATH",
            "CI_EVENT",
            "CI_EXECUTION_SHA",
            "CI_HEAD_SHA",
            "CI_INSTALLATION_ID",
            "CI_MERGE_GROUP_HEAD_REF",
            "CI_OWNER",
            "CI_PULL_REQUEST_NUMBER",
            "CI_REF",
            "CI_REPOSITORY_ID",
            "CI_REPO",
            "CI_RUN_ATTEMPT",
            "CI_RUN_ID",
            "CI_STATIC_JOB_IDS_JSON",
            "CI_TARGET_REGISTRY_PATH",
            "CI_WORKFLOW_PATH",
            "CI_WORKFLOW_REF",
            "CI_WORKFLOW_SHA",
            "PLAN_PATH",
        }
    )
)
GATE_ENVIRONMENT_KEYS: Final = tuple(
    sorted(
        {
            "CI_FALLBACK",
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
    )
)
GATE_ENVIRONMENT_KEY_SETS: Final = (
    GATE_ENVIRONMENT_KEYS,
    tuple(sorted({*GATE_ENVIRONMENT_KEYS, "CI_FULL_CI_RESULT"})),
)


def control_job_projection_hash(
    job: object,
    *,
    workflow: dict[str, object],
) -> str | None:
    """Hash safety-relevant job semantics while excluding cosmetic labels."""

    try:
        projection: dict[str, object] = {
            "job": _normalize_mapping(job, omitted_keys=_OMITTED_JOB_KEYS, step=False)
        }
        for source_key, projection_key in (
            ("defaults", "workflowDefaults"),
            ("env", "workflowEnvironment"),
        ):
            if source_key in workflow:
                projection[projection_key] = _normalize(workflow[source_key])
        return hash_object(projection)
    except (TypeError, ValueError):
        return None


def admitted_control_job_projection_hashes(
    *,
    workflow_path: str,
    workflow_triggers: tuple[str, ...],
    execution_kind: str,
    execution_job_ids: tuple[str, ...],
    invocation_job_id: str,
    plan_request_job_id: str,
    plan_request_workflow_ref: str,
    plan_job_id: str,
    gate_job_id: str,
    fallback_job_id: str | None,
) -> tuple[tuple[str, str, str, str], ...] | None:
    if (
        execution_kind not in {"native-job-set", "witness-shards"}
        or not execution_job_ids
        or type(workflow_triggers) is not tuple
        or any(type(trigger) is not str or not trigger for trigger in workflow_triggers)
        or (execution_kind == "native-job-set" and fallback_job_id is not None)
        or (execution_kind == "witness-shards" and fallback_job_id is None)
        or len(
            {
                job_id.lower()
                for job_id in (
                    invocation_job_id,
                    plan_request_job_id,
                    plan_job_id,
                    gate_job_id,
                    *execution_job_ids,
                    *(() if fallback_job_id is None else (fallback_job_id,)),
                )
            }
        )
        != 4 + len(execution_job_ids) + (fallback_job_id is not None)
    ):
        return None
    dynamic_events = tuple(event for event in _DYNAMIC_PLAN_EVENTS if event in workflow_triggers)
    if not dynamic_events:
        return None
    plan = _plan_job(
        workflow_path=workflow_path,
        execution_kind=execution_kind,
        execution_job_ids=execution_job_ids,
        plan_request_job_id=plan_request_job_id,
    )
    gate = _gate_job(
        workflow_path=workflow_path,
        execution_kind=execution_kind,
        execution_job_ids=execution_job_ids,
        plan_job_id=plan_job_id,
        fallback_job_id=fallback_job_id,
    )
    invocation_hash = control_job_projection_hash(_invocation_job(), workflow={})
    plan_hash = control_job_projection_hash(plan, workflow={})
    gate_hash = control_job_projection_hash(gate, workflow={})
    if invocation_hash is None or plan_hash is None or gate_hash is None:
        raise RuntimeError("generated control-plane projection is invalid")
    draft_policies = (False, True) if "pull_request" in dynamic_events else (False,)
    candidates: list[tuple[str, str, str, str]] = []
    for exclude_draft_pull_requests in draft_policies:
        plan_request = _plan_request_job(
            invocation_job_id=invocation_job_id,
            workflow_events=dynamic_events,
            plan_request_workflow_ref=plan_request_workflow_ref,
            exclude_draft_pull_requests=exclude_draft_pull_requests,
        )
        request_hash = control_job_projection_hash(plan_request, workflow={})
        if request_hash is None:
            raise RuntimeError("generated control-plane projection is invalid")
        candidates.append((invocation_hash, request_hash, plan_hash, gate_hash))
    return tuple(candidates)


def _invocation_job() -> dict[str, object]:
    return {
        "runs-on": "ubuntu-24.04",
        "timeout-minutes": 1,
        "permissions": {},
        "outputs": {"direct": "${{ steps.classify.outputs.direct }}"},
        "steps": [
            {
                "id": "classify",
                "shell": "bash",
                "env": {
                    "CI_CALLER_WORKFLOW_REF": "${{ github.workflow_ref }}",
                    "CI_DEFINING_WORKFLOW_REF": "${{ job.workflow_ref }}",
                },
                "run": (
                    "set -euo pipefail\n"
                    "direct=false\n"
                    'if [ -n "$CI_CALLER_WORKFLOW_REF" ] && '
                    '[ "$CI_CALLER_WORKFLOW_REF" = "$CI_DEFINING_WORKFLOW_REF" ]; then\n'
                    "  direct=true\n"
                    "fi\n"
                    'printf \'direct=%s\\n\' "$direct" >> "$GITHUB_OUTPUT"\n'
                ),
            }
        ],
    }


def _plan_request_job(
    *,
    invocation_job_id: str,
    workflow_events: tuple[str, ...],
    plan_request_workflow_ref: str,
    exclude_draft_pull_requests: bool,
) -> dict[str, object]:
    return {
        "if": _plan_request_condition(
            invocation_job_id=invocation_job_id,
            workflow_events=workflow_events,
            exclude_draft_pull_requests=exclude_draft_pull_requests,
        ),
        "needs": invocation_job_id,
        "permissions": {"id-token": "write"},
        "uses": plan_request_workflow_ref,
        "with": {
            "installation_id": (
                "${{ vars.CI_COORDINATOR_INSTALLATION_ID || github.event.installation.id }}"
            ),
            "plan_url": "${{ vars.CI_COORDINATOR_PLAN_URL }}",
        },
    }


def _plan_request_condition(
    *,
    invocation_job_id: str,
    workflow_events: tuple[str, ...],
    exclude_draft_pull_requests: bool,
) -> str:
    event_predicates = tuple(f"github.event_name == '{event}'" for event in workflow_events)
    event_clause = (
        event_predicates[0]
        if len(event_predicates) == 1
        else "(" + " || ".join(event_predicates) + ")"
    )
    clauses = [f"needs['{invocation_job_id}'].outputs.direct == 'true'", event_clause]
    if exclude_draft_pull_requests:
        clauses.append("(github.event_name != 'pull_request' || !github.event.pull_request.draft)")
    return "${{ " + " && ".join(clauses) + " }}"


def _plan_job(
    *,
    workflow_path: str,
    execution_kind: str,
    execution_job_ids: tuple[str, ...],
    plan_request_job_id: str,
) -> dict[str, object]:
    outputs = {
        "fallback": "${{ steps.validate.outputs.fallback }}",
        "plan_valid": "${{ steps.validate.outputs.plan_valid }}",
        "reason": "${{ steps.validate.outputs.reason }}",
        "selected_jobs": "${{ steps.validate.outputs.selected_jobs }}",
    }
    if execution_kind == "witness-shards":
        outputs.update(
            {
                "matrices": "${{ steps.validate.outputs.matrices }}",
                "max_parallel_by_job": "${{ steps.validate.outputs.max_parallel_by_job }}",
            }
        )
    return {
        "if": "${{ !cancelled() }}",
        "runs-on": "ubuntu-24.04",
        "timeout-minutes": 2,
        "permissions": {"contents": "read"},
        "outputs": outputs,
        "steps": [
            {
                "uses": _CHECKOUT_ACTION,
                "with": {
                    "persist-credentials": False,
                    "ref": "${{ job.workflow_sha }}",
                },
            },
            {
                "uses": _SETUP_NODE_ACTION,
                "with": {"node-version": "24.21.0"},
            },
            {
                "env": _plan_consume_environment(plan_request_job_id),
                "run": PLAN_CONSUME_COMMAND,
            },
            {
                "id": "validate",
                "env": _plan_validate_environment(workflow_path, execution_job_ids),
                "run": PLAN_VALIDATE_COMMAND,
            },
        ],
    }


def _gate_job(
    *,
    workflow_path: str,
    execution_kind: str,
    execution_job_ids: tuple[str, ...],
    plan_job_id: str,
    fallback_job_id: str | None,
) -> dict[str, object]:
    environment = {
        "CI_FALLBACK": _needs_output(plan_job_id, "fallback"),
        "CI_OWNER": "${{ github.repository_owner }}",
        "CI_PLAN_RESULT": f"${{{{ {_needs_job(plan_job_id)}.result }}}}",
        "CI_PLAN_VALID": _needs_output(plan_job_id, "plan_valid"),
        "CI_REPO": "${{ github.event.repository.name }}",
        "CI_SELECTED_JOBS_JSON": _needs_output(plan_job_id, "selected_jobs"),
        "CI_STATIC_JOB_RESULTS_JSON": _static_job_results(execution_job_ids),
        "CI_TARGET_REGISTRY_PATH": ".ci-coordinator/execution-registry.v1.json",
        "CI_WORKFLOW_PATH": workflow_path,
        "CI_WORKFLOW_REF": "${{ job.workflow_ref }}",
    }
    if execution_kind == "witness-shards":
        if fallback_job_id is None:  # pragma: no cover - guarded by caller
            raise RuntimeError("sharded gate requires a fallback job")
        environment["CI_FULL_CI_RESULT"] = f"${{{{ needs.{fallback_job_id}.result }}}}"
    return {
        "if": "${{ always() }}",
        "runs-on": "ubuntu-24.04",
        "timeout-minutes": 2,
        "permissions": {"contents": "read"},
        "steps": [
            {"if": "${{ cancelled() }}", "run": GATE_CANCEL_COMMAND},
            {
                "uses": _CHECKOUT_ACTION,
                "with": {
                    "persist-credentials": False,
                    "ref": "${{ job.workflow_sha }}",
                },
            },
            {
                "uses": _SETUP_NODE_ACTION,
                "with": {"node-version": "24.21.0"},
            },
            {"env": environment, "run": GATE_VALIDATE_COMMAND},
        ],
    }


def _plan_consume_environment(plan_request_job_id: str) -> dict[str, str]:
    return {
        "PLAN_PATH": "${{ runner.temp }}/dynamic-ci-plan.json",
        "CI_COORDINATOR_PLAN_REASON": _needs_output(plan_request_job_id, "reason"),
        **{
            f"CI_COORDINATOR_PLAN_CHUNK_{index}": _needs_output(
                plan_request_job_id,
                f"plan_chunk_{index}",
            )
            for index in range(PLAN_TRANSPORT_CHUNK_COUNT)
        },
    }


def _plan_validate_environment(
    workflow_path: str,
    execution_job_ids: tuple[str, ...],
) -> dict[str, str]:
    return {
        "PLAN_PATH": "${{ runner.temp }}/dynamic-ci-plan.json",
        "CI_COORDINATOR_PLAN_TRUST_ROOT_PATH": ".ci-coordinator/plan-trust-root.v1.json",
        "CI_COORDINATOR_PLAN_OIDC_AUDIENCE": "${{ vars.CI_COORDINATOR_PLAN_URL }}",
        "CI_INSTALLATION_ID": (
            "${{ vars.CI_COORDINATOR_INSTALLATION_ID || github.event.installation.id }}"
        ),
        "CI_REPOSITORY_ID": "${{ github.repository_id }}",
        "CI_OWNER": "${{ github.repository_owner }}",
        "CI_REPO": "${{ github.event.repository.name }}",
        "CI_EVENT": "${{ github.event_name }}",
        "CI_RUN_ATTEMPT": "${{ github.run_attempt }}",
        "CI_RUN_ID": "${{ github.run_id }}",
        "CI_BASE_SHA": (
            "${{ github.event.pull_request.base.sha || "
            "github.event.merge_group.base_sha || github.event.before }}"
        ),
        "CI_HEAD_SHA": (
            "${{ github.event.pull_request.head.sha || "
            "github.event.merge_group.head_sha || github.sha }}"
        ),
        "CI_EXECUTION_SHA": "${{ github.sha }}",
        "CI_REF": "${{ github.ref }}",
        "CI_PULL_REQUEST_NUMBER": "${{ github.event.pull_request.number }}",
        "CI_MERGE_GROUP_HEAD_REF": (
            "${{ github.event.merge_group.head_ref || github.event.merge_group.ref }}"
        ),
        "CI_WORKFLOW_PATH": workflow_path,
        "CI_WORKFLOW_REF": "${{ job.workflow_ref }}",
        "CI_WORKFLOW_SHA": "${{ job.workflow_sha }}",
        "CI_STATIC_JOB_IDS_JSON": json.dumps(
            execution_job_ids,
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "CI_TARGET_REGISTRY_PATH": ".ci-coordinator/execution-registry.v1.json",
    }


def _static_job_results(job_ids: tuple[str, ...]) -> str:
    pairs = (
        f'{json.dumps(job_id, ensure_ascii=False)}:"${{{{ {_needs_job(job_id)}.result }}}}"'
        for job_id in job_ids
    )
    return "{" + ",".join(pairs) + "}"


def _needs_output(job_id: str, output: str) -> str:
    return f"${{{{ {_needs_job(job_id)}.outputs.{output} }}}}"


def _needs_job(job_id: str) -> str:
    if _DOT_PROPERTY.fullmatch(job_id) is not None:
        return f"needs.{job_id}"
    return f"needs['{job_id}']"


def _normalize(value: object) -> object:
    if value is None or type(value) in {bool, int, str}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("workflow control projection contains a non-finite number")
        return value
    if type(value) is list:
        return [_normalize(item) for item in value]
    if type(value) is dict:
        return _normalize_mapping(value, omitted_keys=frozenset(), step=False)
    raise TypeError("workflow control projection contains an unsupported value")


def _normalize_mapping(
    value: object,
    *,
    omitted_keys: frozenset[str],
    step: bool,
) -> dict[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise TypeError("workflow control projection requires string-keyed mappings")
    normalized: dict[str, object] = {}
    for key, item in value.items():
        if key in omitted_keys:
            continue
        if key == "steps":
            if type(item) is not list:
                raise TypeError("workflow control steps must be a sequence")
            normalized[key] = [
                _normalize_mapping(
                    child,
                    omitted_keys=_OMITTED_STEP_KEYS,
                    step=True,
                )
                for child in item
            ]
            continue
        if step and key == "uses" and type(item) is str:
            match = _GIT_OBJECT_ACTION.fullmatch(item)
            normalized[key] = (
                f"{match.group('action')}@<immutable-git-object>" if match is not None else item
            )
            continue
        normalized[key] = _normalize(item)
    return normalized

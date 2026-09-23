from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ruamel.yaml import YAML

from ci_coordinator.repo_context import (
    MAX_WORKFLOW_FILE_BYTES,
    parse_workflow_capability,
)
from scripts.proofkit_common import JsonObject, as_array, as_object

NATIVE_PATH = ".github/workflows/python-persistence.yml"
WORKFLOW_PATH = ".github/workflows/coordinated-checks.yml"
CONTROL_TEMPLATE_PATH = "fixtures/native-target-repository/.github/workflows/full-check.yml"
GATE_ID = "pull-request-gate"
REQUESTER_ID = "trusted-plan-request-entrypoint"
REQUESTER_ASSERTION_ID = "trusted-requester-contract"
MANUAL_JOBS = {
    "api-contract-exploration": ("API_CONTRACT", "api_contract_deep"),
    "serial-qualification": ("SERIAL", "serial_qualification"),
}
NATIVE_JOBS = frozenset(
    {
        "connected-administrator",
        "connected-development-stack",
        "container-runtime-smoke",
        "developer-bootstrap",
        "developer-container",
        "developer-host-lifecycle",
        "native-test-plan",
        "native-test-shards",
        "operator-workbench",
        "persistence-mutation",
        "postgres-witness",
        "repository-quality",
        "secret-scan",
        REQUESTER_ID,
    }
)
UTILITY_JOBS = frozenset(
    {
        "utility-config",
        "utility-docker",
        "utility-go-static",
        "utility-go-vulnerabilities",
        "utility-spelling",
    }
)
_RESULT = re.compile(r"\$\{\{ needs\.([a-z][a-z0-9-]*)\.result \}\}")
_ASSERTION = re.compile(r'test "\$\{?([A-Z_]+)\}?" = (success|skipped|plan_url_invalid)')


@dataclass(frozen=True)
class NativeSource:
    workflow: JsonObject
    jobs: dict[str, JsonObject]
    requester_reason_expression: str
    requester_reason: str
    gate_result_jobs: tuple[str, ...]
    proof_inputs: JsonObject


def read_regular(root: Path, relative: str, maximum: int = MAX_WORKFLOW_FILE_BYTES) -> bytes:
    if Path(relative).is_absolute() or "\\" in relative:
        raise ValueError("source path must be repository relative")
    path = root
    for part in Path(relative).parts:
        if part in {"", ".", ".."}:
            raise ValueError("source path must be repository relative")
        path /= part
        if path.is_symlink():
            raise ValueError(f"source has a symlink component: {relative}")
    if not path.is_file() or path.stat().st_size > maximum:
        raise ValueError(f"source is unavailable or oversized: {relative}")
    content = path.read_bytes()
    if len(content) > maximum:
        raise ValueError(f"source exceeds its bound: {relative}")
    return content


def workflow_value(content: bytes, path: str) -> JsonObject:
    if parse_workflow_capability(content, path=path, revision_sha="0" * 40) is None:
        raise ValueError(f"workflow syntax is not admitted: {path}")
    yaml = YAML(typ="safe", pure=True)
    yaml.version = (1, 2)
    yaml.allow_duplicate_keys = False
    return as_object(yaml.load(content.decode()), path)


def needs(job: JsonObject) -> tuple[str, ...]:
    raw = job.get("needs", [])
    values = [raw] if isinstance(raw, str) else as_array(raw, "job needs")
    if any(not isinstance(item, str) or not item for item in values):
        raise ValueError("job dependencies must be nonempty strings")
    if len(values) != len(set(values)):
        raise ValueError("job dependencies must be unique")
    return tuple(sorted(values))


def admit_native_source(content: bytes) -> NativeSource:
    workflow = workflow_value(content, NATIVE_PATH)
    proof_inputs = _proof_dispatch_inputs(workflow)
    jobs = {
        key: as_object(value, key) for key, value in as_object(workflow["jobs"], "jobs").items()
    }
    expected = NATIVE_JOBS | set(MANUAL_JOBS) | {GATE_ID}
    if not expected <= jobs.keys() or jobs.keys() - expected - UTILITY_JOBS:
        raise ValueError("native job inventory is missing a family or contains an unowned job")
    if "defaults" in workflow or workflow.get("permissions") != {"contents": "read"}:
        raise ValueError("native workflow defaults or permissions changed")
    environment = as_object(workflow.get("env"), "native workflow environment")
    if set(environment) != {
        "PROOFKIT_BASE_REF",
        "PROOFKIT_HEAD_REF",
        "RYUK_CONTAINER_IMAGE",
    }:
        raise ValueError("native workflow environment owner changed")
    selected = {key: value for key, value in jobs.items() if key not in {*MANUAL_JOBS, GATE_ID}}
    for job_id, job in selected.items():
        if "if" in job or job.get("continue-on-error", False) is not False:
            raise ValueError(f"native execution job is conditional or failure-tolerant: {job_id}")
        if set(needs(job)) - selected.keys():
            raise ValueError(f"native dependency leaves the PR execution scope: {job_id}")
    for job_id, (_prefix, input_id) in MANUAL_JOBS.items():
        expected_condition = f"github.event_name == 'workflow_dispatch' && inputs.{input_id}"
        actual = str(jobs[job_id].get("if", "")).removeprefix("${{ ").removesuffix(" }}")
        if actual != expected_condition:
            raise ValueError(f"manual job scope changed: {job_id}")
    results, reason_expression, reason = _gate_operands(jobs[GATE_ID])
    reachable: set[str] = set()
    pending = list(results)
    while pending:
        job_id = pending.pop()
        if job_id in reachable:
            continue
        if job_id not in selected:
            raise ValueError("gate result is outside the native PR scope")
        reachable.add(job_id)
        pending.extend(needs(selected[job_id]))
    if reachable != selected.keys():
        raise ValueError("native gate does not cover every execution job")
    if needs(selected["native-test-shards"]) != ("native-test-plan",) or needs(
        selected["postgres-witness"]
    ) != ("native-test-plan", "native-test-shards"):
        raise ValueError("native coverage artifact dependency closure changed")
    return NativeSource(workflow, selected, reason_expression, reason, results, proof_inputs)


def _proof_dispatch_inputs(workflow: JsonObject) -> JsonObject:
    triggers = as_object(workflow.get("on"), "native workflow triggers")
    dispatch = as_object(triggers.get("workflow_dispatch"), "native manual dispatch")
    inputs = as_object(dispatch.get("inputs"), "native manual inputs")
    if not {"proof_scope", "proofkit_base_sha"} <= inputs.keys():
        raise ValueError("native proof inputs must declare both scope and base SHA")
    scope = as_object(inputs["proof_scope"], "native proof scope input")
    base = as_object(inputs["proofkit_base_sha"], "native proof base SHA input")
    if (
        set(scope) != {"description", "required", "default", "type", "options"}
        or scope.get("type") != "choice"
        or scope.get("required") is not True
        or scope.get("default") != "full"
        or scope.get("options") != ["full", "range"]
        or set(base) != {"description", "required", "type"}
        or base.get("type") != "string"
        or base.get("required") is not False
        or any(
            not isinstance(item.get("description"), str) or not item["description"].strip()
            for item in (scope, base)
        )
    ):
        raise ValueError("native proof input types or supported scope selection changed")
    return {"proof_scope": scope, "proofkit_base_sha": base}


def _gate_operands(gate: JsonObject) -> tuple[tuple[str, ...], str, str]:
    if gate.get("if") != "${{ always() }}" or gate.get("permissions") != {}:
        raise ValueError("native aggregate execution authority changed")
    steps = as_array(gate.get("steps"), "native gate steps")
    if len(steps) != 1 or set(as_object(steps[0], "gate step")) != {
        "name",
        "env",
        "run",
    }:
        raise ValueError("native gate requires an explicit projection of every step")
    step = as_object(steps[0], "gate step")
    environment = as_object(step["env"], "gate environment")
    lines = str(step["run"]).strip().splitlines()
    if not lines or lines.pop(0) != "set -euo pipefail":
        raise ValueError("native gate shell contract changed")
    observed_variables: set[str] = set()
    result_jobs: list[str] = []
    reason_expression = ""
    manual_seen: set[str] = set()
    while lines:
        line = lines.pop(0).strip()
        if line.startswith("if "):
            matched = False
            for job_id, (prefix, input_id) in MANUAL_JOBS.items():
                variable = prefix + "_REQUESTED"
                if line != f'if [ "${variable}" = true ]; then':
                    continue
                block = [
                    f'test "${prefix}_RESULT" = success',
                    "else",
                    f'test "${prefix}_RESULT" = skipped',
                    "fi",
                ]
                if [item.strip() for item in lines[:4]] != block or job_id in manual_seen:
                    raise ValueError("manual native gate predicate changed")
                del lines[:4]
                if (
                    environment.get(variable)
                    != (
                        "${{ github.event_name == 'workflow_dispatch' && inputs." + input_id + " }}"
                    )
                    or environment.get(prefix + "_RESULT") != f"${{{{ needs.{job_id}.result }}}}"
                ):
                    raise ValueError("manual native gate operands changed")
                observed_variables.update((variable, prefix + "_RESULT"))
                manual_seen.add(job_id)
                matched = True
                break
            if not matched:
                raise ValueError("native gate contains an unowned conditional")
            continue
        assertion = _ASSERTION.fullmatch(line)
        if assertion is None:
            raise ValueError("native gate contains an unprojected instruction")
        variable, expected = assertion.groups()
        if variable in observed_variables:
            raise ValueError("native gate repeats an operand")
        observed_variables.add(variable)
        expression = environment.get(variable)
        result = _RESULT.fullmatch(str(expression))
        if result is not None and expected == "success":
            result_jobs.append(result.group(1))
        elif variable == "TRUSTED_PLAN_REQUEST_REASON" and expected == "plan_url_invalid":
            reason_expression = f"${{{{ needs.{REQUESTER_ID}.outputs.reason }}}}"
            if expression != reason_expression:
                raise ValueError("requester reason authority changed")
        else:
            raise ValueError("native gate contains an unprojected assertion")
    if (
        observed_variables != environment.keys()
        or manual_seen != MANUAL_JOBS.keys()
        or not reason_expression
        or len(result_jobs) != len(set(result_jobs))
        or set(needs(gate)) != {*result_jobs, *MANUAL_JOBS}
        or REQUESTER_ID not in result_jobs
    ):
        raise ValueError("native aggregate operand projection is incomplete")
    return tuple(sorted(result_jobs)), reason_expression, "plan_url_invalid"

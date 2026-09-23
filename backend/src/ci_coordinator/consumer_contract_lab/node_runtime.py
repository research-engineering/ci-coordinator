"""Bounded execution of the exact generated consumer control programs."""

from __future__ import annotations

import base64
import gzip
import json
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final, cast

from ci_coordinator.consumer_contract_lab.composition import IssuedScenario
from ci_coordinator.consumer_contract_lab.execution_image import target_execution_image
from ci_coordinator.consumer_contract_lab.model import ConsumerLabScenario
from ci_coordinator.consumer_contract_lab.node_executable import (
    NodeExecutableError,
    admit_node_executable,
)
from ci_coordinator.consumer_contract_lab.process import run_bounded
from ci_coordinator.consumer_contract_lab.source_epoch import PreparedConsumerContract
from ci_coordinator.kernel import canonical_json
from ci_coordinator.target_artifacts import render_plan_trust_root

_NODE_TIMEOUT_SECONDS: Final = 10
_MAX_PROCESS_OUTPUT_BYTES: Final = 1_048_576
_FIXED_NOW_MS: Final = "1785412800000"
_RUNTIME_GUARD: Final = Path(__file__).with_name("resources") / "node-runtime-guard.cjs"
_PLAN_TRANSPORT_CHUNK_COUNT: Final = 7
_PLAN_TRANSPORT_CHUNK_CHARACTERS: Final = 65_536


class ConsumerControlError(RuntimeError):
    """The exact consumer controls rejected or changed the composed contract."""


@dataclass(frozen=True, slots=True)
class ConsumerControlResult:
    scenario_id: str
    mode: str
    selected_jobs: tuple[str, ...]

    def to_mapping(self) -> dict[str, object]:
        return {
            "scenarioId": self.scenario_id,
            "mode": self.mode,
            "selectedJobs": list(self.selected_jobs),
            "jobResultKind": "synthetic-result",
        }


def execute_consumer_controls(
    contract: PreparedConsumerContract,
    scenario: ConsumerLabScenario,
    issued: IssuedScenario,
) -> ConsumerControlResult:
    try:
        node = admit_node_executable(contract.coordinator_root, contract.target_root)
        with target_execution_image(contract) as target_root:
            return _execute_consumer_controls(contract, scenario, issued, target_root, node)
    except ConsumerControlError:
        raise
    except (OSError, UnicodeError, NodeExecutableError) as error:
        raise ConsumerControlError("consumer control evidence is unavailable") from error


def _execute_consumer_controls(
    contract: PreparedConsumerContract,
    scenario: ConsumerLabScenario,
    issued: IssuedScenario,
    target_root: Path,
    node: str,
) -> ConsumerControlResult:
    workflow = contract.target_registry.workflow(contract.profile.workflow_path)
    if workflow is None or workflow.execution_kind != "native-job-set":
        raise ConsumerControlError("consumer lab requires a native-job-set workflow")
    controls = target_root / contract.profile.target_artifacts_directory
    harness = Path(__file__).with_name("resources") / "runtime-harness.cjs"
    with TemporaryDirectory(prefix="ci-consumer-lab-") as temporary:
        root = Path(temporary).resolve()
        plan_path = root / "plan.json"
        output_path = root / "github-output"
        trust_root = root / "plan-trust-root.v1.json"
        trust_root.write_bytes(
            render_plan_trust_root(
                key_id=issued.key_id,
                public_key_pem=issued.public_key_pem,
            )
        )
        environment = _consumer_environment(
            contract,
            issued,
            plan_path=plan_path,
            trust_root=trust_root,
        )
        control_bundle = controls / "ci-coordinator.cjs"
        _run(node, harness, control_bundle, "consume-plan", target_root, root, environment)
        if plan_path.read_bytes() != issued.envelope_bytes:
            raise ConsumerControlError("plan consumer changed the signed plan bytes")

        output_path.write_bytes(b"")
        validation_environment = {
            **environment,
            "CI_STATIC_JOB_IDS_JSON": _json(list(workflow.execution_job_ids)),
            "CI_TARGET_REGISTRY_PATH": str(controls / "execution-registry.v1.json"),
            "GITHUB_OUTPUT": str(output_path),
        }
        _run(
            node,
            harness,
            control_bundle,
            "validate-plan",
            target_root,
            root,
            validation_environment,
        )
        outputs = _outputs(output_path)
        mode = "fallback" if outputs["fallback"] == "true" else "selected"
        selected_jobs = _string_tuple(outputs["selected_jobs"])
        if outputs["plan_valid"] != "true":
            raise ConsumerControlError("generated plan validator rejected the signed plan")
        expected = scenario.expected_outcome
        if (mode, selected_jobs) != (expected.mode, expected.selected_jobs):
            raise ConsumerControlError("consumer outcome differs from target-owned expectation")

        static_results = {
            job_id: ("success" if mode == "fallback" or job_id in selected_jobs else "skipped")
            for job_id in workflow.execution_job_ids
        }
        gate_environment = {
            **validation_environment,
            "CI_PLAN_RESULT": "success",
            "CI_PLAN_VALID": outputs["plan_valid"],
            "CI_FALLBACK": outputs["fallback"],
            "CI_SELECTED_JOBS_JSON": outputs["selected_jobs"],
            "CI_STATIC_JOB_RESULTS_JSON": _json(static_results),
            "CI_FULL_CI_RESULT": "",
        }
        _run(
            node,
            harness,
            control_bundle,
            "validate-gate",
            target_root,
            root,
            gate_environment,
        )
        return ConsumerControlResult(scenario.scenario_id, mode, selected_jobs)


def _consumer_environment(
    contract: PreparedConsumerContract,
    issued: IssuedScenario,
    *,
    plan_path: Path,
    trust_root: Path,
) -> dict[str, str]:
    request = issued.request
    identity = issued.identity
    return {
        "CI_BASE_SHA": request.base_sha,
        "CI_CONSUMER_LAB_NOW_MS": _FIXED_NOW_MS,
        "CI_COORDINATOR_PLAN_OIDC_AUDIENCE": identity.audience,
        "CI_COORDINATOR_PLAN_TRUST_ROOT_PATH": str(trust_root),
        "CI_COORDINATOR_PLAN_URL": "https://coordinator.consumer-lab.invalid/api/v1/dynamic-ci/plan",
        "CI_EVENT": request.event_name,
        "CI_EXECUTION_SHA": request.execution_sha,
        "CI_HEAD_SHA": request.head_sha,
        "CI_INSTALLATION_ID": str(request.installation_id),
        "CI_MERGE_GROUP_HEAD_REF": request.merge_group_head_ref or "",
        "CI_OWNER": request.owner,
        "CI_PULL_REQUEST_NUMBER": (
            "" if request.pull_request_number is None else str(request.pull_request_number)
        ),
        "CI_REF": request.ref,
        "CI_REPO": request.repository,
        "CI_REPOSITORY_ID": str(request.repository_id),
        "CI_RUN_ATTEMPT": str(request.run_attempt),
        "CI_RUN_ID": str(request.workflow_run_id),
        "CI_WORKFLOW_PATH": contract.profile.workflow_path,
        "CI_WORKFLOW_REF": identity.workflow_ref or "",
        "CI_WORKFLOW_SHA": identity.workflow_sha or "",
        "LANG": "C",
        "LC_ALL": "C",
        "PLAN_PATH": str(plan_path),
        **_plan_transport_environment(issued.envelope_bytes),
    }


def _plan_transport_environment(content: bytes) -> dict[str, str]:
    encoded = base64.urlsafe_b64encode(gzip.compress(content, mtime=0)).rstrip(b"=").decode()
    chunks = tuple(
        encoded[index : index + _PLAN_TRANSPORT_CHUNK_CHARACTERS]
        for index in range(0, len(encoded), _PLAN_TRANSPORT_CHUNK_CHARACTERS)
    )
    if len(chunks) > _PLAN_TRANSPORT_CHUNK_COUNT:
        raise ConsumerControlError("signed plan exceeds the admitted transport")
    return {
        "CI_COORDINATOR_PLAN_REASON": "signed_plan_available",
        **{
            f"CI_COORDINATOR_PLAN_CHUNK_{index}": chunks[index] if index < len(chunks) else ""
            for index in range(_PLAN_TRANSPORT_CHUNK_COUNT)
        },
    }


def _run(
    node: str,
    harness: Path,
    program: Path,
    command: str,
    cwd: Path,
    writable_root: Path,
    environment: dict[str, str],
) -> None:
    try:
        completed = run_bounded(
            node,
            (
                "--permission",
                f"--allow-fs-read={_RUNTIME_GUARD}",
                f"--allow-fs-read={harness}",
                f"--allow-fs-read={cwd}",
                f"--allow-fs-read={writable_root}",
                f"--allow-fs-write={writable_root}",
                "--require",
                str(_RUNTIME_GUARD),
                "--require",
                str(harness),
                str(program),
                command,
            ),
            cwd=cwd,
            env=environment,
            max_output_bytes=_MAX_PROCESS_OUTPUT_BYTES,
            timeout_seconds=_NODE_TIMEOUT_SECONDS,
        )
    except (RuntimeError, TypeError, ValueError) as error:
        raise ConsumerControlError("bounded Node.js execution failed") from error
    if completed.status != 0 or completed.error is not None:
        raise ConsumerControlError("generated consumer control rejected the scenario")


def _outputs(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if not separator or key in values:
            raise ConsumerControlError("generated validator output is malformed")
        values[key] = value
    expected = {
        "fallback",
        "matrices",
        "max_parallel_by_job",
        "plan_valid",
        "reason",
        "selected_jobs",
    }
    if set(values) != expected:
        raise ConsumerControlError("generated validator output is incomplete")
    return values


def _string_tuple(value: str) -> tuple[str, ...]:
    parsed = _decode_json(value)
    if type(parsed) is not list or any(type(item) is not str for item in parsed):
        raise ConsumerControlError("generated selected jobs are invalid")
    return tuple(cast(list[str], parsed))


def _decode_json(value: str) -> object:
    try:
        return json.loads(value)
    except json.JSONDecodeError as error:
        raise ConsumerControlError("generated consumer control emitted invalid JSON") from error


def _json(value: object) -> str:
    return canonical_json(value).decode("utf-8")

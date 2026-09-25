from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ci_coordinator.execution_orchestration import parse_target_execution_registry
from ci_coordinator.kernel import canonical_json
from ci_coordinator.repo_context import (
    ProviderWorkflowInventory,
    parse_workflow_capability,
)
from ci_coordinator.target_artifacts.model import (
    DEPENDENCY_GRAPH_FILENAME,
    EXECUTION_REGISTRY_FILENAME,
    TARGET_CONTROL_FILENAME,
    TEST_MANIFEST_FILENAME,
)
from ci_coordinator.target_artifacts.renderer import render_target_artifacts
from ci_coordinator.target_artifacts.source_codec import parse_target_artifacts_source
from ci_coordinator.target_artifacts.workflow import write_exact_file
from ci_coordinator.validation_contract import ValidationCatalog
from scripts.ci_matrix_inventory import MAX_TOTAL_BYTES
from scripts.proofkit_common import JsonObject, as_array, as_object
from scripts.repository_paths import read_repository_regular_file
from scripts.self_ci_catalog import FamilySettings, policy_document, validation_catalog
from scripts.self_ci_controls import (
    COORDINATED_GATE_ID,
    COORDINATED_GATE_NAME,
    LOCAL_REQUESTER,
    PLAN_ID,
    REQUEST_ID,
    render_workflow,
)
from scripts.self_ci_proof_refresh import (
    PROOF_PATHS,
    RISK_PATH,
    ProofSources,
    entrypoint_projection,
    risk_projection,
)
from scripts.self_ci_responsibility import ResponsibilityProjection, responsibility_projection
from scripts.self_ci_source import (
    CONTROL_TEMPLATE_PATH,
    MANUAL_JOBS,
    NATIVE_PATH,
    WORKFLOW_PATH,
    admit_native_source,
    needs,
    read_regular,
    workflow_value,
)

ROOT = Path(__file__).resolve().parents[1]
MAX_OUTPUT_BYTES = 4_194_304
INVENTORY_PATH = ".ci-coordinator/self-ci-inventory.v1.json"
GENERATOR_FILES = (
    "scripts/self_ci_catalog.py",
    "scripts/self_ci_controls.py",
    "scripts/self_ci_generate.py",
    "scripts/self_ci_proof_refresh.py",
    "scripts/self_ci_responsibility.py",
    "scripts/self_ci_source.py",
)
REFRESH_OUTPUT_PATHS = PROOF_PATHS | {
    WORKFLOW_PATH,
    ".ci-coordinator/target-artifacts-source.v1.json",
    ".ci-coordinator/validation-catalog.v1.json",
    INVENTORY_PATH,
    *(
        f".ci-coordinator/{name}"
        for name in (
            DEPENDENCY_GRAPH_FILENAME,
            EXECUTION_REGISTRY_FILENAME,
            TARGET_CONTROL_FILENAME,
            TEST_MANIFEST_FILENAME,
        )
    ),
}


@dataclass(frozen=True)
class SelfCiArtifacts:
    outputs: dict[str, bytes]
    inputs: dict[str, bytes]
    catalog: ValidationCatalog
    dependency_graph: JsonObject
    responsibility: ResponsibilityProjection

    def assert_inputs_current(self, root: Path) -> None:
        if any(read_regular(root, path) != content for path, content in self.inputs.items()):
            raise ValueError("self CI owner inputs changed during generation")
        self.responsibility.assert_current(root)


def render_self_ci(
    root: Path,
    *,
    family_settings: Mapping[str, FamilySettings] | None = None,
    dependency_graph: JsonObject | None = None,
) -> SelfCiArtifacts:
    inputs = {
        path: read_regular(root, path)
        for path in (
            NATIVE_PATH,
            CONTROL_TEMPLATE_PATH,
            "package.json",
            *GENERATOR_FILES,
        )
    }
    native = admit_native_source(inputs[NATIVE_PATH])
    responsibility = responsibility_projection(root)
    if family_settings is not None and family_settings != responsibility.settings:
        raise ValueError("self CI family policy differs from its canonical input owners")
    if dependency_graph is not None and dependency_graph != responsibility.graph:
        raise ValueError("self CI graph differs from its exact finite path universe")
    inputs.update(responsibility.owner_inputs)
    package = as_object(json.loads(inputs["package.json"]), "root package")
    node_version = as_object(package["engines"], "root engines")["node"]
    if not isinstance(node_version, str):
        raise ValueError("Node runtime owner is unavailable")
    workflow_bytes = render_workflow(native, inputs[CONTROL_TEMPLATE_PATH], node_version)
    workflow = workflow_value(workflow_bytes, WORKFLOW_PATH)
    jobs = as_object(workflow["jobs"], "generated jobs")
    execution_jobs = {
        job_id: as_object(job, job_id)
        for job_id, job in jobs.items()
        if job_id not in {"ci-invocation", REQUEST_ID, PLAN_ID, COORDINATED_GATE_ID}
    }
    catalog = validation_catalog(execution_jobs, responsibility.settings)
    workflow_files = _workflow_closure(root, workflow_bytes)
    inputs.update(
        {path: content for path, content in workflow_files.items() if path != WORKFLOW_PATH}
    )
    source = _target_source(execution_jobs, catalog, workflow_files, responsibility.graph)
    source_bytes = canonical_json(source) + b"\n"
    admitted_source = parse_target_artifacts_source(source_bytes)
    rendered = render_target_artifacts(admitted_source)
    registry = parse_target_execution_registry(rendered.execution_registry)
    if registry is None or not registry.admits(catalog):
        raise ValueError("generated self registry and validation catalog disagree")
    _admit_workflow_chain(workflow_files, registry.workflows[0].job_topology)
    report = {
        "schemaVersion": 1,
        "profileId": "ci-coordinator.self-ci-qualification",
        "sourceWorkflow": NATIVE_PATH,
        "generatedWorkflow": WORKFLOW_PATH,
        "sourceInputs": [
            {"path": path, "sha256": _digest(content)} for path, content in sorted(inputs.items())
        ],
        "nativeJobIds": sorted(native.jobs),
        "responsibilityInventory": responsibility.inventory,
        "executionJobIds": sorted(execution_jobs),
        "nativeAggregateResultJobs": list(native.gate_result_jobs),
        "nativeAggregateOutputPredicates": [
            {
                "expression": native.requester_reason_expression,
                "equals": native.requester_reason,
            }
        ],
        "nativeConditionalSteps": [
            {"jobId": job_id, "stepIndex": index, "condition": step["if"]}
            for job_id, job in sorted(native.jobs.items())
            for index, raw_step in enumerate(as_array(job.get("steps", []), "native steps"))
            if "if" in (step := as_object(raw_step, "native step"))
        ],
        "separateManualJobs": sorted(MANUAL_JOBS),
        "validationCatalogHash": catalog.catalog_hash,
        "targetRegistryHash": registry.registry_hash,
        "nonClaims": [
            "Static generation does not prove provider execution or default-branch activation.",
            "Native families remain required; only two direct-input utilities may be omitted.",
            "No signing key, installation identity or production admission is generated.",
        ],
    }
    outputs = {
        WORKFLOW_PATH: workflow_bytes,
        ".ci-coordinator/target-artifacts-source.v1.json": source_bytes,
        ".ci-coordinator/validation-catalog.v1.json": canonical_json(catalog.to_identity_mapping())
        + b"\n",
        INVENTORY_PATH: canonical_json(report) + b"\n",
        **{f".ci-coordinator/{name}": content for name, content in rendered.by_filename()},
    }
    result = SelfCiArtifacts(
        outputs, inputs, catalog, as_object(source["dependencyGraph"], "graph"), responsibility
    )
    result.assert_inputs_current(root)
    return result


def _target_source(
    jobs: dict[str, JsonObject],
    catalog: ValidationCatalog,
    workflows: dict[str, bytes],
    dependency_graph: JsonObject | None,
) -> JsonObject:
    required_witnesses = {
        witness
        for obligation in catalog.obligations
        if not obligation.omit_allowed
        for witness in obligation.required_witness_ids
    }
    return {
        "schemaVersion": "dynamic-ci-target-artifacts-source/v1",
        "generator": {"id": "self-ci", "version": "1"},
        "adapterWorkflowFiles": [
            {"path": path, "sha256": _digest(content)}
            for path, content in sorted(workflows.items())
        ],
        "executionWorkflows": [
            {
                "workflowPath": WORKFLOW_PATH,
                "executionKind": "native-job-set",
                "executionJobs": [
                    {"jobId": job_id, "needs": list(needs(job))}
                    for job_id, job in sorted(jobs.items())
                ],
                "planRequestJobId": REQUEST_ID,
                "planRequestWorkflowRef": LOCAL_REQUESTER,
                "planJobId": PLAN_ID,
                "fallbackJobId": None,
                "gateJobId": COORDINATED_GATE_ID,
                "gateSignalName": COORDINATED_GATE_NAME,
                "requiredJobIds": sorted(required_witnesses),
            }
        ],
        "executionProfiles": [
            {
                **{
                    key: value
                    for key, value in profile.to_identity_mapping().items()
                    if key != "shardingPolicy"
                },
                "workflowPath": WORKFLOW_PATH,
                "jobId": profile.profile_id,
                "executionKind": "native-job-set",
            }
            for profile in catalog.execution_profiles
        ],
        "tests": [],
        "dependencyGraph": (
            {
                "source": "configured",
                "invalidatesWhenChanged": ["**"],
                "globalRiskPaths": ["**"],
                "nodes": [],
            }
            if dependency_graph is None
            else dependency_graph
        ),
    }


def _workflow_closure(root: Path, workflow_bytes: bytes) -> dict[str, bytes]:
    files = {WORKFLOW_PATH: workflow_bytes}
    pending = [WORKFLOW_PATH]
    while pending:
        path = pending.pop()
        capability = parse_workflow_capability(files[path], path=path, revision_sha="0" * 40)
        if capability is None:
            raise ValueError(f"generated workflow closure is not admitted: {path}")
        for local in capability.local_reusable_workflow_paths:
            if local not in files:
                if len(files) >= 32:
                    raise ValueError("self workflow closure exceeds its artifact bound")
                files[local] = read_regular(root, local)
                pending.append(local)
    return files


def _admit_workflow_chain(
    files: dict[str, bytes], topology: tuple[tuple[str, tuple[str, ...]], ...]
) -> None:
    capabilities = tuple(
        parse_workflow_capability(content, path=path, revision_sha="0" * 40)
        for path, content in sorted(files.items())
    )
    if any(item is None for item in capabilities):
        raise ValueError("self workflow closure contains an invalid workflow")
    inventory = ProviderWorkflowInventory(
        revision_sha="0" * 40,
        default_branch_workflows=(),
        revision_capabilities=tuple(item for item in capabilities if item is not None),
    )
    execution_ids = tuple(
        job_id
        for job_id, _dependencies in topology
        if job_id not in {"ci-invocation", REQUEST_ID, PLAN_ID, COORDINATED_GATE_ID}
    )
    if not (
        inventory.admits_local_reusable_workflow_closure(
            root_paths=(WORKFLOW_PATH,), expected_paths=tuple(sorted(files))
        )
        and inventory.admits_exact_job_topology(
            workflow_path=WORKFLOW_PATH,
            expected=topology,
            require_default_branch_activation=False,
        )
        and inventory.admits_static_gate(
            workflow_path=WORKFLOW_PATH,
            job_id=COORDINATED_GATE_ID,
            job_name=COORDINATED_GATE_NAME,
            required_dependencies=tuple(sorted((PLAN_ID, *execution_ids))),
            require_default_branch_activation=False,
        )
        and inventory.admits_control_plane(
            workflow_path=WORKFLOW_PATH,
            execution_kind="native-job-set",
            invocation_job_id="ci-invocation",
            plan_request_job_id=REQUEST_ID,
            plan_request_workflow_ref=LOCAL_REQUESTER,
            plan_job_id=PLAN_ID,
            gate_job_id=COORDINATED_GATE_ID,
            fallback_job_id=None,
            execution_job_ids=execution_ids,
        )
    ):
        raise ValueError(
            "self workflow topology, local closure or control authority is not admitted"
        )


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def render_proof_refresh(root: Path) -> tuple[ProofSources, dict[str, bytes]]:
    sources = ProofSources.capture(root)
    entrypoint_projection(sources, {})
    risk = risk_projection(sources)
    artifacts = render_self_ci(root)
    outputs = {RISK_PATH: risk, **artifacts.outputs}
    inventory = as_object(json.loads(outputs[INVENTORY_PATH]), "self CI inventory")
    risk_inputs = [
        as_object(row, "self CI source input")
        for row in as_array(inventory["sourceInputs"], "self CI source inputs")
        if as_object(row, "self CI source input")["path"] == RISK_PATH
    ]
    if len(risk_inputs) != 1 or risk_inputs[0]["sha256"] != _digest(sources.read(RISK_PATH)):
        raise ValueError("self CI inventory lost its exact risk input binding")
    risk_inputs[0]["sha256"] = _digest(risk)
    outputs[INVENTORY_PATH] = canonical_json(inventory) + b"\n"
    outputs.update(entrypoint_projection(sources, outputs))
    if outputs.keys() != REFRESH_OUTPUT_PATHS:
        raise ValueError("proof refresh output population needs explicit owner admission")
    sources.assert_current()
    return sources, outputs


def write_proof_refresh(
    sources: ProofSources,
    outputs: dict[str, bytes],
    *,
    refresh_proof_hashes: bool = False,
) -> None:
    if outputs.keys() != REFRESH_OUTPUT_PATHS:
        raise ValueError("proof refresh output population needs explicit owner admission")
    for content in outputs.values():
        if type(content) is not bytes or not content or len(content) > MAX_OUTPUT_BYTES:
            raise ValueError("proof refresh output must be nonempty bounded bytes")
    changed = {
        path: content
        for path, content in outputs.items()
        if sources.read(path, MAX_OUTPUT_BYTES) != content
    }
    if not refresh_proof_hashes and PROOF_PATHS.intersection(changed):
        raise ValueError(
            "stale proof hashes require --write --refresh-proof-hashes and source review"
        )
    if outputs[RISK_PATH] != risk_projection(sources):
        raise ValueError("proof refresh risk output differs from its admitted projection")
    sources.assert_current()
    if any(
        outputs[path] != content
        for path, content in entrypoint_projection(sources, outputs).items()
    ):
        raise ValueError("proof refresh disposition differs from its complete projection")
    if sum(len(content) for content in (sources.files | changed).values()) > MAX_TOTAL_BYTES:
        raise ValueError("proof refresh postimage exceeds the matrix aggregate byte bound")
    for path, content in changed.items():
        if read_repository_regular_file(
            sources.root,
            Path(path),
            "proof refresh output preimage",
            maximum_bytes=MAX_OUTPUT_BYTES,
        ) != sources.read(path):
            raise ValueError("proof refresh output preimage changed")
        write_exact_file(sources.root / path, content)
        (sources.root / path).chmod(sources.modes[path])
    sources.assert_current(changed)
    entrypoint_projection(sources, outputs)


def main() -> int:
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--write", action="store_true")
    modes.add_argument("--check", action="store_true")
    modes.add_argument("--policy", action="store_true")
    parser.add_argument("--refresh-proof-hashes", action="store_true")
    parser.add_argument("--installation-id", type=int)
    parser.add_argument("--repository-id", type=int)
    parser.add_argument("--default-branch")
    arguments = parser.parse_args()
    if arguments.refresh_proof_hashes and not arguments.write:
        parser.error("--refresh-proof-hashes requires --write; CI checks never refresh")
    if arguments.policy != all(
        value is not None
        for value in (
            arguments.installation_id,
            arguments.repository_id,
            arguments.default_branch,
        )
    ) or (
        not arguments.policy
        and any(
            value is not None
            for value in (
                arguments.installation_id,
                arguments.repository_id,
                arguments.default_branch,
            )
        )
    ):
        parser.error(
            "--policy requires exactly installation, repository and default-branch identity"
        )
    try:
        if arguments.policy:
            artifacts = render_self_ci(ROOT)
            sys.stdout.buffer.write(
                policy_document(
                    artifacts.catalog,
                    installation_id=arguments.installation_id,
                    repository_id=arguments.repository_id,
                    default_branch=arguments.default_branch,
                    dependency_graph=artifacts.dependency_graph,
                )
            )
            return 0
        sources, outputs = render_proof_refresh(ROOT)
        if arguments.write:
            write_proof_refresh(
                sources, outputs, refresh_proof_hashes=arguments.refresh_proof_hashes
            )
        else:
            drift = [
                relative
                for relative, content in outputs.items()
                if sources.read(relative, MAX_OUTPUT_BYTES) != content
            ]
            if drift:
                raise ValueError("self CI artifacts drift: " + ", ".join(drift))
        print(
            json.dumps(
                {
                    "state": "passed",
                    "artifacts": sorted(outputs),
                    "semanticApproval": "not-established",
                }
            )
        )
    except (OSError, KeyError, TypeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

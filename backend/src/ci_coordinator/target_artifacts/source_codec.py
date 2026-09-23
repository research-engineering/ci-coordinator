"""Strict semantic admission for the target-artifact source."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final, cast

from ci_coordinator.execution_orchestration import (
    MAX_TARGET_ADAPTER_WORKFLOWS,
    MAX_TARGET_SERVICE_PROFILE_IDS,
    ExecutionKind,
    TargetAdapterFileBinding,
    TargetJobBinding,
    TargetProfileBinding,
    TargetWorkflowBinding,
)
from ci_coordinator.kernel import StrictJsonError, load_strict_json, utf16_sort_key
from ci_coordinator.repo_context import DependencyGraphNode
from ci_coordinator.repo_context.freshness import is_safe_relative_path, validate_path_pattern
from ci_coordinator.target_artifacts.model import (
    DependencyGraphDefinition,
    GeneratorIdentity,
    ManifestTestDefinition,
    TargetArtifactsSource,
)
from ci_coordinator.target_artifacts.schemas import (
    TargetArtifactSchemaError,
    validate_artifact_value,
)

SOURCE_SCHEMA_VERSION: Final = "dynamic-ci-target-artifacts-source/v1"
MAX_SOURCE_BYTES: Final = 4_194_304
MAX_PROFILES: Final = 64
MAX_WORKFLOWS: Final = MAX_TARGET_ADAPTER_WORKFLOWS
MAX_TESTS: Final = 4_096
MAX_GRAPH_NODES: Final = 100_000


class TargetArtifactsAdmissionError(ValueError):
    """The target-artifact source is not in the admitted finite language."""


def parse_target_artifacts_source(content: bytes) -> TargetArtifactsSource:
    try:
        value = load_strict_json(content, max_bytes=MAX_SOURCE_BYTES)
        validate_artifact_value("source", value)
        root = _exact_object(
            value,
            {
                "schemaVersion",
                "generator",
                "adapterWorkflowFiles",
                "executionWorkflows",
                "executionProfiles",
                "tests",
                "dependencyGraph",
            },
        )
        if root["schemaVersion"] != SOURCE_SCHEMA_VERSION:
            raise ValueError("target artifact source schema version is unsupported")
        generator = _generator(root["generator"])
        adapter_workflow_files = _adapter_workflow_files(root["adapterWorkflowFiles"])
        workflows = _workflows(root["executionWorkflows"])
        profiles = _profiles(root["executionProfiles"])
        tests = _tests(root["tests"])
        graph = _dependency_graph(root["dependencyGraph"])
        return TargetArtifactsSource(
            generator,
            adapter_workflow_files,
            workflows,
            profiles,
            tests,
            graph,
        )
    except (KeyError, StrictJsonError, TargetArtifactSchemaError, TypeError, ValueError) as error:
        raise TargetArtifactsAdmissionError("target artifact source is invalid") from error


def _generator(value: object) -> GeneratorIdentity:
    record = _exact_object(value, {"id", "version"})
    return GeneratorIdentity(_text(record["id"]), _text(record["version"]))


def _adapter_workflow_files(value: object) -> tuple[TargetAdapterFileBinding, ...]:
    records = _bounded_array(
        value,
        minimum=1,
        maximum=MAX_TARGET_ADAPTER_WORKFLOWS,
        name="adapter workflow files",
    )
    bindings = []
    for item in records:
        record = _exact_object(item, {"path", "sha256"})
        binding = TargetAdapterFileBinding(
            path=_text(record["path"]),
            sha256=_text(record["sha256"]),
        )
        if not binding.is_workflow:
            raise ValueError("target adapter source may declare only workflow files")
        bindings.append(binding)
    return _sorted_unique_objects(
        bindings,
        lambda item: item.path,
        "adapter workflow files",
    )


def _profiles(value: object) -> tuple[TargetProfileBinding, ...]:
    records = _bounded_array(value, minimum=1, maximum=MAX_PROFILES, name="profiles")
    profiles: list[TargetProfileBinding] = []
    for value in records:
        record = _exact_object(
            value,
            {
                "profileId",
                "workflowPath",
                "jobId",
                "executionKind",
                "runnerProfileId",
                "permissionProfileId",
                "credentialProfileId",
                "fixtureProfileId",
                "serviceProfileIds",
                "capacityClassId",
            },
        )
        services = _unique_sorted_texts(
            record["serviceProfileIds"],
            "service profiles",
            maximum=MAX_TARGET_SERVICE_PROFILE_IDS,
        )
        profiles.append(
            TargetProfileBinding(
                profile_id=_text(record["profileId"]),
                workflow_path=_text(record["workflowPath"]),
                job_id=_text(record["jobId"]),
                execution_kind=_execution_kind(record["executionKind"]),
                runner_profile_id=_text(record["runnerProfileId"]),
                permission_profile_id=_text(record["permissionProfileId"]),
                credential_profile_id=_text(record["credentialProfileId"]),
                fixture_profile_id=_text(record["fixtureProfileId"]),
                service_profile_ids=services,
                capacity_class_id=_text(record["capacityClassId"]),
            )
        )
    return _sorted_unique_objects(profiles, lambda item: item.profile_id, "profiles")


def _workflows(value: object) -> tuple[TargetWorkflowBinding, ...]:
    records = _bounded_array(value, minimum=1, maximum=MAX_WORKFLOWS, name="workflows")
    workflows: list[TargetWorkflowBinding] = []
    for value in records:
        record = _exact_object(
            value,
            {
                "workflowPath",
                "executionKind",
                "executionJobs",
                "fallbackJobId",
                "gateJobId",
                "gateSignalName",
                "planRequestJobId",
                "planRequestWorkflowRef",
                "planJobId",
                "requiredJobIds",
            },
        )
        execution_jobs = _bounded_array(
            record["executionJobs"],
            minimum=1,
            maximum=MAX_PROFILES,
            name="workflow execution jobs",
        )
        workflows.append(
            TargetWorkflowBinding(
                workflow_path=_text(record["workflowPath"]),
                execution_kind=_execution_kind(record["executionKind"]),
                execution_jobs=tuple(_target_job(item) for item in execution_jobs),
                plan_request_job_id=_text(record["planRequestJobId"]),
                plan_request_workflow_ref=_text(record["planRequestWorkflowRef"]),
                plan_job_id=_text(record["planJobId"]),
                fallback_job_id=(
                    None if record["fallbackJobId"] is None else _text(record["fallbackJobId"])
                ),
                gate_job_id=_text(record["gateJobId"]),
                gate_signal_name=_text(record["gateSignalName"]),
                required_job_ids=_unique_sorted_texts(
                    record["requiredJobIds"],
                    "required workflow jobs",
                ),
            )
        )
    return _sorted_unique_objects(
        workflows,
        lambda item: item.workflow_path,
        "workflows",
    )


def _target_job(value: object) -> TargetJobBinding:
    record = _exact_object(value, {"jobId", "needs"})
    return TargetJobBinding(
        job_id=_text(record["jobId"]),
        needs=_unique_sorted_texts(
            record["needs"],
            "workflow execution dependencies",
        ),
    )


def _tests(value: object) -> tuple[ManifestTestDefinition, ...]:
    records = _bounded_array(value, minimum=0, maximum=MAX_TESTS, name="tests")
    tests: list[ManifestTestDefinition] = []
    for value in records:
        record = _exact_object(value, {"testId", "witnessId", "expectedSeconds"})
        duration = record["expectedSeconds"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise ValueError("expected seconds must be numeric")
        tests.append(
            ManifestTestDefinition(
                test_id=_text(record["testId"]),
                witness_id=_text(record["witnessId"]),
                expected_seconds=float(duration),
            )
        )
    return _sorted_unique_objects(tests, lambda item: item.test_id, "tests")


def _dependency_graph(value: object) -> DependencyGraphDefinition:
    record = _exact_object(
        value,
        {"source", "invalidatesWhenChanged", "globalRiskPaths", "nodes"},
    )
    source = record["source"]
    if source not in {"configured", "generated"}:
        raise ValueError("dependency graph source is invalid")
    invalidators = _unique_sorted_texts(record["invalidatesWhenChanged"], "graph invalidators")
    global_risk_paths = _unique_sorted_texts(record["globalRiskPaths"], "global risk paths")
    if any(validate_path_pattern(pattern) is not None for pattern in invalidators):
        raise ValueError("dependency graph invalidator is not an admitted path pattern")
    if any(validate_path_pattern(pattern) is not None for pattern in global_risk_paths):
        raise ValueError("global risk path is not an admitted path pattern")

    raw_nodes = _bounded_array(record["nodes"], minimum=0, maximum=MAX_GRAPH_NODES, name="nodes")
    nodes: list[DependencyGraphNode] = []
    for value in raw_nodes:
        node = _exact_object(value, {"path", "dependents", "riskClasses"})
        path = _text(node["path"])
        dependents = _unique_sorted_texts(node["dependents"], "graph dependents")
        risk_classes = _unique_sorted_texts(node["riskClasses"], "risk classes")
        if not is_safe_relative_path(path) or any(
            not is_safe_relative_path(dependent) for dependent in dependents
        ):
            raise ValueError("dependency graph path is unsafe")
        if any(not _canonical_identifier(risk_class) for risk_class in risk_classes):
            raise ValueError("dependency graph risk class is not canonical")
        nodes.append(DependencyGraphNode(path, dependents, risk_classes))
    canonical_nodes = _sorted_unique_objects(nodes, lambda item: item.path, "graph nodes")
    node_paths = {node.path for node in canonical_nodes}
    if any(
        dependent not in node_paths for node in canonical_nodes for dependent in node.dependents
    ):
        raise ValueError("dependency graph contains a dangling edge")
    return DependencyGraphDefinition(
        source=source,
        invalidates_when_changed=invalidators,
        global_risk_paths=global_risk_paths,
        nodes=canonical_nodes,
    )


def _exact_object(value: object, keys: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("target artifact object shape is invalid")
    return cast(dict[str, object], value)


def _bounded_array(
    value: object,
    *,
    minimum: int,
    maximum: int,
    name: str,
) -> list[object]:
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise ValueError(f"{name} cardinality is invalid")
    return cast(list[object], value)


def _unique_sorted_texts(
    value: object,
    name: str,
    *,
    maximum: int = MAX_GRAPH_NODES,
) -> tuple[str, ...]:
    items = _bounded_array(value, minimum=0, maximum=maximum, name=name)
    texts = tuple(_text(item) for item in items)
    if len(texts) != len(set(texts)):
        raise ValueError(f"{name} contains duplicates")
    return tuple(sorted(texts, key=utf16_sort_key))


def _sorted_unique_objects[T](
    values: list[T],
    key: Callable[[T], str],
    name: str,
) -> tuple[T, ...]:
    identities = tuple(key(value) for value in values)
    if len(identities) != len(set(identities)):
        raise ValueError(f"{name} contains duplicate identities")
    return tuple(sorted(values, key=lambda value: utf16_sort_key(key(value))))


def _text(value: object) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > 4_096:
        raise ValueError("target artifact text is invalid")
    return value


def _canonical_identifier(value: str) -> bool:
    if not 1 <= len(value) <= 64 or not "a" <= value[0] <= "z":
        return False
    return all(
        character.isascii() and (character.islower() or character.isdigit() or character in "._-")
        for character in value
    )


def _execution_kind(value: object) -> ExecutionKind:
    if value == "witness-shards":
        return "witness-shards"
    if value == "native-job-set":
        return "native-job-set"
    raise ValueError("target execution kind is invalid")

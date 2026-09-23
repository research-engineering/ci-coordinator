"""Deterministic rendering closed over every runtime consumer."""

from __future__ import annotations

from importlib.resources import files
from typing import Final

from ci_coordinator.execution_orchestration import (
    LOCAL_PLAN_REQUEST_WORKFLOW_PATH,
    LOCAL_PLAN_REQUEST_WORKFLOW_REF,
    TargetAdapterFileBinding,
    TargetExecutionRegistry,
    digest_adapter_file,
    parse_target_execution_registry,
)
from ci_coordinator.kernel import canonical_json, utf16_sort_key
from ci_coordinator.repo_context import parse_dependency_graph_artifact
from ci_coordinator.runner_capacity import parse_test_manifest
from ci_coordinator.target_artifacts.model import (
    TARGET_CONTROL_FILENAME,
    RenderedTargetArtifacts,
    TargetArtifactsSource,
)
from ci_coordinator.target_artifacts.requester import render_plan_requester
from ci_coordinator.target_artifacts.schemas import validate_artifact_value


class TargetArtifactsRenderError(ValueError):
    """Rendered bytes are outside at least one consumer contract."""


_MAX_VALIDATOR_RESOURCE_BYTES: Final = 1_048_576
_VALIDATOR_RESOURCE_PACKAGE: Final = "ci_coordinator.target_artifacts.resources"


def render_target_artifacts(source: TargetArtifactsSource) -> RenderedTargetArtifacts:
    if type(source) is not TargetArtifactsSource:
        raise TypeError("rendering requires an exact target artifact source")
    if any(
        item.plan_request_workflow_ref == LOCAL_PLAN_REQUEST_WORKFLOW_REF
        for item in source.workflows
    ):
        expected = TargetAdapterFileBinding(
            path=LOCAL_PLAN_REQUEST_WORKFLOW_PATH,
            sha256=digest_adapter_file(render_plan_requester()),
        )
        if expected not in source.adapter_workflow_files:
            raise TargetArtifactsRenderError("local requester must match the packaged artifact")
    control_bundle = _control_resource()
    adapter_files = tuple(
        sorted(
            (
                *source.adapter_workflow_files,
                TargetAdapterFileBinding(
                    path=f".ci-coordinator/{TARGET_CONTROL_FILENAME}",
                    sha256=digest_adapter_file(control_bundle),
                ),
            ),
            key=lambda item: utf16_sort_key(item.path),
        )
    )
    registry = TargetExecutionRegistry(
        generator_id=source.generator.generator_id,
        generator_version=source.generator.version,
        adapter_files=adapter_files,
        workflows=source.workflows,
        profiles=source.profiles,
    ).to_identity_mapping()
    manifest = {
        "schemaVersion": "dynamic-ci-test-manifest/v1",
        "generator": source.generator.to_mapping(),
        "tests": [test.to_mapping() for test in source.tests],
    }
    graph = source.dependency_graph.to_artifact_mapping(source.generator)
    validate_artifact_value("execution_registry", registry)
    validate_artifact_value("test_manifest", manifest)
    validate_artifact_value("graph", graph)
    rendered = RenderedTargetArtifacts(
        execution_registry=_line(registry),
        test_manifest=_line(manifest),
        dependency_graph=_line(graph),
        control_bundle=control_bundle,
    )
    if (
        parse_target_execution_registry(rendered.execution_registry) is None
        or parse_test_manifest(rendered.test_manifest) is None
        or parse_dependency_graph_artifact(
            rendered.dependency_graph,
            retrieved_for_sha="0" * 40,
            trusted=True,
        )
        is None
    ):
        raise TargetArtifactsRenderError("rendered target artifact is not runtime-admitted")
    return rendered


def _line(value: object) -> bytes:
    return canonical_json(value) + b"\n"


def _control_resource() -> bytes:
    content = files(_VALIDATOR_RESOURCE_PACKAGE).joinpath(TARGET_CONTROL_FILENAME).read_bytes()
    if (
        not content
        or len(content) > _MAX_VALIDATOR_RESOURCE_BYTES
        or not content.endswith(b"\n")
        or b"\x00" in content
    ):
        raise TargetArtifactsRenderError("target-control resource is invalid")
    return content

"""Canonical source and rendered target-artifact values."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Final, Literal

from ci_coordinator.execution_orchestration import (
    TargetAdapterFileBinding,
    TargetProfileBinding,
    TargetWorkflowBinding,
)
from ci_coordinator.kernel import bounded_canonical_json, utf16_sort_key
from ci_coordinator.repo_context import (
    DEPENDENCY_GRAPH_SCHEMA_VERSION,
    MAX_DEPENDENCY_GRAPH_BYTES,
    DependencyGraphNode,
)
from ci_coordinator.runner_capacity import MAX_EXPECTED_SECONDS, MIN_EXPECTED_SECONDS

_IDENTIFIER: Final = re.compile(r"[a-z][a-z0-9._-]{0,63}")
_VERSION: Final = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}")

EXECUTION_REGISTRY_FILENAME: Final = "execution-registry.v1.json"
TEST_MANIFEST_FILENAME: Final = "test-manifest.v1.json"
DEPENDENCY_GRAPH_FILENAME: Final = "dependency-graph.v1.json"
TARGET_CONTROL_FILENAME: Final = "ci-coordinator.cjs"


@dataclass(frozen=True, slots=True)
class GeneratorIdentity:
    generator_id: str
    version: str

    def __post_init__(self) -> None:
        if type(self.generator_id) is not str or _IDENTIFIER.fullmatch(self.generator_id) is None:
            raise ValueError("generator id must be a canonical identifier")
        if type(self.version) is not str or _VERSION.fullmatch(self.version) is None:
            raise ValueError("generator version must be a canonical version")

    def to_mapping(self) -> dict[str, str]:
        return {"id": self.generator_id, "version": self.version}


@dataclass(frozen=True, slots=True)
class ManifestTestDefinition:
    test_id: str
    witness_id: str
    expected_seconds: float

    def __post_init__(self) -> None:
        if (
            type(self.test_id) is not str
            or not self.test_id
            or not self.test_id.isascii()
            or len(self.test_id.encode("ascii")) > 256
            or any(ord(character) < 0x20 or ord(character) > 0x7E for character in self.test_id)
        ):
            raise ValueError("test id must be bounded printable ASCII")
        if type(self.witness_id) is not str or _IDENTIFIER.fullmatch(self.witness_id) is None:
            raise ValueError("witness id must be a canonical identifier")
        if (
            type(self.expected_seconds) is not float
            or not math.isfinite(self.expected_seconds)
            or not MIN_EXPECTED_SECONDS <= self.expected_seconds <= MAX_EXPECTED_SECONDS
        ):
            raise ValueError("expected seconds is outside the manifest contract")

    def to_mapping(self) -> dict[str, object]:
        return {
            "testId": self.test_id,
            "witnessId": self.witness_id,
            "expectedSeconds": self.expected_seconds,
        }


@dataclass(frozen=True, slots=True)
class DependencyGraphDefinition:
    source: Literal["configured", "generated"]
    invalidates_when_changed: tuple[str, ...]
    global_risk_paths: tuple[str, ...]
    nodes: tuple[DependencyGraphNode, ...]

    def __post_init__(self) -> None:
        if self.source not in {"configured", "generated"}:
            raise ValueError("dependency graph source is invalid")
        _require_canonical_strings(self.invalidates_when_changed, "graph invalidators")
        _require_canonical_strings(self.global_risk_paths, "global risk paths")
        if type(self.nodes) is not tuple or any(
            type(node) is not DependencyGraphNode for node in self.nodes
        ):
            raise TypeError("dependency graph nodes must be exact")
        node_paths = tuple(node.path for node in self.nodes)
        if tuple(sorted(set(node_paths), key=utf16_sort_key)) != node_paths:
            raise ValueError("dependency graph nodes must be canonical")

    def to_artifact_mapping(self, generator: GeneratorIdentity) -> dict[str, object]:
        if type(generator) is not GeneratorIdentity:
            raise TypeError("dependency graph projection requires an exact generator")
        return {
            "provenance": {
                "source": self.source,
                "schemaVersion": DEPENDENCY_GRAPH_SCHEMA_VERSION,
                "generator": f"{generator.generator_id}@{generator.version}",
            },
            "invalidatesWhenChanged": list(self.invalidates_when_changed),
            "globalRiskPaths": list(self.global_risk_paths),
            "nodes": [node.to_identity_mapping() for node in self.nodes],
        }


@dataclass(frozen=True, slots=True)
class TargetArtifactsSource:
    generator: GeneratorIdentity
    adapter_workflow_files: tuple[TargetAdapterFileBinding, ...]
    workflows: tuple[TargetWorkflowBinding, ...]
    profiles: tuple[TargetProfileBinding, ...]
    tests: tuple[ManifestTestDefinition, ...]
    dependency_graph: DependencyGraphDefinition

    def __post_init__(self) -> None:
        if type(self.generator) is not GeneratorIdentity:
            raise TypeError("target artifacts require an exact generator identity")
        if (
            type(self.adapter_workflow_files) is not tuple
            or not self.adapter_workflow_files
            or any(
                type(binding) is not TargetAdapterFileBinding or not binding.is_workflow
                for binding in self.adapter_workflow_files
            )
        ):
            raise TypeError("target artifacts require exact adapter workflow files")
        adapter_paths = tuple(binding.path for binding in self.adapter_workflow_files)
        if tuple(sorted(set(adapter_paths), key=utf16_sort_key)) != adapter_paths:
            raise ValueError("target adapter workflow files must be canonical")
        if type(self.workflows) is not tuple or any(
            type(workflow) is not TargetWorkflowBinding for workflow in self.workflows
        ):
            raise TypeError("target artifact workflows must be exact")
        workflow_paths = tuple(workflow.workflow_path for workflow in self.workflows)
        if (
            not workflow_paths
            or tuple(sorted(set(workflow_paths), key=utf16_sort_key)) != workflow_paths
        ):
            raise ValueError("target artifact workflows must be non-empty and canonical")
        if not set(workflow_paths).issubset(adapter_paths):
            raise ValueError("execution workflows must belong to the adapter workflow set")
        if type(self.profiles) is not tuple or any(
            type(profile) is not TargetProfileBinding for profile in self.profiles
        ):
            raise TypeError("target artifact profiles must be exact")
        profile_ids = tuple(profile.profile_id for profile in self.profiles)
        if not profile_ids or tuple(sorted(set(profile_ids), key=utf16_sort_key)) != profile_ids:
            raise ValueError("target artifact profiles must be non-empty and canonical")
        if type(self.tests) is not tuple or any(
            type(test) is not ManifestTestDefinition for test in self.tests
        ):
            raise TypeError("target artifact tests must be exact")
        test_ids = tuple(test.test_id for test in self.tests)
        if tuple(sorted(set(test_ids), key=utf16_sort_key)) != test_ids:
            raise ValueError("target artifact tests must be canonical")
        if type(self.dependency_graph) is not DependencyGraphDefinition:
            raise TypeError("target artifacts require an exact dependency graph")
        bounded_canonical_json(
            self.dependency_graph.to_artifact_mapping(self.generator),
            # The rendered decoder budget also includes the required trailing LF.
            max_bytes=MAX_DEPENDENCY_GRAPH_BYTES - 1,
        )


@dataclass(frozen=True, slots=True)
class RenderedTargetArtifacts:
    execution_registry: bytes
    test_manifest: bytes
    dependency_graph: bytes
    control_bundle: bytes

    def __post_init__(self) -> None:
        if any(
            type(content) is not bytes or not content
            for content in (
                self.execution_registry,
                self.test_manifest,
                self.dependency_graph,
                self.control_bundle,
            )
        ):
            raise ValueError("rendered target artifacts must be non-empty bytes")

    def by_filename(self) -> tuple[tuple[str, bytes], ...]:
        return (
            (EXECUTION_REGISTRY_FILENAME, self.execution_registry),
            (TEST_MANIFEST_FILENAME, self.test_manifest),
            (DEPENDENCY_GRAPH_FILENAME, self.dependency_graph),
            (TARGET_CONTROL_FILENAME, self.control_bundle),
        )


def _require_canonical_strings(values: tuple[str, ...], field_name: str) -> None:
    if type(values) is not tuple or any(type(value) is not str for value in values):
        raise TypeError(f"{field_name} must be an exact string tuple")
    if tuple(sorted(set(values), key=utf16_sort_key)) != values:
        raise ValueError(f"{field_name} must be canonical")

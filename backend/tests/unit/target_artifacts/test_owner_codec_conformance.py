from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from ci_coordinator.execution_orchestration import parse_target_execution_registry
from ci_coordinator.repo_context import (
    MAX_DEPENDENCY_GRAPH_BYTES,
    parse_dependency_graph_artifact,
)
from ci_coordinator.runner_capacity import parse_test_manifest
from ci_coordinator.target_artifacts import (
    TargetArtifactsAdmissionError,
    parse_target_artifacts_source,
    render_target_artifacts,
)

ROOT = Path(__file__).resolve().parents[4]
SPEC_DIRECTORY = ROOT / "docs" / "specs" / "ci-coordinator-runtime"
SOURCE_PATH = (
    ROOT
    / "fixtures"
    / "target-repository"
    / ".ci-coordinator"
    / ("target-artifacts-source.v1.json")
)


def test_registry_schema_and_decoder_reject_noncanonical_generator() -> None:
    registry, _, _ = _rendered_documents()
    _object(registry["generator"])["id"] = "UPPER CASE"

    assert not _schema_valid("target-execution-registry.schema.v1.json", registry)
    assert parse_target_execution_registry(_bytes(registry)) is None


def test_manifest_schema_and_decoder_share_the_duration_and_identity_language() -> None:
    _, manifest, _ = _rendered_documents()
    test = _object(_array(manifest["tests"])[0])
    test["expectedSeconds"] = 0.000_000_1

    assert not _schema_valid("test-manifest.schema.v1.json", manifest)
    assert parse_test_manifest(_bytes(manifest)) is None

    test["expectedSeconds"] = 1
    test["witnessId"] = "UPPER CASE"
    assert not _schema_valid("test-manifest.schema.v1.json", manifest)
    assert parse_test_manifest(_bytes(manifest)) is None


@pytest.mark.parametrize("mutation", ["version", "extra", "duplicate"])
def test_graph_schema_and_decoder_reject_structural_or_canonical_drift(mutation: str) -> None:
    _, _, graph = _rendered_documents()
    if mutation == "version":
        _object(graph["provenance"])["schemaVersion"] = "dependency-graph/v2"
    elif mutation == "extra":
        graph["revision"] = "self-declared"
    else:
        graph["invalidatesWhenChanged"] = ["policy/**", "policy/**"]

    assert not _schema_valid("dependency-graph.schema.v1.json", graph)
    assert (
        parse_dependency_graph_artifact(
            _bytes(graph),
            retrieved_for_sha="a" * 40,
            trusted=True,
        )
        is None
    )


def test_source_schema_rejects_a_path_that_the_output_schema_cannot_represent() -> None:
    source = json.loads(SOURCE_PATH.read_bytes())
    _object(source["dependencyGraph"])["globalRiskPaths"] = ["a" * 513]

    with pytest.raises(TargetArtifactsAdmissionError):
        parse_target_artifacts_source(_bytes(source))


def test_graph_decoder_admits_a_complete_large_repository_projection() -> None:
    _, _, graph = _rendered_documents()
    graph["nodes"] = [
        {
            "path": f"backend/src/package_{index:05d}/module_{index:05d}.py",
            "dependents": [],
            "riskClasses": [],
        }
        for index in range(6_100)
    ]
    content = _bytes(graph)

    assert 524_288 < len(content) <= MAX_DEPENDENCY_GRAPH_BYTES
    assert (
        parse_dependency_graph_artifact(
            content,
            retrieved_for_sha="a" * 40,
            trusted=True,
        )
        is not None
    )


def test_graph_decoder_rejects_content_above_its_byte_budget() -> None:
    _, _, graph = _rendered_documents()
    content = _bytes(graph)
    oversized = content + b" " * (MAX_DEPENDENCY_GRAPH_BYTES - len(content) + 1)

    assert len(oversized) == MAX_DEPENDENCY_GRAPH_BYTES + 1
    assert (
        parse_dependency_graph_artifact(
            oversized,
            retrieved_for_sha="a" * 40,
            trusted=True,
        )
        is None
    )


def test_source_rejects_a_dependency_graph_that_cannot_be_rendered() -> None:
    source = json.loads(SOURCE_PATH.read_bytes())
    _object(source["dependencyGraph"])["nodes"] = [
        {
            "path": f"backend/src/package_{index:05d}/module_{index:05d}.py",
            "dependents": [],
            "riskClasses": [],
        }
        for index in range(12_000)
    ]

    with pytest.raises(TargetArtifactsAdmissionError):
        parse_target_artifacts_source(_bytes(source))


def _rendered_documents() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    source = parse_target_artifacts_source(SOURCE_PATH.read_bytes())
    rendered = render_target_artifacts(source)
    return (
        deepcopy(_object(json.loads(rendered.execution_registry))),
        deepcopy(_object(json.loads(rendered.test_manifest))),
        deepcopy(_object(json.loads(rendered.dependency_graph))),
    )


def _schema_valid(name: str, value: object) -> bool:
    schema = _object(json.loads((SPEC_DIRECTORY / name).read_bytes()))
    return Draft202012Validator(schema).is_valid(value)


def _bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _object(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _array(value: object) -> list[object]:
    assert isinstance(value, list)
    return value

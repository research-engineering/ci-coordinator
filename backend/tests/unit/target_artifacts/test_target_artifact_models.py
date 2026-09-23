from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import pytest

from ci_coordinator.execution_orchestration import (
    TargetAdapterFileBinding,
    digest_adapter_file,
    is_adapter_workflow_path,
)
from ci_coordinator.repo_context import DependencyGraphNode
from ci_coordinator.target_artifacts import parse_target_artifacts_source
from ci_coordinator.target_artifacts.model import (
    TARGET_CONTROL_FILENAME,
    DependencyGraphDefinition,
    GeneratorIdentity,
    ManifestTestDefinition,
    RenderedTargetArtifacts,
)

ROOT = Path(__file__).resolve().parents[4]
SOURCE_PATH = (
    ROOT
    / "fixtures"
    / "native-target-repository"
    / ".ci-coordinator"
    / "target-artifacts-source.v1.json"
)


@pytest.mark.parametrize(
    ("generator_id", "version"),
    [
        ("Invalid", "1"),
        ("valid", ""),
        ("valid", "x" * 65),
        (cast(Any, 1), "1"),
        ("valid", cast(Any, 1)),
    ],
)
def test_generator_identity_rejects_values_outside_its_finite_language(
    generator_id: str,
    version: str,
) -> None:
    with pytest.raises(ValueError):
        GeneratorIdentity(generator_id, version)


@pytest.mark.parametrize(
    ("test_id", "witness_id", "expected_seconds"),
    [
        ("", "witness", 1.0),
        ("\N{LATIN SMALL LETTER E WITH ACUTE}", "witness", 1.0),
        ("x" * 257, "witness", 1.0),
        ("line\nbreak", "witness", 1.0),
        ("test", "Invalid", 1.0),
        ("test", "witness", cast(Any, 1)),
        ("test", "witness", math.inf),
        ("test", "witness", 0.0),
        ("test", "witness", 604_801.0),
    ],
)
def test_manifest_test_definition_rejects_unrepresentable_tests(
    test_id: str,
    witness_id: str,
    expected_seconds: float,
) -> None:
    with pytest.raises(ValueError):
        ManifestTestDefinition(test_id, witness_id, expected_seconds)


@pytest.mark.parametrize(
    "build",
    [
        lambda node: DependencyGraphDefinition(
            cast(Any, "unknown"),
            (),
            (),
            (),
        ),
        lambda node: DependencyGraphDefinition(
            "generated",
            cast(Any, []),
            (),
            (),
        ),
        lambda node: DependencyGraphDefinition(
            "generated",
            (cast(Any, 1),),
            (),
            (),
        ),
        lambda node: DependencyGraphDefinition(
            "generated",
            ("same", "same"),
            (),
            (),
        ),
        lambda node: DependencyGraphDefinition(
            "generated",
            (),
            (),
            cast(Any, []),
        ),
        lambda node: DependencyGraphDefinition(
            "generated",
            (),
            (),
            (cast(Any, object()),),
        ),
        lambda node: DependencyGraphDefinition(
            "generated",
            (),
            (),
            (node, node),
        ),
    ],
    ids=(
        "source",
        "invalidator-container",
        "invalidator-member",
        "invalidator-order",
        "node-container",
        "node-member",
        "node-order",
    ),
)
def test_dependency_graph_definition_rejects_ambiguous_authority(
    build: Callable[[DependencyGraphNode], object],
) -> None:
    node = DependencyGraphNode("a.py", (), ())

    with pytest.raises((TypeError, ValueError)):
        build(node)


def test_target_artifact_source_rejects_incomplete_or_ambiguous_ownership() -> None:
    source = parse_target_artifacts_source(SOURCE_PATH.read_bytes())
    workflow = source.workflows[0]
    profile = source.profiles[0]
    test = source.tests[0] if source.tests else ManifestTestDefinition("test", "witness", 1.0)
    mutations: tuple[Callable[[], object], ...] = (
        lambda: replace(source, generator=cast(Any, object())),
        lambda: replace(source, adapter_workflow_files=cast(Any, [])),
        lambda: replace(source, adapter_workflow_files=()),
        lambda: replace(
            source,
            adapter_workflow_files=(cast(Any, object()),),
        ),
        lambda: replace(
            source,
            adapter_workflow_files=(
                source.adapter_workflow_files[0],
                source.adapter_workflow_files[0],
            ),
        ),
        lambda: replace(source, workflows=cast(Any, [])),
        lambda: replace(source, workflows=(cast(Any, object()),)),
        lambda: replace(source, workflows=()),
        lambda: replace(source, workflows=(workflow, workflow)),
        lambda: replace(
            source,
            workflows=(
                replace(
                    workflow,
                    workflow_path=".github/workflows/unbound.yml",
                ),
            ),
        ),
        lambda: replace(source, profiles=cast(Any, [])),
        lambda: replace(source, profiles=(cast(Any, object()),)),
        lambda: replace(source, profiles=()),
        lambda: replace(source, profiles=(profile, profile)),
        lambda: replace(source, tests=cast(Any, [])),
        lambda: replace(source, tests=(cast(Any, object()),)),
        lambda: replace(source, tests=(test, test)),
        lambda: replace(source, dependency_graph=cast(Any, object())),
    )

    for mutate in mutations:
        with pytest.raises((TypeError, ValueError)):
            mutate()


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/nested/test.yml",
        ".github/workflows/test.txt",
        ".github/workflows/" + "x" * 240 + ".yml",
        cast(Any, 1),
    ],
)
def test_adapter_workflow_path_rejects_non_snapshotable_paths(path: str) -> None:
    assert not is_adapter_workflow_path(path)
    with pytest.raises(ValueError):
        TargetAdapterFileBinding(path, "a" * 64)


@pytest.mark.parametrize("digest", ["A" * 64, "a" * 63, cast(Any, 1)])
def test_adapter_binding_rejects_noncanonical_digests(digest: str) -> None:
    with pytest.raises(ValueError):
        TargetAdapterFileBinding(".github/workflows/test.yml", digest)


def test_adapter_digest_and_rendered_artifacts_require_exact_nonempty_bytes() -> None:
    with pytest.raises(TypeError):
        digest_adapter_file(cast(Any, "bytes"))
    with pytest.raises(ValueError):
        RenderedTargetArtifacts(b"x", b"x", b"x", b"")

    rendered = RenderedTargetArtifacts(*(b"x",) * 4)
    filenames = tuple(filename for filename, _content in rendered.by_filename())
    assert TARGET_CONTROL_FILENAME == "ci-coordinator.cjs"
    assert filenames == (
        "execution-registry.v1.json",
        "test-manifest.v1.json",
        "dependency-graph.v1.json",
        "ci-coordinator.cjs",
    )

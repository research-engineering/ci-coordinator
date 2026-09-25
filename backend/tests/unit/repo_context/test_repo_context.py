from __future__ import annotations

from dataclasses import replace

import pytest

from ci_coordinator.kernel import path_patterns
from ci_coordinator.repo_context import (
    DependencyGraphArtifact,
    DependencyGraphNode,
    DiffBuildInput,
    DiffContext,
    DiffFileChangeInput,
    DiffSource,
    GraphProvenance,
    PolicySnapshot,
    RepositoryEpoch,
    build_dependency_graph,
    build_diff_context,
    build_planning_input,
    freshness,
)
from ci_coordinator.repo_context.freshness import matches_path_pattern, validate_path_pattern

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
EPOCH_HASH = "0" * 64
POLICY_HASH = "1" * 64


def test_freshness_preserves_the_public_path_functions_as_exact_kernel_aliases() -> None:
    assert freshness.validate_path_pattern is path_patterns.validate_path_pattern
    assert freshness.matches_path_pattern is path_patterns.matches_path_pattern
    assert freshness.is_safe_relative_path is path_patterns.is_safe_relative_path
    assert freshness.is_unicode_scalar_string is path_patterns.is_unicode_scalar_string


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("src/*.py", "src/main.py", True),
        ("src/*.py", "src/nested/main.py", False),
        ("src/?.py", "src/a.py", True),
        ("src/?.py", "src/ab.py", False),
        ("**/test_{unit,integration}.py", "test_unit.py", True),
        ("**/test_{unit,integration}.py", "backend/test_integration.py", True),
        ("assets/**", "assets/icons/logo.svg", True),
        ("assets/**", "src/assets/logo.svg", False),
        ("src", "src", True),
        ("src", "src/a.py", False),
        ("src/", "src/a.py", False),
        ("src/**/", "src/a/b.py", False),
        ("**/", "a.py", False),
        ("file-\u00e9/**", "file-\u00e9/a.py", True),
        ("file-\u00e9/**", "file-e\u0301/a.py", False),
        ("file-e\u0301/**", "file-e\u0301/a.py", True),
        ("file-e\u0301/**", "file-\u00e9/a.py", False),
    ],
)
def test_path_pattern_automaton_preserves_the_admitted_glob_language(
    pattern: str,
    path: str,
    expected: bool,
) -> None:
    assert matches_path_pattern(pattern, path) is expected


def test_path_pattern_automaton_bounds_adversarial_wildcard_state() -> None:
    pattern = "*a" * 255 + "*x"
    path = "a" * 4_095 + "y"

    assert len(pattern) == 512
    assert len(path) == 4_096
    assert validate_path_pattern(pattern) is None
    assert not matches_path_pattern(pattern, path)
    assert validate_path_pattern(pattern + "*") == "path pattern exceeds admitted size"
    assert not matches_path_pattern("*", path + "z")


def test_diff_preserves_distinct_unicode_paths_and_rename_coordinates() -> None:
    nfc, nfd = "file-\u00e9/a.py", "file-e\u0301/a.py"
    renamed = valid_diff(DiffFileChangeInput(path=nfd, previous_path=nfc, status="renamed"))
    first = valid_diff(DiffFileChangeInput(path=nfc, status="modified"))
    second = valid_diff(DiffFileChangeInput(path=nfd, status="modified"))

    assert not renamed.full_ci_invalidating
    assert (renamed.files[0].path, renamed.files[0].previous_path) == (nfd, nfc)
    assert first.diff_hash != second.diff_hash
    assert first.files[0].path == nfc
    assert second.files[0].path == nfd


def test_diff_hash_is_stable_under_file_permutation() -> None:
    source = DiffSource(provider="github", complete=True, page_count=1, file_count=2, max_files=100)
    first = build_diff_context(
        DiffBuildInput(
            base_sha=BASE_SHA,
            head_sha=HEAD_SHA,
            source=source,
            files=(
                DiffFileChangeInput(path="src/z.py", status="modified", additions=2),
                DiffFileChangeInput(path="src/a.py", status="added", patch="line"),
            ),
        )
    )
    second = build_diff_context(
        DiffBuildInput(
            base_sha=BASE_SHA,
            head_sha=HEAD_SHA,
            source=source,
            files=(
                DiffFileChangeInput(path="src/a.py", status="added", patch="line"),
                DiffFileChangeInput(path="src/z.py", status="modified", additions=2),
            ),
        )
    )

    assert first.diff_hash == second.diff_hash
    assert first.files == second.files
    assert first.full_ci_invalidating is False


def test_diff_unknown_or_incomplete_evidence_is_full_ci_invalidating() -> None:
    diff = build_diff_context(
        DiffBuildInput(
            base_sha=BASE_SHA,
            head_sha=HEAD_SHA,
            source=DiffSource(
                provider="github",
                complete=False,
                page_count=1,
                file_count=2,
                max_files=100,
            ),
            files=(
                DiffFileChangeInput(path="../unsafe.py", status="modified"),
                DiffFileChangeInput(path="src/moved.py", status="renamed"),
            ),
        )
    )

    assert diff.truncated is True
    assert diff.full_ci_invalidating is True
    assert diff.invalidating_reasons == (
        "diff_source_incomplete",
        "incomplete_rename_evidence",
        "unsafe_file_path",
    )
    assert {item.status for item in diff.files} == {"unknown"}


@pytest.mark.parametrize("status", [[], {}])
def test_unhashable_file_status_is_conservatively_full_ci_invalidating(
    status: object,
) -> None:
    diff = build_diff_context(
        DiffBuildInput(
            base_sha=BASE_SHA,
            head_sha=HEAD_SHA,
            source=DiffSource(
                provider="github",
                complete=True,
                page_count=1,
                file_count=1,
                max_files=100,
            ),
            files=(DiffFileChangeInput(path="src/unknown.py", status=status),),
        )
    )

    assert diff.full_ci_invalidating is True
    assert diff.invalidating_reasons == ("unknown_file_status",)
    assert diff.files[0].status == "unknown"


@pytest.mark.parametrize(
    ("path", "previous_path", "expected_reasons"),
    [
        ("src/\ud800unsafe.py", None, ("unsafe_file_path",)),
        ("src/renamed.py", "src/\ud800unsafe.py", ("unsafe_previous_file_path",)),
    ],
)
def test_surrogate_paths_are_normalized_before_canonical_sorting(
    path: str,
    previous_path: str | None,
    expected_reasons: tuple[str, ...],
) -> None:
    diff = build_diff_context(
        DiffBuildInput(
            base_sha=BASE_SHA,
            head_sha=HEAD_SHA,
            source=DiffSource(
                provider="github",
                complete=True,
                page_count=1,
                file_count=1,
                max_files=100,
            ),
            files=(DiffFileChangeInput(path=path, previous_path=previous_path, status="modified"),),
        )
    )

    assert diff.full_ci_invalidating is True
    assert diff.invalidating_reasons == expected_reasons
    assert all("\ud800" not in file.path for file in diff.files)
    assert all(
        file.previous_path is None or "\ud800" not in file.previous_path for file in diff.files
    )


def test_surrogate_patch_is_full_ci_invalidating_without_a_hash_escape() -> None:
    diff = build_diff_context(
        DiffBuildInput(
            base_sha=BASE_SHA,
            head_sha=HEAD_SHA,
            source=DiffSource(
                provider="github",
                complete=True,
                page_count=1,
                file_count=1,
                max_files=100,
            ),
            files=(DiffFileChangeInput(path="src/safe.py", status="modified", patch="\ud800"),),
        )
    )

    assert diff.full_ci_invalidating is True
    assert diff.invalidating_reasons == ("unsafe_patch",)
    assert diff.files[0].status == "unknown"
    assert diff.files[0].patch_hash is None


def test_graph_freshness_observes_exact_diff_and_dangling_edges() -> None:
    epoch = repository_epoch()
    policy = policy_snapshot()
    diff = valid_diff(DiffFileChangeInput(path="graph/input.json", status="modified"))
    graph = build_dependency_graph(
        epoch,
        diff,
        policy,
        DependencyGraphArtifact(
            provenance=GraphProvenance(
                source="generated",
                schema_version="v1",
                generator="generator/v1",
                retrieved_for_sha=HEAD_SHA,
                trusted=True,
                invalidates_when_changed=("graph/**",),
            ),
            nodes=(
                DependencyGraphNode(
                    path="src/a.py",
                    dependents=("src/missing.py",),
                    risk_classes=("backend",),
                ),
            ),
            global_risk_paths=(),
        ),
    )

    assert graph.fresh is False
    assert graph.invalidating_reasons == ("dangling_graph_edge", "graph_invalidated_by_diff")


def test_graph_hash_is_stable_when_invalidator_order_is_permuted() -> None:
    epoch = repository_epoch()
    policy = policy_snapshot()
    diff = valid_diff(DiffFileChangeInput(path="src/a.py", status="modified"))
    first = build_dependency_graph(
        epoch,
        diff,
        policy,
        graph_artifact(("ci/**", "graph/**")),
    )
    second = build_dependency_graph(
        epoch,
        diff,
        policy,
        graph_artifact(("graph/**", "ci/**")),
    )

    assert first.graph_hash == second.graph_hash


def test_graph_uses_policy_owned_global_risk_and_source_facts() -> None:
    epoch = repository_epoch()
    diff = valid_diff(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = replace(
        policy_snapshot(),
        dependency_graph_source="configured",
        global_risk_paths=("**",),
    )
    graph = build_dependency_graph(
        epoch,
        diff,
        policy,
        DependencyGraphArtifact(
            provenance=GraphProvenance(
                source="generated",
                schema_version="v1",
                generator="generator/v1",
                retrieved_for_sha=HEAD_SHA,
                trusted=True,
                invalidates_when_changed=(),
            ),
            nodes=(DependencyGraphNode("docs/guide.md", (), ("backend",)),),
            global_risk_paths=(),
        ),
    )

    assert graph.global_risk_paths == ("**",)
    assert graph.invalidating_reasons == (
        "graph_global_risk_paths_mismatch",
        "graph_source_mismatch",
    )


def test_planning_input_has_no_fallback_for_complete_compatible_evidence() -> None:
    epoch = repository_epoch()
    policy = policy_snapshot()
    diff = valid_diff(DiffFileChangeInput(path="src/a.py", status="modified"))
    graph = build_dependency_graph(
        epoch,
        diff,
        policy,
        DependencyGraphArtifact(
            provenance=GraphProvenance(
                source="generated",
                schema_version="v1",
                generator="generator/v1",
                retrieved_for_sha=HEAD_SHA,
                trusted=True,
                invalidates_when_changed=(),
            ),
            nodes=(DependencyGraphNode("src/a.py", (), ("backend",)),),
            global_risk_paths=(),
        ),
    )
    planning_input = build_planning_input(epoch, diff, graph, policy)

    assert planning_input.full_ci_invalidating is False
    assert planning_input.fallback_reasons == ()
    assert len(planning_input.input_hash) == 64


def test_planning_input_rejects_graph_replayed_for_another_diff() -> None:
    epoch = repository_epoch()
    policy = policy_snapshot()
    first_diff = valid_diff(DiffFileChangeInput(path="src/a.py", status="modified"))
    graph = build_dependency_graph(
        epoch,
        first_diff,
        policy,
        graph_artifact(("graph/**",)),
    )
    second_diff = valid_diff(DiffFileChangeInput(path="graph/config.yml", status="modified"))
    planning_input = build_planning_input(epoch, second_diff, graph, policy)

    assert planning_input.full_ci_invalidating is True
    assert planning_input.fallback_reasons == ("graph_diff_mismatch",)


def test_graph_hash_rejects_sidecar_identity_relabeling() -> None:
    epoch = repository_epoch()
    policy = policy_snapshot()
    diff = valid_diff(DiffFileChangeInput(path="src/a.py", status="modified"))
    graph = build_dependency_graph(epoch, diff, policy, graph_artifact(()))
    with pytest.raises(ValueError, match="graph hash does not seal"):
        replace(graph, admitted_diff_hash="f" * 64)


def test_planning_input_rejects_context_bound_to_another_config_epoch() -> None:
    epoch = repository_epoch()
    original_policy = policy_snapshot()
    input_policy = replace(original_policy, epoch_id="3" * 64)
    diff = valid_diff(DiffFileChangeInput(path="src/a.py", status="modified"))
    graph = build_dependency_graph(epoch, diff, original_policy, graph_artifact(()))
    planning_input = build_planning_input(epoch, diff, graph, input_policy)

    assert planning_input.fallback_reasons == ("graph_config_epoch_mismatch",)


def repository_epoch() -> RepositoryEpoch:
    return RepositoryEpoch(
        installation_id=1,
        repository_id=2,
        owner="example-org",
        name="ci-coordinator",
        event_name="pull_request",
        ref="refs/pull/1/merge",
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
    )


def policy_snapshot() -> PolicySnapshot:
    return PolicySnapshot(
        epoch_id=EPOCH_HASH,
        compiled_policy_hash=EPOCH_HASH,
        policy_hash=POLICY_HASH,
        dependency_graph_source="generated",
        global_risk_paths=(),
        risk_classes=("backend",),
    )


def valid_diff(*files: DiffFileChangeInput) -> DiffContext:
    return build_diff_context(
        DiffBuildInput(
            base_sha=BASE_SHA,
            head_sha=HEAD_SHA,
            files=files,
            source=DiffSource(
                provider="github",
                complete=True,
                page_count=1,
                file_count=len(files),
                max_files=100,
            ),
        )
    )


def graph_artifact(invalidators: tuple[str, ...]) -> DependencyGraphArtifact:
    return DependencyGraphArtifact(
        provenance=GraphProvenance(
            source="generated",
            schema_version="v1",
            generator="generator/v1",
            retrieved_for_sha=HEAD_SHA,
            trusted=True,
            invalidates_when_changed=invalidators,
        ),
        nodes=(DependencyGraphNode("src/a.py", (), ("backend",)),),
        global_risk_paths=(),
    )

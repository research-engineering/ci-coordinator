from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from scripts.ci_matrix_contract import load_matrix
from scripts.self_ci_catalog import FamilySettings, policy_document, validation_catalog
from scripts.self_ci_generate import SelfCiArtifacts
from scripts.self_ci_responsibility import GLOBAL_RISK_PATHS, family_responsibilities
from scripts.self_ci_source import WORKFLOW_PATH, read_regular, workflow_value

from ci_coordinator.config_control import ValidatedEpochDraft, admit_policy_document
from ci_coordinator.config_control.planning_projection import project_dynamic_ci_planning
from ci_coordinator.planning_core import DeterministicPlan, PlanningPolicy, plan
from ci_coordinator.repo_context import (
    DependencyGraphArtifact,
    DependencyGraphNode,
    DiffBuildInput,
    DiffFileChangeInput,
    DiffSource,
    GraphProvenance,
    PlanningInput,
    PolicySnapshot,
    RepositoryEpoch,
    build_dependency_graph,
    build_diff_context,
    build_planning_input,
    parse_dependency_graph_artifact,
)
from ci_coordinator.verification_core import verify

ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATHS = tuple(
    sorted(
        (
            "docs/guide.md",
            "frontend/biome.json",
            "config.example.yaml",
            "backend/alembic.ini",
            "tooling/actionlint/main.go",
            "tooling/actionlint/go.mod",
            "tooling/actionlint/embed.txt",
            "scripts/ci_matrix.py",
            ".github/workflows/python-persistence.yml",
            "tooling/quality/uv.lock",
        )
    )
)


def _fixture(
    changed: DiffFileChangeInput,
    *,
    complete: bool = True,
    absent_node: str | None = None,
    global_risk_paths: tuple[str, ...] = GLOBAL_RISK_PATHS,
    invalidates_when_changed: tuple[str, ...] = GLOBAL_RISK_PATHS,
) -> tuple[PlanningInput, PlanningPolicy]:
    profile, _quality = load_matrix(ROOT)
    settings = family_responsibilities(profile, FIXTURE_PATHS, ("tooling/actionlint",))
    workflow = workflow_value(read_regular(ROOT, WORKFLOW_PATH), WORKFLOW_PATH)
    excluded = {"ci-invocation", "plan-request", "plan", "coordinated-checks-gate"}
    jobs = {job_id: job for job_id, job in workflow["jobs"].items() if job_id not in excluded}
    catalog = validation_catalog(jobs, settings)
    graph = {"source": "configured", "globalRiskPaths": list(global_risk_paths)}
    admitted = admit_policy_document(
        policy_document(
            catalog,
            installation_id=100,
            repository_id=200,
            default_branch="master",
            dependency_graph=graph,
        ),
        "json",
    )
    assert isinstance(admitted, ValidatedEpochDraft)
    projection = project_dynamic_ci_planning(admitted)
    assert projection is not None
    policy = PlanningPolicy.from_projection(projection)
    snapshot = PolicySnapshot.from_projection(projection)
    epoch = RepositoryEpoch(
        installation_id=100,
        repository_id=200,
        owner="research-engineering",
        name="ci-coordinator",
        event_name="push",
        ref="refs/heads/ci-qualification/tests",
        base_sha="a" * 40,
        head_sha="b" * 40,
    )
    diff = build_diff_context(
        DiffBuildInput(
            base_sha=epoch.base_sha,
            head_sha=epoch.head_sha,
            files=(changed,),
            source=DiffSource(
                provider="github", complete=complete, page_count=1, file_count=1, max_files=10
            ),
        )
    )
    artifact = DependencyGraphArtifact(
        provenance=GraphProvenance(
            source="configured",
            schema_version="dependency-graph/v1",
            generator="self-ci@1",
            retrieved_for_sha=epoch.head_sha,
            trusted=True,
            invalidates_when_changed=invalidates_when_changed,
        ),
        nodes=tuple(
            DependencyGraphNode(path, (), ("source",))
            for path in FIXTURE_PATHS
            if path != absent_node
        ),
        global_risk_paths=global_risk_paths,
    )
    admitted_graph = build_dependency_graph(epoch, diff, snapshot, artifact)
    return build_planning_input(epoch, diff, admitted_graph, snapshot), policy


@pytest.mark.parametrize(
    ("path", "omitted"),
    [
        ("docs/guide.md", {"utility-config", "utility-go-static"}),
        ("tooling/actionlint/main.go", {"utility-config"}),
        ("tooling/actionlint/embed.txt", {"utility-config"}),
        ("frontend/biome.json", {"utility-go-static"}),
        ("backend/alembic.ini", {"utility-go-static"}),
        ("config.example.yaml", {"utility-go-static"}),
    ],
)
def test_native_planner_and_independent_verifier_admit_only_unaffected_utilities(
    path: str, omitted: set[str]
) -> None:
    planning_input, policy = _fixture(DiffFileChangeInput(path=path, status="modified"))
    candidate = plan(planning_input, policy)
    assert isinstance(candidate, DeterministicPlan)
    admitted = verify(planning_input, policy, candidate)
    assert not admitted.fallback.triggered
    assert {row.obligation_id for row in admitted.omitted_obligations} == omitted
    always_required = {
        row.obligation_id for row in policy.catalog.obligations if not row.omit_allowed
    }
    assert always_required <= {row.obligation_id for row in admitted.selected_obligations}
    assert {"utility-docker", "utility-go-vulnerabilities", "utility-spelling"} <= always_required


@pytest.mark.parametrize(
    ("changed", "complete", "absent"),
    [
        (DiffFileChangeInput(path="scripts/ci_matrix.py", status="modified"), True, None),
        (DiffFileChangeInput(path="tooling/quality/uv.lock", status="modified"), True, None),
        (DiffFileChangeInput(path="docs/new.md", status="added"), True, None),
        (DiffFileChangeInput(path="docs/guide.md", status="removed"), True, "docs/guide.md"),
        (
            DiffFileChangeInput(
                path="docs/new.md", status="renamed", previous_path="docs/guide.md"
            ),
            True,
            None,
        ),
        (DiffFileChangeInput(path="docs/guide.md", status="modified"), False, None),
        (DiffFileChangeInput(path="docs/guide.md", status="modified"), True, "docs/guide.md"),
    ],
)
def test_unknown_deleted_renamed_incomplete_and_control_inputs_force_full_ci(
    changed: DiffFileChangeInput, complete: bool, absent: str | None
) -> None:
    planning_input, policy = _fixture(changed, complete=complete, absent_node=absent)
    candidate = plan(planning_input, policy)
    assert isinstance(candidate, DeterministicPlan)
    admitted = verify(planning_input, policy, candidate)
    assert admitted.fallback.triggered
    assert admitted.omitted_obligations == ()
    assert len(admitted.selected_obligations) == len(policy.catalog.obligations)


@pytest.mark.parametrize("guard", ["global-risk", "invalidation"])
@pytest.mark.parametrize(
    "path",
    ["scripts/ci_matrix.py", ".github/workflows/python-persistence.yml", "tooling/quality/uv.lock"],
)
def test_independent_graph_controls_preserve_full_ci_without_responsibility_duplication(
    guard: str,
    path: str,
) -> None:
    planning_input, policy = _fixture(
        DiffFileChangeInput(path=path, status="modified"),
        global_risk_paths=GLOBAL_RISK_PATHS if guard == "global-risk" else (),
        invalidates_when_changed=GLOBAL_RISK_PATHS if guard == "invalidation" else (),
    )
    omittable = [row for row in policy.catalog.obligations if row.omit_allowed]
    assert len(omittable) == 2
    assert all(
        not {"scripts/**", ".github/**", "tooling/quality/**"}.intersection(
            row.responsibility_paths
        )
        for row in omittable
    )
    candidate = plan(planning_input, policy)
    assert isinstance(candidate, DeterministicPlan)
    admitted = verify(planning_input, policy, candidate)
    assert admitted.fallback.triggered and admitted.omitted_obligations == ()


def test_verified_docs_plan_cannot_be_reused_for_changed_go_inputs() -> None:
    docs, policy = _fixture(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    changed_go, _ = _fixture(
        DiffFileChangeInput(path="tooling/actionlint/embed.txt", status="modified")
    )
    candidate = plan(docs, policy)
    assert isinstance(candidate, DeterministicPlan)
    admitted = verify(changed_go, policy, candidate)
    assert admitted.fallback.triggered and admitted.omitted_obligations == ()
    with pytest.raises(ValueError):
        replace(
            candidate,
            selected_witnesses=tuple(
                item
                for item in candidate.selected_witnesses
                if item.witness_id != "native-test-shards"
            ),
        )


def test_default_cli_projection_has_exact_finite_nodes_and_rejects_truncated_graph(
    self_ci_artifacts: SelfCiArtifacts,
) -> None:
    artifacts = self_ci_artifacts
    expected_paths = {path for path, _mode in artifacts.responsibility.path_inventory}
    raw_graph = artifacts.outputs[".ci-coordinator/dependency-graph.v1.json"]
    graph = parse_dependency_graph_artifact(raw_graph, retrieved_for_sha="b" * 40, trusted=True)
    assert graph is not None
    assert {node.path for node in graph.nodes} == expected_paths
    assert all(not node.dependents for node in graph.nodes)
    assert "**" not in graph.global_risk_paths
    assert (
        parse_dependency_graph_artifact(raw_graph[:-20], retrieved_for_sha="b" * 40, trusted=True)
        is None
    )
    assert {row.obligation_id for row in artifacts.catalog.obligations if row.omit_allowed} == {
        "utility-config",
        "utility-go-static",
    }


def test_native_omission_cannot_be_enabled_through_family_overrides() -> None:
    workflow = workflow_value(read_regular(ROOT, WORKFLOW_PATH), WORKFLOW_PATH)
    excluded = {"ci-invocation", "plan-request", "plan", "coordinated-checks-gate"}
    jobs = {job_id: job for job_id, job in workflow["jobs"].items() if job_id not in excluded}
    with pytest.raises(ValueError, match="must remain non-omittable"):
        validation_catalog(jobs, {"python-native-coverage": FamilySettings(omit_allowed=True)})

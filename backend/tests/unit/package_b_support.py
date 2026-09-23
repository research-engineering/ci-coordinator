from __future__ import annotations

from datetime import UTC, datetime

from ci_coordinator.execution_orchestration import (
    TARGET_CONTROL_FILE_PATHS,
    TargetAdapterFileBinding,
    TargetExecutionRegistry,
    TargetJobBinding,
    TargetProfileBinding,
    TargetWorkflowBinding,
    TrustedExecutionTarget,
)
from ci_coordinator.planning_core import DeterministicPlan, PlanningPolicy
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
)
from ci_coordinator.runner_capacity import (
    ManifestTest,
    RunnerCapacity,
    RunnerSnapshot,
    ShardPlan,
    TrustedExecutionProjection,
    build_test_manifest,
    optimize_shards,
)
from ci_coordinator.validation_contract import (
    ExecutableWitness,
    ExecutionProfile,
    ShardingPolicy,
    ValidationCatalog,
    ValidationObligation,
)
from ci_coordinator.verification_core import VerifiedPlan

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
EPOCH_HASH = "c" * 64
POLICY_HASH = "d" * 64
TEST_NOW = datetime(2026, 7, 16, tzinfo=UTC)
PLAN_REQUEST_JOB_ID = "plan-request"
PLAN_REQUEST_WORKFLOW_REF = (
    "example-org/ci-coordinator/.github/workflows/trusted-plan-request.yml@" + "1" * 40
)


def assert_deterministic_plan(value: DeterministicPlan | object) -> DeterministicPlan:
    assert isinstance(value, DeterministicPlan)
    return value


def make_catalog() -> ValidationCatalog:
    witness_ids = (
        "backend-witness",
        "baseline-witness",
        "docs-witness",
        "shared-quality",
    )
    return ValidationCatalog(
        obligations=(
            ValidationObligation(
                obligation_id="backend-tests",
                responsibility_paths=("src/**",),
                responsibility_risk_classes=("backend", "test"),
                required_witness_ids=("backend-witness", "shared-quality"),
                default_depth="targeted",
                full_depth="full",
                omit_allowed=True,
            ),
            ValidationObligation(
                obligation_id="docs-lint",
                responsibility_paths=("docs/**",),
                responsibility_risk_classes=("docs",),
                required_witness_ids=("docs-witness", "shared-quality"),
                default_depth="smoke",
                full_depth="standard",
                omit_allowed=True,
            ),
            ValidationObligation(
                obligation_id="required-baseline",
                responsibility_paths=("ci/**",),
                responsibility_risk_classes=("test",),
                required_witness_ids=("baseline-witness",),
                default_depth="standard",
                full_depth="full",
                omit_allowed=False,
            ),
        ),
        witnesses=tuple(
            ExecutableWitness(
                witness_id=witness_id,
                execution_profile_id="python-default",
                supported_depths=("smoke", "targeted", "standard", "full", "exhaustive"),
            )
            for witness_id in witness_ids
        ),
        execution_profiles=(
            ExecutionProfile(
                profile_id="python-default",
                runner_profile_id="ubuntu-24.04",
                permission_profile_id="contents-read",
                credential_profile_id="none",
                fixture_profile_id="unit",
                service_profile_ids=(),
                capacity_class_id="self-hosted-default",
                sharding_policy=ShardingPolicy(8, 8, 10_000, 10.0),
            ),
        ),
    )


def make_policy(
    *,
    policy_hash: str = POLICY_HASH,
    catalog: ValidationCatalog | None = None,
    config_epoch_id: str = EPOCH_HASH,
    compiled_policy_hash: str = EPOCH_HASH,
) -> PlanningPolicy:
    return PlanningPolicy(
        config_epoch_id=config_epoch_id,
        compiled_policy_hash=compiled_policy_hash,
        policy_hash=policy_hash,
        catalog=catalog or make_catalog(),
        fallback_timeout_seconds=300,
    )


def make_target_registry(catalog: ValidationCatalog) -> TargetExecutionRegistry:
    workflow_path = ".github/workflows/ci-coordinator-bootstrap.yml"
    profiles = tuple(
        TargetProfileBinding.from_profile(
            item,
            workflow_path=workflow_path,
            job_id="selected-" + item.profile_id.replace(".", "-"),
        )
        for item in catalog.execution_profiles
    )
    return TargetExecutionRegistry(
        generator_id="test-generator",
        generator_version="1",
        adapter_files=make_adapter_files(workflow_path),
        workflows=(
            TargetWorkflowBinding(
                workflow_path=workflow_path,
                execution_kind="witness-shards",
                execution_jobs=tuple(TargetJobBinding(item.job_id, ("plan",)) for item in profiles),
                plan_request_job_id=PLAN_REQUEST_JOB_ID,
                plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
                plan_job_id="plan",
                fallback_job_id="full-ci",
                gate_job_id="bootstrap-gate",
                gate_signal_name="Dynamic CI Bootstrap",
            ),
        ),
        profiles=profiles,
    )


def make_shard_plan(
    verified_plan: VerifiedPlan,
    *,
    free_slots: int = 8,
    now: datetime = TEST_NOW,
) -> ShardPlan:
    tests = tuple(
        ManifestTest(test_id=item.witness_id, witness_id=item.witness_id)
        for item in verified_plan.selected_witnesses
    )
    manifest = build_test_manifest(
        verified_plan_id=verified_plan.execution_plan_id,
        target_registry_hash=make_target_registry(verified_plan.catalog).registry_hash,
        selected_witness_ids=(item.witness_id for item in verified_plan.selected_witnesses),
        tests=tests,
        catalog=verified_plan.catalog,
    )
    return optimize_shards(
        manifest,
        RunnerSnapshot(
            observed_at=now,
            freshness_ttl_seconds=60,
            capacities=(RunnerCapacity("self-hosted-default", free_slots),),
            source="operator",
        ),
        {item.test_id: 10.0 for item in tests},
        now=now,
    )


def make_execution_projection(
    verified_plan: VerifiedPlan,
    *,
    free_slots: int = 8,
    now: datetime = TEST_NOW,
) -> TrustedExecutionProjection:
    registry = make_target_registry(verified_plan.catalog)
    shard_plan = make_shard_plan(
        verified_plan,
        free_slots=free_slots,
        now=now,
    )
    workflow = registry.workflows[0]
    return TrustedExecutionProjection(
        target=TrustedExecutionTarget(
            verified_plan_id=verified_plan.execution_plan_id,
            workflow_path=workflow.workflow_path,
            selected_profile_ids=tuple(item.profile_id for item in shard_plan.profile_plans),
            target_registry=registry,
        ),
        shard_plan=shard_plan,
    )


def make_native_execution_projection(
    verified_plan: VerifiedPlan,
) -> TrustedExecutionProjection:
    workflow_path = ".github/workflows/full-check.yml"
    selected_witness_ids = {item.witness_id for item in verified_plan.selected_witnesses}
    selected_profile_ids = tuple(
        sorted(
            {
                item.execution_profile_id
                for item in verified_plan.catalog.witnesses
                if item.witness_id in selected_witness_ids
            }
        )
    )
    profiles = tuple(
        TargetProfileBinding.from_profile(
            profile,
            workflow_path=workflow_path,
            job_id="native-" + profile.profile_id.replace(".", "-"),
            execution_kind="native-job-set",
        )
        for profile in verified_plan.catalog.execution_profiles
    )
    registry = TargetExecutionRegistry(
        generator_id="test-generator",
        generator_version="1",
        adapter_files=make_adapter_files(workflow_path),
        workflows=(
            TargetWorkflowBinding(
                workflow_path=workflow_path,
                execution_kind="native-job-set",
                execution_jobs=tuple(TargetJobBinding(item.job_id, ("plan",)) for item in profiles),
                plan_request_job_id=PLAN_REQUEST_JOB_ID,
                plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
                plan_job_id="plan",
                fallback_job_id=None,
                gate_job_id="pr-gate",
                gate_signal_name="Pull Request Gate",
            ),
        ),
        profiles=profiles,
    )
    return TrustedExecutionProjection(
        target=TrustedExecutionTarget(
            verified_plan_id=verified_plan.execution_plan_id,
            workflow_path=workflow_path,
            selected_profile_ids=selected_profile_ids,
            target_registry=registry,
        ),
        shard_plan=None,
    )


def make_adapter_files(*workflow_paths: str) -> tuple[TargetAdapterFileBinding, ...]:
    return tuple(
        TargetAdapterFileBinding(path=path, sha256="0" * 64)
        for path in sorted((*workflow_paths, *TARGET_CONTROL_FILE_PATHS))
    )


def make_input(
    *files: DiffFileChangeInput,
    graph_nodes: tuple[DependencyGraphNode, ...] | None = None,
    global_risk_paths: tuple[str, ...] = (".github/workflows/**", "ci/**"),
    graph_retrieved_for_sha: str = HEAD_SHA,
    config_epoch_id: str = EPOCH_HASH,
    compiled_policy_hash: str = EPOCH_HASH,
    policy_hash: str = POLICY_HASH,
) -> PlanningInput:
    epoch = RepositoryEpoch(
        installation_id=100,
        repository_id=200,
        owner="example-org",
        name="ci-coordinator",
        event_name="pull_request",
        ref="refs/pull/42/merge",
        base_sha=BASE_SHA,
        head_sha=HEAD_SHA,
    )
    snapshot = PolicySnapshot(
        epoch_id=config_epoch_id,
        compiled_policy_hash=compiled_policy_hash,
        policy_hash=policy_hash,
        dependency_graph_source="generated",
        global_risk_paths=global_risk_paths,
        risk_classes=("backend", "docs", "test"),
    )
    diff = build_diff_context(
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
    graph = build_dependency_graph(
        epoch,
        diff,
        snapshot,
        DependencyGraphArtifact(
            provenance=GraphProvenance(
                source="generated",
                schema_version="dynamic-ci-graph/v1",
                generator="test/v1",
                retrieved_for_sha=graph_retrieved_for_sha,
                trusted=True,
                invalidates_when_changed=(),
            ),
            nodes=graph_nodes
            if graph_nodes is not None
            else (
                DependencyGraphNode("docs/guide.md", (), ("docs",)),
                DependencyGraphNode("src/service.py", (), ("backend",)),
                DependencyGraphNode("ci/pipeline.yml", (), ("test",)),
            ),
            global_risk_paths=global_risk_paths,
        ),
    )
    return build_planning_input(epoch, diff, graph, snapshot)

"""Real application-path composition for one admitted local scenario."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from ci_coordinator.app import (
    CapacityPlanningService,
    DeterministicCandidatePlanner,
    DurableReconciliationRegistrar,
    DynamicPlanCommand,
    DynamicPlanService,
)
from ci_coordinator.config_control import (
    RepositoryScope,
    ValidatedEpochDraft,
    admit_policy_document,
)
from ci_coordinator.config_control.planning_projection import project_dynamic_ci_planning
from ci_coordinator.config_epochs import ActiveConfigEpoch, ActiveConfigEpochSnapshot
from ci_coordinator.consumer_contract_lab.composition_adapters import (
    ActiveEpochs,
    Contexts,
    ExecutionInputs,
    NoRunnerSnapshot,
    RegistrationStore,
)
from ci_coordinator.consumer_contract_lab.lab_authority import (
    SyntheticLabAuthority,
    lab_private_key_pem,
)
from ci_coordinator.consumer_contract_lab.model import ConsumerLabScenario
from ci_coordinator.consumer_contract_lab.source_epoch import PreparedConsumerContract
from ci_coordinator.execution_orchestration import (
    LOCAL_PLAN_REQUEST_WORKFLOW_PATH,
    LOCAL_PLAN_REQUEST_WORKFLOW_REF,
)
from ci_coordinator.identity_admission import TrustedActionsRun
from ci_coordinator.kernel import FixedClock, canonical_json, hash_object
from ci_coordinator.plan_issuance import (
    InMemoryIssuanceStore,
    Issued,
    PlanRequest,
    SignedPlanIssuer,
    SignedPlanSigner,
)
from ci_coordinator.reconciliation import ReconciliationPersistence
from ci_coordinator.repo_context import (
    DiffBuildInput,
    DiffFileChangeInput,
    DiffSource,
    PlanningInput,
    PolicySnapshot,
    RepositoryEpoch,
    build_dependency_graph,
    build_diff_context,
    build_planning_input,
    parse_dependency_graph_artifact,
)

_FIXED_TIME = datetime(2026, 7, 30, 12, 0, tzinfo=UTC)


class ConsumerLabExecutionError(RuntimeError):
    """An admitted contract failed to traverse the real application path."""


@dataclass(frozen=True, slots=True)
class IssuedScenario:
    scenario_id: str
    key_id: str
    request: PlanRequest
    identity: TrustedActionsRun
    envelope_bytes: bytes
    public_key_pem: bytes
    capacity_input_calls: int
    reconciliation_registration_count: int

    def __post_init__(self) -> None:
        if type(self.request) is not PlanRequest:
            raise TypeError("issued scenario requires an exact plan request")
        if not self.key_id:
            raise ValueError("issued scenario requires a plan key identity")
        if type(self.identity) is not TrustedActionsRun:
            raise TypeError("issued scenario requires an exact trusted identity")
        if not self.envelope_bytes.endswith(b"\n"):
            raise ValueError("issued scenario envelope must be one canonical JSON line")
        if not self.public_key_pem:
            raise ValueError("issued scenario requires a plan verification key")
        selected = self.capacity_input_calls == self.reconciliation_registration_count == 1
        fallback = self.capacity_input_calls == self.reconciliation_registration_count == 0
        if not (selected or fallback):
            raise ValueError("issued scenario selected-only path counts are inconsistent")


def issue_scenario(
    contract: PreparedConsumerContract,
    scenario: ConsumerLabScenario,
    *,
    scenario_index: int,
) -> IssuedScenario:
    if type(contract) is not PreparedConsumerContract:
        raise TypeError("scenario issuance requires an exact prepared contract")
    if type(scenario) is not ConsumerLabScenario:
        raise TypeError("scenario issuance requires an exact scenario")
    if type(scenario_index) is not int or scenario_index < 1:
        raise ValueError("scenario index must be positive")

    active = _active_epoch(contract)
    projection = project_dynamic_ci_planning(active.draft)
    if projection is None:
        raise ConsumerLabExecutionError("consumer policy has no dynamic planning projection")
    policy = PolicySnapshot.from_projection(projection)
    request = _request(contract, scenario, scenario_index=scenario_index)
    identity = _identity(contract, request)
    planning_input = _planning_input(contract, scenario, request, policy)
    scope = RepositoryScope(
        contract.profile.repository.installation_id,
        contract.profile.repository.repository_id,
    )
    workflow_identity = f"{contract.profile.repository.full_name}/{contract.profile.workflow_path}"
    if identity.job_workflow_ref is None:
        raise ConsumerLabExecutionError("consumer requester identity is absent")
    authority = SyntheticLabAuthority(
        scope=scope,
        config_epoch_id=projection.epoch_id,
        compiled_policy_hash=projection.compiled_policy_hash,
        policy_hash=projection.policy_hash,
        catalog_hash=projection.validation_catalog.catalog_hash,
        workflow_path_identity=workflow_identity,
        job_workflow_ref=identity.job_workflow_ref,
        target_registry_hash=contract.target_registry.registry_hash,
        coordinator_commit=contract.coordinator_source.head,
        source_epoch_id=contract.source_epoch_id,
        scenario_id=scenario.scenario_id,
        now=_FIXED_TIME,
    )
    clock = FixedClock(_FIXED_TIME)
    private_key = lab_private_key_pem(
        domain="plan",
        source_epoch_id=contract.source_epoch_id,
        scenario_id=scenario.scenario_id,
    )
    signer = SignedPlanSigner(
        key_id="consumer-lab-plan-"
        + hash_object(
            {
                "scenarioId": scenario.scenario_id,
                "sourceEpochId": contract.source_epoch_id,
            }
        )[:16],
        private_key_pem=private_key,
        ttl_seconds=120,
        clock=clock,
    )
    reconciliation_store = RegistrationStore()
    reconciliation = DurableReconciliationRegistrar(
        cast(ReconciliationPersistence, reconciliation_store),
        rollout_profile_id=hash_object(
            {
                "profileId": contract.profile.profile_id,
                "sourceEpochId": contract.source_epoch_id,
            }
        ),
    )
    execution_inputs = ExecutionInputs(
        contract.profile,
        contract.target_registry,
        identity.workflow_sha,
    )
    service = DynamicPlanService(
        active_epochs=ActiveEpochs(active),
        candidates=DeterministicCandidatePlanner(Contexts(planning_input)),
        issuer=SignedPlanIssuer(store=InMemoryIssuanceStore(), signer=signer),
        reconciliation=reconciliation,
        capacity=CapacityPlanningService(
            snapshots=NoRunnerSnapshot(),
            inputs=execution_inputs,
            clock=clock,
        ),
        enforcement_authority=authority,
    )
    result = asyncio.run(
        service.request_dynamic_plan(DynamicPlanCommand(request=request, identity=identity))
    )
    if not isinstance(result, Issued):
        raise ConsumerLabExecutionError("real plan service did not issue a signed envelope")
    envelope = result.record.envelope
    selected = envelope.payload.verified_plan_id is not None
    expected_selected_only_calls = 1 if selected else 0
    if (
        execution_inputs.load_count != expected_selected_only_calls
        or reconciliation_store.registration_count != expected_selected_only_calls
        or len(reconciliation_store.snapshots) != expected_selected_only_calls
    ):
        raise ConsumerLabExecutionError(
            "selected-only capacity and reconciliation paths did not match the route"
        )
    envelope_bytes = (
        canonical_json({**envelope.unsigned_mapping(), "signature": envelope.signature}) + b"\n"
    )
    return IssuedScenario(
        scenario_id=scenario.scenario_id,
        key_id=envelope.key_id,
        request=request,
        identity=identity,
        envelope_bytes=envelope_bytes,
        public_key_pem=signer.public_key_pem(),
        capacity_input_calls=execution_inputs.load_count,
        reconciliation_registration_count=reconciliation_store.registration_count,
    )


def _active_epoch(contract: PreparedConsumerContract) -> ActiveConfigEpochSnapshot:
    repository = contract.profile.repository
    workflow = contract.target_registry.workflow(contract.profile.workflow_path)
    if workflow is None:
        raise ConsumerLabExecutionError("consumer workflow is absent from target registry")
    dynamic = contract.dynamic_policy["dynamicCi"]
    if type(dynamic) is not dict:
        raise ConsumerLabExecutionError("consumer dynamic policy is invalid")
    policy = {
        "schemaVersion": "ci-repository-policy/v1",
        "repository": {
            "installationId": repository.installation_id,
            "repositoryId": repository.repository_id,
            "owner": repository.owner,
            "name": repository.name,
            "defaultBranch": repository.default_branch,
            "rules": [
                {
                    "name": f"consumer-lab-{event}",
                    "on": {
                        "event": event,
                        "branches": [repository.default_branch],
                    },
                    "mode": "observe",
                    "expectedSignals": [
                        {
                            "kind": "workflow",
                            "name": workflow.gate_signal_name,
                            "workflowFile": contract.profile.workflow_path,
                            "source": "native",
                            "requiredConclusion": "success",
                            "required": True,
                        }
                    ],
                }
                for event in contract.profile.event_surface
            ],
            "dynamicCi": dynamic,
        },
    }
    admitted = admit_policy_document(canonical_json(policy), "json")
    if not isinstance(admitted, ValidatedEpochDraft):
        raise ConsumerLabExecutionError("target dynamic policy failed canonical admission")
    return ActiveConfigEpochSnapshot(
        active=ActiveConfigEpoch(admitted.scope, admitted.epoch_id, 1),
        draft=admitted,
    )


def _planning_input(
    contract: PreparedConsumerContract,
    scenario: ConsumerLabScenario,
    request: PlanRequest,
    policy: PolicySnapshot,
) -> PlanningInput:
    epoch = RepositoryEpoch(
        installation_id=request.installation_id,
        repository_id=request.repository_id,
        owner=request.owner,
        name=request.repository,
        event_name=request.event_name,
        ref=request.ref,
        base_sha=request.base_sha,
        head_sha=request.head_sha,
    )
    diff = build_diff_context(
        DiffBuildInput(
            base_sha=request.base_sha,
            head_sha=request.head_sha,
            files=tuple(
                DiffFileChangeInput(
                    path=change.path,
                    status=change.status,
                    previous_path=change.previous_path,
                )
                for change in scenario.changes
            ),
            source=DiffSource(
                provider="github",
                complete=True,
                page_count=1,
                file_count=len(scenario.changes),
                max_files=1_000,
            ),
        )
    )
    graph_path = (
        f"{contract.profile.target_artifacts_directory.rstrip('/')}/dependency-graph.v1.json"
    )
    graph_content = contract.file_bytes(graph_path)
    graph_artifact = parse_dependency_graph_artifact(
        graph_content,
        retrieved_for_sha=request.head_sha,
        trusted=True,
    )
    if graph_artifact is None:
        raise ConsumerLabExecutionError("target dependency graph is not runtime-admitted")
    graph = build_dependency_graph(epoch, diff, policy, graph_artifact)
    return build_planning_input(epoch, diff, graph, policy)


def _request(
    contract: PreparedConsumerContract,
    scenario: ConsumerLabScenario,
    *,
    scenario_index: int,
) -> PlanRequest:
    repository = contract.profile.repository
    base_sha = hash_object({"scenarioId": scenario.scenario_id, "kind": "base"})[:40]
    head_sha = hash_object({"scenarioId": scenario.scenario_id, "kind": "head"})[:40]
    execution_sha = hash_object({"scenarioId": scenario.scenario_id, "kind": "execution"})[:40]
    pull_request_number = scenario_index if scenario.event_name == "pull_request" else None
    merge_group_head_ref = (
        f"refs/heads/gh-readonly-queue/{repository.default_branch}/"
        f"pr-{scenario_index}-{head_sha[:8]}"
        if scenario.event_name == "merge_group"
        else None
    )
    ref = (
        f"refs/pull/{pull_request_number}/merge"
        if pull_request_number is not None
        else merge_group_head_ref
        if merge_group_head_ref is not None
        else f"refs/heads/{repository.default_branch}"
    )
    return PlanRequest(
        schema_version="dynamic-ci-plan-request/v2",
        request_id=f"{repository.repository_id}:{scenario_index}:1",
        installation_id=repository.installation_id,
        repository_id=repository.repository_id,
        owner=repository.owner,
        repository=repository.name,
        event_name=scenario.event_name,
        ref=ref,
        base_sha=base_sha,
        head_sha=head_sha,
        execution_sha=execution_sha,
        workflow_run_id=scenario_index,
        run_attempt=1,
        pull_request_number=pull_request_number,
        merge_group_head_ref=merge_group_head_ref,
    )


def _identity(
    contract: PreparedConsumerContract,
    request: PlanRequest,
) -> TrustedActionsRun:
    workflow = contract.target_registry.workflow(contract.profile.workflow_path)
    if workflow is None:
        raise ConsumerLabExecutionError("consumer workflow is absent from target registry")
    job_workflow_ref = workflow.plan_request_workflow_ref
    if job_workflow_ref == LOCAL_PLAN_REQUEST_WORKFLOW_REF:
        job_workflow_ref = (
            f"{contract.profile.repository.full_name}/"
            f"{LOCAL_PLAN_REQUEST_WORKFLOW_PATH}@{request.ref}"
        )
        job_workflow_sha = contract.execution_authority.coordinate
    else:
        _, _, job_workflow_sha = job_workflow_ref.rpartition("@")
    return TrustedActionsRun(
        issuer="https://token.actions.githubusercontent.com",
        audience="https://coordinator.consumer-lab.invalid/api/v1/dynamic-ci/plan",
        repository=contract.profile.repository.full_name,
        repository_id=request.repository_id,
        ref=request.ref,
        run_id=request.workflow_run_id,
        run_attempt=request.run_attempt,
        event_name=request.event_name,
        execution_sha=request.execution_sha,
        workflow_ref=(
            f"{contract.profile.repository.full_name}/"
            f"{contract.profile.workflow_path}@{request.ref}"
        ),
        workflow_sha=contract.execution_authority.coordinate,
        job_workflow_ref=job_workflow_ref,
        job_workflow_sha=job_workflow_sha,
        check_run_id=None,
        verified_at=_FIXED_TIME,
        claim_hash=hash_object(
            {
                "authority": "lab-fixture",
                "request": request.identity_mapping(),
            }
        ),
    )

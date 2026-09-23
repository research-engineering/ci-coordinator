from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import create_autospec

from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from production_admission_support import ROLLOUT_PROFILE_ID

from ci_coordinator.app import (
    CapacityPlanningService,
    DeterministicCandidatePlanner,
    DurableReconciliationRegistrar,
    DynamicPlanCommand,
    DynamicPlanService,
)
from ci_coordinator.app.candidate_planning import LoadedRepositoryContext, RepositoryContextProvider
from ci_coordinator.app.capacity_planning import (
    RequestRunnerSnapshotProvider,
    TrustedExecutionInputsProvider,
)
from ci_coordinator.app.production_request_evidence import ProductionRequestAuthorityService
from ci_coordinator.config_control.planning_projection import project_dynamic_ci_planning
from ci_coordinator.identity_admission import TrustedActionsRun
from ci_coordinator.kernel import SystemClock, hash_object
from ci_coordinator.persistence.config_epoch_unit_of_work import PostgresConfigEpochUnitOfWork
from ci_coordinator.persistence.production_cutover_unit_of_work import (
    PostgresProductionCutoverUnitOfWork,
)
from ci_coordinator.persistence.production_registration_adapter import (
    TransactionalProductionRegistrationStore,
)
from ci_coordinator.persistence.runtime_adapters import (
    TransactionalConfigEpochStore,
    TransactionalIssuanceStore,
    TransactionalReconciliationStore,
)
from ci_coordinator.persistence.runtime_state_unit_of_work import PostgresIngressIssuanceUnitOfWork
from ci_coordinator.persistence.shadow_reconciliation_unit_of_work import (
    PostgresShadowReconciliationUnitOfWork,
)
from ci_coordinator.plan_issuance import PlanRequest, SignedPlanIssuer, SignedPlanSigner
from ci_coordinator.plan_issuance.store import IssuanceStore
from ci_coordinator.production_admission.ports import (
    CurrentProductionSourcesReader,
    ProductionRegistrationPersistence,
)
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
from ci_coordinator.runner_capacity import TrustedExecutionInputs

from ._production_cutover_support import CutoverDatabase
from ._reconciliation_support import POLICY


@dataclass(frozen=True)
class ProductionPlanning:
    service: DynamicPlanService
    command: DynamicPlanCommand
    signer: SignedPlanSigner
    authorities: ProductionRequestAuthorityService


def production_planning(
    db: CutoverDatabase,
    *,
    run_id: int = 70,
    provider_available: bool = True,
    plan_ttl_seconds: int = 120,
    issuer_store: IssuanceStore | None = None,
    registration: ProductionRegistrationPersistence | None = None,
) -> ProductionPlanning:
    fixture = db.fixture
    clock = SystemClock()
    request = PlanRequest(
        schema_version="dynamic-ci-plan-request/v2",
        request_id=f"22:{run_id}:1",
        installation_id=11,
        repository_id=22,
        owner="example-org",
        repository="consumer",
        event_name="push",
        ref="refs/heads/master",
        base_sha="c" * 40,
        head_sha="b" * 40,
        execution_sha="b" * 40,
        workflow_run_id=run_id,
        run_attempt=1,
        pull_request_number=None,
        merge_group_head_ref=None,
    )
    identity = TrustedActionsRun(
        issuer="https://token.actions.githubusercontent.com",
        audience="https://coordinator.invalid/api/v1/dynamic-ci/plan",
        repository="example-org/consumer",
        repository_id=22,
        ref=request.ref,
        run_id=run_id,
        run_attempt=1,
        event_name="push",
        execution_sha=request.execution_sha,
        workflow_ref=fixture.candidate.workflow_ref,
        workflow_sha=fixture.candidate.workflow_sha,
        job_workflow_ref=fixture.candidate.job_workflow_ref,
        job_workflow_sha=fixture.candidate.job_workflow_sha,
        check_run_id=None,
        verified_at=fixture.now,
        claim_hash=hash_object(request.identity_mapping()),
    )
    sources = create_autospec(CurrentProductionSourcesReader, instance=True)
    sources.read.return_value = fixture.sources if provider_available else None
    authorities = ProductionRequestAuthorityService(
        authorities=db.store, verifier=fixture.verifier(), sources=sources
    )
    context = create_autospec(LoadedRepositoryContext, instance=True)
    context.planning_input = _planning_input(db, request)
    contexts = create_autospec(RepositoryContextProvider, instance=True)
    contexts.load.return_value = context
    inputs = create_autospec(TrustedExecutionInputsProvider, instance=True)
    inputs.load.return_value = TrustedExecutionInputs(
        fixture.producer.producer.target_artifacts.registry, None
    )
    runners = create_autospec(RequestRunnerSnapshotProvider, instance=True)
    runners.capture.side_effect = AssertionError("native jobs do not require runner capacity")
    signer = SignedPlanSigner(
        key_id="integration-plan",
        private_key_pem=fixture.signing_key.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        ),
        ttl_seconds=plan_ttl_seconds,
        clock=clock,
    )
    reconciler = TransactionalReconciliationStore(
        lambda: PostgresShadowReconciliationUnitOfWork(db.runtime), clock, POLICY
    )
    service = DynamicPlanService(
        active_epochs=TransactionalConfigEpochStore(
            lambda: PostgresConfigEpochUnitOfWork(db.runtime)
        ),
        candidates=DeterministicCandidatePlanner(contexts),
        issuer=SignedPlanIssuer(
            store=issuer_store
            if issuer_store is not None
            else TransactionalIssuanceStore(lambda: PostgresIngressIssuanceUnitOfWork(db.runtime)),
            signer=signer,
        ),
        reconciliation=DurableReconciliationRegistrar(
            reconciler,
            ROLLOUT_PROFILE_ID,
            production=registration
            if registration is not None
            else TransactionalProductionRegistrationStore(
                lambda: PostgresProductionCutoverUnitOfWork(db.runtime), POLICY
            ),
        ),
        capacity=CapacityPlanningService(snapshots=runners, inputs=inputs, clock=clock),
        enforcement_authority=authorities,
    )
    return ProductionPlanning(service, DynamicPlanCommand(request, identity), signer, authorities)


def _planning_input(db: CutoverDatabase, request: PlanRequest) -> PlanningInput:
    projection = project_dynamic_ci_planning(db.fixture.draft)
    assert projection is not None
    policy = PolicySnapshot.from_projection(projection)
    epoch = RepositoryEpoch(
        request.installation_id,
        request.repository_id,
        request.owner,
        request.repository,
        request.event_name,
        request.ref,
        request.base_sha,
        request.head_sha,
    )
    diff = build_diff_context(
        DiffBuildInput(
            request.base_sha,
            request.head_sha,
            (DiffFileChangeInput("src/service.py", "modified", None),),
            DiffSource("github", True, 1, 1, 1000),
        )
    )
    graph = build_dependency_graph(
        epoch,
        diff,
        policy,
        DependencyGraphArtifact(
            GraphProvenance(
                "configured", "dynamic-ci-graph/v1", "native-witness/v1", request.head_sha, True, ()
            ),
            (DependencyGraphNode("src/service.py", (), ("source",)),),
            policy.global_risk_paths,
        ),
    )
    return build_planning_input(epoch, diff, graph, policy)

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from package_b_support import (
    BASE_SHA,
    HEAD_SHA,
    make_execution_projection,
    make_input,
    make_native_execution_projection,
    make_policy,
)
from package_b_support import assert_deterministic_plan as deterministic_candidate
from production_admission_support import make_production_grant, synthetic_relation_binding
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ci_coordinator.config_control import (
    RepositoryScope,
    ValidatedEpochDraft,
    admit_policy_document,
)
from ci_coordinator.github_ingestion.ports import (
    PreparedDeliveryClaim,
    prepare_delivery_claim,
)
from ci_coordinator.github_ingestion.provenance import WebhookProvenance
from ci_coordinator.identity_admission import TrustedActionsRun
from ci_coordinator.kernel import FixedClock, canonical_json
from ci_coordinator.persistence import (
    PostgresIngressIssuanceUnitOfWork,
)
from ci_coordinator.persistence.runtime_adapters import TransactionalIssuanceStore
from ci_coordinator.persistence.schema import (
    active_config_epochs,
    config_epochs,
)
from ci_coordinator.plan_issuance import (
    AuthenticatedRunBinding,
    FullCiExecution,
    InMemoryIssuanceStore,
    IssuanceSaveResult,
    Issued,
    IssuedPlanRecord,
    PlanIssuanceContext,
    PlanRequest,
    RepositoryBinding,
    SignedPlanEnvelope,
    SignedPlanIssuer,
    SignedPlanPayload,
    SignedPlanSigner,
)
from ci_coordinator.planning_core import plan
from ci_coordinator.production_admission import (
    AuthorizedProductionAdmission,
    ProductionAdmissionRegistration,
    ProductionIssuanceGuard,
    project_candidate_subject,
    project_plan_subject,
)
from ci_coordinator.production_admission.current_evidence import admit_current_production_evidence
from ci_coordinator.reconciliation import ReconciliationSubject
from ci_coordinator.repo_context import DiffFileChangeInput
from ci_coordinator.verification_core import verify

from ._selected_generation_support import initialize_selected_generation, selected_current_sources

_NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
_REPOSITORY = RepositoryBinding(100, 200, "example-org", "ci-coordinator")
_AUTHENTICATED_RUN = AuthenticatedRunBinding(
    issuer="https://token.actions.githubusercontent.com",
    audience="ci-coordinator",
    repository="example-org/ci-coordinator",
    repository_id=200,
    ref="refs/pull/42/merge",
    run_id=7001,
    run_attempt=1,
    event_name="pull_request",
    workflow_ref="example-org/ci-coordinator/.github/workflows/ci.yml@refs/heads/main",
    workflow_sha=None,
    job_workflow_ref=None,
    job_workflow_sha=None,
    check_run_id=None,
    verified_at=_NOW,
    verifier_version="test-verifier/v1",
    claim_hash=None,
    execution_sha="c" * 40,
)


def _record(idempotency_key: str, request_id: str) -> IssuedPlanRecord:
    request = PlanRequest(
        schema_version="dynamic-ci-plan-request/v2",
        request_id=request_id,
        installation_id=100,
        repository_id=200,
        owner="example-org",
        repository="ci-coordinator",
        event_name="pull_request",
        ref="refs/pull/42/merge",
        base_sha="a" * 40,
        head_sha="b" * 40,
        workflow_run_id=7001,
        run_attempt=1,
        pull_request_number=42,
        execution_sha="c" * 40,
    )
    payload = SignedPlanPayload(
        schema_version="dynamic-ci-signed-plan-payload/v2",
        plan_id=f"fallback-{request_id}",
        repository=_REPOSITORY,
        request=request,
        authenticated_run=_AUTHENTICATED_RUN,
        verified_plan_id=None,
        production_admission_receipt_id=None,
        execution=FullCiExecution(mode="full-ci", reason="test_fallback"),
        verifier_version=None,
        fallback_reason="test_fallback",
    )
    envelope = SignedPlanEnvelope(
        schema_version="dynamic-ci-signed-plan-envelope/v1",
        key_id="test-key",
        algorithm="Ed25519",
        issued_at=_NOW,
        expires_at=_NOW + timedelta(minutes=5),
        payload=payload,
        signature="test-signature",
    )
    return IssuedPlanRecord.create(idempotency_key, request, envelope)


async def _selected_record_and_guard(
    now: datetime,
    config_draft: ValidatedEpochDraft,
    *,
    native_execution: bool = False,
    plan_ttl_seconds: int = 60,
) -> tuple[
    IssuedPlanRecord,
    ProductionIssuanceGuard,
    ProductionAdmissionRegistration,
]:
    planning_input = make_input(
        DiffFileChangeInput(path="docs/guide.md", status="modified"),
        config_epoch_id=config_draft.epoch_id,
        compiled_policy_hash=config_draft.epoch_hash,
    )
    policy = make_policy(
        config_epoch_id=config_draft.epoch_id,
        compiled_policy_hash=config_draft.epoch_hash,
    )
    verified = verify(
        planning_input,
        policy,
        deterministic_candidate(plan(planning_input, policy)),
    )
    execution_projection = (
        make_native_execution_projection(verified)
        if native_execution
        else make_execution_projection(verified, now=now)
    )
    request = PlanRequest(
        "dynamic-ci-plan-request/v2",
        "selected-request",
        100,
        200,
        "example-org",
        "ci-coordinator",
        "pull_request",
        "refs/pull/42/merge",
        BASE_SHA,
        HEAD_SHA,
        7001,
        1,
        42,
        execution_sha="c" * 40,
    )
    identity = TrustedActionsRun(
        "https://token.actions.githubusercontent.com",
        "ci-coordinator",
        "example-org/ci-coordinator",
        200,
        request.ref,
        request.workflow_run_id,
        request.run_attempt,
        request.event_name,
        "example-org/ci-coordinator/.github/workflows/ci.yml@refs/heads/main",
        "a" * 40,
        None,
        None,
        None,
        now,
        execution_sha=request.execution_sha,
    )
    sources = selected_current_sources()
    grant = make_production_grant(
        verified,
        execution_projection,
        identity,
        RepositoryScope(100, 200),
        now=now,
        relation=replace(
            synthetic_relation_binding(),
            workflow_manifest_digest=sources.workflows[0].manifest.manifest_digest,
            source_binding_digest=sources.workflows[0].source_binding.binding_digest,
            provider_authority_digest=sources.provider.authority_digest,
        ),
    )
    subject = ReconciliationSubject.create(
        installation_id=request.installation_id,
        repository_id=request.repository_id,
        event_name=request.event_name,
        ref=request.ref,
        base_sha=request.base_sha,
        head_sha=request.head_sha,
        workflow_run_id=request.workflow_run_id,
        run_attempt=request.run_attempt,
    )
    scope_grant = grant.scope_grant(RepositoryScope(100, 200))
    assert scope_grant is not None
    authorization = grant.authorize(
        project_plan_subject(
            RepositoryScope(100, 200),
            verified,
            identity,
            execution_projection.target_registry_hash,
            subject.subject_id,
        ),
        current_evidence=admit_current_production_evidence(
            candidate=project_candidate_subject(RepositoryScope(100, 200), verified, identity),
            scope_grant=scope_grant,
            scope_revision=3,
            database_started_at=now,
            workflow_sources=sources.workflows,
            provider_sources=sources.provider,
        ),
    )
    assert isinstance(authorization, AuthorizedProductionAdmission)
    signer = SignedPlanSigner(
        key_id="test-key",
        private_key_pem=Ed25519PrivateKey.generate().private_bytes(
            Encoding.PEM,
            PrivateFormat.PKCS8,
            NoEncryption(),
        ),
        ttl_seconds=plan_ttl_seconds,
        clock=FixedClock(now),
    )
    outcome = await SignedPlanIssuer(store=InMemoryIssuanceStore(), signer=signer).issue(
        request,
        identity,
        PlanIssuanceContext(
            _REPOSITORY,
            verified,
            authorization,
            execution_projection=execution_projection,
        ),
    )
    assert isinstance(outcome, Issued)
    return outcome.record, authorization.issuance_guard(), grant.registration


async def _initialize_selected_state(
    admin_engine: AsyncEngine,
    runtime_engine: AsyncEngine,
    draft: ValidatedEpochDraft,
    guard: ProductionIssuanceGuard,
    registration: ProductionAdmissionRegistration,
) -> None:
    async with admin_engine.begin() as connection:
        await _insert_epoch(connection, draft)
        await connection.execute(
            insert(active_config_epochs).values(
                installation_id=guard.scope.installation_id,
                repository_id=guard.scope.repository_id,
                epoch_id=guard.config_epoch_id,
                revision=1,
            )
        )
    async with PostgresIngressIssuanceUnitOfWork(runtime_engine) as unit_of_work:
        await unit_of_work.production_admissions.register(registration)
        await unit_of_work.commit()
    await initialize_selected_generation(admin_engine, runtime_engine, guard)


async def _save_selected(
    engine: AsyncEngine,
    record: IssuedPlanRecord,
    guard: ProductionIssuanceGuard,
) -> IssuanceSaveResult:
    return await TransactionalIssuanceStore(lambda: PostgresIngressIssuanceUnitOfWork(engine)).save(
        record, guard
    )


async def _insert_epoch(
    connection: AsyncConnection,
    draft: ValidatedEpochDraft,
    *,
    source_bytes: bytes | None = None,
) -> None:
    await connection.execute(
        insert(config_epochs).values(
            epoch_id=draft.epoch_id,
            installation_id=draft.scope.installation_id,
            repository_id=draft.scope.repository_id,
            source_format=draft.source_format,
            source_bytes=draft.source_bytes if source_bytes is None else source_bytes,
            normalized_document_bytes=draft.normalized_document_bytes,
            compiled_policy_bytes=draft.compiled_policy_bytes,
            document_schema_id=draft.document_schema_id,
            document_profile_id=draft.document_profile_id,
            semantic_profile_id=draft.semantic_profile_id,
            compiled_schema_id=draft.compiled_schema_id,
            producer_resource_profile_id=draft.producer_resource_profile_id,
            producer_byte_profile_id=draft.producer_byte_profile_id,
            producer_feasibility_profile_id=draft.producer_feasibility_profile_id,
            source_hash=draft.source_hash,
            document_hash=draft.document_hash,
            epoch_hash=draft.epoch_hash,
        )
    )


def _config_draft(default_branch: str) -> ValidatedEpochDraft:
    source = canonical_json(
        {
            "schemaVersion": "ci-repository-policy/v1",
            "repository": {
                "installationId": 100,
                "repositoryId": 200,
                "owner": "example-org",
                "name": "ci-coordinator",
                "defaultBranch": default_branch,
                "dynamicCi": None,
                "rules": [
                    {
                        "name": "default-branch",
                        "on": {"event": "push", "branches": [default_branch]},
                        "mode": "observe",
                        "timing": {
                            "expectedSignalTimeoutSeconds": 3600,
                            "absenceVerificationWindowSeconds": 300,
                            "absencePollLookbackSeconds": 3600,
                            "lateFindingWindowSeconds": 86400,
                            "mutableDecisionWindowSeconds": 300,
                        },
                        "expectedSignals": [
                            {
                                "kind": "workflow",
                                "name": "CI",
                                "workflowFile": "ci.yml",
                                "source": "native",
                                "requiredConclusion": "success",
                                "required": True,
                            }
                        ],
                        "omittedSignals": [],
                    }
                ],
            },
        }
    )
    result = admit_policy_document(source, "json")
    assert type(result) is ValidatedEpochDraft
    return result


def _delivery_claim(
    delivery_id: str,
    body_sha256: str,
    verified_at: datetime,
) -> PreparedDeliveryClaim:
    return prepare_delivery_claim(
        WebhookProvenance(
            delivery_id=delivery_id,
            event_name="push",
            body_sha256=body_sha256,
            verified_at=verified_at,
            verifier_version="test-verifier/v1",
        )
    )

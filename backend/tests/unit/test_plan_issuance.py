from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
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
from package_b_support import assert_deterministic_plan as _candidate
from production_admission_support import make_production_grant

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.identity_admission import TrustedActionsRun
from ci_coordinator.kernel import FixedClock
from ci_coordinator.plan_issuance import (
    FullCiExecution,
    InMemoryIssuanceStore,
    IssuanceGuardRejected,
    IssuanceRejected,
    IssuanceSaveResult,
    Issued,
    IssuedPlanRecord,
    PlanIssuanceContext,
    PlanRequest,
    RepositoryBinding,
    SelectedExecution,
    SignedNativeProfileExecution,
    SignedPlanIssuer,
    SignedPlanSigner,
    SignedProfileExecution,
    parse_plan_request,
    production_guard_binds_record,
    verify_signed_plan,
)
from ci_coordinator.planning_core import plan
from ci_coordinator.production_admission import (
    AuthorizedProductionAdmission,
    ProductionIssuanceGuard,
    project_plan_subject,
)
from ci_coordinator.reconciliation import ReconciliationSubject
from ci_coordinator.repo_context import DiffFileChangeInput
from ci_coordinator.verification_core import VerifiedPlan, verify

NOW = datetime(2026, 7, 14, tzinfo=UTC)
EXECUTION_SHA = "c" * 40


def test_signed_issuance_is_idempotent_and_payload_tampering_fails() -> None:
    input = make_input(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    verified = verify(input, make_policy(), _candidate(plan(input, make_policy())))
    signer = SignedPlanSigner(
        key_id="test-key",
        private_key_pem=Ed25519PrivateKey.generate().private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        ),
        ttl_seconds=60,
        clock=FixedClock(NOW),
    )
    issuer = SignedPlanIssuer(store=InMemoryIssuanceStore(), signer=signer)
    request = PlanRequest(
        "dynamic-ci-plan-request/v2",
        "request-1",
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
        execution_sha=EXECUTION_SHA,
    )
    identity = TrustedActionsRun(
        "https://token.actions.githubusercontent.com",
        "ci-coordinator",
        "example-org/ci-coordinator",
        200,
        "refs/pull/42/merge",
        7001,
        1,
        "pull_request",
        "workflow@ref",
        None,
        None,
        None,
        None,
        NOW,
        execution_sha=EXECUTION_SHA,
    )
    context = PlanIssuanceContext(
        RepositoryBinding(100, 200, "example-org", "ci-coordinator"),
        verified,
        _admission(verified, identity),
        execution_projection=make_execution_projection(verified, now=NOW),
    )

    first = asyncio.run(issuer.issue(request, identity, context))
    second = asyncio.run(issuer.issue(request, identity, context))

    assert isinstance(first, Issued) and first.duplicate is False
    assert isinstance(second, Issued) and second.duplicate is True
    assert (
        verify_signed_plan(
            first.record.envelope,
            public_key_pem=signer.public_key_pem(),
            clock=FixedClock(NOW),
        )
        is None
    )
    assert first.record.envelope.payload.authenticated_run.workflow_ref == "workflow@ref"
    assert first.record.envelope.payload.request.execution_sha == EXECUTION_SHA
    assert first.record.envelope.payload.authenticated_run.execution_sha == EXECUTION_SHA
    execution = first.record.envelope.payload.execution
    assert isinstance(execution, SelectedExecution)
    assert execution.deterministic_plan_id == verified.source_plan.plan_id
    assert execution.catalog_hash == verified.catalog.catalog_hash
    assert execution.selected_obligation_ids == ("docs-lint", "required-baseline")
    assert execution.selected_witness_ids == (
        "baseline-witness",
        "docs-witness",
        "shared-quality",
    )
    assert len(execution.profiles) == 1
    profile = execution.profiles[0]
    assert isinstance(profile, SignedProfileExecution)
    assert profile.max_parallel == 1
    assert profile.shards[0].test_ids == execution.selected_witness_ids
    tampered_run = replace(
        first.record.envelope.payload.authenticated_run,
        verifier_version="tampered",
    )
    tampered_envelope = replace(
        first.record.envelope,
        payload=replace(first.record.envelope.payload, authenticated_run=tampered_run),
    )
    assert (
        verify_signed_plan(
            tampered_envelope,
            public_key_pem=signer.public_key_pem(),
            clock=FixedClock(NOW),
        )
        == "signed_plan_signature_invalid"
    )
    assert (
        verify_signed_plan(
            replace(first.record.envelope, signature="invalid"),
            public_key_pem=signer.public_key_pem(),
            clock=FixedClock(NOW),
        )
        == "signed_plan_signature_invalid"
    )

    assert (
        verify_signed_plan(
            second.record.envelope,
            public_key_pem=signer.public_key_pem(),
            clock=FixedClock(NOW + timedelta(seconds=60)),
        )
        == "signed_plan_expired"
    )

    mismatched_request = asyncio.run(
        issuer.issue(
            replace(request, head_sha="e" * 40),
            identity,
            context,
        )
    )
    assert isinstance(mismatched_request, Issued)
    assert (
        mismatched_request.record.envelope.payload.fallback_reason
        == "verified_plan_request_mismatch"
    )
    rejected = asyncio.run(issuer.issue(request, replace(identity, run_attempt=2), context))
    assert isinstance(rejected, IssuanceRejected)
    execution_mismatch = asyncio.run(
        issuer.issue(request, replace(identity, execution_sha="d" * 40), context)
    )
    assert isinstance(execution_mismatch, IssuanceRejected)


def test_request_contract_rejects_an_invalid_direct_constructor_call() -> None:
    with pytest.raises(ValueError, match="git SHA"):
        PlanRequest(
            "dynamic-ci-plan-request/v2",
            "request-1",
            100,
            200,
            "example-org",
            "ci-coordinator",
            "pull_request",
            "refs/pull/42/merge",
            "not-a-sha",
            HEAD_SHA,
            7001,
            1,
            execution_sha=EXECUTION_SHA,
        )


def test_request_contract_requires_event_specific_identity() -> None:
    with pytest.raises(ValueError, match="pull request events"):
        PlanRequest(
            "dynamic-ci-plan-request/v2",
            "request-1",
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
            execution_sha=EXECUTION_SHA,
        )

    with pytest.raises(ValueError, match="cannot carry merge group identity"):
        PlanRequest(
            "dynamic-ci-plan-request/v2",
            "request-1",
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
            "refs/heads/gh-readonly-queue/main/pr-42",
            execution_sha=EXECUTION_SHA,
        )

    with pytest.raises(ValueError, match="ref does not match"):
        PlanRequest(
            "dynamic-ci-plan-request/v2",
            "request-1",
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
            43,
            execution_sha=EXECUTION_SHA,
        )

    with pytest.raises(ValueError, match="cannot carry pull request identity"):
        PlanRequest(
            "dynamic-ci-plan-request/v2",
            "request-1",
            100,
            200,
            "example-org",
            "ci-coordinator",
            "merge_group",
            "refs/heads/gh-readonly-queue/main/pr-42",
            BASE_SHA,
            HEAD_SHA,
            7001,
            1,
            42,
            "refs/heads/gh-readonly-queue/main/pr-42",
            execution_sha=EXECUTION_SHA,
        )


def test_request_parser_rejects_unknown_and_event_incompatible_fields() -> None:
    raw_request = {
        "schemaVersion": "dynamic-ci-plan-request/v2",
        "requestId": "request-1",
        "installationId": 100,
        "repositoryId": 200,
        "owner": "example-org",
        "repository": "ci-coordinator",
        "eventName": "pull_request",
        "ref": "refs/pull/42/merge",
        "baseSha": BASE_SHA,
        "headSha": HEAD_SHA,
        "executionSha": EXECUTION_SHA,
        "workflowRunId": 7001,
        "runAttempt": 1,
        "pullRequestNumber": 42,
    }

    assert isinstance(parse_plan_request(raw_request), PlanRequest)
    assert (
        parse_plan_request({**raw_request, "pullRequestNumber": 43})
        == "pull request ref does not match its pull request number"
    )
    assert parse_plan_request({**raw_request, "unexpected": True}) == "plan_request_unknown_fields"
    assert (
        parse_plan_request(
            {**raw_request, "mergeGroupHeadRef": "refs/heads/gh-readonly-queue/main/pr-42"}
        )
        == "pull request events cannot carry merge group identity"
    )


def test_issuance_binds_a_selected_plan_to_the_request_epoch_and_enforcement_mode() -> None:
    input = make_input(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy()
    verified = verify(input, policy, _candidate(plan(input, policy)))
    signer = SignedPlanSigner(
        key_id="test-key",
        private_key_pem=Ed25519PrivateKey.generate().private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        ),
        ttl_seconds=60,
        clock=FixedClock(NOW),
    )
    issuer = SignedPlanIssuer(store=InMemoryIssuanceStore(), signer=signer)
    request = PlanRequest(
        "dynamic-ci-plan-request/v2",
        "request-1",
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
        execution_sha=EXECUTION_SHA,
    )
    identity = TrustedActionsRun(
        "https://token.actions.githubusercontent.com",
        "ci-coordinator",
        "example-org/ci-coordinator",
        200,
        "refs/pull/42/merge",
        7001,
        1,
        "pull_request",
        "workflow@ref",
        None,
        None,
        None,
        None,
        NOW,
        execution_sha=EXECUTION_SHA,
    )
    repository = RepositoryBinding(100, 200, "example-org", "ci-coordinator")
    selected = asyncio.run(
        issuer.issue(
            request,
            identity,
            PlanIssuanceContext(
                repository,
                verified,
                _admission(verified, identity),
                execution_projection=make_execution_projection(verified, now=NOW),
            ),
        )
    )
    mismatch_issuer = SignedPlanIssuer(store=InMemoryIssuanceStore(), signer=signer)
    mismatched = asyncio.run(
        mismatch_issuer.issue(
            replace(request, head_sha="c" * 40),
            identity,
            PlanIssuanceContext(
                repository,
                verified,
                _admission(verified, identity),
                execution_projection=make_execution_projection(verified, now=NOW),
            ),
        )
    )
    disabled = asyncio.run(
        issuer.issue(request, identity, PlanIssuanceContext(repository, verified, None))
    )
    forced = asyncio.run(
        SignedPlanIssuer(store=InMemoryIssuanceStore(), signer=signer).issue(
            request,
            identity,
            PlanIssuanceContext(
                repository,
                verified,
                _admission(verified, identity),
                forced_fallback_reason="operator_override",
            ),
        )
    )

    assert isinstance(selected, Issued)
    selected_execution = selected.record.envelope.payload.execution
    assert isinstance(selected_execution, SelectedExecution)
    selected_profile = selected_execution.profiles[0]
    assert isinstance(selected_profile, SignedProfileExecution)
    assert selected_profile.max_parallel == 1
    assert isinstance(mismatched, Issued)
    assert mismatched.record.envelope.payload.fallback_reason == "verified_plan_request_mismatch"
    assert isinstance(disabled, Issued)
    assert disabled.record.envelope.payload.fallback_reason == "dynamic_enforcement_disabled"
    assert isinstance(disabled.record.envelope.payload.execution, FullCiExecution)
    assert isinstance(forced, Issued)
    assert forced.record.envelope.payload.fallback_reason == "operator_override"
    with pytest.raises(ValueError, match="merge group events"):
        PlanRequest(
            "dynamic-ci-plan-request/v2",
            "request-1",
            100,
            200,
            "example-org",
            "ci-coordinator",
            "merge_group",
            "refs/heads/gh-readonly-queue/main/pr-42",
            BASE_SHA,
            HEAD_SHA,
            7001,
            1,
            execution_sha=EXECUTION_SHA,
        )


def test_execution_projection_must_bind_the_exact_verified_plan() -> None:
    input = make_input(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy()
    verified = verify(input, policy, _candidate(plan(input, policy)))
    other_input = make_input(DiffFileChangeInput(path="src/service.py", status="modified"))
    other_verified = verify(
        other_input,
        policy,
        _candidate(plan(other_input, policy)),
    )

    with pytest.raises(ValueError, match="must bind"):
        PlanIssuanceContext(
            RepositoryBinding(100, 200, "example-org", "ci-coordinator"),
            other_verified,
            None,
            execution_projection=make_execution_projection(verified, now=NOW),
        )


def test_native_execution_projects_static_jobs_without_synthetic_shards() -> None:
    input = make_input(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy()
    verified = verify(input, policy, _candidate(plan(input, policy)))
    projection = make_native_execution_projection(verified)

    execution = SelectedExecution.project(verified, projection)

    assert execution.execution_kind == "native-job-set"
    assert execution.workflow_path == ".github/workflows/full-check.yml"
    assert execution.test_manifest_id is None
    assert all(type(profile) is SignedNativeProfileExecution for profile in execution.profiles)
    assert execution.gate_provider_signal.job_id == "pr-gate"
    with pytest.raises(ValueError, match="gate does not bind"):
        replace(execution, workflow_path=".github/workflows/other.yml")


def test_production_authority_bounds_expiry_and_revalidation_falls_back() -> None:
    input = make_input(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy()
    verified = verify(input, policy, _candidate(plan(input, policy)))
    execution_projection = make_execution_projection(verified, now=NOW)
    request = _plan_request()
    identity = _trusted_identity()
    grant = make_production_grant(
        verified,
        execution_projection,
        identity,
        RepositoryScope(100, 200),
        now=NOW,
        expires_at=NOW + timedelta(seconds=30),
    )
    authorization = grant.authorize(
        project_plan_subject(
            RepositoryScope(100, 200),
            verified,
            identity,
            execution_projection.target_registry_hash,
            _reconciliation_subject_id(request),
        )
    )
    assert isinstance(authorization, AuthorizedProductionAdmission)
    signer = SignedPlanSigner(
        key_id="test-key",
        private_key_pem=Ed25519PrivateKey.generate().private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()
        ),
        ttl_seconds=60,
        clock=FixedClock(NOW),
    )
    store = InMemoryIssuanceStore()
    selected = asyncio.run(
        SignedPlanIssuer(store=store, signer=signer).issue(
            request,
            identity,
            PlanIssuanceContext(
                RepositoryBinding(100, 200, "example-org", "ci-coordinator"),
                verified,
                authorization,
                execution_projection=execution_projection,
            ),
        )
    )
    rotated_grant = make_production_grant(
        verified,
        execution_projection,
        identity,
        RepositoryScope(100, 200),
        now=NOW,
        expires_at=NOW + timedelta(seconds=30),
    )
    rotated_authorization = rotated_grant.authorize(
        project_plan_subject(
            RepositoryScope(100, 200),
            verified,
            identity,
            execution_projection.target_registry_hash,
            _reconciliation_subject_id(request),
        )
    )
    assert isinstance(rotated_authorization, AuthorizedProductionAdmission)
    rotated = asyncio.run(
        SignedPlanIssuer(store=store, signer=signer).issue(
            request,
            identity,
            PlanIssuanceContext(
                RepositoryBinding(100, 200, "example-org", "ci-coordinator"),
                verified,
                rotated_authorization,
                execution_projection=execution_projection,
            ),
        )
    )
    rejected = asyncio.run(
        SignedPlanIssuer(store=_RejectingProductionStore(), signer=signer).issue(
            request,
            identity,
            PlanIssuanceContext(
                RepositoryBinding(100, 200, "example-org", "ci-coordinator"),
                verified,
                authorization,
                execution_projection=execution_projection,
            ),
        )
    )

    assert isinstance(selected, Issued)
    assert selected.duplicate is False
    assert selected.record.envelope.expires_at == authorization.not_after
    guard = authorization.issuance_guard()
    assert production_guard_binds_record(guard, selected.record)
    payload = selected.record.envelope.payload
    assert isinstance(payload.execution, SelectedExecution)
    guard_mutants = (
        replace(
            payload,
            authenticated_run=replace(payload.authenticated_run, workflow_ref="other@ref"),
        ),
        replace(payload, execution=replace(payload.execution, catalog_hash="e" * 64)),
        replace(payload, request=replace(payload.request, head_sha="e" * 40)),
    )
    assert all(
        not production_guard_binds_record(
            guard,
            replace(
                selected.record,
                envelope=replace(selected.record.envelope, payload=mutant),
            ),
        )
        for mutant in guard_mutants
    )
    assert isinstance(rotated, Issued)
    assert rotated.duplicate is False
    assert rotated.record.idempotency_key != selected.record.idempotency_key
    assert rotated_authorization.authority_id != authorization.authority_id
    assert isinstance(rejected, Issued)
    assert rejected.record.envelope.payload.fallback_reason == "production_revalidation_failed"
    assert rejected.record.envelope.payload.production_admission_receipt_id is None


class _RejectingProductionStore:
    async def save(
        self,
        attempted: IssuedPlanRecord,
        guard: ProductionIssuanceGuard | None = None,
    ) -> IssuanceSaveResult:
        if guard is not None:
            return IssuanceGuardRejected("config_epoch_changed")
        return None


def _plan_request() -> PlanRequest:
    return PlanRequest(
        "dynamic-ci-plan-request/v2",
        "request-1",
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
        execution_sha=EXECUTION_SHA,
    )


def _trusted_identity() -> TrustedActionsRun:
    return TrustedActionsRun(
        "https://token.actions.githubusercontent.com",
        "ci-coordinator",
        "example-org/ci-coordinator",
        200,
        "refs/pull/42/merge",
        7001,
        1,
        "pull_request",
        "workflow@ref",
        None,
        None,
        None,
        None,
        NOW,
        execution_sha=EXECUTION_SHA,
    )


def _admission(
    verified: VerifiedPlan,
    identity: TrustedActionsRun,
) -> AuthorizedProductionAdmission:
    grant = make_production_grant(
        verified,
        make_execution_projection(verified, now=NOW),
        identity,
        RepositoryScope(100, 200),
        now=NOW,
    )
    authorization = grant.authorize(
        project_plan_subject(
            RepositoryScope(100, 200),
            verified,
            identity,
            make_execution_projection(verified, now=NOW).target_registry_hash,
            _reconciliation_subject_id(_plan_request()),
        )
    )
    assert isinstance(authorization, AuthorizedProductionAdmission)
    return authorization


def _reconciliation_subject_id(request: PlanRequest) -> str:
    return ReconciliationSubject.create(
        installation_id=request.installation_id,
        repository_id=request.repository_id,
        event_name=request.event_name,
        ref=request.ref,
        base_sha=request.base_sha,
        head_sha=request.head_sha,
        workflow_run_id=request.workflow_run_id,
        run_attempt=request.run_attempt,
    ).subject_id

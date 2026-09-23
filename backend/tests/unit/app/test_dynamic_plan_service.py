from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import replace
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import cast
from unittest.mock import create_autospec

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from package_b_support import (
    make_adapter_files,
    make_catalog,
    make_execution_projection,
    make_input,
    make_native_execution_projection,
    make_policy,
)
from production_admission_support import make_production_grant
from prometheus_support import prometheus_samples

from ci_coordinator.app import (
    DynamicPlanCommand,
    DynamicPlanService,
    ExecutionPlanningResult,
    PlanningOverrideDecision,
)
from ci_coordinator.app.reconciliation_registration import DurableReconciliationRegistrar
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs import ActiveConfigEpochSnapshot
from ci_coordinator.execution_orchestration import (
    LOCAL_PLAN_REQUEST_WORKFLOW_PATH,
    LOCAL_PLAN_REQUEST_WORKFLOW_REF,
    ProviderSignal,
)
from ci_coordinator.identity_admission import TrustedActionsRun
from ci_coordinator.kernel import FixedClock
from ci_coordinator.observability import (
    RuntimeDiagnosticObserver,
    RuntimeMetrics,
    StructuredEventLogger,
)
from ci_coordinator.plan_issuance import (
    FullCiExecution,
    InMemoryIssuanceStore,
    IssuanceRejected,
    Issued,
    PlanIssuanceContext,
    PlanRequest,
    SelectedExecution,
    SignedPlanIssuer,
    SignedPlanSigner,
    SignedProfileExecution,
)
from ci_coordinator.planning_core import DeterministicPlan, plan
from ci_coordinator.production_admission import (
    ProductionAdmissionGrant,
    ProductionCandidateSubject,
    ProductionIssuanceGuard,
)
from ci_coordinator.production_admission.request_authority import PreparedProductionAuthority
from ci_coordinator.reconciliation import ReconciliationPersistence
from ci_coordinator.repo_context import DiffFileChangeInput
from ci_coordinator.runner_capacity import TrustedExecutionProjection
from ci_coordinator.validation_contract import ValidationCatalog
from ci_coordinator.verification_core import VerifiedPlan, verify

NOW = datetime(2026, 7, 14, tzinfo=UTC)
_PLAN_REQUEST_WORKFLOW_REF = (
    "example-org/ci-coordinator/.github/workflows/"
    "trusted-plan-request.yml@1111111111111111111111111111111111111111"
)


class _ActiveEpochs:
    def __init__(
        self,
        snapshot: ActiveConfigEpochSnapshot | None,
        error: Exception | None = None,
    ) -> None:
        self.snapshot = snapshot
        self.error = error
        self.scopes: list[RepositoryScope] = []

    async def load_active(self, scope: RepositoryScope) -> ActiveConfigEpochSnapshot | None:
        self.scopes.append(scope)
        if self.error is not None:
            raise self.error
        return self.snapshot


class _Candidates:
    def __init__(self, result: VerifiedPlan | None = None) -> None:
        self.result = result
        self.calls = 0

    async def build_candidate(
        self,
        command: DynamicPlanCommand,
        active_epoch: ActiveConfigEpochSnapshot,
    ) -> VerifiedPlan | None:
        del command, active_epoch
        self.calls += 1
        return self.result


class _Overrides:
    async def resolve(self, command: DynamicPlanCommand) -> PlanningOverrideDecision:
        del command
        return PlanningOverrideDecision(True, "operator_override")


class _UnavailableOverrides:
    async def resolve(self, command: DynamicPlanCommand) -> PlanningOverrideDecision:
        del command
        raise RuntimeError("override storage unavailable")


class _Capacity:
    def __init__(
        self,
        result: ExecutionPlanningResult | BaseException | None,
    ) -> None:
        self._result = result
        self.calls = 0
        self.authority_shas: list[str | None] = []

    async def project(
        self,
        verified_plan: VerifiedPlan,
        request: PlanRequest,
        execution_authority_sha: str | None,
    ) -> ExecutionPlanningResult | None:
        del verified_plan, request
        self.calls += 1
        self.authority_shas.append(execution_authority_sha)
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class _RecordingIssuer:
    def __init__(self) -> None:
        self.context: PlanIssuanceContext | None = None

    async def issue(
        self,
        request: PlanRequest,
        identity: TrustedActionsRun,
        context: PlanIssuanceContext,
    ) -> IssuanceRejected:
        del request, identity
        self.context = context
        return IssuanceRejected("recorded")


class _Reconciliation:
    def __init__(self, admitted: bool | BaseException) -> None:
        self._admitted = admitted
        self.calls: list[tuple[bool, tuple[ProviderSignal, ...]]] = []

    async def register(
        self,
        command: DynamicPlanCommand,
        verified_plan: VerifiedPlan,
        *,
        full_ci: bool,
        provider_signals: tuple[ProviderSignal, ...],
        production_guard: ProductionIssuanceGuard | None = None,
    ) -> bool:
        del command, verified_plan, production_guard
        self.calls.append((full_ci, provider_signals))
        if isinstance(self._admitted, BaseException):
            raise self._admitted
        return self._admitted


def test_missing_active_epoch_issues_a_durable_full_ci_fallback() -> None:
    active_epochs = _ActiveEpochs(None)
    candidates = _Candidates()
    service = DynamicPlanService(
        active_epochs=active_epochs,
        candidates=candidates,
        issuer=_issuer(),
    )

    result = asyncio.run(service.request_dynamic_plan(DynamicPlanCommand(_request(), _identity())))

    assert isinstance(result, Issued)
    assert result.record.envelope.payload.fallback_reason == "verified_plan_unavailable"
    assert active_epochs.scopes == [RepositoryScope(100, 200)]
    assert candidates.calls == 0


def test_operator_override_skips_epoch_and_candidate_work() -> None:
    active_epochs = _ActiveEpochs(None)
    candidates = _Candidates()
    service = DynamicPlanService(
        active_epochs=active_epochs,
        candidates=candidates,
        issuer=_issuer(),
        overrides=_Overrides(),
    )

    result = asyncio.run(service.request_dynamic_plan(DynamicPlanCommand(_request(), _identity())))

    assert isinstance(result, Issued)
    assert result.record.envelope.payload.fallback_reason == "operator_override"
    assert active_epochs.scopes == []
    assert candidates.calls == 0


def test_unavailable_optimization_inputs_still_issue_signed_full_ci() -> None:
    for active_epochs, overrides, reason, stage, unavailable_reason in (
        (
            _ActiveEpochs(None, RuntimeError("config storage unavailable")),
            None,
            "verified_plan_unavailable",
            "active_epoch",
            "active_epoch_exception",
        ),
        (
            _ActiveEpochs(None),
            _UnavailableOverrides(),
            "override_state_unavailable",
            "override",
            "override_exception",
        ),
    ):
        metrics = RuntimeMetrics()
        service = DynamicPlanService(
            active_epochs=active_epochs,
            candidates=_Candidates(),
            issuer=_issuer(),
            overrides=overrides,
            runtime_metrics=metrics,
        )

        result = asyncio.run(
            service.request_dynamic_plan(DynamicPlanCommand(_request(), _identity()))
        )

        assert isinstance(result, Issued)
        assert result.record.envelope.payload.fallback_reason == reason
        assert (
            prometheus_samples(metrics)[
                (
                    "ci_coordinator_planning_unavailable_total",
                    (("reason", unavailable_reason), ("stage", stage)),
                )
            ]
            == 1
        )


def test_enforcing_plan_passes_exact_closed_execution_projection_to_issuer() -> None:
    verified = _verified_plan()
    projected = make_execution_projection(verified, free_slots=2, now=NOW)
    capacity = _Capacity(_planning(projected))
    issuer = _RecordingIssuer()
    service = DynamicPlanService(
        active_epochs=_ActiveEpochs(cast(ActiveConfigEpochSnapshot, object())),
        candidates=_Candidates(verified),
        issuer=issuer,
        reconciliation=_Reconciliation(True),
        capacity=capacity,
        enforcement_authority=_authority(verified),
    )

    result = asyncio.run(service.request_dynamic_plan(DynamicPlanCommand(_request(), _identity())))

    assert isinstance(result, IssuanceRejected)
    assert issuer.context is not None
    assert issuer.context.execution_projection is projected
    assert projected.shard_plan is not None
    assert projected.shard_plan.manifest.selected_witness_ids == tuple(
        witness.witness_id for witness in verified.selected_witnesses
    )
    assert capacity.calls == 1


def test_execution_adapter_uses_the_caller_workflow_sha_not_the_called_workflow_sha() -> None:
    verified = _verified_plan()
    projected = make_execution_projection(verified, free_slots=2, now=NOW)
    identity = replace(
        _identity(),
        workflow_sha="c" * 40,
        job_workflow_ref=_PLAN_REQUEST_WORKFLOW_REF,
        job_workflow_sha="1" * 40,
    )
    capacity = _Capacity(_planning(projected))
    service = DynamicPlanService(
        active_epochs=_ActiveEpochs(cast(ActiveConfigEpochSnapshot, object())),
        candidates=_Candidates(verified),
        issuer=_RecordingIssuer(),
        reconciliation=_Reconciliation(True),
        capacity=capacity,
        enforcement_authority=_authority(verified, identity=identity),
    )

    asyncio.run(service.request_dynamic_plan(DynamicPlanCommand(_request(), identity)))

    assert capacity.authority_shas == ["c" * 40]


def test_enforcing_plan_registers_exact_selected_execution_signals_before_issuance() -> None:
    verified = _verified_plan()
    projected = make_execution_projection(verified, free_slots=2, now=NOW)
    reconciliation = _Reconciliation(True)
    issuer = _RecordingIssuer()
    service = DynamicPlanService(
        active_epochs=_ActiveEpochs(cast(ActiveConfigEpochSnapshot, object())),
        candidates=_Candidates(verified),
        issuer=issuer,
        reconciliation=reconciliation,
        capacity=_Capacity(_planning(projected)),
        enforcement_authority=_authority(verified),
    )

    result = asyncio.run(service.request_dynamic_plan(DynamicPlanCommand(_request(), _identity())))

    assert isinstance(result, IssuanceRejected)
    expected_execution = SelectedExecution.project(verified, projected)
    expected_signals: list[ProviderSignal] = []
    for profile in expected_execution.profiles:
        assert isinstance(profile, SignedProfileExecution)
        expected_signals.extend(shard.provider_signal for shard in profile.shards)
    assert reconciliation.calls == [
        (
            False,
            tuple(expected_signals),
        )
    ]
    assert issuer.context is not None
    assert issuer.context.verified_plan is verified
    assert issuer.context.execution_projection is projected


@pytest.mark.parametrize("matching_identity", [False, True])
def test_local_requester_identity_is_checked_before_public_selected_issuance(
    matching_identity: bool,
) -> None:
    verified = _verified_plan()
    projected = make_native_execution_projection(verified)
    registry = projected.target_registry
    registry = replace(
        registry,
        workflows=(
            replace(
                registry.workflows[0], plan_request_workflow_ref=LOCAL_PLAN_REQUEST_WORKFLOW_REF
            ),
        ),
        adapter_files=make_adapter_files(projected.workflow_path, LOCAL_PLAN_REQUEST_WORKFLOW_PATH),
    )
    projected = replace(projected, target=replace(projected.target, target_registry=registry))
    trusted = replace(
        _identity(workflow_path=projected.workflow_path),
        workflow_sha="1" * 40,
        job_workflow_ref=f"example-org/ci-coordinator/{LOCAL_PLAN_REQUEST_WORKFLOW_PATH}@refs/heads/master",
        job_workflow_sha="1" * 40,
    )
    identity = trusted if matching_identity else replace(trusted, job_workflow_sha="2" * 40)
    reconciliation = _Reconciliation(True)
    issuer = _RecordingIssuer()
    service = DynamicPlanService(
        active_epochs=_ActiveEpochs(cast(ActiveConfigEpochSnapshot, object())),
        candidates=_Candidates(verified),
        issuer=issuer,
        reconciliation=reconciliation,
        capacity=_Capacity(_planning(projected)),
        enforcement_authority=_authority(verified, projected, identity=trusted),
    )

    asyncio.run(service.request_dynamic_plan(DynamicPlanCommand(_request(), identity)))

    assert issuer.context is not None
    if matching_identity:
        assert issuer.context.execution_projection is projected
        assert reconciliation.calls
    else:
        assert issuer.context.execution_projection is None
        assert reconciliation.calls == []


def test_native_execution_registers_only_its_declared_aggregate_gate() -> None:
    verified = _verified_plan()
    projected = make_native_execution_projection(verified)
    identity = _identity(workflow_path=projected.workflow_path)
    reconciliation = _Reconciliation(True)
    issuer = _RecordingIssuer()
    service = DynamicPlanService(
        active_epochs=_ActiveEpochs(cast(ActiveConfigEpochSnapshot, object())),
        candidates=_Candidates(verified),
        issuer=issuer,
        reconciliation=reconciliation,
        capacity=_Capacity(_planning(projected)),
        enforcement_authority=_authority(verified, projected, identity=identity),
    )

    result = asyncio.run(service.request_dynamic_plan(DynamicPlanCommand(_request(), identity)))

    assert isinstance(result, IssuanceRejected)
    execution = SelectedExecution.project(verified, projected)
    assert execution.execution_kind == "native-job-set"
    assert execution.test_manifest_id is None
    assert reconciliation.calls == [(False, (execution.gate_provider_signal,))]
    assert issuer.context is not None
    assert issuer.context.execution_projection is projected


def test_reconciliation_registration_failure_withholds_selected_execution() -> None:
    verified = _verified_plan()
    projected = make_execution_projection(verified, free_slots=2, now=NOW)
    reconciliation = _Reconciliation(False)
    issuer = _RecordingIssuer()
    service = DynamicPlanService(
        active_epochs=_ActiveEpochs(cast(ActiveConfigEpochSnapshot, object())),
        candidates=_Candidates(verified),
        issuer=issuer,
        reconciliation=reconciliation,
        capacity=_Capacity(_planning(projected)),
        enforcement_authority=_authority(verified),
    )

    result = asyncio.run(service.request_dynamic_plan(DynamicPlanCommand(_request(), _identity())))

    assert isinstance(result, IssuanceRejected)
    assert len(reconciliation.calls) == 1
    assert issuer.context is not None
    assert issuer.context.verified_plan is None
    assert issuer.context.execution_projection is None


def test_reconciliation_exception_withholds_selected_execution_and_is_observable() -> None:
    verified = _verified_plan()
    projected = make_execution_projection(verified, free_slots=2, now=NOW)
    persistence = create_autospec(ReconciliationPersistence, instance=True, spec_set=True)
    persistence.register_subject.side_effect = RuntimeError("private database failure")
    reconciliation = DurableReconciliationRegistrar(persistence, "c" * 64)
    output = StringIO()
    logger = logging.Logger("registration-diagnostic-test")
    logger.addHandler(logging.StreamHandler(output))
    metrics = RuntimeMetrics()
    issuer = _RecordingIssuer()
    service = DynamicPlanService(
        active_epochs=_ActiveEpochs(cast(ActiveConfigEpochSnapshot, object())),
        candidates=_Candidates(verified),
        issuer=issuer,
        reconciliation=reconciliation,
        capacity=_Capacity(_planning(projected)),
        enforcement_authority=_authority(verified),
        runtime_metrics=metrics,
        diagnostics=RuntimeDiagnosticObserver(StructuredEventLogger(logger)),
    )

    result = asyncio.run(service.request_dynamic_plan(DynamicPlanCommand(_request(), _identity())))

    assert isinstance(result, IssuanceRejected)
    assert issuer.context is not None
    assert issuer.context.verified_plan is None
    assert issuer.context.execution_projection is None
    persistence.register_subject.assert_awaited_once()
    record = json.loads(output.getvalue())
    assert record["event"] == "unexpected_failure"
    assert record["stage"] == "reconciliation_registration"
    assert record["exceptionType"] == "RuntimeError"
    assert "private database failure" not in output.getvalue()
    assert (
        prometheus_samples(metrics)[
            (
                "ci_coordinator_planning_unavailable_total",
                (
                    ("reason", "reconciliation_registration_unavailable"),
                    ("stage", "reconciliation"),
                ),
            )
        ]
        == 1
    )


@pytest.mark.parametrize(
    "capacity_result",
    [None, RuntimeError("capacity unavailable")],
    ids=["unavailable", "failure"],
)
def test_enforcing_plan_without_an_execution_projection_issues_signed_full_ci(
    capacity_result: BaseException | None,
) -> None:
    verified = _verified_plan()
    capacity = _Capacity(capacity_result)
    reconciliation = _Reconciliation(True)
    service = DynamicPlanService(
        active_epochs=_ActiveEpochs(cast(ActiveConfigEpochSnapshot, object())),
        candidates=_Candidates(verified),
        issuer=_issuer(),
        reconciliation=reconciliation,
        capacity=capacity,
        enforcement_authority=_authority(verified),
    )

    result = asyncio.run(service.request_dynamic_plan(DynamicPlanCommand(_request(), _identity())))

    assert isinstance(result, Issued)
    payload = result.record.envelope.payload
    assert payload.fallback_reason == "selected_execution_unavailable"
    assert payload.verified_plan_id is None
    assert payload.execution == FullCiExecution("full-ci", "selected_execution_unavailable")
    assert capacity.calls == 1
    assert reconciliation.calls == []


def test_enforcing_fallback_keeps_exact_gate_when_only_capacity_is_unavailable() -> None:
    verified = _verified_plan()
    projected = make_execution_projection(verified, free_slots=2, now=NOW)
    planning = _planning(projected, execution_ready=False)
    reconciliation = _Reconciliation(True)
    service = DynamicPlanService(
        active_epochs=_ActiveEpochs(cast(ActiveConfigEpochSnapshot, object())),
        candidates=_Candidates(verified),
        issuer=_issuer(),
        reconciliation=reconciliation,
        capacity=_Capacity(planning),
        enforcement_authority=_authority(verified),
    )

    result = asyncio.run(service.request_dynamic_plan(DynamicPlanCommand(_request(), _identity())))

    assert isinstance(result, Issued)
    assert result.record.envelope.payload.fallback_reason == "selected_execution_unavailable"
    assert reconciliation.calls == [(True, (projected.target.provider_signal,))]


def test_enforcing_plan_propagates_capacity_cancellation() -> None:
    verified = _verified_plan()
    capacity = _Capacity(asyncio.CancelledError())
    service = DynamicPlanService(
        active_epochs=_ActiveEpochs(cast(ActiveConfigEpochSnapshot, object())),
        candidates=_Candidates(verified),
        issuer=_RecordingIssuer(),
        reconciliation=_Reconciliation(True),
        capacity=capacity,
        enforcement_authority=_authority(verified),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(service.request_dynamic_plan(DynamicPlanCommand(_request(), _identity())))

    assert capacity.calls == 1


def test_enforcing_service_requires_durable_reconciliation() -> None:
    verified = _verified_plan()

    with pytest.raises(ValueError, match="requires durable reconciliation"):
        DynamicPlanService(
            active_epochs=_ActiveEpochs(cast(ActiveConfigEpochSnapshot, object())),
            candidates=_Candidates(verified),
            issuer=_RecordingIssuer(),
            capacity=_Capacity(
                _planning(make_execution_projection(verified, free_slots=2, now=NOW))
            ),
            enforcement_authority=_authority(verified),
        )


def test_plan_without_execution_or_reconciliation_consumer_skips_capacity() -> None:
    verified = _verified_plan()
    capacity = _Capacity(RuntimeError("capacity must not be requested"))
    issuer = _RecordingIssuer()
    service = DynamicPlanService(
        active_epochs=_ActiveEpochs(cast(ActiveConfigEpochSnapshot, object())),
        candidates=_Candidates(verified),
        issuer=issuer,
        capacity=capacity,
    )

    result = asyncio.run(service.request_dynamic_plan(DynamicPlanCommand(_request(), _identity())))

    assert isinstance(result, IssuanceRejected)
    assert capacity.calls == 0
    assert issuer.context is not None
    assert issuer.context.execution_projection is None


def test_non_enforcing_fallback_registers_the_exact_workflow_gate() -> None:
    verified = _verified_plan()
    projected = make_execution_projection(verified, free_slots=2, now=NOW)
    reconciliation = _Reconciliation(True)
    service = DynamicPlanService(
        active_epochs=_ActiveEpochs(cast(ActiveConfigEpochSnapshot, object())),
        candidates=_Candidates(verified),
        issuer=_RecordingIssuer(),
        reconciliation=reconciliation,
        capacity=_Capacity(_planning(projected)),
    )

    asyncio.run(service.request_dynamic_plan(DynamicPlanCommand(_request(), _identity())))

    assert reconciliation.calls == [(True, (projected.target.provider_signal,))]


def test_native_non_enforcing_fallback_registers_its_declared_gate() -> None:
    verified = _verified_plan()
    projected = make_native_execution_projection(verified)
    identity = _identity(workflow_path=projected.workflow_path)
    reconciliation = _Reconciliation(True)
    service = DynamicPlanService(
        active_epochs=_ActiveEpochs(cast(ActiveConfigEpochSnapshot, object())),
        candidates=_Candidates(verified),
        issuer=_RecordingIssuer(),
        reconciliation=reconciliation,
        capacity=_Capacity(_planning(projected)),
    )

    asyncio.run(service.request_dynamic_plan(DynamicPlanCommand(_request(), identity)))

    assert reconciliation.calls == [(True, (projected.target.provider_signal,))]


def test_foreign_projected_workflow_cannot_issue_or_register_execution() -> None:
    verified = _verified_plan()
    projected = make_execution_projection(verified, free_slots=2, now=NOW)
    reconciliation = _Reconciliation(True)
    metrics = RuntimeMetrics()
    issuer = _RecordingIssuer()
    service = DynamicPlanService(
        active_epochs=_ActiveEpochs(cast(ActiveConfigEpochSnapshot, object())),
        candidates=_Candidates(verified),
        issuer=issuer,
        reconciliation=reconciliation,
        capacity=_Capacity(_planning(projected)),
        runtime_metrics=metrics,
    )

    asyncio.run(
        service.request_dynamic_plan(
            DynamicPlanCommand(
                _request(),
                _identity(workflow_path=".github/workflows/foreign.yml"),
            )
        )
    )

    assert reconciliation.calls == []
    assert issuer.context is not None
    assert issuer.context.execution_projection is None
    assert (
        prometheus_samples(metrics)[
            (
                "ci_coordinator_planning_unavailable_total",
                (
                    ("reason", "capacity_workflow_run_mismatch"),
                    ("stage", "capacity"),
                ),
            )
        ]
        == 1
    )


def test_unbound_plan_requester_cannot_issue_or_register_selected_execution() -> None:
    verified = _verified_plan()
    projected = make_execution_projection(verified, free_slots=2, now=NOW)
    reconciliation = _Reconciliation(True)
    metrics = RuntimeMetrics()
    issuer = _RecordingIssuer()
    service = DynamicPlanService(
        active_epochs=_ActiveEpochs(cast(ActiveConfigEpochSnapshot, object())),
        candidates=_Candidates(verified),
        issuer=issuer,
        reconciliation=reconciliation,
        capacity=_Capacity(_planning(projected)),
        runtime_metrics=metrics,
    )

    asyncio.run(
        service.request_dynamic_plan(
            DynamicPlanCommand(
                _request(),
                replace(_identity(), job_workflow_sha="2" * 40),
            )
        )
    )

    assert reconciliation.calls == []
    assert issuer.context is not None
    assert issuer.context.execution_projection is None
    assert (
        prometheus_samples(metrics)[
            (
                "ci_coordinator_planning_unavailable_total",
                (
                    ("reason", "capacity_plan_request_identity_mismatch"),
                    ("stage", "capacity"),
                ),
            )
        ]
        == 1
    )


def test_non_enforcing_zero_witness_plan_skips_capacity_and_issues_signed_fallback() -> None:
    verified = _zero_selected_verified_plan()
    capacity = _Capacity(RuntimeError("capacity must not be requested"))
    service = DynamicPlanService(
        active_epochs=_ActiveEpochs(cast(ActiveConfigEpochSnapshot, object())),
        candidates=_Candidates(verified),
        issuer=_issuer(),
        capacity=capacity,
    )

    result = asyncio.run(service.request_dynamic_plan(DynamicPlanCommand(_request(), _identity())))

    assert isinstance(result, Issued)
    assert result.record.envelope.payload.fallback_reason == "dynamic_enforcement_disabled"
    assert result.record.envelope.payload.verified_plan_id is None
    assert capacity.calls == 0


def _issuer() -> SignedPlanIssuer:
    signer = SignedPlanSigner(
        key_id="test-key",
        private_key_pem=Ed25519PrivateKey.generate().private_bytes(
            Encoding.PEM,
            PrivateFormat.PKCS8,
            NoEncryption(),
        ),
        ttl_seconds=60,
        clock=FixedClock(NOW),
    )
    return SignedPlanIssuer(store=InMemoryIssuanceStore(), signer=signer)


def _planning(
    projection: TrustedExecutionProjection,
    *,
    execution_ready: bool = True,
) -> ExecutionPlanningResult:
    return ExecutionPlanningResult(
        target=projection.target,
        execution=projection if execution_ready else None,
    )


class _MemoryAuthority:
    def __init__(self, grant: ProductionAdmissionGrant) -> None:
        self._grant = grant

    async def prepare(
        self, candidate: ProductionCandidateSubject
    ) -> PreparedProductionAuthority | None:
        if not self._grant.preauthorizes(candidate):
            return None
        return PreparedProductionAuthority(self._grant, None)


def _authority(
    verified_plan: VerifiedPlan,
    projection: TrustedExecutionProjection | None = None,
    *,
    identity: TrustedActionsRun | None = None,
) -> _MemoryAuthority:
    return _MemoryAuthority(
        make_production_grant(
            verified_plan,
            projection or make_execution_projection(verified_plan, free_slots=2, now=NOW),
            identity or _identity(),
            RepositoryScope(100, 200),
            now=NOW,
        )
    )


def _verified_plan() -> VerifiedPlan:
    planning_input = make_input(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy()
    candidate = plan(planning_input, policy)
    assert isinstance(candidate, DeterministicPlan)
    return verify(planning_input, policy, candidate)


def _zero_selected_verified_plan() -> VerifiedPlan:
    planning_input = make_input()
    catalog = make_catalog()
    policy = make_policy(
        catalog=ValidationCatalog(
            obligations=tuple(
                replace(obligation, omit_allowed=True) for obligation in catalog.obligations
            ),
            witnesses=catalog.witnesses,
            execution_profiles=catalog.execution_profiles,
        )
    )
    candidate = plan(planning_input, policy)
    assert isinstance(candidate, DeterministicPlan)
    verified = verify(planning_input, policy, candidate)
    assert verified.selected_obligations == ()
    assert verified.selected_witnesses == ()
    assert verified.fallback.triggered is False
    return verified


def _request() -> PlanRequest:
    return PlanRequest(
        "dynamic-ci-plan-request/v2",
        "request-1",
        100,
        200,
        "example-org",
        "ci-coordinator",
        "push",
        "refs/heads/master",
        "a" * 40,
        "b" * 40,
        7001,
        1,
        execution_sha="c" * 40,
    )


def _identity(
    *,
    workflow_path: str = ".github/workflows/ci-coordinator-bootstrap.yml",
) -> TrustedActionsRun:
    return TrustedActionsRun(
        "https://token.actions.githubusercontent.com",
        "ci-coordinator",
        "example-org/ci-coordinator",
        200,
        "refs/heads/master",
        7001,
        1,
        "push",
        f"example-org/ci-coordinator/{workflow_path}@refs/heads/master",
        None,
        _PLAN_REQUEST_WORKFLOW_REF,
        "1" * 40,
        None,
        NOW,
        execution_sha="c" * 40,
    )

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Never

import pytest
from package_b_support import make_input, make_policy
from planning_command_support import dynamic_plan_command as _command

from ci_coordinator.app.reconciliation_registration import DurableReconciliationRegistrar
from ci_coordinator.execution_orchestration.provider_signal import ProviderSignal
from ci_coordinator.kernel import hash_object
from ci_coordinator.planning_core import DeterministicPlan, plan
from ci_coordinator.reconciliation import (
    ReconciliationAttemptClaim,
    ReconciliationContract,
    ReconciliationResult,
    ReconciliationSnapshot,
    ReconciliationSubject,
    SubjectRegistered,
    SubjectRegistrationConflict,
)
from ci_coordinator.reconciliation.observation import SignalObservation
from ci_coordinator.repo_context import DiffFileChangeInput
from ci_coordinator.verification_core import VerifiedPlan, verify

NOW = datetime(2026, 7, 15, tzinfo=UTC)
ROLLOUT_PROFILE_ID = "c" * 64
PROVIDER_SIGNALS = (
    ProviderSignal.derive(
        execution_profile_id="python-314",
        shard_id="ci_shard_22222222222222222222222222222222",
    ),
    ProviderSignal.derive(
        execution_profile_id="python-313",
        shard_id="ci_shard_11111111111111111111111111111111",
    ),
)


class _Persistence:
    def __init__(self, mode: str = "registered") -> None:
        self.mode = mode
        self.calls: list[tuple[ReconciliationSubject, ReconciliationContract]] = []

    async def register_subject(
        self,
        subject: ReconciliationSubject,
        contract: ReconciliationContract,
    ) -> SubjectRegistered | SubjectRegistrationConflict:
        self.calls.append((subject, contract))
        if self.mode == "failure":
            raise RuntimeError("database unavailable")
        if self.mode == "cancel":
            raise asyncio.CancelledError
        snapshot = ReconciliationSnapshot(subject, contract, 0, ())
        if self.mode == "conflict":
            return SubjectRegistrationConflict(snapshot)
        return SubjectRegistered(snapshot)

    async def load_snapshot(self, claim: ReconciliationAttemptClaim) -> Never:
        del claim
        raise AssertionError("not used")

    async def append_observation(
        self,
        claim: ReconciliationAttemptClaim,
        expected_revision: int,
        observation: SignalObservation,
    ) -> Never:
        del claim, expected_revision, observation
        raise AssertionError("not used")

    async def record_result(
        self,
        claim: ReconciliationAttemptClaim,
        expected_revision: int,
        result: ReconciliationResult,
    ) -> Never:
        del claim, expected_revision, result
        raise AssertionError("not used")

    async def defer_claim(
        self,
        claim: ReconciliationAttemptClaim,
    ) -> Never:
        del claim
        raise AssertionError("not used")


def test_registration_binds_request_to_exact_provider_signals_and_omissions() -> None:
    persistence = _Persistence()
    registrar = DurableReconciliationRegistrar(persistence, ROLLOUT_PROFILE_ID)
    verified = _verified_plan()

    admitted = asyncio.run(
        registrar.register(
            _command(NOW),
            verified,
            full_ci=False,
            provider_signals=PROVIDER_SIGNALS,
        )
    )

    assert admitted is True
    subject, contract = persistence.calls[0]
    assert subject.repository_id == 200
    assert subject.head_sha == "b" * 40
    assert contract.provider_signals == tuple(
        sorted(PROVIDER_SIGNALS, key=lambda signal: signal.signal_id)
    )
    assert tuple(signal.signal_id for signal in contract.omitted_signals) == ("backend-tests",)
    assert contract.candidate_evidence is None
    assert contract.planning_evidence is not None
    assert contract.planning_evidence.request_hash == hash_object(
        _command(NOW).request.identity_mapping()
    )
    assert contract.planning_evidence.verified_plan_id == verified.execution_plan_id
    assert contract.planning_evidence.planner_version == "planning-core/v1"
    assert contract.planning_evidence.verifier_version == "verification-core/v1"


def test_registration_conflict_withholds_selected_execution() -> None:
    registrar = DurableReconciliationRegistrar(_Persistence("conflict"), ROLLOUT_PROFILE_ID)

    admitted = asyncio.run(
        registrar.register(
            _command(NOW),
            _verified_plan(),
            full_ci=False,
            provider_signals=PROVIDER_SIGNALS,
        )
    )

    assert admitted is False


@pytest.mark.parametrize(
    ("mode", "error"), [("cancel", asyncio.CancelledError), ("failure", RuntimeError)]
)
def test_registration_errors_reach_the_caller_failure_boundary(
    mode: str, error: type[BaseException]
) -> None:
    registrar = DurableReconciliationRegistrar(_Persistence(mode), ROLLOUT_PROFILE_ID)

    with pytest.raises(error):
        asyncio.run(
            registrar.register(
                _command(NOW),
                _verified_plan(),
                full_ci=False,
                provider_signals=PROVIDER_SIGNALS,
            )
        )


def test_registration_with_empty_provider_identity_fails_closed_before_persistence() -> None:
    persistence = _Persistence()
    registrar = DurableReconciliationRegistrar(persistence, ROLLOUT_PROFILE_ID)

    admitted = asyncio.run(
        registrar.register(
            _command(NOW),
            _verified_plan(),
            full_ci=False,
            provider_signals=(),
        )
    )

    assert admitted is False
    assert persistence.calls == []


def test_full_ci_contract_expects_candidate_omissions_to_execute() -> None:
    persistence = _Persistence()
    registrar = DurableReconciliationRegistrar(persistence, ROLLOUT_PROFILE_ID)

    admitted = asyncio.run(
        registrar.register(
            _command(NOW),
            _verified_plan(),
            full_ci=True,
            provider_signals=PROVIDER_SIGNALS,
        )
    )

    assert admitted is True
    _, contract = persistence.calls[0]
    assert contract.provider_signals == tuple(
        sorted(PROVIDER_SIGNALS, key=lambda signal: signal.signal_id)
    )
    assert contract.omitted_signals == ()
    assert contract.candidate_evidence is not None
    assert contract.candidate_evidence.profile_id == ROLLOUT_PROFILE_ID
    assert contract.candidate_evidence.repository == "example-org/ci-coordinator"
    assert tuple(signal.signal_id for signal in contract.candidate_evidence.omitted_signals) == (
        "backend-tests",
    )


def _verified_plan() -> VerifiedPlan:
    planning_input = make_input(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    policy = make_policy()
    candidate = plan(planning_input, policy)
    assert isinstance(candidate, DeterministicPlan)
    return verify(planning_input, policy, candidate)

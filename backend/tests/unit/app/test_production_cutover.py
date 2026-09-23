from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import timedelta
from functools import partial
from hashlib import sha256
from unittest.mock import AsyncMock, Mock, create_autospec

import pytest
from production_admission_support import PRODUCTION_KEY_ID
from production_cutover_support import ProductionCutoverFixture, production_cutover_fixture

from ci_coordinator.app.config_admission import ConfigScopeAuthorizer
from ci_coordinator.app.production_cutover import (
    ProductionAdministrationError,
    ProductionAdministrationResult,
    ProductionCutoverService,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs import (
    ActiveConfigEpoch,
    ActiveConfigEpochSnapshot,
    ConfigEpochRepository,
    ConfigEpochStoreUnavailable,
)
from ci_coordinator.kernel import hash_object
from ci_coordinator.production_admission import ProductionAdmissionGrant
from ci_coordinator.production_admission.cutover_commands import (
    ProductionCutoverApplied,
    ProductionCutoverCommand,
    ProductionCutoverKind,
    ProductionCutoverRejected,
    production_stage_input_digest,
)
from ci_coordinator.production_admission.cutover_drain import admit_production_drain
from ci_coordinator.production_admission.cutover_state import ProductionScopeState
from ci_coordinator.production_admission.ports import (
    ProductionCutoverStore,
    ProductionCutoverUnavailable,
    ProductionEvidenceAdmissionUnavailable,
)

_KINDS: tuple[ProductionCutoverKind, ...] = ("stage", "begin", "activate")


@dataclass
class _Harness:
    fixture: ProductionCutoverFixture
    service: ProductionCutoverService
    authorizer: Mock
    store: Mock
    epochs: Mock
    verifier: Mock
    worker: AsyncMock
    observation: AsyncMock
    states: tuple[ProductionScopeState, ProductionScopeState, ProductionScopeState]

    def command(self, kind: ProductionCutoverKind) -> ProductionCutoverCommand:
        fixture = self.fixture
        digest = {
            "stage": production_stage_input_digest(
                fixture.signed.content,
                fixture.staged.canonical_bytes,
                fixture.staged.lookup.provider_paths,
            ),
            "begin": hash_object({}),
            "activate": sha256(fixture.drain_bytes(self.states[1])).hexdigest(),
        }[kind]
        return ProductionCutoverCommand(
            kind,
            fixture.draft.scope,
            kind,
            "operator",
            "admit exact successor",
            _KINDS.index(kind),
            fixture.signed.grant.authority_id,
            digest,
        )

    async def invoke(self, command: ProductionCutoverCommand) -> ProductionAdministrationResult:
        if command.kind == "stage":
            return await self.service.stage(
                command,
                envelope=self.fixture.signed.content,
                evidence=self.fixture.staged.canonical_bytes,
                provider_paths=self.fixture.staged.lookup.provider_paths,
            )
        if command.kind == "begin":
            return await self.service.begin(command)
        return await self.service.activate(
            command, drain_envelope=self.fixture.drain_bytes(self.states[1])
        )


@pytest.fixture(scope="module")
def evidence_seed() -> ProductionCutoverFixture:
    return production_cutover_fixture()


@pytest.fixture
def harness(evidence_seed: ProductionCutoverFixture) -> _Harness:
    fixture = evidence_seed.isolated_copy()
    staged = ProductionScopeState(
        fixture.draft.scope, 1, staged_authority_id=fixture.signed.grant.authority_id
    )
    latched = replace(
        staged,
        revision=2,
        latch_override_id="override_" + "a" * 32,
        latch_applied_at=fixture.now,
    )
    active = ProductionScopeState(
        fixture.draft.scope,
        3,
        generation=1,
        active_authority_id=fixture.signed.grant.authority_id,
        active_subject_digest=fixture.staged.scope_grant.admission_subject_digest,
    )
    authorizer = create_autospec(ConfigScopeAuthorizer, instance=True, spec_set=True)
    authorizer.allows_scope.return_value = True
    store = create_autospec(ProductionCutoverStore, instance=True, spec_set=True)
    store.resolve.return_value = None
    store.inspect.return_value = active
    store.load_evidence.return_value = fixture.staged.canonical_bytes
    store.load_authority.return_value = fixture.retained(latched)
    for kind, state in zip(_KINDS, (staged, latched, active), strict=True):
        getattr(store, kind).return_value = ProductionCutoverApplied(state)
    epochs = create_autospec(ConfigEpochRepository, instance=True, spec_set=True)
    epochs.load_active.return_value = ActiveConfigEpochSnapshot(
        ActiveConfigEpoch(fixture.draft.scope, fixture.draft.epoch_id, 1), fixture.draft
    )
    verifier = Mock(side_effect=fixture.verifier())
    worker = AsyncMock(return_value=fixture.staged)
    observation = AsyncMock(return_value=fixture.activation(latched))
    service = ProductionCutoverService(
        authorizer=authorizer,
        store=store,
        epochs=epochs,
        verifier=verifier,
        drain_verifier=partial(
            admit_production_drain,
            public_key_pem=fixture.signed.public_key_pem,
            expected_key_id=PRODUCTION_KEY_ID,
        ),
        evidence_admission=worker,
        observe_activation=observation,
    )
    return _Harness(
        fixture,
        service,
        authorizer,
        store,
        epochs,
        verifier,
        worker,
        observation,
        (staged, latched, active),
    )


def test_cutover_orchestration_preserves_exact_evidence_and_commands(
    harness: _Harness,
) -> None:
    async def scenario() -> None:
        for kind, state in zip(_KINDS, harness.states, strict=True):
            command = harness.command(kind)
            assert await harness.invoke(command) == ProductionCutoverApplied(state)
            harness.authorizer.allows_scope.assert_awaited_with(
                actor=command.actor, scope=command.scope
            )
            harness.store.resolve.assert_awaited_with(command)
            assert getattr(harness.store, kind).await_args.args[0] == command
        harness.worker.assert_awaited_once_with(
            harness.fixture.staged.canonical_bytes,
            harness.fixture.staged.scope_grant,
            harness.fixture.draft,
            harness.fixture.staged.lookup.provider_paths,
        )
        harness.observation.assert_awaited_once()
        observation_call = harness.observation.await_args
        assert observation_call is not None
        retained, grant = observation_call.args
        assert retained == harness.fixture.retained(harness.states[1])
        assert type(grant) is ProductionAdmissionGrant
        for name in (
            "authority_id",
            "envelope_canonical_json",
            "expires_at",
            "issued_at",
            "key_id",
            "public_key_spki_der",
            "scope_bindings",
        ):
            assert getattr(grant.registration, name) == getattr(
                harness.fixture.signed.grant.registration, name
            ), name
        assert grant.authority_id == harness.fixture.signed.grant.authority_id
        assert grant.not_after == harness.fixture.signed.grant.not_after
        assert grant.scope_grant(harness.fixture.draft.scope) == harness.fixture.staged.scope_grant
        assert observation_call.kwargs == {}
        activation = harness.store.activate.await_args.kwargs
        assert activation["current"] == harness.fixture.activation(harness.states[1])
        assert activation["drain"].matches(
            harness.states[1], harness.fixture.staged.scope_grant, database_now=harness.fixture.now
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", _KINDS)
def test_exact_historical_replay_does_not_renew_or_revalidate_expired_authority(
    harness: _Harness, kind: ProductionCutoverKind
) -> None:
    async def scenario() -> None:
        historical = ProductionCutoverApplied(harness.states[_KINDS.index(kind)], duplicate=True)
        harness.store.resolve.return_value = historical
        harness.verifier.side_effect = harness.fixture.verifier(
            at=harness.fixture.now + timedelta(days=1)
        )
        harness.store.load_authority.return_value = None
        assert await harness.invoke(harness.command(kind)) == historical
        harness.verifier.assert_not_called()
        harness.epochs.load_active.assert_not_awaited()
        harness.worker.assert_not_awaited()
        harness.observation.assert_not_awaited()
        for mutation in _KINDS:
            getattr(harness.store, mutation).assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", _KINDS)
def test_scope_denial_precedes_historical_lookup_and_every_effect(
    harness: _Harness, kind: ProductionCutoverKind
) -> None:
    async def scenario() -> None:
        harness.authorizer.allows_scope.return_value = False
        assert await harness.invoke(harness.command(kind)) == ProductionAdministrationError(
            "forbidden"
        )
        assert harness.store.mock_calls == []
        assert harness.epochs.mock_calls == []
        harness.verifier.assert_not_called()
        harness.worker.assert_not_awaited()
        harness.observation.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", _KINDS)
def test_command_input_substitution_has_no_effect(
    harness: _Harness, kind: ProductionCutoverKind
) -> None:
    async def scenario() -> None:
        command = replace(harness.command(kind), input_digest="0" * 64)
        assert await harness.invoke(command) == ProductionAdministrationError("invalid_evidence")
        assert harness.store.mock_calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", _KINDS)
def test_conflicting_operation_is_not_reinterpreted_as_a_new_command(
    harness: _Harness, kind: ProductionCutoverKind
) -> None:
    async def scenario() -> None:
        conflict = ProductionCutoverRejected("command_conflict")
        harness.store.resolve.return_value = conflict
        assert await harness.invoke(harness.command(kind)) == conflict
        harness.verifier.assert_not_called()
        getattr(harness.store, kind).assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", (ValueError, TypeError, ProductionEvidenceAdmissionUnavailable))
def test_stage_worker_failure_never_reaches_mutation(
    harness: _Harness, failure: type[Exception]
) -> None:
    async def scenario() -> None:
        harness.worker.side_effect = failure("sensitive evidence")
        expected = (
            ProductionAdministrationError("unavailable")
            if failure is ProductionEvidenceAdmissionUnavailable
            else ProductionAdministrationError("invalid_evidence")
        )
        assert await harness.invoke(harness.command("stage")) == expected
        harness.store.stage.assert_not_awaited()

    asyncio.run(scenario())


def test_stage_requires_the_current_config_epoch(harness: _Harness) -> None:
    async def scenario() -> None:
        harness.epochs.load_active.return_value = None
        assert await harness.invoke(harness.command("stage")) == ProductionCutoverRejected(
            "config_changed"
        )
        harness.worker.assert_not_awaited()
        harness.store.stage.assert_not_awaited()
        harness.epochs.load_active.side_effect = ConfigEpochStoreUnavailable
        assert await harness.invoke(harness.command("stage")) == ProductionAdministrationError(
            "unavailable"
        )

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", _KINDS)
def test_ambiguous_durable_result_is_unavailable_not_absent(
    harness: _Harness, kind: ProductionCutoverKind
) -> None:
    async def scenario() -> None:
        getattr(harness.store, kind).side_effect = ProductionCutoverUnavailable("commit unknown")
        assert await harness.invoke(harness.command(kind)) == ProductionAdministrationError(
            "unavailable"
        )
        assert getattr(harness.store, kind).await_count == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ("stage", "activate"))
def test_cancellation_is_not_translated_into_a_retryable_success(
    harness: _Harness, kind: ProductionCutoverKind
) -> None:
    async def scenario() -> None:
        effect = harness.worker if kind == "stage" else harness.observation
        effect.side_effect = asyncio.CancelledError
        with pytest.raises(asyncio.CancelledError):
            await harness.invoke(harness.command(kind))
        getattr(harness.store, kind).assert_not_awaited()

    asyncio.run(scenario())


def test_unavailable_current_observation_keeps_activation_unmodified(
    harness: _Harness,
) -> None:
    async def scenario() -> None:
        harness.observation.return_value = None
        assert await harness.invoke(harness.command("activate")) == ProductionCutoverRejected(
            "current_evidence_invalid"
        )
        harness.store.activate.assert_not_awaited()

    asyncio.run(scenario())


def test_reads_are_scope_authorized_and_preserve_retained_bytes(harness: _Harness) -> None:
    async def scenario() -> None:
        kwargs = {"actor": "operator", "scope": harness.fixture.draft.scope}
        assert (
            await harness.service.inspect(actor="operator", scope=harness.fixture.draft.scope)
            == harness.states[2]
        )
        assert (
            await harness.service.evidence(
                actor="operator",
                scope=harness.fixture.draft.scope,
                authority_id=harness.fixture.signed.grant.authority_id,
            )
            == harness.fixture.staged.canonical_bytes
        )
        harness.authorizer.allows_scope.assert_awaited_with(**kwargs)
        harness.store.reset_mock()
        harness.authorizer.allows_scope.return_value = False
        assert await harness.service.inspect(
            actor="operator", scope=harness.fixture.draft.scope
        ) == ProductionAdministrationError("forbidden")
        assert await harness.service.evidence(
            actor="operator",
            scope=harness.fixture.draft.scope,
            authority_id=harness.fixture.signed.grant.authority_id,
        ) == ProductionAdministrationError("forbidden")
        assert harness.store.mock_calls == []

    asyncio.run(scenario())


def test_scope_substitution_in_a_durable_read_is_not_returned(harness: _Harness) -> None:
    async def scenario() -> None:
        harness.store.inspect.return_value = replace(
            harness.states[2], scope=RepositoryScope(99, 100)
        )
        assert await harness.service.inspect(
            actor="operator", scope=harness.fixture.draft.scope
        ) == ProductionAdministrationError("unavailable")

    asyncio.run(scenario())

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import Literal

import pytest
from production_cutover_support import (
    CUTOVER_NOW,
    ProductionCutoverFixture,
    production_cutover_fixture,
)

from ci_coordinator.app.production_request_evidence import ProductionRequestAuthorityService
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.production_admission.cutover_state import ProductionScopeState
from ci_coordinator.production_admission.evidence_lookup import ProductionEvidenceLookup
from ci_coordinator.production_admission.ports import (
    CurrentProductionSources,
    RetainedProductionAuthority,
)


@dataclass
class _Authorities:
    value: RetainedProductionAuthority | None
    purposes: list[str] = field(default_factory=list)

    async def load_authority(
        self, scope: RepositoryScope, *, purpose: Literal["active", "staged"]
    ) -> RetainedProductionAuthority | None:
        self.purposes.append(purpose)
        assert self.value is None or scope == self.value.state.scope
        return self.value


@dataclass
class _Sources:
    value: CurrentProductionSources | None
    calls: list[tuple[str, ...]] = field(default_factory=list)
    failure: BaseException | None = None

    async def read(
        self, lookup: ProductionEvidenceLookup, *, revisions: tuple[str, ...]
    ) -> CurrentProductionSources | None:
        self.calls.append(revisions)
        if self.failure is not None:
            raise self.failure
        return self.value


@pytest.fixture(scope="module")
def evidence_seed() -> ProductionCutoverFixture:
    return production_cutover_fixture()


@pytest.fixture
def fixture(evidence_seed: ProductionCutoverFixture) -> ProductionCutoverFixture:
    return evidence_seed.isolated_copy()


def _active(fixture: ProductionCutoverFixture, *, revision: int = 3) -> ProductionScopeState:
    return ProductionScopeState(
        scope=fixture.draft.scope,
        revision=revision,
        generation=fixture.staged.relation.generation,
        revoked_through_generation=fixture.staged.relation.predecessor_generation,
        active_authority_id=fixture.signed.grant.authority_id,
        active_subject_digest=fixture.staged.scope_grant.admission_subject_digest,
    )


def test_active_durable_receipt_is_revalidated_and_hot_successor_needs_no_restart(
    fixture: ProductionCutoverFixture,
) -> None:
    async def scenario() -> None:
        authorities = _Authorities(fixture.retained(_active(fixture)))
        sources = _Sources(fixture.sources)
        service = ProductionRequestAuthorityService(
            authorities=authorities,
            verifier=fixture.verifier(),
            sources=sources,
        )
        initial = await service.prepare(fixture.candidate)
        assert initial is not None and initial.current is not None
        assert initial.grant.authority_id == fixture.signed.grant.authority_id
        assert initial.current.scope_revision == 3
        assert initial.current.database_started_at == CUTOVER_NOW

        successor = production_cutover_fixture(generation=2, signing_key=fixture.signing_key)
        authorities.value = successor.retained(_active(successor, revision=6))
        sources.value = successor.sources
        current = await service.prepare(successor.candidate)
        assert current is not None and current.current is not None
        assert current.grant.authority_id == successor.signed.grant.authority_id
        assert current.grant.authority_id != initial.grant.authority_id
        assert current.current.scope_revision == 6
        assert current.current.scope_grant.relation.generation == 2
        assert authorities.purposes == ["active", "active"]
        assert sources.calls == [(fixture.staged.lookup.source_commit,)] * 2

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "operand",
    ["absent", "expired", "wrong_key", "revoked", "foreign_receipt", "provider", "source_sha"],
)
def test_missing_permission_operand_cannot_yield_prepared_production_authority(
    fixture: ProductionCutoverFixture,
    operand: str,
) -> None:
    async def scenario() -> None:
        state = _active(fixture)
        retained = fixture.retained(state)
        verifier = fixture.verifier()
        candidate = fixture.candidate
        sources = _Sources(fixture.sources)
        if operand == "expired":
            verifier = fixture.verifier(CUTOVER_NOW + timedelta(days=2))
        elif operand == "wrong_key":
            verifier = production_cutover_fixture().verifier()
        elif operand == "revoked":
            retained = fixture.retained(replace(state, revoked_through_generation=1))
        elif operand == "foreign_receipt":
            retained = fixture.retained(
                replace(state, active_authority_id="production_admission_" + "f" * 32)
            )
        elif operand == "provider":
            sources.value = None
        elif operand == "source_sha":
            candidate = replace(candidate, workflow_sha=None, job_workflow_sha=None)
        authorities = _Authorities(None if operand == "absent" else retained)
        service = ProductionRequestAuthorityService(
            authorities=authorities, verifier=verifier, sources=sources
        )
        assert await service.prepare(candidate) is None
        if operand in {"absent", "expired", "wrong_key", "foreign_receipt", "source_sha"}:
            assert sources.calls == []

    asyncio.run(scenario())


def test_activation_observation_uses_retained_source_without_actions_run(
    fixture: ProductionCutoverFixture,
) -> None:
    async def scenario() -> None:
        state = ProductionScopeState(
            fixture.draft.scope, 1, staged_authority_id=fixture.signed.grant.authority_id
        )
        retained = fixture.retained(state)
        authorities, sources = _Authorities(None), _Sources(fixture.sources)
        service = ProductionRequestAuthorityService(
            authorities=authorities, verifier=fixture.verifier(), sources=sources
        )
        current = await service.observe_activation(retained, fixture.signed.grant)
        assert current is not None
        assert current == fixture.activation(state)
        assert authorities.purposes == []
        assert sources.calls == [(fixture.staged.lookup.source_commit,)]

    asyncio.run(scenario())


def test_provider_cancellation_is_not_converted_into_authority(
    fixture: ProductionCutoverFixture,
) -> None:
    async def scenario() -> None:
        service = ProductionRequestAuthorityService(
            authorities=_Authorities(fixture.retained(_active(fixture))),
            verifier=fixture.verifier(),
            sources=_Sources(None, failure=asyncio.CancelledError()),
        )
        with pytest.raises(asyncio.CancelledError):
            await service.prepare(fixture.candidate)

    asyncio.run(scenario())

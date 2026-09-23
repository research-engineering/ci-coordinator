from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from production_admission_support import PRODUCTION_KEY_ID
from production_cutover_support import ProductionCutoverFixture, production_cutover_fixture
from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.kernel import hash_object
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.production_cutover_adapter import (
    TransactionalProductionCutoverStore,
)
from ci_coordinator.persistence.production_cutover_unit_of_work import (
    PostgresProductionCutoverUnitOfWork,
)
from ci_coordinator.persistence.schema import active_config_epochs
from ci_coordinator.production_admission.current_evidence import CurrentActivationEvidence
from ci_coordinator.production_admission.cutover_commands import (
    ProductionCutoverApplied,
    ProductionCutoverCommand,
    ProductionCutoverKind,
    ProductionCutoverResult,
    production_stage_input_digest,
)
from ci_coordinator.production_admission.cutover_drain import (
    AdmittedProductionDrain,
    admit_production_drain,
)
from ci_coordinator.production_admission.cutover_state import ProductionScopeState

from ._reconciliation_support import database_time
from ._runtime_ingress_issuance_support import _insert_epoch


@dataclass(frozen=True)
class CutoverDatabase:
    admin: AsyncEngine
    runtime: AsyncEngine
    fixture: ProductionCutoverFixture
    store: TransactionalProductionCutoverStore

    def command(
        self,
        kind: ProductionCutoverKind,
        revision: int,
        digest: str,
        *,
        fixture: ProductionCutoverFixture | None = None,
    ) -> ProductionCutoverCommand:
        admitted = self.fixture if fixture is None else fixture
        return ProductionCutoverCommand(
            kind,
            admitted.draft.scope,
            f"{kind}-{revision}",
            "integration-operator",
            "qualify exact production generation",
            revision,
            admitted.signed.grant.authority_id,
            digest,
        )

    def stage_command(
        self, revision: int = 0, *, fixture: ProductionCutoverFixture | None = None
    ) -> ProductionCutoverCommand:
        admitted = self.fixture if fixture is None else fixture
        return self.command(
            "stage",
            revision,
            production_stage_input_digest(
                admitted.signed.content,
                admitted.staged.canonical_bytes,
                admitted.staged.lookup.provider_paths,
            ),
            fixture=admitted,
        )

    async def stage(
        self, revision: int = 0, *, fixture: ProductionCutoverFixture | None = None
    ) -> ProductionScopeState:
        admitted = self.fixture if fixture is None else fixture
        result = await self.store.stage(
            self.stage_command(revision, fixture=admitted),
            grant=admitted.signed.grant,
            evidence=admitted.staged,
        )
        assert isinstance(result, ProductionCutoverApplied), result
        assert not result.duplicate
        return result.state

    async def begin(
        self, state: ProductionScopeState, *, fixture: ProductionCutoverFixture | None = None
    ) -> ProductionScopeState:
        result = await self.store.begin(
            self.command("begin", state.revision, hash_object({}), fixture=fixture)
        )
        assert isinstance(result, ProductionCutoverApplied), result
        return result.state

    async def activation_inputs(
        self,
        state: ProductionScopeState,
        *,
        fixture: ProductionCutoverFixture | None = None,
        drain_expires_at: datetime | None = None,
    ) -> tuple[ProductionCutoverCommand, AdmittedProductionDrain, CurrentActivationEvidence]:
        admitted = self.fixture if fixture is None else fixture
        assert state.latch_applied_at is not None
        async with asyncio.timeout(2):
            while True:
                sampled = await database_time(self.runtime)
                observed = sampled.replace(microsecond=sampled.microsecond // 1000 * 1000)
                if observed >= state.latch_applied_at:
                    break
                await asyncio.sleep(0.001)
        content = admitted.drain_bytes(state, at=observed, expires_at=drain_expires_at)
        drain = admit_production_drain(
            content,
            public_key_pem=admitted.signed.public_key_pem,
            expected_key_id=PRODUCTION_KEY_ID,
        )
        assert drain is not None
        current = admitted.activation(state, at=sampled)
        return (
            self.command("activate", state.revision, sha256(content).hexdigest(), fixture=admitted),
            drain,
            current,
        )

    async def activate(
        self, state: ProductionScopeState, *, fixture: ProductionCutoverFixture | None = None
    ) -> ProductionCutoverResult:
        admitted = self.fixture if fixture is None else fixture
        command, drain, current = await self.activation_inputs(state, fixture=admitted)
        return await self.store.activate(
            command, grant=admitted.signed.grant, current=current, drain=drain
        )


@asynccontextmanager
async def cutover_database(
    admin_url: str, runtime_url: str, *, local_requester: bool = False
) -> AsyncIterator[CutoverDatabase]:
    admin = create_postgres_engine(admin_url)
    runtime = create_postgres_engine(runtime_url)
    try:
        sampled: datetime = await database_time(admin)
        fixture = production_cutover_fixture(
            now=sampled.replace(microsecond=0), local_requester=local_requester
        )
        async with admin.begin() as connection:
            await _insert_epoch(connection, fixture.draft)
            await connection.execute(
                insert(active_config_epochs).values(
                    installation_id=fixture.draft.scope.installation_id,
                    repository_id=fixture.draft.scope.repository_id,
                    epoch_id=fixture.draft.epoch_id,
                    revision=1,
                )
            )
        store = TransactionalProductionCutoverStore(
            lambda: PostgresProductionCutoverUnitOfWork(runtime)
        )
        yield CutoverDatabase(admin, runtime, fixture, store)
    finally:
        await runtime.dispose()
        await admin.dispose()

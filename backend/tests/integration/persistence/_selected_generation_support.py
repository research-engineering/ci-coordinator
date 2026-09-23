"""Isolated issuance fixtures; synthetic relation rows are not end-to-end cutover proof."""

from __future__ import annotations

from dataclasses import replace
from hashlib import sha256

from package_b_support import BASE_SHA, HEAD_SHA
from sqlalchemy import func, insert, update
from sqlalchemy.ext.asyncio import AsyncEngine
from target_authority_producers.factories import WORKFLOW_DOCUMENT, observation_sources
from workflow_authority.factories import evidence as workflow_evidence

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls import (
    ActiveOverride,
    OverrideApplied,
    OverrideAuditEvent,
    OverrideCommand,
)
from ci_coordinator.persistence.operator_override_repository import (
    DurableOperatorOverrideStore,
    PostgresOperatorOverrideUnitOfWork,
)
from ci_coordinator.persistence.production_cutover_unit_of_work import (
    PostgresProductionCutoverUnitOfWork,
)
from ci_coordinator.persistence.schema import (
    production_evidence_bundles,
    production_scope_states,
    production_staged_grants,
)
from ci_coordinator.production_admission import ProductionIssuanceGuard
from ci_coordinator.production_admission.evidence_lookup import ProductionEvidenceLookup
from ci_coordinator.production_admission.ports import CurrentProductionSources
from ci_coordinator.reconciliation import ReconciliationSubject
from ci_coordinator.target_authority_producers.sources import ProviderAuthoritySources

from ._reconciliation_support import POLICY, contract, database_time


def selected_current_sources() -> CurrentProductionSources:
    base = observation_sources()
    scope = RepositoryScope(100, 200)
    repository = replace(
        base.provider_authority.governance.repository,
        scope=scope,
        name="ci-coordinator",
        full_name="example-org/ci-coordinator",
        default_branch="main",
    )
    provider = ProviderAuthoritySources(
        replace(
            base.provider_authority.workflows,
            scope=scope,
            name=repository.name,
            default_branch=repository.default_branch,
        ),
        replace(base.provider_authority.governance, repository=repository),
    )
    source = workflow_evidence(
        scope=scope,
        owner=repository.owner,
        name=repository.name,
        default_branch=repository.default_branch,
        workflow_content=WORKFLOW_DOCUMENT.encode(),
    )
    return CurrentProductionSources((source,), provider)


async def initialize_selected_generation(
    admin: AsyncEngine,
    runtime: AsyncEngine,
    guard: ProductionIssuanceGuard,
    *,
    register_subject: bool = True,
) -> None:
    current = guard.current_evidence
    assert current is not None and current.scope_revision == 3
    relation = current.scope_grant.relation
    sources = selected_current_sources()
    lookup = ProductionEvidenceLookup(
        sources.provider.repository, "a" * 40, (".github/workflows/ci.yml",)
    )
    content = b"{}\n"
    scope = {
        "installation_id": guard.scope.installation_id,
        "repository_id": guard.scope.repository_id,
    }
    async with admin.begin() as connection:
        await connection.execute(
            insert(production_evidence_bundles).values(
                bundle_digest=relation.evidence_bundle_digest,
                content_sha256=sha256(content).hexdigest(),
                canonical_json=content,
                byte_count=len(content),
                retained_at=func.clock_timestamp(),
            )
        )
        await connection.execute(
            insert(production_staged_grants).values(
                **scope,
                authority_id=guard.authority_id,
                generation=1,
                bundle_digest=relation.evidence_bundle_digest,
                lookup_canonical_json=lookup.canonical_bytes,
                staged_at=func.clock_timestamp(),
            )
        )
        await connection.execute(
            insert(production_scope_states).values(
                **scope,
                revision=1,
                generation=0,
                revoked_through_generation=0,
                staged_authority_id=guard.authority_id,
            )
        )
    controls = DurableOperatorOverrideStore(lambda: PostgresOperatorOverrideUnitOfWork(runtime))
    disabled = ActiveOverride.create(
        OverrideCommand(
            "disable_omission",
            guard.scope,
            None,
            "fixture-disable",
            "integration-test",
            "initialize isolated issuance guard state",
            None,
        ),
        await database_time(runtime),
    )
    assert isinstance(
        await controls.apply(disabled, OverrideAuditEvent.applied(disabled)), OverrideApplied
    )
    async with admin.begin() as connection:
        statement = update(production_scope_states).where(
            production_scope_states.c.installation_id == guard.scope.installation_id,
            production_scope_states.c.repository_id == guard.scope.repository_id,
        )
        await connection.execute(
            statement.values(
                revision=2,
                latch_override_id=disabled.override_id,
                latch_applied_at=disabled.applied_at,
            )
        )
        await connection.execute(
            statement.values(
                revision=3,
                generation=1,
                active_authority_id=guard.authority_id,
                staged_authority_id=None,
                latch_override_id=None,
                latch_applied_at=None,
            )
        )
    enabled = ActiveOverride.create(
        OverrideCommand(
            "enable_omission",
            guard.scope,
            disabled.override_id,
            "fixture-enable",
            "integration-test",
            "finish isolated issuance guard initialization",
            None,
        ),
        await database_time(runtime),
    )
    assert isinstance(
        await controls.apply(enabled, OverrideAuditEvent.applied(enabled)), OverrideApplied
    )
    if register_subject:
        subject = ReconciliationSubject.create(
            installation_id=100,
            repository_id=200,
            event_name="pull_request",
            ref="refs/pull/42/merge",
            base_sha=BASE_SHA,
            head_sha=HEAD_SHA,
            workflow_run_id=7001,
            run_attempt=1,
        )
        assert subject.subject_id == guard.reconciliation_subject_id
        selected_contract = contract()
        assert selected_contract.planning_evidence is not None
        selected_contract = replace(
            selected_contract,
            planning_evidence=replace(
                selected_contract.planning_evidence,
                config_epoch_id=guard.config_epoch_id,
                verified_plan_id=guard.execution_plan_id,
            ),
        )
        async with PostgresProductionCutoverUnitOfWork(runtime) as transaction:
            assert await transaction.registrations.register(
                subject, selected_contract, POLICY, guard
            )
            await transaction.commit()

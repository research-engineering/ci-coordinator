from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import text

from ci_coordinator.persistence import PostgresCiEconomicsUnitOfWork, PostgresUnitOfWork
from ci_coordinator.persistence.compatibility_repository import admit_current_capabilities
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import DatabaseCapabilityUnavailable
from ci_coordinator.persistence.schema_capabilities import (
    CI_ECONOMICS_EVIDENCE,
    CI_ECONOMICS_EVIDENCE_V2,
    CI_ECONOMICS_EVIDENCE_V3,
    CapabilityDefinition,
)
from ci_coordinator.persistence.webhook_ingestion_unit_of_work import (
    PostgresWebhookIngestionUnitOfWork,
)

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize(
    "unit_of_work", [PostgresCiEconomicsUnitOfWork, PostgresWebhookIngestionUnitOfWork]
)
@pytest.mark.parametrize(
    "retired", [CI_ECONOMICS_EVIDENCE, CI_ECONOMICS_EVIDENCE_V2, CI_ECONOMICS_EVIDENCE_V3]
)
def test_current_runtime_rejects_a_prior_economics_capability_before_exposure(
    runtime_postgres_database_url: str,
    unit_of_work: type[PostgresUnitOfWork],
    retired: CapabilityDefinition,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with unit_of_work(engine) as healthy:
                assert any(
                    item.capability_id == "ci-economics-evidence/v4"
                    for item in healthy._required_capabilities
                )
            prior = unit_of_work(engine)
            prior._required_capabilities = tuple(
                retired.declaration() if item.capability_id == "ci-economics-evidence/v4" else item
                for item in prior._required_capabilities
            )
            with pytest.raises(DatabaseCapabilityUnavailable, match="every required capability"):
                async with prior:
                    pytest.fail("prior economics writer was exposed after capability retirement")
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "unit_of_work", [PostgresCiEconomicsUnitOfWork, PostgresWebhookIngestionUnitOfWork]
)
@pytest.mark.parametrize(
    ("damage", "repair", "message"),
    [
        (
            "ALTER TABLE ci_coordinator.ci_workflow_observations DROP CONSTRAINT "
            "ck_ci_workflow_observations_canonical_byte_limit",
            "ALTER TABLE ci_coordinator.ci_workflow_observations ADD CONSTRAINT "
            "ck_ci_workflow_observations_canonical_byte_limit CHECK "
            "(octet_length(observation_canonical_json) >= 1 AND "
            "octet_length(observation_canonical_json) <= 16384)",
            "CI economics schema",
        ),
        (
            "DROP INDEX ci_coordinator.ix_ci_workflow_observations_attempt_jobs",
            "CREATE INDEX ix_ci_workflow_observations_attempt_jobs ON "
            "ci_coordinator.ci_workflow_observations (installation_id, repository_id, "
            "workflow_run_id, run_attempt, head_sha, provider_job_id, semantic_hash, "
            "delivery_id) WHERE observation_kind = 'workflow_job'",
            "CI economics schema",
        ),
        (
            "ALTER TABLE ci_coordinator.ci_workflow_observations DISABLE TRIGGER "
            "tr_ci_workflow_observations_retention_guard",
            "ALTER TABLE ci_coordinator.ci_workflow_observations ENABLE TRIGGER "
            "tr_ci_workflow_observations_retention_guard",
            "CI economics schema",
        ),
        (
            "GRANT UPDATE ON ci_coordinator.ci_workflow_observations TO PUBLIC",
            "REVOKE UPDATE ON ci_coordinator.ci_workflow_observations FROM PUBLIC",
            "PUBLIC",
        ),
        (
            "ALTER TABLE ci_coordinator.ci_economics_budget_signals DROP CONSTRAINT "
            "fk_ci_economics_budget_signals_report",
            "ALTER TABLE ci_coordinator.ci_economics_budget_signals ADD CONSTRAINT "
            "fk_ci_economics_budget_signals_report FOREIGN KEY "
            "(report_id, subject_id, report_digest, retain_until) REFERENCES "
            "ci_coordinator.ci_job_measurement_reports "
            "(report_id, subject_id, report_digest, retain_until) ON DELETE CASCADE",
            "CI economics schema",
        ),
        (
            "ALTER TABLE ci_coordinator.ci_economics_budget_signals DISABLE TRIGGER "
            "tr_ci_economics_budget_signals_retention_guard",
            "ALTER TABLE ci_coordinator.ci_economics_budget_signals ENABLE TRIGGER "
            "tr_ci_economics_budget_signals_retention_guard",
            "CI economics schema",
        ),
        (
            "DROP INDEX ci_coordinator.ix_ci_economics_budget_signals_scope",
            "CREATE INDEX ix_ci_economics_budget_signals_scope ON "
            "ci_coordinator.ci_economics_budget_signals "
            '(installation_id, repository_id, signal_id COLLATE "C")',
            "CI economics schema",
        ),
        (
            "ALTER TABLE ci_coordinator.ci_economics_budget_policies DROP CONSTRAINT "
            "ck_ci_economics_budget_policies_payload",
            "ALTER TABLE ci_coordinator.ci_economics_budget_policies ADD CONSTRAINT "
            "ck_ci_economics_budget_policies_payload CHECK "
            "(octet_length(policy_canonical) BETWEEN 1 AND 2048)",
            "CI economics schema",
        ),
    ],
    ids=[
        "constraint",
        "index",
        "trigger",
        "public-acl",
        "budget-report-identity",
        "budget-retention",
        "budget-cursor-index",
        "budget-policy-bound",
    ],
)
def test_runtime_rejects_missing_economics_facts_despite_unchanged_declaration(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    unit_of_work: type[PostgresUnitOfWork],
    damage: str,
    repair: str,
    message: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        admin = create_postgres_engine(postgres_database_url)
        try:
            async with unit_of_work(engine) as healthy:
                await healthy.rollback()
            async with admin.begin() as connection:
                await connection.execute(text(damage))
            try:
                rejected = unit_of_work(engine)
                async with engine.connect() as connection:
                    declared = await admit_current_capabilities(
                        connection, rejected._profile, rejected._required_capabilities
                    )
                    assert declared is not None
                with pytest.raises(DatabaseCapabilityUnavailable, match=message):
                    async with rejected:
                        pytest.fail("runtime exposed repositories before independent attestation")
            finally:
                async with admin.begin() as connection:
                    await connection.execute(text(repair))
            async with unit_of_work(engine) as restored:
                await restored.rollback()
        finally:
            await admin.dispose()
            await engine.dispose()

    asyncio.run(scenario())

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import cast

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url
from testcontainers.community.postgres import PostgresContainer

from ci_coordinator.persistence.migration_settings import to_psycopg_connection_string
from ci_coordinator.persistence.runtime_principal_access import (
    apply_runtime_principal_access,
)

POSTGRES_IMAGE = (
    "postgres:18.6-trixie@sha256:86c951e05bf56c93d95d397747fb8820ac76cc3bedb78f43abd83eedbe3666ae"
)
ALEMBIC_CONFIG_PATH = Path(__file__).parents[3] / "alembic.ini"
RUNTIME_ROLE = "ci_coordinator_runtime_test"
RUNTIME_PASSWORD = "ci_coordinator_runtime_test_password"


def alembic_config(database_url: str) -> Config:
    config = Config(str(ALEMBIC_CONFIG_PATH))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


@pytest.fixture(scope="session")
def postgres_database_url() -> Iterator[str]:
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as postgres:
        yield postgres.get_connection_url(driver="psycopg")


@pytest.fixture(scope="session")
def runtime_postgres_database_url(postgres_database_url: str) -> str:
    return (
        make_url(postgres_database_url)
        .set(
            username=RUNTIME_ROLE,
            password=RUNTIME_PASSWORD,
        )
        .render_as_string(hide_password=False)
    )


@pytest.fixture
def unmigrated_database_url() -> Iterator[str]:
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as postgres:
        yield postgres.get_connection_url(driver="psycopg")


@pytest.fixture(autouse=True)
def clean_migrated_database(request: pytest.FixtureRequest) -> Iterator[None]:
    if "unmigrated_database_url" in request.fixturenames:
        yield
        return
    postgres_database_url = cast(str, request.getfixturevalue("postgres_database_url"))
    config = alembic_config(postgres_database_url)
    command.upgrade(config, "head")
    _provision_runtime_principal(postgres_database_url)
    _reset_ledger(postgres_database_url)
    yield
    command.upgrade(config, "head")
    _provision_runtime_principal(postgres_database_url)
    _reset_ledger(postgres_database_url)


def _reset_ledger(database_url: str) -> None:
    with (
        psycopg.connect(to_psycopg_connection_string(database_url)) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute(
            "TRUNCATE ci_coordinator.ci_economics_budget_signals, "
            "ci_coordinator.activity_events, ci_coordinator.activity_diagnostic_buckets, "
            "ci_coordinator.ci_economics_budget_policies, "
            "ci_coordinator.ci_observation_gaps, "
            "ci_coordinator.ci_observation_scans, "
            "ci_coordinator.ci_observation_subscriptions, "
            "ci_coordinator.ci_history_details, ci_coordinator.ci_history_jobs, "
            "ci_coordinator.ci_history_attempts, ci_coordinator.ci_history_gaps, "
            "ci_coordinator.ci_history_rechecks, ci_coordinator.ci_history_scans, "
            "ci_coordinator.analytics_purpose_settings, "
            "ci_coordinator.ci_history_datasets, "
            "ci_coordinator.ci_history_defaults, "
            "ci_coordinator.ci_history_delivery_inbox, "
            "ci_coordinator.ci_workflow_attempt_snapshot_jobs, "
            "ci_coordinator.ci_workflow_attempt_snapshots, "
            "ci_coordinator.ci_job_measurement_reports, "
            "ci_coordinator.ci_workflow_attempt_collections, "
            "ci_coordinator.ci_workflow_observations, "
            "ci_coordinator.production_scope_states, "
            "ci_coordinator.production_staged_grants, "
            "ci_coordinator.production_evidence_bundles, "
            "ci_coordinator.operator_overrides, "
            "ci_coordinator.governance_baseline_operations, "
            "ci_coordinator.governance_baselines, "
            "ci_coordinator.control_plane_logout_replays, "
            "ci_coordinator.control_plane_sessions, "
            "ci_coordinator.repository_attestation_transactions, "
            "ci_coordinator.reconciliation_results, "
            "ci_coordinator.reconciliation_observations, ci_coordinator.reconciliation_subjects, "
            "ci_coordinator.shadow_evidence, ci_coordinator.config_epoch_activations, "
            "ci_coordinator.active_config_epochs, ci_coordinator.workflow_proposal_reviews, "
            "ci_coordinator.config_epoch_registrations, ci_coordinator.config_epochs, "
            "ci_coordinator.webhook_deliveries, ci_coordinator.issued_plan_envelopes, "
            "ci_coordinator.production_admission_scope_bindings, "
            "ci_coordinator.production_admission_authorities"
        )
        cursor.execute(
            "INSERT INTO ci_coordinator.ci_history_defaults "
            "(singleton, revision, detail_policy_canonical, updated_at) "
            "VALUES (TRUE, 1, %s, clock_timestamp())",
            (b'{"anchor":"first_successful_detail_import","days":365,"mode":"days"}',),
        )
        cursor.execute(
            "INSERT INTO ci_coordinator.audit_ledger_head "
            "(head_id, revision, last_sequence, last_event_hash) VALUES (1, 0, NULL, NULL) "
            "ON CONFLICT (head_id) DO UPDATE SET revision = 0, last_sequence = NULL, "
            "last_event_hash = NULL"
        )
        cursor.execute("DELETE FROM ci_coordinator.audit_events")


def _provision_runtime_principal(database_url: str) -> None:
    with psycopg.connect(to_psycopg_connection_string(database_url)) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = %s", (RUNTIME_ROLE,))
            if cursor.fetchone() is None:
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
                    ).format(
                        sql.Identifier(RUNTIME_ROLE),
                        sql.Literal(RUNTIME_PASSWORD),
                    )
                )
            else:
                cursor.execute(
                    sql.SQL(
                        "ALTER ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB "
                        "NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS"
                    ).format(
                        sql.Identifier(RUNTIME_ROLE),
                        sql.Literal(RUNTIME_PASSWORD),
                    )
                )
        apply_runtime_principal_access(connection, RUNTIME_ROLE)

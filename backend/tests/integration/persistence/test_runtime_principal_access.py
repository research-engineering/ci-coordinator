from __future__ import annotations

from typing import Literal

import psycopg
import pytest
from psycopg import sql

from ci_coordinator.persistence.migration_settings import to_psycopg_connection_string
from ci_coordinator.persistence.runtime_principal_access import (
    RuntimePrincipalAccessError,
    apply_runtime_principal_access,
    check_runtime_principal_access,
)

from .conftest import RUNTIME_ROLE

pytestmark = pytest.mark.persistence


def test_apply_is_idempotent_and_repairs_direct_privilege_drift(
    postgres_database_url: str,
) -> None:
    with psycopg.connect(to_psycopg_connection_string(postgres_database_url)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("GRANT UPDATE, MAINTAIN ON ci_coordinator.audit_events TO {}").format(
                    sql.Identifier(RUNTIME_ROLE)
                )
            )
        with pytest.raises(RuntimePrincipalAccessError) as mismatch:
            check_runtime_principal_access(connection, RUNTIME_ROLE)
        assert mismatch.value.code == "runtime_access_mismatch"
        connection.rollback()

        apply_runtime_principal_access(connection, RUNTIME_ROLE)
        apply_runtime_principal_access(connection, RUNTIME_ROLE)
        check_runtime_principal_access(connection, RUNTIME_ROLE)


def test_apply_rejects_a_live_runtime_role_before_acl_mutation(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    with (
        psycopg.connect(
            to_psycopg_connection_string(runtime_postgres_database_url)
        ) as runtime_connection,
        psycopg.connect(to_psycopg_connection_string(postgres_database_url)) as connection,
    ):
        with runtime_connection.cursor() as runtime_cursor:
            runtime_cursor.execute("SELECT 1")
            assert runtime_cursor.fetchone() == (1,)

        with pytest.raises(RuntimePrincipalAccessError) as rejection:
            apply_runtime_principal_access(connection, RUNTIME_ROLE)

        assert rejection.value.code == "runtime_role_not_drained"
        connection.rollback()
        check_runtime_principal_access(connection, RUNTIME_ROLE)


def test_apply_removes_previously_granted_archive_default_column_updates(
    postgres_database_url: str,
) -> None:
    with psycopg.connect(to_psycopg_connection_string(postgres_database_url)) as connection:
        connection.execute(
            sql.SQL(
                "GRANT UPDATE (revision, detail_policy_canonical, updated_at) "
                "ON ci_coordinator.ci_history_defaults TO {}"
            ).format(sql.Identifier(RUNTIME_ROLE))
        )
        connection.commit()
        assert connection.execute(
            "SELECT has_any_column_privilege(%s, 'ci_coordinator.ci_history_defaults', 'UPDATE')",
            (RUNTIME_ROLE,),
        ).fetchone() == (True,)
        apply_runtime_principal_access(connection, RUNTIME_ROLE)
        check_runtime_principal_access(connection, RUNTIME_ROLE)
        assert connection.execute(
            "SELECT has_any_column_privilege(%s, 'ci_coordinator.ci_history_defaults', 'UPDATE')",
            (RUNTIME_ROLE,),
        ).fetchone() == (False,)


@pytest.mark.parametrize("attribute", ("INHERIT", "CREATEROLE"))
def test_role_attribute_preconditions_fail_without_mutating_existing_access(
    postgres_database_url: str,
    attribute: Literal["INHERIT", "CREATEROLE"],
) -> None:
    with psycopg.connect(to_psycopg_connection_string(postgres_database_url)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("ALTER ROLE {} {}").format(
                    sql.Identifier(RUNTIME_ROLE),
                    sql.SQL(attribute),
                )
            )
            cursor.execute(
                sql.SQL("GRANT UPDATE ON ci_coordinator.audit_events TO {}").format(
                    sql.Identifier(RUNTIME_ROLE)
                )
            )
        with pytest.raises(RuntimePrincipalAccessError) as rejection:
            apply_runtime_principal_access(connection, RUNTIME_ROLE)
        assert rejection.value.code == "runtime_role_unsafe"
        connection.rollback()

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_catalog.has_table_privilege(%s, "
                "'ci_coordinator.audit_events', 'UPDATE')",
                (RUNTIME_ROLE,),
            )
            assert cursor.fetchone() == (False,)


@pytest.mark.parametrize("runtime_is_parent", (False, True))
def test_either_membership_direction_is_rejected(
    postgres_database_url: str,
    runtime_is_parent: bool,
) -> None:
    other_role = "ci_coordinator_membership_probe"
    with psycopg.connect(to_psycopg_connection_string(postgres_database_url)) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(other_role)))
            parent = RUNTIME_ROLE if runtime_is_parent else other_role
            member = other_role if runtime_is_parent else RUNTIME_ROLE
            cursor.execute(
                sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(parent),
                    sql.Identifier(member),
                )
            )
        with pytest.raises(RuntimePrincipalAccessError) as rejection:
            check_runtime_principal_access(connection, RUNTIME_ROLE)
        assert rejection.value.code == "runtime_role_unsafe"
        connection.rollback()

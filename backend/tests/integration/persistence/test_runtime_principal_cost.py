from __future__ import annotations

import asyncio
import json

import pytest
from sqlalchemy import event, text
from sqlalchemy.sql.elements import TextClause

from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.principal_attestation import runtime_principal_is_restricted
from ci_coordinator.persistence.runtime_principal_access import (
    RUNTIME_COLUMN_GRANTS,
    RUNTIME_TABLE_GRANTS,
)

pytestmark = pytest.mark.persistence


def test_runtime_grant_maps_preserve_exact_owner_relations_in_one_admission_statement(
    runtime_postgres_database_url: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        captured: list[tuple[TextClause, dict[str, object]]] = []

        def capture(
            _connection: object,
            statement: object,
            _multiparams: object,
            parameters: object,
            _options: object,
        ) -> None:
            assert isinstance(statement, TextClause)
            assert isinstance(parameters, dict)
            assert all(isinstance(key, str) for key in parameters)
            captured.append((statement, dict(parameters)))

        try:
            async with engine.connect() as connection:
                event.listen(engine.sync_engine, "before_execute", capture)
                try:
                    assert await runtime_principal_is_restricted(connection, load_bundled_profile())
                finally:
                    event.remove(engine.sync_engine, "before_execute", capture)
                assert len(captured) == 1
                query, parameters = captured[0]
                source = str(query)
                table_payload = parameters["expected_table_grants"]
                column_payload = parameters["expected_column_grants"]
                assert isinstance(table_payload, str) and isinstance(column_payload, str)
                tables = json.loads(table_payload)
                columns = json.loads(column_payload)
                assert isinstance(tables, dict) and isinstance(columns, dict)
                assert set(tables) == set(RUNTIME_TABLE_GRANTS)
                assert set(columns) == set(RUNTIME_COLUMN_GRANTS)
                assert all(isinstance(privileges, list) for privileges in tables.values())
                assert all(isinstance(mapping, dict) for mapping in columns.values())
                assert all(
                    isinstance(privileges, list)
                    for mapping in columns.values()
                    for privileges in mapping.values()
                )
                assert {
                    (relation, privilege)
                    for relation, privileges in tables.items()
                    for privilege in privileges
                } == {
                    (relation, privilege)
                    for relation, privileges in RUNTIME_TABLE_GRANTS.items()
                    for privilege in privileges
                }
                assert {
                    (relation, column, privilege)
                    for relation, mapping in columns.items()
                    for column, privileges in mapping.items()
                    for privilege in privileges
                } == {
                    (relation, column, grant.privilege)
                    for relation, grants in RUNTIME_COLUMN_GRANTS.items()
                    for grant in grants
                    for column in grant.columns
                }
                observations: list[dict[str, object]] = []
                for sample in range(3):
                    result = await connection.scalar(
                        text("EXPLAIN (ANALYZE, TIMING OFF, FORMAT JSON) " + source),
                        parameters,
                    )
                    assert isinstance(result, list) and len(result) == 1
                    plan = result[0]
                    assert isinstance(plan, dict)
                    observations.append(
                        {
                            "sample": sample,
                            "planningMilliseconds": plan["Planning Time"],
                            "executionMilliseconds": plan["Execution Time"],
                        }
                    )
                with capsys.disabled():
                    print(json.dumps({"runtimePrincipalCost": observations}))
        finally:
            await engine.dispose()

    asyncio.run(scenario())

from datetime import datetime, timedelta

import pytest
from alembic import command
from ci_economics.report_factories import measurement_report
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import Connection
from tests.integration.persistence._ci_economics_support import provider_source
from tests.integration.persistence.conftest import alembic_config

from ci_coordinator.ci_economics import initial_collection_state, load_bundled_ci_economics_profile
from ci_coordinator.ci_economics.reports import MeasurementReportOrigin, StoredMeasurementReport
from ci_coordinator.persistence import migration_result_attestation
from ci_coordinator.persistence.ci_economics_collection_codec import encode_collection_record
from ci_coordinator.persistence.ci_economics_schema_attestation import (
    ci_economics_schema_matches_contract_sync,
)
from ci_coordinator.persistence.ci_economics_v2_schema_contract import V2_CATALOG
from ci_coordinator.persistence.ci_economics_v3_schema_contract import V3_CATALOG
from ci_coordinator.persistence.ci_measurement_report_codec import encode_measurement_report
from ci_coordinator.persistence.schema import (
    ci_economics_budget_policies,
    ci_economics_budget_signals,
    ci_job_measurement_reports,
    ci_workflow_attempt_collections,
)

pytestmark = pytest.mark.persistence
_PREDECESSOR = "20260908_0007"
_SUCCESSOR = "20260909_0009"


@pytest.mark.parametrize("reject_result", [False, True], ids=["upgrade", "atomic-rollback"])
def test_budget_upgrade_preserves_retained_report_bytes_and_retires_previous_writer(
    unmigrated_database_url: str, monkeypatch: pytest.MonkeyPatch, reject_result: bool
) -> None:
    config = alembic_config(unmigrated_database_url)
    command.upgrade(config, _PREDECESSOR)
    engine = create_engine(unmigrated_database_url)
    try:
        with engine.begin() as connection:
            now = connection.scalar(select(func.statement_timestamp()))
            assert isinstance(now, datetime)
            source = provider_source(771, now - timedelta(seconds=1))
            state = initial_collection_state(
                source.source_id,
                source.run_created_at,
                now,
                load_bundled_ci_economics_profile().collection_policy,
            )
            record = StoredMeasurementReport(
                measurement_report(attempt=source.attempt),
                source,
                MeasurementReportOrigin("c" * 64, "d" * 64),
                now,
                state.evidence_retain_until,
            )
            connection.execute(
                ci_workflow_attempt_collections.insert().values(
                    encode_collection_record(state, source)
                )
            )
            connection.execute(
                ci_job_measurement_reports.insert().values(encode_measurement_report(record))
            )
            before = tuple(connection.execute(select(ci_job_measurement_reports)))
            declarations = tuple(
                connection.execute(
                    text(
                        "SELECT * FROM ci_coordinator.database_compatibility_declarations "
                        "ORDER BY generation"
                    )
                )
            )
            assert ci_economics_schema_matches_contract_sync(connection, contract=V2_CATALOG)

        if reject_result:

            def reject(_connection: Connection) -> bool:
                raise RuntimeError("injected budget catalog rejection")

            with monkeypatch.context() as patch:
                patch.setitem(
                    migration_result_attestation._CAPABILITY_ATTESTORS,
                    "ci-economics-evidence/v3",
                    reject,
                )
                with pytest.raises(RuntimeError, match="injected budget catalog rejection"):
                    command.upgrade(config, _SUCCESSOR)
            with engine.connect() as connection:
                assert (
                    connection.scalar(text("SELECT version_num FROM public.alembic_version"))
                    == _PREDECESSOR
                )
                assert tuple(connection.execute(select(ci_job_measurement_reports))) == before
                assert (
                    tuple(
                        connection.execute(
                            text(
                                "SELECT * FROM ci_coordinator.database_compatibility_declarations "
                                "ORDER BY generation"
                            )
                        )
                    )
                    == declarations
                )
                assert (
                    connection.scalar(
                        text("SELECT to_regclass('ci_coordinator.ci_economics_budget_signals')")
                    )
                    is None
                )
                assert ci_economics_schema_matches_contract_sync(connection, contract=V2_CATALOG)

        command.upgrade(config, _SUCCESSOR)
        command.upgrade(config, _SUCCESSOR)
        with engine.connect() as connection:
            assert tuple(connection.execute(select(ci_job_measurement_reports))) == before
            for table in (ci_economics_budget_policies, ci_economics_budget_signals):
                assert connection.scalar(select(func.count()).select_from(table)) == 0
            assert ci_economics_schema_matches_contract_sync(connection, contract=V3_CATALOG)
            assert not ci_economics_schema_matches_contract_sync(connection, contract=V2_CATALOG)
            assert tuple(
                tuple(row)
                for row in connection.execute(
                    text(
                        "SELECT revision_id, transition_kind FROM "
                        "ci_coordinator.database_compatibility_declarations "
                        "WHERE generation >= 8 ORDER BY generation"
                    )
                )
            ) == (("20260909_0008", "contract"), (_SUCCESSOR, "expand"))
            assert set(
                connection.scalars(
                    text(
                        "SELECT capability_id FROM "
                        "ci_coordinator.database_compatibility_capabilities "
                        "WHERE revision_id = :revision "
                        "AND capability_id LIKE 'ci-economics-evidence/%'"
                    ),
                    {"revision": _SUCCESSOR},
                )
            ) == {"ci-economics-evidence/v3"}
        with pytest.raises(RuntimeError, match="forward"):
            command.downgrade(config, _PREDECESSOR)
    finally:
        engine.dispose()

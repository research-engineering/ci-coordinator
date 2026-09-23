from ci_economics.archive_analytics_factories import analytics_query
from sqlalchemy.dialects.postgresql import dialect

from ci_coordinator.ci_economics.archive_analytics_models import PurposeEntry, PurposeMapping
from ci_coordinator.persistence.ci_history_analytics_queries import (
    analytics_daily,
    analytics_job_count,
)


def test_native_sql_reads_scalar_timings_and_binds_exact_scope_and_job_as_parameters() -> None:
    name = "literal'); DROP TABLE example; --"
    query = analytics_query(job_name=name, purpose="lint")
    mapping = PurposeMapping(
        installation_id=101,
        repository_id=202,
        generation=1,
        version="v1",
        provenance="repository-config",
        entries=(PurposeEntry(workflow_id=9001, job_name=name, purposes=("lint", "build")),),
    )
    postgres = dialect()  # type: ignore[no-untyped-call]
    compiled = analytics_daily(query, mapping).compile(dialect=postgres)
    sql = str(compiled)
    assert name not in sql and name in compiled.params.values()
    assert "job_canonical" not in sql and "ci_history_details" not in sql
    assert "GROUP BY" in sql and "date_trunc" in sql
    assert "created_at" in sql and "started_at" in sql and "completed_at" in sql
    assert "generation" in sql and "installation_id" in sql and "repository_id" in sql
    count = analytics_job_count(query).compile(dialect=postgres)
    assert 1000001 in count.params.values()

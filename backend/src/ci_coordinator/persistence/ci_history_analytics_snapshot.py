from sqlalchemy import and_, func, select, true
from sqlalchemy.sql.selectable import Select

from ci_coordinator.ci_economics.archive_analytics_models import (
    MAX_ANALYTICS_ATTEMPTS,
    MAX_ANALYTICS_JOBS,
    AnalyticsQuery,
    PurposeMapping,
)
from ci_coordinator.persistence._schema_analytics_purpose import analytics_purpose_settings
from ci_coordinator.persistence._schema_ci_history_control import ci_history_datasets
from ci_coordinator.persistence.ci_history_analytics_queries import (
    analytics_daily,
    analytics_definition_changes,
    analytics_job_count,
    analytics_population,
)
from ci_coordinator.persistence.ci_history_state_store import history_scope_predicate


def analytics_snapshot_rows(
    query: AnalyticsQuery, mapping: PurposeMapping | None
) -> Select[tuple[object, ...]]:
    dataset = ci_history_datasets
    settings = analytics_purpose_settings
    population = (
        analytics_population(query)
        .add_columns(
            analytics_job_count(query).scalar_subquery().label("scoped_job_count"),
            analytics_definition_changes(query).scalar_subquery().label("definition_changes"),
        )
        .subquery("population")
    )
    daily = (
        analytics_daily(query, mapping)
        .where(
            population.c.attempts <= MAX_ANALYTICS_ATTEMPTS,
            population.c.scoped_job_count <= MAX_ANALYTICS_JOBS,
        )
        .correlate(population)
        .lateral("daily")
    )
    return (
        select(
            *dataset.c,
            *(column.label(f"purpose_{column.name}") for column in settings.c),
            *(column.label(f"population_{column.name}") for column in population.c),
            population.c.scoped_job_count,
            population.c.definition_changes,
            func.statement_timestamp().label("observed_at"),
            *daily.c,
        )
        .select_from(
            dataset.outerjoin(
                settings,
                and_(
                    dataset.c.installation_id == settings.c.installation_id,
                    dataset.c.repository_id == settings.c.repository_id,
                ),
            )
            .join(population, true())
            .outerjoin(daily, true())
        )
        .where(history_scope_predicate(dataset, query.scope))
        .order_by(daily.c.day)
        .limit(367)
    )

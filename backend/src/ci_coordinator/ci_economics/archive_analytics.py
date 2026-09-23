from ci_coordinator.ci_economics.archive_analytics_degradation import assess_degradation
from ci_coordinator.ci_economics.archive_analytics_forecast import forecast_occupancy
from ci_coordinator.ci_economics.archive_analytics_models import AnalyticsReport, AnalyticsSnapshot


def summarize_archive(snapshot: AnalyticsSnapshot) -> AnalyticsReport:
    snapshot = AnalyticsSnapshot.model_validate(snapshot)
    return AnalyticsReport(
        query=snapshot.query,
        observed_at=snapshot.observed_at,
        data_revision=snapshot.data_revision,
        configuration_revision=snapshot.configuration_revision,
        dataset_state=snapshot.dataset_state,
        mapping=snapshot.mapping,
        mapping_digest=None if snapshot.mapping is None else snapshot.mapping.digest,
        runs=snapshot.runs,
        attempts=snapshot.attempts,
        cohort=snapshot.cohort,
        buckets=snapshot.buckets,
        forecast=forecast_occupancy(snapshot.query, snapshot.buckets, snapshot.cohort),
        degradation=assess_degradation(
            snapshot.query, snapshot.buckets, snapshot.cohort, snapshot.mapping
        ),
    )

from datetime import timedelta
from typing import Annotated, Literal, Self

from pydantic import ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from ci_coordinator.ci_economics.archive_statistics import ArchiveInstant
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel, JsonTuple
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

MAX_ANALYTICS_ATTEMPTS = 100000
MAX_ANALYTICS_JOBS = 1000000
MAX_ANALYTICS_DAYS = 366
FORECAST_TRAINING_DAYS = 14
FORECAST_CALIBRATION_FOLDS = 4
FORECAST_MINIMUM_EVALUATION_FOLDS = 4
FORECAST_MINIMUM_COVERAGE_BPS = 8000
type Count = Annotated[int, Field(ge=0, le=MAX_SAFE_JSON_INTEGER)]
type PositiveId = Annotated[int, Field(ge=1, le=MAX_SAFE_JSON_INTEGER)]
type Purpose = Literal["lint", "typecheck", "test", "build", "deploy"]
type PurposeFilter = Literal["lint", "typecheck", "test", "build", "deploy", "mixed", "unknown"]
type JobName = Annotated[str, Field(min_length=1, max_length=512, pattern=r"^[^\x00]+$")]


class AnalyticsModel(EconomicsPayloadModel):
    model_config = ConfigDict(alias_generator=to_camel, validate_by_name=True)


class PurposeEntry(AnalyticsModel):
    workflow_id: PositiveId
    job_name: JobName
    purposes: JsonTuple[Purpose] = Field(min_length=1, max_length=5)

    @model_validator(mode="after")
    def unique_purposes(self) -> Self:
        if any(0xD800 <= ord(char) <= 0xDFFF for char in self.job_name):
            raise ValueError("job names require Unicode scalar text")
        if len(self.job_name.encode("utf-8")) > 2048:
            raise ValueError("job name exceeds its UTF-8 byte bound")
        if len(set(self.purposes)) != len(self.purposes):
            raise ValueError("purpose entries cannot repeat a category")
        return self


class PurposeMapping(AnalyticsModel):
    installation_id: PositiveId
    repository_id: PositiveId
    generation: PositiveId
    version: str = Field(min_length=1, max_length=64)
    provenance: str = Field(min_length=1, max_length=256)
    entries: JsonTuple[PurposeEntry] = Field(max_length=64)

    @model_validator(mode="after")
    def unique_jobs(self) -> Self:
        keys = {(entry.workflow_id, entry.job_name) for entry in self.entries}
        if len(keys) != len(self.entries):
            raise ValueError("purpose mapping repeats a workflow/job key")
        return self

    @property
    def digest(self) -> str:
        return hash_object(self.model_dump(mode="json"))


class AnalyticsQuery(AnalyticsModel):
    installation_id: PositiveId
    repository_id: PositiveId
    generation: PositiveId
    created_from: ArchiveInstant
    created_until: ArchiveInstant
    workflow_id: PositiveId | None = None
    job_name: JobName | None = None
    purpose: PurposeFilter | None = None
    horizon_days: int = Field(default=7, ge=1, le=30)
    minimum_daily_samples: int = Field(default=3, ge=1, le=1000)
    degradation_relative_bps: int = Field(default=2000, ge=1, le=100000)
    degradation_absolute_ms: int = Field(default=1000, ge=1, le=86400000)
    persistence_days: int = Field(default=3, ge=2, le=14)

    @model_validator(mode="after")
    def whole_day_window(self) -> Self:
        for instant in (self.created_from, self.created_until):
            if (instant.hour, instant.minute, instant.second, instant.microsecond) != (0, 0, 0, 0):
                raise ValueError("analytics windows require whole UTC days")
        if (
            not timedelta(0)
            < self.created_until - self.created_from
            <= timedelta(days=MAX_ANALYTICS_DAYS)
        ):
            raise ValueError("analytics window exceeds its bounded day range")
        return self

    @property
    def scope(self) -> RepositoryScope:
        return RepositoryScope(self.installation_id, self.repository_id)


class JobAggregate(AnalyticsModel):
    jobs: Count = 0
    failures: Count = 0
    cancellations: Count = 0
    duration_samples: Count = 0
    queue_samples: Count = 0
    runner_ms: Count = 0
    queue_ms: Count = 0
    inconsistent_timings: Count = 0
    missing_duration: Count = 0
    missing_queue: Count = 0
    mixed_jobs: Count = 0
    unknown_purpose_jobs: Count = 0

    @model_validator(mode="after")
    def sample_partition(self) -> Self:
        if (
            self.duration_samples + self.missing_duration + self.inconsistent_timings != self.jobs
            or self.queue_samples + self.missing_queue + self.inconsistent_timings != self.jobs
            or self.failures + self.cancellations > self.jobs
            or self.mixed_jobs + self.unknown_purpose_jobs > self.jobs
            or (self.duration_samples == 0 and self.runner_ms != 0)
            or (self.queue_samples == 0 and self.queue_ms != 0)
        ):
            raise ValueError("analytics sample counts do not partition selected jobs")
        return self


class DailyBucket(AnalyticsModel):
    day: ArchiveInstant
    coverage: Literal["unknown", "partial", "complete_retained"]
    runs: Count
    attempts: Count
    matching_attempts: Count
    complete_attempts: Count
    partial_attempts: Count
    unavailable_attempts: Count
    conflict_attempts: Count
    known_missing_jobs: Count
    unknown_population_attempts: Count
    conflict_excluded_jobs: Count
    selected: JobAggregate
    observed_runner_ms: Count | None
    observed_queue_ms: Count | None

    @model_validator(mode="after")
    def population_partition(self) -> Self:
        if (
            self.attempts
            != (
                self.complete_attempts
                + self.partial_attempts
                + self.unavailable_attempts
                + self.conflict_attempts
            )
            or not self.matching_attempts <= self.attempts
            or self.runs > self.attempts
        ):
            raise ValueError("daily attempt populations do not partition")
        expected = (
            "unknown"
            if not self.attempts
            else ("complete_retained" if self.complete_attempts == self.attempts else "partial")
        )
        if (
            self.coverage != expected
            or self.observed_runner_ms
            != (self.selected.runner_ms if self.selected.duration_samples else None)
            or self.observed_queue_ms
            != (self.selected.queue_ms if self.selected.queue_samples else None)
        ):
            raise ValueError("daily coverage or units contradict observed samples")
        return self


class CohortEvidence(AnalyticsModel):
    profile: Literal["workflow_blob_job_event/v1"] = "workflow_blob_job_event/v1"
    known_workflow_versions: Count
    unknown_workflow_attempts: Count
    event_count: Count
    definition_stable: bool
    observed_definition_changes: bool
    compatible: bool
    contributors: Literal["unclassified"] = "unclassified"


class AnalyticsSnapshot(AnalyticsModel):
    query: AnalyticsQuery
    observed_at: ArchiveInstant
    data_revision: PositiveId
    configuration_revision: PositiveId
    dataset_state: Literal["active", "paused"]
    mapping: PurposeMapping | None
    runs: Count
    attempts: int = Field(ge=0, le=MAX_ANALYTICS_ATTEMPTS)
    cohort: CohortEvidence
    buckets: JsonTuple[DailyBucket] = Field(max_length=MAX_ANALYTICS_DAYS)

    @model_validator(mode="after")
    def bind_population(self) -> Self:
        query = self.query
        if query.created_until > self.observed_at:
            raise ValueError("future or unfinished UTC days are not historical observations")
        if self.mapping is not None and (
            self.mapping.installation_id,
            self.mapping.repository_id,
            self.mapping.generation,
        ) != (query.installation_id, query.repository_id, query.generation):
            raise ValueError("purpose mapping crosses repository/generation")
        if query.purpose not in {None, "unknown"} and self.mapping is None:
            raise ValueError("purpose selection requires an explicit mapping")
        if len(self.buckets) != (query.created_until - query.created_from).days or any(
            bucket.day != query.created_from + timedelta(days=index)
            for index, bucket in enumerate(self.buckets)
        ):
            raise ValueError("buckets must cover each requested UTC day in order")
        if self.attempts != sum(b.attempts for b in self.buckets) or self.runs > self.attempts:
            raise ValueError("snapshot counts contradict the bucket population")
        if (
            sum(b.selected.jobs + b.conflict_excluded_jobs for b in self.buckets)
            > MAX_ANALYTICS_JOBS
        ):
            raise ValueError("snapshot jobs exceed the query budget")
        return self


class ForecastFold(AnalyticsModel):
    training_until: ArchiveInstant
    test_until: ArchiveInstant
    predicted_runner_ms: Count
    actual_runner_ms: Count
    lower_runner_ms: Count | None
    upper_runner_ms: Count | None
    phase: Literal["calibration", "evaluation"]

    @model_validator(mode="after")
    def fold_algebra(self) -> Self:
        if self.training_until >= self.test_until:
            raise ValueError("a forecast holdout must follow its training prefix")
        lower, upper = self.lower_runner_ms, self.upper_runner_ms
        if self.phase == "calibration":
            if lower is not None or upper is not None:
                raise ValueError("calibration folds cannot carry evaluated intervals")
        elif lower is None or upper is None or not lower <= self.predicted_runner_ms <= upper:
            raise ValueError("evaluation folds require an ordered prediction interval")
        return self


class ForecastResult(AnalyticsModel):
    model_version: Literal["expanding-daily-mean/v1"] = "expanding-daily-mean/v1"
    target: Literal["retained_run_creation_occupancy"] = "retained_run_creation_occupancy"
    backtest_basis: Literal["current_archive_reconstructed_chronology"] = (
        "current_archive_reconstructed_chronology"
    )
    status: Literal["available", "unavailable"]
    reason: Literal[
        "incomplete_daily_coverage",
        "insufficient_backtest",
        "poor_calibration",
        "incompatible_cohort",
        "conditional_on_unchanged_collection_and_workload",
    ]
    cutoff: ArchiveInstant
    horizon_days: int = Field(ge=1, le=30)
    predicted_runner_ms: Count | None = None
    lower_runner_ms: Count | None = None
    upper_runner_ms: Count | None = None
    backtest_mae_ms: Count | None = None
    empirical_coverage_bps: int | None = Field(default=None, ge=0, le=10000)
    usable_days: Count = 0
    duration_samples: Count = 0
    folds: JsonTuple[ForecastFold] = Field(default=(), max_length=MAX_ANALYTICS_DAYS)
    monetary_estimate: None = None
    monetary_unavailable_reason: Literal["no_explicit_versioned_tariff"] = (
        "no_explicit_versioned_tariff"
    )

    @model_validator(mode="after")
    def result_algebra(self) -> Self:
        predicted, lower, upper = (
            self.predicted_runner_ms,
            self.lower_runner_ms,
            self.upper_runner_ms,
        )
        if self.status == "available":
            if (
                self.reason != "conditional_on_unchanged_collection_and_workload"
                or predicted is None
                or lower is None
                or upper is None
                or not lower <= predicted <= upper
                or self.backtest_mae_ms is None
                or self.empirical_coverage_bps is None
                or self.empirical_coverage_bps < FORECAST_MINIMUM_COVERAGE_BPS
                or self.usable_days
                < FORECAST_TRAINING_DAYS
                + self.horizon_days
                * (FORECAST_CALIBRATION_FOLDS + FORECAST_MINIMUM_EVALUATION_FOLDS)
                or self.duration_samples < self.usable_days
                or sum(fold.phase == "calibration" for fold in self.folds)
                != FORECAST_CALIBRATION_FOLDS
                or sum(fold.phase == "evaluation" for fold in self.folds)
                < FORECAST_MINIMUM_EVALUATION_FOLDS
            ):
                raise ValueError("available forecast requires estimate, interval and support")
        elif (
            any(value is not None for value in (predicted, lower, upper))
            or self.reason == "conditional_on_unchanged_collection_and_workload"
        ):
            raise ValueError("unavailable forecast cannot carry an actionable estimate")
        if (self.backtest_mae_ms is None) != (self.empirical_coverage_bps is None):
            raise ValueError("backtest diagnostics require both error and coverage")
        if self.backtest_mae_ms is not None and not self.folds:
            raise ValueError("backtest diagnostics require their folds")
        evaluated = False
        previous = None
        for fold in self.folds:
            if (
                fold.test_until > self.cutoff
                or (previous is not None and fold.training_until != previous)
                or fold.test_until - fold.training_until != timedelta(days=self.horizon_days)
            ):
                raise ValueError("forecast folds require ordered horizon-sized holdouts")
            if evaluated and fold.phase == "calibration":
                raise ValueError("calibration cannot follow evaluation")
            evaluated = evaluated or fold.phase == "evaluation"
            previous = fold.test_until
        return self


class DegradationEvent(AnalyticsModel):
    key: str = Field(pattern=r"^[0-9a-f]{64}$")
    day: ArchiveInstant
    state: Literal["observed_slowdown", "recovered"]
    mean_duration_ms: Count


class DegradationResult(AnalyticsModel):
    model_version: Literal["fixed-baseline-hysteresis/v1"] = "fixed-baseline-hysteresis/v1"
    status: Literal["unavailable", "clear", "pending", "observed_slowdown", "recovered"]
    reason: Literal["incompatible_cohort", "insufficient_samples", "observed_duration_only"]
    baseline_until: ArchiveInstant | None = None
    baseline_mean_ms: Count | None = None
    baseline_samples: Count = 0
    events: JsonTuple[DegradationEvent] = Field(default=(), max_length=MAX_ANALYTICS_DAYS)
    contributor: Literal["unclassified"] = "unclassified"


class AnalyticsReport(AnalyticsSnapshot):
    schema_version: Literal["ci-history-analytics/v1"] = "ci-history-analytics/v1"
    provider_coverage: Literal["unknown"] = "unknown"
    unit: Literal["milliseconds"] = "milliseconds"
    mapping_digest: str | None
    forecast: ForecastResult
    degradation: DegradationResult

    @model_validator(mode="after")
    def derived_binding(self) -> Self:
        if self.mapping_digest != (None if self.mapping is None else self.mapping.digest):
            raise ValueError("report purpose mapping digest differs")
        if self.forecast.cutoff != self.query.created_until or (
            self.forecast.horizon_days != self.query.horizon_days
        ):
            raise ValueError("forecast does not bind the requested cutoff and horizon")
        if self.forecast.status == "available" and (
            self.forecast.usable_days != len(self.buckets)
            or self.forecast.duration_samples
            != sum(b.selected.duration_samples for b in self.buckets)
        ):
            raise ValueError("forecast support differs from the returned bucket population")
        return self


class AnalyticsUnavailable(AnalyticsModel):
    reason: Literal[
        "dataset_unavailable",
        "generation_changed",
        "query_budget_exceeded",
        "snapshot_changed",
        "purpose_mapping_unavailable",
        "future_window",
    ]

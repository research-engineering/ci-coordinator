from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal

from ci_coordinator.ci_economics.model import AttemptIdentity
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.kernel import hash_object
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

type ReportCounter = Literal["cpu_user", "cpu_system", "elapsed"]
type CounterUnavailableReason = Literal[
    "unsupported_platform", "counter_error", "out_of_range", "incomplete_scope"
]

REPORT_METHOD: Final = "waited_children/v1"
MAX_REPORT_BYTES: Final = 131_072
MAX_REPORTS_PER_ATTEMPT: Final = 2_000
REPORT_COUNTERS: Final = frozenset({"cpu_user", "cpu_system", "elapsed"})
_UNAVAILABLE_REASONS: Final = frozenset(
    {"unsupported_platform", "counter_error", "out_of_range", "incomplete_scope"}
)
_DIGEST = re.compile(r"[0-9a-f]{64}")
_SAMPLE_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")


@dataclass(frozen=True, slots=True)
class ReportMeasurement:
    counter: ReportCounter
    value_us: int | None
    unavailable_reason: CounterUnavailableReason | None = None

    def __post_init__(self) -> None:
        if type(self.counter) is not str or self.counter not in REPORT_COUNTERS:
            raise ValueError("report counter is unsupported")
        if self.value_us is None:
            if (
                type(self.unavailable_reason) is not str
                or self.unavailable_reason not in _UNAVAILABLE_REASONS
            ):
                raise ValueError("unavailable counter requires an exact reason")
        elif (
            type(self.value_us) is not int
            or not 0 <= self.value_us <= MAX_SAFE_JSON_INTEGER
            or self.unavailable_reason is not None
        ):
            raise ValueError("observed counter requires a nonnegative safe integer and no reason")

    @property
    def scope(self) -> Literal["waited_children", "reporter_interval"]:
        return "reporter_interval" if self.counter == "elapsed" else "waited_children"

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "counter": self.counter,
            "unit": "microsecond",
            "scope": self.scope,
            "value": self.value_us,
            "unavailableReason": self.unavailable_reason,
        }


@dataclass(frozen=True, slots=True)
class ReportedWorkload:
    """Producer declarations are matching hints, not independently verified equivalence."""

    protected_inputs_digest: str
    runner_class_digest: str
    cache_class_digest: str

    def __post_init__(self) -> None:
        for value in (
            self.protected_inputs_digest,
            self.runner_class_digest,
            self.cache_class_digest,
        ):
            if type(value) is not str or _DIGEST.fullmatch(value) is None:
                raise ValueError("reported workload dimensions require SHA-256 digests")

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "protectedInputsDigest": self.protected_inputs_digest,
            "runnerClassDigest": self.runner_class_digest,
            "cacheClassDigest": self.cache_class_digest,
        }


@dataclass(frozen=True, slots=True)
class JobMeasurementReport:
    attempt: AttemptIdentity
    provider_job_id: int
    check_run_id: int
    sample_key: str
    producer_digest: str
    workload: ReportedWorkload
    reported_at: datetime
    command_exit_code: int
    measurements: tuple[ReportMeasurement, ...]

    def __post_init__(self) -> None:
        if type(self.attempt) is not AttemptIdentity:
            raise TypeError("measurement report requires an exact attempt")
        if type(self.workload) is not ReportedWorkload:
            raise TypeError("measurement report requires an exact workload declaration")
        for value in (self.provider_job_id, self.check_run_id):
            if type(value) is not int or not 1 <= value <= MAX_SAFE_JSON_INTEGER:
                raise ValueError("measurement report job identities must be positive safe integers")
        require_report_sample_key(self.sample_key)
        require_report_digest(self.producer_digest)
        if (
            type(self.reported_at) is not datetime
            or self.reported_at.tzinfo is None
            or self.reported_at.utcoffset() is None
        ):
            raise ValueError("measurement report time must be timezone-aware")
        object.__setattr__(self, "reported_at", self.reported_at.astimezone(UTC))
        if (
            type(self.command_exit_code) is not int
            or abs(self.command_exit_code) > MAX_SAFE_JSON_INTEGER
        ):
            raise ValueError("command exit code must be an exact safe integer")
        if (
            type(self.measurements) is not tuple
            or len(self.measurements) != len(REPORT_COUNTERS)
            or any(type(item) is not ReportMeasurement for item in self.measurements)
            or {item.counter for item in self.measurements} != REPORT_COUNTERS
        ):
            raise ValueError("report must contain each method counter exactly once")
        object.__setattr__(self, "measurements", tuple(sorted(self.measurements, key=_counter_key)))

    @property
    def report_id(self) -> str:
        # A changed producer or value conflicts at this slot instead of adding another sample.
        return hash_object(
            {
                "schemaVersion": "ci-economics-job-report-identity/v1",
                "attempt": self.attempt.canonical_mapping(),
                "providerJobId": self.provider_job_id,
                "sampleKey": self.sample_key,
            }
        )

    @property
    def report_digest(self) -> str:
        return hash_object(self.canonical_mapping())

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": "ci-economics-job-report/v1",
            "method": REPORT_METHOD,
            "attempt": self.attempt.canonical_mapping(),
            "providerJobId": self.provider_job_id,
            "checkRunId": self.check_run_id,
            "sampleKey": self.sample_key,
            "producerDigest": self.producer_digest,
            "workload": self.workload.canonical_mapping(),
            "reportedAt": self.reported_at.isoformat(timespec="microseconds").replace(
                "+00:00", "Z"
            ),
            "commandExitCode": self.command_exit_code,
            "measurements": [item.canonical_mapping() for item in self.measurements],
        }


@dataclass(frozen=True, slots=True)
class MeasurementReportOrigin:
    producer_claim_hash: str
    provider_binding_digest: str

    def __post_init__(self) -> None:
        for value in (self.producer_claim_hash, self.provider_binding_digest):
            if type(value) is not str or _DIGEST.fullmatch(value) is None:
                raise ValueError("report origin requires exact receiver-observed digests")


@dataclass(frozen=True, slots=True)
class StoredMeasurementReport:
    report: JobMeasurementReport
    source: ProviderRunCollectionSource
    origin: MeasurementReportOrigin
    received_at: datetime
    retain_until: datetime

    def __post_init__(self) -> None:
        if type(self.report) is not JobMeasurementReport:
            raise TypeError("stored report requires an exact immutable payload")
        if type(self.source) is not ProviderRunCollectionSource:
            raise TypeError("stored report requires an exact provider source")
        if type(self.origin) is not MeasurementReportOrigin:
            raise TypeError("stored report requires exact receiver-observed origin")
        if self.report.attempt != self.source.attempt:
            raise ValueError("stored report differs from its provider attempt")
        for name, value in (("received_at", self.received_at), ("retain_until", self.retain_until)):
            if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("stored report requires aware receipt and retention instants")
            object.__setattr__(self, name, value.astimezone(UTC))
        if not self.source.run_created_at <= self.received_at < self.retain_until:
            raise ValueError("stored report receipt is outside its source lifetime")


def _counter_key(value: ReportMeasurement) -> str:
    return value.counter


def require_report_sample_key(
    value: object, *, message: str = "measurement sample key must be a bounded ASCII identifier"
) -> None:
    if type(value) is not str or _SAMPLE_KEY.fullmatch(value) is None:
        raise ValueError(message)


def require_report_digest(value: object) -> None:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError("measurement producer requires a lowercase SHA-256 digest")

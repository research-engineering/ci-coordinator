from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
from typing import Final, Literal, cast

from ci_coordinator.kernel import StrictJsonError, hash_object, load_strict_json

type CapacityMetricComparison = Literal["lte"]
type CapacityMetricUnit = Literal["bytes", "bytes-per-day", "count", "microseconds"]


@dataclass(frozen=True, slots=True)
class CapacityMetricDefinition:
    metric_id: str
    unit: CapacityMetricUnit
    comparison: CapacityMetricComparison

    def to_mapping(self) -> dict[str, object]:
        return {
            "comparison": self.comparison,
            "metricId": self.metric_id,
            "unit": self.unit,
        }


@dataclass(frozen=True, slots=True)
class CapacityQualificationLimits:
    maximum_clock_skew_seconds: int
    maximum_envelope_bytes: int
    maximum_evidence_age_seconds: int
    maximum_receipt_lifetime_seconds: int
    maximum_sample_count: int

    def to_mapping(self) -> dict[str, object]:
        return {
            "maximumClockSkewSeconds": self.maximum_clock_skew_seconds,
            "maximumEnvelopeBytes": self.maximum_envelope_bytes,
            "maximumEvidenceAgeSeconds": self.maximum_evidence_age_seconds,
            "maximumReceiptLifetimeSeconds": self.maximum_receipt_lifetime_seconds,
            "maximumSampleCount": self.maximum_sample_count,
        }


@dataclass(frozen=True, slots=True)
class CapacityQualificationProfile:
    schema_version: int
    profile_id: str
    receipt_schema: str
    envelope_schema: str
    algorithm: str
    fixed_budgets: tuple[tuple[str, int], ...]
    limits: CapacityQualificationLimits
    metrics: tuple[CapacityMetricDefinition, ...]
    non_claims: tuple[str, ...]

    @property
    def digest(self) -> str:
        return hash_object(self.to_mapping())

    @property
    def metric_ids(self) -> tuple[str, ...]:
        return tuple(metric.metric_id for metric in self.metrics)

    def to_mapping(self) -> dict[str, object]:
        return {
            "algorithm": self.algorithm,
            "envelopeSchema": self.envelope_schema,
            "fixedBudgets": dict(self.fixed_budgets),
            "limits": self.limits.to_mapping(),
            "metrics": [metric.to_mapping() for metric in self.metrics],
            "nonClaims": list(self.non_claims),
            "profileId": self.profile_id,
            "receiptSchema": self.receipt_schema,
            "schemaVersion": self.schema_version,
        }


def _load_profile() -> CapacityQualificationProfile:
    content = (
        files(__package__)
        .joinpath("resources", "capacity-qualification-profile.v1.json")
        .read_bytes()
    )
    try:
        root = _exact_object(
            load_strict_json(content, max_bytes=65_536),
            {
                "algorithm",
                "envelopeSchema",
                "fixedBudgets",
                "limits",
                "metrics",
                "nonClaims",
                "profileId",
                "receiptSchema",
                "schemaVersion",
            },
        )
        limits = _limits(root["limits"])
        metrics = _metrics(root["metrics"])
        non_claims = _text_tuple(root["nonClaims"], "capacity profile non-claims")
        profile = CapacityQualificationProfile(
            schema_version=_positive_integer(root["schemaVersion"], "profile schema"),
            profile_id=_text(root["profileId"], "profile id"),
            receipt_schema=_text(root["receiptSchema"], "receipt schema"),
            envelope_schema=_text(root["envelopeSchema"], "envelope schema"),
            algorithm=_text(root["algorithm"], "algorithm"),
            fixed_budgets=_fixed_budgets(root["fixedBudgets"]),
            limits=limits,
            metrics=metrics,
            non_claims=non_claims,
        )
    except (KeyError, StrictJsonError, TypeError, ValueError) as error:
        raise RuntimeError("capacity qualification profile is invalid") from error
    if (
        profile.schema_version != 1
        or profile.profile_id != "ci-coordinator-capacity-qualification/v1"
        or profile.receipt_schema != "ci-coordinator-capacity-receipt/v1"
        or profile.envelope_schema != "ci-coordinator-capacity-envelope/v1"
        or profile.algorithm != "Ed25519"
        or tuple(metric_id for metric_id, _ in profile.fixed_budgets)
        != ("forced-termination-count", "safety-invariant-violation-count")
        or not {metric_id for metric_id, _ in profile.fixed_budgets}.issubset(profile.metric_ids)
        or not profile.non_claims
    ):
        raise RuntimeError("capacity qualification profile identity is invalid")
    return profile


def _limits(value: object) -> CapacityQualificationLimits:
    record = _exact_object(
        value,
        {
            "maximumClockSkewSeconds",
            "maximumEnvelopeBytes",
            "maximumEvidenceAgeSeconds",
            "maximumReceiptLifetimeSeconds",
            "maximumSampleCount",
        },
    )
    return CapacityQualificationLimits(
        maximum_clock_skew_seconds=_positive_integer(
            record["maximumClockSkewSeconds"], "maximum clock skew"
        ),
        maximum_envelope_bytes=_positive_integer(
            record["maximumEnvelopeBytes"], "maximum envelope bytes"
        ),
        maximum_evidence_age_seconds=_positive_integer(
            record["maximumEvidenceAgeSeconds"], "maximum evidence age"
        ),
        maximum_receipt_lifetime_seconds=_positive_integer(
            record["maximumReceiptLifetimeSeconds"], "maximum receipt lifetime"
        ),
        maximum_sample_count=_positive_integer(
            record["maximumSampleCount"], "maximum sample count"
        ),
    )


def _metrics(value: object) -> tuple[CapacityMetricDefinition, ...]:
    if type(value) is not list or not value:
        raise ValueError("capacity profile requires metrics")
    metrics: list[CapacityMetricDefinition] = []
    for item in value:
        record = _exact_object(item, {"comparison", "metricId", "unit"})
        comparison = _text(record["comparison"], "metric comparison")
        unit = _text(record["unit"], "metric unit")
        if comparison != "lte" or unit not in {"bytes", "bytes-per-day", "count", "microseconds"}:
            raise ValueError("capacity metric algebra is unsupported")
        metrics.append(
            CapacityMetricDefinition(
                metric_id=_text(record["metricId"], "metric id"),
                unit=cast(CapacityMetricUnit, unit),
                comparison=cast(CapacityMetricComparison, comparison),
            )
        )
    metric_ids = tuple(metric.metric_id for metric in metrics)
    if tuple(sorted(set(metric_ids))) != metric_ids:
        raise ValueError("capacity profile metrics must be unique and sorted")
    return tuple(metrics)


def _fixed_budgets(value: object) -> tuple[tuple[str, int], ...]:
    if type(value) is not dict or not value:
        raise ValueError("capacity profile fixed budgets are invalid")
    budgets: list[tuple[str, int]] = []
    for metric_id, budget in value.items():
        budgets.append(
            (
                _text(metric_id, "fixed-budget metric id"),
                _nonnegative_integer(budget, "fixed budget"),
            )
        )
    metric_ids = tuple(metric_id for metric_id, _ in budgets)
    if tuple(sorted(set(metric_ids))) != metric_ids:
        raise ValueError("capacity fixed budgets must be unique and sorted")
    return tuple(budgets)


def _exact_object(value: object, keys: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("capacity profile object shape is invalid")
    return cast(dict[str, object], value)


def _text(value: object, name: str) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > 512:
        raise ValueError(f"{name} is invalid")
    return value


def _text_tuple(value: object, name: str) -> tuple[str, ...]:
    if type(value) is not list or not value:
        raise ValueError(f"{name} is invalid")
    return tuple(_text(item, name) for item in value)


def _positive_integer(value: object, name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} is invalid")
    return value


def _nonnegative_integer(value: object, name: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} is invalid")
    return value


CAPACITY_QUALIFICATION_PROFILE: Final = _load_profile()
CAPACITY_QUALIFICATION_PROFILE_DIGEST: Final = CAPACITY_QUALIFICATION_PROFILE.digest

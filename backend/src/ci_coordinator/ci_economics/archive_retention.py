from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final, Literal

from ci_coordinator.ci_economics._observation_values import positive_id, utc_time

DEFAULT_ARCHIVE_DETAIL_DAYS: Final = 365
MAX_ARCHIVE_DETAIL_DAYS: Final = 36_500
type DetailRetentionMode = Literal["disabled", "days", "forever"]
type ArchiveDetailState = Literal["not_imported", "retained", "expired"]
type DetailPolicySource = Literal["service_default", "repository_override"]


@dataclass(frozen=True, slots=True)
class DetailPolicyReference:
    source: DetailPolicySource
    revision: int

    def __post_init__(self) -> None:
        if self.source not in {"service_default", "repository_override"}:
            raise ValueError("unknown detail policy source")
        positive_id(self.revision, "detail policy source revision")


@dataclass(frozen=True, slots=True)
class DetailRetentionPolicy:
    mode: DetailRetentionMode
    days: int | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"disabled", "days", "forever"}:
            raise ValueError("unsupported archive detail retention mode")
        if self.mode == "days":
            if type(self.days) is not int or not 1 <= self.days <= MAX_ARCHIVE_DETAIL_DAYS:
                raise ValueError("detail duration must be a positive bounded integer")
        elif self.days is not None:
            raise ValueError("only days retention admits a duration")

    @classmethod
    def default(cls) -> DetailRetentionPolicy:
        return cls("days", DEFAULT_ARCHIVE_DETAIL_DAYS)

    def canonical_mapping(self) -> dict[str, object]:
        if self.mode == "days":
            return {
                "mode": self.mode,
                "days": self.days,
                "anchor": "first_successful_detail_import",
            }
        return {"mode": self.mode}


@dataclass(frozen=True, slots=True)
class ArchiveDetailRetention:
    state: ArchiveDetailState = "not_imported"
    first_imported_at: datetime | None = None
    applied_policy: DetailRetentionPolicy | None = None
    applied_reference: DetailPolicyReference | None = None

    def __post_init__(self) -> None:
        if self.state not in {"not_imported", "retained", "expired"}:
            raise ValueError("unsupported archive detail state")
        if self.state == "not_imported":
            if any(
                value is not None
                for value in (self.first_imported_at, self.applied_policy, self.applied_reference)
            ):
                raise ValueError("never imported detail cannot have applied retention")
            return
        if (
            type(self.first_imported_at) is not datetime
            or type(self.applied_policy) is not DetailRetentionPolicy
        ):
            raise TypeError("imported detail requires exact first import and applied policy")
        if type(self.applied_reference) is not DetailPolicyReference:
            raise ValueError("imported detail requires its exact applied policy reference")
        object.__setattr__(self, "first_imported_at", utc_time(self.first_imported_at))
        if self.state == "retained" and self.applied_policy.mode == "disabled":
            raise ValueError("disabled applied policy cannot admit retained detail")
        _ = self.expires_at

    @property
    def expires_at(self) -> datetime | None:
        if self.applied_policy is None or self.applied_policy.days is None:
            return None
        if self.first_imported_at is None:
            raise ValueError("finite detail retention requires a first import")
        try:
            return self.first_imported_at + timedelta(days=self.applied_policy.days)
        except OverflowError:
            raise ValueError("detail deadline is outside the representable time domain") from None

    def due_at(self, now: datetime) -> bool:
        now = utc_time(now)
        deadline = self.expires_at
        return self.state == "retained" and deadline is not None and now >= deadline


def record_first_detail_import(
    prior: ArchiveDetailRetention,
    policy: DetailRetentionPolicy,
    reference: DetailPolicyReference,
    *,
    now: datetime,
) -> ArchiveDetailRetention:
    if type(prior) is not ArchiveDetailRetention or type(policy) is not DetailRetentionPolicy:
        raise TypeError("detail import requires exact retention values")
    if type(reference) is not DetailPolicyReference:
        raise TypeError("detail import requires an exact policy reference")
    now = utc_time(now)
    if prior.state != "not_imported" or policy.mode == "disabled":
        return prior
    return ArchiveDetailRetention("retained", now, policy, reference)


def expire_archive_detail(
    prior: ArchiveDetailRetention, *, now: datetime
) -> ArchiveDetailRetention:
    if type(prior) is not ArchiveDetailRetention:
        raise TypeError("detail expiry requires an exact retention record")
    return replace(prior, state="expired") if prior.due_at(now) else prior


def apply_detail_policy(
    prior: ArchiveDetailRetention,
    policy: DetailRetentionPolicy,
    reference: DetailPolicyReference,
    *,
    now: datetime,
) -> ArchiveDetailRetention:
    if type(prior) is not ArchiveDetailRetention or type(policy) is not DetailRetentionPolicy:
        raise TypeError("detail policy application requires exact retention values")
    if type(reference) is not DetailPolicyReference:
        raise TypeError("detail application requires an exact policy reference")
    now = utc_time(now)
    previous_reference = prior.applied_reference
    if previous_reference is not None:
        if reference.source == previous_reference.source and reference.revision < (
            previous_reference.revision
        ):
            raise ValueError("detail policy application cannot regress its revision")
        if reference == previous_reference and policy != prior.applied_policy:
            raise ValueError("one policy revision cannot name conflicting policies")
    prior = expire_archive_detail(prior, now=now)
    if prior.state != "retained":
        return prior
    if reference == prior.applied_reference:
        return prior
    successor = replace(
        prior,
        state="expired" if policy.mode == "disabled" else "retained",
        applied_policy=policy,
        applied_reference=reference,
    )
    return expire_archive_detail(successor, now=now)

"""Fail-closed resolution of durable validation-increasing overrides."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls.override import ActiveOverride


@dataclass(frozen=True, slots=True)
class ActiveOverrideRecords:
    """Latest active record for each independent validation-increasing control."""

    force_full_ci: ActiveOverride | None = None
    disable_dynamic: ActiveOverride | None = None


@dataclass(frozen=True, slots=True)
class OverrideLookupUnavailable:
    """The durable authority could not prove the current override state."""


type OverrideLookupResult = ActiveOverrideRecords | OverrideLookupUnavailable


class ActiveOverrideReader(Protocol):
    async def resolve_active(
        self,
        *,
        scope: RepositoryScope,
        subject_id: str | None,
        now: datetime,
    ) -> OverrideLookupResult: ...


@dataclass(frozen=True, slots=True)
class OverrideResolution:
    """A planning-facing result whose unavailable state always disables omission."""

    lookup_available: bool
    force_full_ci_override: ActiveOverride | None = None
    disable_dynamic_override: ActiveOverride | None = None

    def __post_init__(self) -> None:
        if type(self.lookup_available) is not bool:
            raise ValueError("override lookup availability must be exact")
        if not self.lookup_available and (
            self.force_full_ci_override is not None or self.disable_dynamic_override is not None
        ):
            raise ValueError("unavailable override lookup cannot expose records")
        for override, kind in (
            (self.force_full_ci_override, "force_full_ci"),
            (self.disable_dynamic_override, "disable_omission"),
        ):
            if override is not None and (
                type(override) is not ActiveOverride or override.command.kind != kind
            ):
                raise ValueError("override resolution record kind is invalid")

    @property
    def force_full_ci(self) -> bool:
        return (
            not self.lookup_available
            or self.force_full_ci_override is not None
            or self.disable_dynamic_override is not None
        )

    @property
    def dynamic_ci_disabled(self) -> bool:
        return self.force_full_ci


async def resolve_active_overrides(
    reader: ActiveOverrideReader,
    *,
    scope: RepositoryScope,
    subject_id: str | None,
    now: datetime,
) -> OverrideResolution:
    """Resolve exact active records and turn every unprovable state into Full CI."""

    _require_query(scope, subject_id, now)
    result = await reader.resolve_active(scope=scope, subject_id=subject_id, now=now)
    if type(result) is OverrideLookupUnavailable:
        return OverrideResolution(lookup_available=False)
    if type(result) is not ActiveOverrideRecords:
        return OverrideResolution(lookup_available=False)
    if not _records_match_query(result, scope=scope, subject_id=subject_id, now=now):
        return OverrideResolution(lookup_available=False)
    return OverrideResolution(
        lookup_available=True,
        force_full_ci_override=result.force_full_ci,
        disable_dynamic_override=result.disable_dynamic,
    )


def _records_match_query(
    records: ActiveOverrideRecords,
    *,
    scope: RepositoryScope,
    subject_id: str | None,
    now: datetime,
) -> bool:
    force = records.force_full_ci
    if force is not None and (
        type(force) is not ActiveOverride
        or force.command.kind != "force_full_ci"
        or force.command.scope != scope
        or subject_id is None
        or force.command.subject_id != subject_id
        or not force.applies_at(now)
    ):
        return False
    disable = records.disable_dynamic
    return disable is None or (
        type(disable) is ActiveOverride
        and disable.command.kind == "disable_omission"
        and disable.command.scope == scope
        and disable.applies_at(now)
    )


def _require_query(
    scope: RepositoryScope,
    subject_id: str | None,
    now: datetime,
) -> None:
    if type(scope) is not RepositoryScope:
        raise ValueError("override resolution scope must be exact")
    if subject_id is not None and (
        type(subject_id) is not str or not subject_id or len(subject_id.encode("utf-8")) > 512
    ):
        raise ValueError("override resolution subject must be bounded text or null")
    if type(now) is not datetime or now.tzinfo is None:
        raise ValueError("override resolution time must be timezone-aware")

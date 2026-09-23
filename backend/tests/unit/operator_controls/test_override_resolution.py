from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.operator_controls.override import ActiveOverride, OverrideCommand
from ci_coordinator.operator_controls.resolution import (
    ActiveOverrideRecords,
    OverrideLookupResult,
    OverrideLookupUnavailable,
    OverrideResolution,
    resolve_active_overrides,
)

NOW = datetime(2026, 7, 15, 10, tzinfo=UTC)
SCOPE = RepositoryScope(101, 202)


@dataclass
class _Reader:
    result: OverrideLookupResult

    async def resolve_active(
        self,
        *,
        scope: RepositoryScope,
        subject_id: str | None,
        now: datetime,
    ) -> OverrideLookupResult:
        assert scope == SCOPE
        assert subject_id == "run-7"
        assert now == NOW
        return self.result


def test_no_active_override_preserves_dynamic_planning() -> None:
    resolution = asyncio.run(
        resolve_active_overrides(
            _Reader(ActiveOverrideRecords()),
            scope=SCOPE,
            subject_id="run-7",
            now=NOW,
        )
    )

    assert resolution.lookup_available is True
    assert resolution.force_full_ci is False
    assert resolution.dynamic_ci_disabled is False


def test_matching_subject_and_repository_controls_both_force_full_ci() -> None:
    force = _override("force_full_ci", subject_id="run-7", operation_id="force-1")
    disable = _override(
        "disable_omission",
        subject_id=None,
        operation_id="disable-1",
    )

    resolution = asyncio.run(
        resolve_active_overrides(
            _Reader(ActiveOverrideRecords(force, disable)),
            scope=SCOPE,
            subject_id="run-7",
            now=NOW,
        )
    )

    assert resolution.lookup_available is True
    assert resolution.force_full_ci_override == force
    assert resolution.disable_dynamic_override == disable
    assert resolution.force_full_ci is True
    assert resolution.dynamic_ci_disabled is True


def test_unavailable_or_invalid_lookup_fails_closed() -> None:
    unavailable = asyncio.run(
        resolve_active_overrides(
            _Reader(OverrideLookupUnavailable()),
            scope=SCOPE,
            subject_id="run-7",
            now=NOW,
        )
    )
    wrong_subject = asyncio.run(
        resolve_active_overrides(
            _Reader(
                ActiveOverrideRecords(
                    force_full_ci=_override(
                        "force_full_ci",
                        subject_id="another-run",
                        operation_id="force-2",
                    )
                )
            ),
            scope=SCOPE,
            subject_id="run-7",
            now=NOW,
        )
    )
    expired = asyncio.run(
        resolve_active_overrides(
            _Reader(
                ActiveOverrideRecords(
                    force_full_ci=_override(
                        "force_full_ci",
                        subject_id="run-7",
                        operation_id="force-3",
                        applied_at=NOW - timedelta(minutes=2),
                        expires_at=NOW,
                    )
                )
            ),
            scope=SCOPE,
            subject_id="run-7",
            now=NOW,
        )
    )

    for resolution in (unavailable, wrong_subject, expired):
        assert resolution.lookup_available is False
        assert resolution.force_full_ci is True
        assert resolution.dynamic_ci_disabled is True


def test_query_rejects_ambiguous_values_before_reader_access() -> None:
    reader = _Reader(ActiveOverrideRecords())

    with pytest.raises(ValueError, match="subject"):
        asyncio.run(
            resolve_active_overrides(
                reader,
                scope=SCOPE,
                subject_id="",
                now=NOW,
            )
        )
    with pytest.raises(ValueError, match="timezone"):
        asyncio.run(
            resolve_active_overrides(
                reader,
                scope=SCOPE,
                subject_id="run-7",
                now=datetime(2026, 7, 15, 10),
            )
        )


def test_reader_cancellation_propagates() -> None:
    class _CancellingReader:
        async def resolve_active(
            self,
            *,
            scope: RepositoryScope,
            subject_id: str | None,
            now: datetime,
        ) -> OverrideLookupResult:
            del scope, subject_id, now
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            resolve_active_overrides(
                _CancellingReader(),
                scope=SCOPE,
                subject_id="run-7",
                now=NOW,
            )
        )


def test_resolution_value_rejects_an_unavailable_record_claim() -> None:
    with pytest.raises(ValueError, match="unavailable"):
        OverrideResolution(
            lookup_available=False,
            force_full_ci_override=_override(
                "force_full_ci",
                subject_id="run-7",
                operation_id="contradiction",
            ),
        )


def _override(
    kind: str,
    *,
    subject_id: str | None,
    operation_id: str,
    applied_at: datetime = NOW,
    expires_at: datetime | None = None,
) -> ActiveOverride:
    expiry = (
        expires_at
        if expires_at is not None
        else (NOW + timedelta(minutes=10) if kind == "force_full_ci" else None)
    )
    command = OverrideCommand(
        kind,  # type: ignore[arg-type]
        SCOPE,
        subject_id,
        operation_id,
        "operator",
        "safety investigation",
        expiry,
    )
    return ActiveOverride.create(command, applied_at)

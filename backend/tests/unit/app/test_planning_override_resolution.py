from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from planning_command_support import dynamic_plan_command as _command

from ci_coordinator.app import DurablePlanningOverrideResolver
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import FixedClock
from ci_coordinator.operator_controls import ActiveOverride, OverrideCommand
from ci_coordinator.operator_controls.resolution import (
    ActiveOverrideRecords,
    OverrideLookupResult,
    OverrideLookupUnavailable,
)

NOW = datetime(2026, 7, 15, tzinfo=UTC)


class _Reader:
    def __init__(self, result: OverrideLookupResult) -> None:
        self._result = result
        self.subject_ids: list[str | None] = []

    async def resolve_active(
        self,
        *,
        scope: RepositoryScope,
        subject_id: str | None,
        now: datetime,
    ) -> OverrideLookupResult:
        assert scope == RepositoryScope(100, 200)
        assert now == NOW
        self.subject_ids.append(subject_id)
        return self._result


def test_unavailable_override_authority_forces_full_ci() -> None:
    resolver = DurablePlanningOverrideResolver(
        _Reader(OverrideLookupUnavailable()),
        FixedClock(NOW),
    )

    decision = asyncio.run(resolver.resolve(_command(NOW)))

    assert decision.force_full_ci is True
    assert decision.reason == "override_state_unavailable"


def test_active_repository_override_forces_full_ci() -> None:
    active = ActiveOverride.create(
        OverrideCommand(
            "disable_omission",
            RepositoryScope(100, 200),
            None,
            "operation-1",
            "operator",
            "incident response",
            None,
        ),
        NOW,
    )
    reader = _Reader(ActiveOverrideRecords(disable_dynamic=active))
    resolver = DurablePlanningOverrideResolver(reader, FixedClock(NOW))

    decision = asyncio.run(resolver.resolve(_command(NOW)))

    assert decision.force_full_ci is True
    assert decision.reason == "operator_override"
    assert reader.subject_ids[0] is not None
    assert len(reader.subject_ids[0]) == 64


def test_absent_override_preserves_dynamic_planning() -> None:
    resolver = DurablePlanningOverrideResolver(
        _Reader(ActiveOverrideRecords()),
        FixedClock(NOW),
    )

    decision = asyncio.run(resolver.resolve(_command(NOW)))

    assert decision.force_full_ci is False
    assert decision.reason is None

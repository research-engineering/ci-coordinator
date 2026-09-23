from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import cast

import pytest

from ci_coordinator.ci_economics.discovery import (
    ProviderObservationPage,
    ProviderRunDiscoveryPage,
    RunDiscoveryWindow,
)
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.config_control import RepositoryScope

from .factories import ATTEMPT

START = datetime(2026, 9, 1, tzinfo=UTC)
END = START + timedelta(days=7)
WINDOW = RunDiscoveryWindow(START, END)
SOURCE = ProviderRunCollectionSource(ATTEMPT, START, "2026-03-10", "e" * 64)


@pytest.mark.parametrize("end", [START, END])
def test_window_boundaries_and_timezone_normalization(end: datetime) -> None:
    localized = START.astimezone(timezone(timedelta(hours=2)))
    assert RunDiscoveryWindow(localized, end) == RunDiscoveryWindow(START, end)


@pytest.mark.parametrize(
    "start,end",
    [
        (START, START - timedelta(seconds=1)),
        (START, END + timedelta(seconds=1)),
        (START.replace(tzinfo=None), END),
        (START, END.replace(tzinfo=None)),
        (START + timedelta(microseconds=1), END),
        (START, END + timedelta(microseconds=1)),
    ],
)
def test_window_rejects_each_invalid_time_operand(start: datetime, end: datetime) -> None:
    with pytest.raises(ValueError):
        RunDiscoveryWindow(start, end)


@pytest.mark.parametrize("value", [None, "2026-09-01T00:00:00Z", 1])
def test_window_requires_typed_times(value: object) -> None:
    with pytest.raises(ValueError):
        RunDiscoveryWindow(cast(datetime, value), END)


@pytest.mark.parametrize("created_at", [START, END])
def test_page_contains_both_window_boundaries(created_at: datetime) -> None:
    page = ProviderRunDiscoveryPage(
        ATTEMPT.scope, WINDOW, 1, 1, (replace(SOURCE, run_created_at=created_at),), "exhausted"
    )
    assert page.sources[0].run_created_at == created_at


@pytest.mark.parametrize(
    "source",
    [
        replace(SOURCE, attempt=replace(ATTEMPT, scope=RepositoryScope(102, 202))),
        replace(SOURCE, attempt=replace(ATTEMPT, scope=RepositoryScope(101, 203))),
        replace(SOURCE, run_created_at=START - timedelta(seconds=1)),
        replace(SOURCE, run_created_at=END + timedelta(seconds=1)),
    ],
)
def test_page_does_not_admit_another_scope_or_window(source: ProviderRunCollectionSource) -> None:
    with pytest.raises(ValueError, match="scope or window"):
        ProviderRunDiscoveryPage(ATTEMPT.scope, WINDOW, 1, 1, (source,), "exhausted")


def test_same_run_different_attempts_are_not_two_discovered_runs() -> None:
    second = replace(SOURCE, attempt=replace(ATTEMPT, run_attempt=ATTEMPT.run_attempt + 1))
    with pytest.raises(ValueError, match="distinct runs"):
        ProviderRunDiscoveryPage(ATTEMPT.scope, WINDOW, 1, 2, (SOURCE, second), "exhausted")


@pytest.mark.parametrize("ids", [None, [10], (), (10, 11), (True,), (0,), (-1,), (2**53,), ("10",)])
def test_observation_rejects_incomplete_or_invalid_workflow_associations(ids: object) -> None:
    page = ProviderRunDiscoveryPage(ATTEMPT.scope, WINDOW, 1, 1, (SOURCE,), "exhausted")
    with pytest.raises((TypeError, ValueError)):
        ProviderObservationPage(page, cast(tuple[int, ...], ids))


def test_observation_allows_multiple_runs_of_one_workflow() -> None:
    second = replace(SOURCE, attempt=replace(ATTEMPT, workflow_run_id=ATTEMPT.workflow_run_id + 1))
    page = ProviderRunDiscoveryPage(ATTEMPT.scope, WINDOW, 1, 2, (SOURCE, second), "exhausted")
    assert ProviderObservationPage(page, (10, 10)).workflow_ids == (10, 10)
    with pytest.raises(TypeError):
        ProviderObservationPage(cast(ProviderRunDiscoveryPage, None), ())

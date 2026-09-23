from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast

import pytest

from ci_coordinator.ci_economics.discovery import ProviderRunDiscoveryPage, RunDiscoveryWindow
from ci_coordinator.ci_economics.observation_windows import (
    ObservationCursor,
    advance_discovery_cursor,
)
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource

from .factories import ATTEMPT
from .observation_factories import NOW, cursor


def _sources(position: ObservationCursor, count: int) -> tuple[ProviderRunCollectionSource, ...]:
    return tuple(
        ProviderRunCollectionSource(
            replace(ATTEMPT, workflow_run_id=10 + index),
            position.window.created_from,
            "2026-03-10",
            "e" * 64,
        )
        for index in range(count)
    )


def test_first_subwindow_is_bounded_and_completion_preserves_outer_interval() -> None:
    position = cursor()
    assert position.window == RunDiscoveryWindow(NOW - timedelta(hours=8), NOW - timedelta(hours=2))
    page = ProviderRunDiscoveryPage(ATTEMPT.scope, position.window, 1, 0, (), "exhausted")
    progress = advance_discovery_cursor(position, page)
    assert progress.register_sources and progress.gap_reason is None
    assert progress.cursor is not None
    assert progress.cursor.interval == position.interval
    assert progress.cursor.window == RunDiscoveryWindow(NOW - timedelta(hours=2), NOW)
    assert progress.cursor.cycle_started_at == NOW
    terminal = replace(page, window=progress.cursor.window)
    assert advance_discovery_cursor(progress.cursor, terminal).cursor is None


@pytest.mark.parametrize("fraction", [0, 1, 500_000, 999_999])
def test_successor_window_does_not_drop_fractional_sources(fraction: int) -> None:
    first = cursor()
    second = first.advance_window()
    assert second is not None
    created = first.window.created_through + timedelta(microseconds=fraction)
    assert second.window.created_from <= created <= second.window.created_through
    source = replace(_sources(second, 1)[0], run_created_at=created)
    page = ProviderRunDiscoveryPage(ATTEMPT.scope, second.window, 1, 1, (source,), "exhausted")
    assert advance_discovery_cursor(second, page).register_sources


def test_saturated_search_splits_before_any_source_registration() -> None:
    position = cursor()
    page = ProviderRunDiscoveryPage(
        ATTEMPT.scope, position.window, 1, 1_001, _sources(position, 100), "next_page"
    )
    progress = advance_discovery_cursor(position, page)
    assert not progress.register_sources
    assert progress.gap_reason is None
    assert progress.cursor is not None
    assert progress.cursor.window == RunDiscoveryWindow(
        NOW - timedelta(hours=8), NOW - timedelta(hours=5)
    )
    assert progress.cursor.page_number == 1
    assert progress.cursor.interval == position.interval
    assert progress.cursor.advance_window() is not None


@pytest.mark.parametrize("seconds", [0, 1])
def test_irreducible_saturation_preserves_observed_sources_and_explicit_gap(seconds: int) -> None:
    interval = RunDiscoveryWindow(NOW - timedelta(seconds=seconds), NOW)
    position = ObservationCursor.start(interval, NOW)
    page = ProviderRunDiscoveryPage(
        ATTEMPT.scope, interval, 1, 1_001, _sources(position, 100), "next_page"
    )
    progress = advance_discovery_cursor(position, page)
    assert progress.register_sources
    assert progress.gap_reason == "provider_truncated"
    assert progress.cursor is None


def test_split_is_finite_and_each_step_strictly_reduces_span() -> None:
    position = cursor()
    for _ in range(15):
        child = position.split()
        if child is None:
            break
        assert (
            timedelta(0)
            < child.window.created_through - child.window.created_from
            < position.window.created_through - position.window.created_from
        )
        assert child.window.created_from == position.window.created_from
        assert child.interval == position.interval
        position = child
    assert position.window.created_through - position.window.created_from == timedelta(seconds=1)
    assert position.split() is None


def test_next_page_keeps_window_and_cycle_identity() -> None:
    position = cursor()
    page = ProviderRunDiscoveryPage(
        ATTEMPT.scope, position.window, 1, 200, _sources(position, 100), "next_page"
    )
    progress = advance_discovery_cursor(position, page)
    assert progress.cursor == replace(position, page_number=2)
    assert progress.register_sources and progress.gap_reason is None


def test_provider_partial_page_does_not_become_complete_history() -> None:
    position = cursor()
    page = ProviderRunDiscoveryPage(
        ATTEMPT.scope, position.window, 1, 2, _sources(position, 1), "truncated"
    )
    progress = advance_discovery_cursor(position, page)
    assert progress.gap_reason == "provider_truncated"
    assert progress.cursor == position.advance_window()


@pytest.mark.parametrize("operand", ["page", "window"])
def test_mismatched_page_cannot_advance_cursor(operand: str) -> None:
    position = cursor()
    page = ProviderRunDiscoveryPage(ATTEMPT.scope, position.window, 1, 0, (), "exhausted")
    changed = (
        replace(position, page_number=2)
        if operand == "page"
        else replace(
            position,
            window=RunDiscoveryWindow(
                position.window.created_from, position.window.created_through - timedelta(seconds=1)
            ),
        )
    )
    with pytest.raises(ValueError, match="exact cursor"):
        advance_discovery_cursor(changed, page)


@pytest.mark.parametrize(
    "field,value",
    [
        ("page_number", 0),
        ("page_number", 11),
        ("page_number", True),
        ("page_number", 1.0),
        ("cycle_started_at", NOW - timedelta(seconds=1)),
        ("cycle_started_at", NOW.replace(tzinfo=None)),
        ("window", RunDiscoveryWindow(NOW - timedelta(hours=9), NOW - timedelta(hours=8))),
        ("window", RunDiscoveryWindow(NOW - timedelta(hours=8), NOW)),
    ],
)
def test_invalid_cursor_is_rejected(field: str, value: object) -> None:
    position = cursor()
    with pytest.raises(ValueError):
        ObservationCursor(
            position.interval,
            cast(RunDiscoveryWindow, value) if field == "window" else position.window,
            cast(int, value) if field == "page_number" else position.page_number,
            cast(datetime, value) if field == "cycle_started_at" else position.cycle_started_at,
        )


@pytest.mark.parametrize("field", ["interval", "window"])
def test_cursor_requires_typed_intervals(field: str) -> None:
    position = cursor()
    with pytest.raises(TypeError):
        ObservationCursor(
            cast(RunDiscoveryWindow, {}) if field == "interval" else position.interval,
            cast(RunDiscoveryWindow, {}) if field == "window" else position.window,
            position.page_number,
            position.cycle_started_at,
        )


@pytest.mark.parametrize("value", [None, "today", 1])
def test_cycle_start_requires_aware_datetime(value: object) -> None:
    with pytest.raises(ValueError):
        ObservationCursor.start(cursor().interval, cast(datetime, value))

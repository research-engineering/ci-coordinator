from dataclasses import replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import cast

import pytest
from pydantic import ValidationError

from ci_coordinator.ci_economics.discovery import ProviderRunDiscoveryPage, RunDiscoveryWindow
from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_cursor import HistoryCursor, advance_history_cursor
from ci_coordinator.ci_economics.history_payload import HistoryCursorPayload
from ci_coordinator.ci_economics.model import AttemptIdentity
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

SCOPE = RepositoryScope(101, 202)
START = datetime(2020, 1, 1, tzinfo=UTC)


def _cursor(days: int = 18) -> HistoryCursor:
    end = START + timedelta(days=days)
    return HistoryCursor.start(SCOPE, START, end, cycle_started_at=end)


def _empty_page(cursor: HistoryCursor) -> ProviderRunDiscoveryPage:
    return ProviderRunDiscoveryPage(SCOPE, cursor.window, 1, 0, (), "exhausted")


def test_history_crosses_bounded_segments_without_skipping_boundary_seconds() -> None:
    cursor: HistoryCursor | None = _cursor()
    through = START + timedelta(days=18)
    previous_end = START
    pages = 0
    while cursor is not None:
        assert cursor.created_from == START
        assert cursor.created_through == through
        assert cursor.window.created_from == previous_end
        assert cursor.window.created_through - cursor.window.created_from <= timedelta(days=7)
        previous_end = cursor.window.created_through
        progress = advance_history_cursor(cursor, _empty_page(cursor))
        assert progress.register_sources and progress.gap_reason is None
        cursor = progress.cursor
        pages += 1
        assert pages <= 3
    assert pages == 3
    assert previous_end == through


def test_zero_width_history_is_one_bounded_query_not_an_infinite_scan() -> None:
    cursor = _cursor(0)
    assert cursor.window == RunDiscoveryWindow(START, START)
    assert advance_history_cursor(cursor, _empty_page(cursor)).cursor is None


def test_saturated_history_splits_then_reports_a_gap_at_minimum_resolution() -> None:
    cursor = _cursor(0)
    page = ProviderRunDiscoveryPage(SCOPE, cursor.window, 1, 1001, (), "truncated")
    result = advance_history_cursor(cursor, page)
    assert result.cursor is None and result.register_sources
    assert result.gap_reason == "provider_truncated"
    broad = _cursor(1)
    page = replace(page, window=broad.window)
    result = advance_history_cursor(broad, page)
    assert result.cursor is not None and not result.register_sources
    assert result.gap_reason is None
    assert result.cursor.window.created_through < broad.window.created_through
    assert result.cursor.created_through == broad.created_through


@pytest.mark.parametrize("field", ["scope", "window", "page_number"])
def test_foreign_page_cannot_advance_history(field: str) -> None:
    cursor = _cursor()
    page = _empty_page(cursor)
    if field == "scope":
        page = replace(page, scope=RepositoryScope(101, 203))
    elif field == "window":
        page = replace(page, window=RunDiscoveryWindow(START, START))
    else:
        page = replace(page, page_number=2, provider_total=100)
    with pytest.raises(ValueError):
        advance_history_cursor(cursor, page)


@pytest.mark.parametrize(
    ("start", "end", "cycle"),
    [
        (START.replace(tzinfo=None), START, START),
        (START, START.replace(tzinfo=None), START),
        (
            START + timedelta(microseconds=1),
            START + timedelta(seconds=1),
            START + timedelta(seconds=1),
        ),
        (START, START + timedelta(microseconds=1), START + timedelta(seconds=1)),
        (START + timedelta(seconds=1), START, START),
        (START, START + timedelta(seconds=1), START),
    ],
)
def test_invalid_temporal_population_is_rejected(
    start: datetime, end: datetime, cycle: datetime
) -> None:
    with pytest.raises(ValueError):
        HistoryCursor.start(SCOPE, start, end, cycle_started_at=cycle)


def test_canonical_history_keeps_all_scope_and_resume_operands() -> None:
    cursor = _cursor()
    assert cursor.canonical_mapping() == {
        "installationId": 101,
        "repositoryId": 202,
        "createdFrom": "2020-01-01T00:00:00+00:00",
        "createdThrough": "2020-01-19T00:00:00+00:00",
        "windowFrom": "2020-01-01T00:00:00+00:00",
        "windowThrough": "2020-01-08T00:00:00+00:00",
        "pageNumber": 1,
        "cycleStartedAt": "2020-01-19T00:00:00+00:00",
    }
    assert replace(cursor) == cursor
    assert HistoryCursorPayload.model_validate(cursor.canonical_mapping()).to_cursor() == cursor


@pytest.mark.parametrize("seconds", [0, 1, 2, 3, 8, 13])
def test_saturated_windows_cover_fractional_sources_with_explicit_gaps(seconds: int) -> None:
    end = START + timedelta(seconds=seconds)
    cursor: HistoryCursor | None = HistoryCursor.start(SCOPE, START, end, cycle_started_at=end)
    visited: list[RunDiscoveryWindow] = []
    transitions = 0
    while cursor is not None:
        recovered = HistoryCursorPayload.model_validate(cursor.canonical_mapping()).to_cursor()
        assert recovered == cursor
        page = ProviderRunDiscoveryPage(SCOPE, cursor.window, 1, 1001, (), "truncated")
        progress = advance_history_cursor(cursor, page)
        if progress.register_sources:
            assert cursor.window.created_through - cursor.window.created_from <= timedelta(
                seconds=1
            )
            assert progress.gap_reason == "provider_truncated"
            visited.append(cursor.window)
        else:
            assert progress.gap_reason is None
            assert progress.cursor is not None
            assert progress.cursor.window.created_through < cursor.window.created_through
        cursor = progress.cursor
        transitions += 1
        assert transitions <= (seconds + 1) ** 2
    assert visited[0].created_from == START
    assert visited[-1].created_through == end
    assert all(left.created_through == right.created_from for left, right in pairwise(visited))
    for index in range(seconds * 2 + 1):
        instant = START + timedelta(seconds=index / 2)
        assert any(window.created_from <= instant <= window.created_through for window in visited)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("installationId", True),
        ("pageNumber", 11),
        ("pageNumber", "1"),
        ("windowFrom", "2019-12-31T00:00:00Z"),
        ("windowThrough", "2020-01-01T00:00:00Z"),
        ("windowThrough", "2020-01-19T00:00:00Z"),
        ("createdThrough", "2019-12-31T00:00:00Z"),
        ("cycleStartedAt", "2020-01-01T00:00:00Z"),
        ("createdFrom", "2020-01-01T00:00:00.000001Z"),
    ],
)
def test_serialized_cursor_rejects_mutated_bounds_or_scalar_coercion(
    field: str, value: object
) -> None:
    raw = _cursor().canonical_mapping()
    raw[field] = value
    with pytest.raises(ValidationError):
        HistoryCursorPayload.model_validate(raw)


def test_maximum_representable_terminal_second_does_not_overflow() -> None:
    instant = datetime(9999, 12, 31, 23, 59, 59, tzinfo=UTC)
    cursor = HistoryCursor.start(SCOPE, instant, instant, cycle_started_at=instant)
    assert advance_history_cursor(cursor, _empty_page(cursor)).cursor is None


def _source(run: int, created: datetime) -> ProviderRunCollectionSource:
    return ProviderRunCollectionSource(
        AttemptIdentity(SCOPE, run, 1, "a" * 40), created, "2022-11-28", "b" * 64
    )


@pytest.mark.parametrize("seconds", [0, 1])
def test_irreducible_saturation_consumes_all_ten_pages_before_one_gap(seconds: int) -> None:
    end = START + timedelta(seconds=seconds)
    cursor = HistoryCursor.start(SCOPE, START, end, cycle_started_at=end)
    visited: list[int] = []
    for page_number in range(1, 11):
        sources = tuple(
            _source(run, START) for run in range((page_number - 1) * 100 + 1, page_number * 100 + 1)
        )
        page = ProviderRunDiscoveryPage(
            SCOPE,
            cursor.window,
            page_number,
            1001,
            sources,
            "next_page" if page_number < 10 else "truncated",
        )
        progress = advance_history_cursor(cursor, page)
        assert progress.register_sources
        visited.extend(source.attempt.workflow_run_id for source in sources)
        if page_number < 10:
            assert progress.gap_reason is None and progress.cursor is not None
            assert progress.cursor.window == cursor.window
            assert progress.cursor.page_number == page_number + 1
            cursor = HistoryCursorPayload.model_validate(
                progress.cursor.canonical_mapping()
            ).to_cursor()
        else:
            assert progress.cursor is None and progress.gap_reason == "provider_truncated"
    assert visited == list(range(1, 1001))


def test_shared_window_boundary_preserves_fractional_timestamp_after_it() -> None:
    first = _cursor()
    boundary = first.window.created_through
    successor = advance_history_cursor(first, _empty_page(first)).cursor
    assert successor is not None
    source = _source(1, boundary + timedelta(microseconds=500_000))
    page = ProviderRunDiscoveryPage(SCOPE, successor.window, 1, 1, (source,), "exhausted")
    assert advance_history_cursor(successor, page).register_sources
    assert successor.window.created_from == boundary


def test_201_runs_resume_page_two_and_consume_every_page_before_window_advance() -> None:
    cursor = _cursor()
    window = cursor.window
    sources = tuple(_source(run, START) for run in range(1, 202))
    visited: list[int] = []
    for page_number, bounds in enumerate(((0, 100), (100, 200), (200, 201)), start=1):
        if page_number == 2:
            cursor = HistoryCursorPayload.model_validate_json(
                HistoryCursorPayload.model_validate(cursor.canonical_mapping()).model_dump_json()
            ).to_cursor()
        assert cursor.page_number == page_number
        assert cursor.window == window
        page_sources = sources[bounds[0] : bounds[1]]
        page = ProviderRunDiscoveryPage(
            SCOPE,
            window,
            page_number,
            201,
            page_sources,
            "exhausted" if page_number == 3 else "next_page",
        )
        progress = advance_history_cursor(cursor, page)
        assert progress.register_sources and progress.gap_reason is None
        assert progress.cursor is not None
        visited.extend(source.attempt.workflow_run_id for source in page.sources)
        cursor = progress.cursor
    assert visited == list(range(1, 202))
    assert cursor.page_number == 1
    assert cursor.window.created_from == window.created_through
    assert cursor.window.created_through > window.created_through


def test_attempt_enumeration_keeps_a_fixed_ceiling_and_visits_each_attempt() -> None:
    cursor: HistoryAttemptCursor | None = HistoryAttemptCursor(SCOPE, 303, 3)
    for attempt in (1, 2, 3):
        assert cursor is not None and cursor.next_attempt == attempt
        assert cursor.latest_attempt == 3
        assert cursor.canonical_mapping() == {
            "installationId": 101,
            "repositoryId": 202,
            "workflowRunId": 303,
            "latestAttempt": 3,
            "nextAttempt": attempt,
        }
        cursor = cursor.advance(SCOPE, 303, attempt)
    assert cursor is None


def test_large_attempt_ceiling_does_not_materialize_its_population() -> None:
    cursor = HistoryAttemptCursor(SCOPE, 303, MAX_SAFE_JSON_INTEGER)
    successor = cursor.advance(SCOPE, 303, 1)
    assert successor == replace(cursor, next_attempt=2)
    assert len(cursor.canonical_mapping()) == len(successor.canonical_mapping()) == 5


@pytest.mark.parametrize("field", ["workflow_run_id", "latest_attempt", "next_attempt"])
@pytest.mark.parametrize("value", [True, 0, -1, 1.0, "1", MAX_SAFE_JSON_INTEGER + 1])
def test_attempt_fields_are_exact_positive_safe_integers(field: str, value: object) -> None:
    values = {"workflow_run_id": 303, "latest_attempt": 3, "next_attempt": 1}
    values[field] = cast(int, value)
    with pytest.raises(ValueError):
        HistoryAttemptCursor(
            SCOPE, values["workflow_run_id"], values["latest_attempt"], values["next_attempt"]
        )


@pytest.mark.parametrize(
    ("scope", "run", "attempt"),
    [(RepositoryScope(102, 202), 303, 1), (SCOPE, 304, 1), (SCOPE, 303, 2)],
)
def test_foreign_or_out_of_order_attempt_completion_is_rejected(
    scope: RepositoryScope, run: int, attempt: int
) -> None:
    with pytest.raises(ValueError):
        HistoryAttemptCursor(SCOPE, 303, 3).advance(scope, run, attempt)


def test_repeated_attempt_cannot_advance_the_successor() -> None:
    first = HistoryAttemptCursor(SCOPE, 303, 2)
    successor = first.advance(SCOPE, 303, 1)
    assert successor is not None
    with pytest.raises(ValueError):
        successor.advance(SCOPE, 303, 1)
    with pytest.raises(ValueError):
        replace(first, next_attempt=3)
    with pytest.raises(TypeError):
        replace(first, scope=cast(RepositoryScope, None))

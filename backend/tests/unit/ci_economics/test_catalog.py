from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from typing import cast

import pytest

from ci_coordinator.ci_economics.catalog import (
    MeasurementReportPage,
    MeasurementReportPointer,
    ProviderSourcePage,
    RecordedProviderSource,
    decode_source_cursor,
    require_catalog_page,
)
from ci_coordinator.ci_economics.collection import (
    CollectionTerminalized,
    acquire_collection_claim,
    expire_collection_state,
)
from ci_coordinator.config_control import RepositoryScope
from ci_economics.factories import ATTEMPT, NOW, POLICY, recorded_source


@pytest.mark.parametrize("cursor", ("1.1", "9007199254740991.9007199254740991", "303.2"))
def test_source_cursor_preserves_numeric_tuple(cursor: str) -> None:
    pair = decode_source_cursor(cursor)
    assert f"{pair[0]}.{pair[1]}" == cursor


@pytest.mark.parametrize(
    "cursor",
    (
        "",
        "0.1",
        "1.0",
        "01.2",
        "1.02",
        "1.2.3",
        "1e3.1",
        "1.2\n",
        "9007199254740992.1",
        "1.9007199254740992",
        "1" * 100,
        "1.\u0662",
        None,
        12,
    ),
)
def test_source_cursor_rejects_noncanonical_or_unsafe_ids(cursor: object) -> None:
    with pytest.raises(ValueError):
        decode_source_cursor(cast(str, cursor))


@pytest.mark.parametrize("operand", ("identity", "creation", "expired"))
def test_recorded_source_rejects_mismatched_or_expired_state(operand: str) -> None:
    record = recorded_source()
    state = record.state
    if operand == "identity":
        state = replace(state, subject_id="0" * 64)
    elif operand == "creation":
        state = replace(state, source_created_at=NOW - timedelta(seconds=1))
    else:
        terminal = acquire_collection_claim(
            state, record.source, worker_id="a" * 64, now=state.deadline_at, policy=POLICY
        )
        assert isinstance(terminal, CollectionTerminalized)
        expired = expire_collection_state(terminal.state, state.evidence_retain_until)
        assert expired is not None
        state = expired
    with pytest.raises(ValueError, match="expired or differs"):
        RecordedProviderSource(record.source, state)


@pytest.mark.parametrize("limit", (0, 101, -1, True, 1.5))
def test_page_limit_is_exact_and_bounded(limit: object) -> None:
    with pytest.raises(ValueError):
        require_catalog_page(ATTEMPT.scope, cast(int, limit))


def test_catalog_pages_preserve_identity_order_and_exact_continuation() -> None:
    source = recorded_source()
    newer = recorded_source(replace(ATTEMPT, run_attempt=3))
    page = ProviderSourcePage(ATTEMPT.scope, (newer, source), source.cursor)
    assert tuple(row.key for row in page.items) == ((303, 3), (303, 2))
    assert page.next_cursor == "303.2"
    pointer = MeasurementReportPointer("a" * 64, "b" * 64, NOW, NOW + timedelta(days=1))
    later = replace(pointer, report_id="c" * 64)
    reports = MeasurementReportPage(source.source, (pointer, later), later.report_id)
    assert reports.source == source.source and reports.next_cursor == later.report_id
    assert ProviderSourcePage(ATTEMPT.scope, (), None).items == ()
    assert MeasurementReportPage(source.source, (), None).items == ()


@pytest.mark.parametrize(
    "operand", ("scope", "duplicate", "order", "overflow", "cursor", "empty_cursor")
)
def test_source_page_rejects_each_independent_contradiction(operand: str) -> None:
    row = recorded_source()
    newer = recorded_source(replace(ATTEMPT, run_attempt=3))
    items: tuple[RecordedProviderSource, ...] = (newer, row)
    scope, cursor = ATTEMPT.scope, None
    if operand == "scope":
        scope = RepositoryScope(101, 203)
    elif operand == "duplicate":
        items = (row, row)
    elif operand == "order":
        items = (row, newer)
    elif operand == "overflow":
        items = tuple(recorded_source(replace(ATTEMPT, run_attempt=i)) for i in range(101, 0, -1))
    elif operand == "cursor":
        cursor = newer.cursor
    else:
        items, cursor = (), row.cursor
    with pytest.raises(ValueError):
        ProviderSourcePage(scope, items, cursor)


@pytest.mark.parametrize(
    ("report_id", "report_digest", "received_at", "retain_until"),
    (
        ("invalid", "b" * 64, NOW, NOW + timedelta(days=1)),
        ("a" * 64, "B" * 64, NOW, NOW + timedelta(days=1)),
        ("a" * 64, "b" * 64, NOW.replace(tzinfo=None), NOW + timedelta(days=1)),
        ("a" * 64, "b" * 64, NOW, NOW),
    ),
    ids=("id", "digest", "naive", "expiry"),
)
def test_report_pointer_rejects_each_invalid_operand(
    report_id: str, report_digest: str, received_at: datetime, retain_until: datetime
) -> None:
    with pytest.raises(ValueError):
        MeasurementReportPointer(report_id, report_digest, received_at, retain_until)


@pytest.mark.parametrize("operand", ("duplicate", "order", "overflow", "cursor", "empty_cursor"))
def test_report_page_rejects_each_independent_contradiction(operand: str) -> None:
    pointer = MeasurementReportPointer("a" * 64, "b" * 64, NOW, NOW + timedelta(days=1))
    later = replace(pointer, report_id="c" * 64)
    items: tuple[MeasurementReportPointer, ...] = (pointer, later)
    cursor = None
    if operand == "duplicate":
        items = (pointer, pointer)
    elif operand == "order":
        items = (later, pointer)
    elif operand == "overflow":
        items = tuple(replace(pointer, report_id=f"{i:064x}") for i in range(101))
    elif operand == "cursor":
        cursor = pointer.report_id
    else:
        items, cursor = (), pointer.report_id
    with pytest.raises(ValueError):
        MeasurementReportPage(recorded_source().source, items, cursor)

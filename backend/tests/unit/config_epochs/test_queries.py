from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import cast

import pytest

from ci_coordinator.config_control import PolicySourceFormat, RepositoryScope
from ci_coordinator.config_epochs import (
    MAX_CONFIG_EPOCH_PAGE_SIZE,
    ActiveConfigEpoch,
    ConfigEpochPage,
    ConfigEpochStatus,
    ConfigEpochSummary,
)

_SCOPE = RepositoryScope(1, 2)


def _summary(epoch_id: str) -> ConfigEpochSummary:
    normalized_id = epoch_id if len(epoch_id) == 64 else epoch_id * 64
    return ConfigEpochSummary(
        epoch_id=normalized_id,
        source_format="json",
        source_hash="a" * 64,
        document_hash="b" * 64,
        epoch_hash="c" * 64,
        source_byte_count=2,
    )


def test_epoch_page_requires_strict_order_and_exact_cursor() -> None:
    left = _summary("1")
    right = _summary("2")

    page = ConfigEpochPage((left, right), right.epoch_id)

    assert page.items == (left, right)
    assert page.next_cursor == right.epoch_id


@pytest.mark.parametrize(
    ("items", "cursor", "error"),
    (
        ((_summary("2"), _summary("1")), None, "strictly ordered"),
        ((_summary("1"), _summary("1")), None, "strictly ordered"),
        ((), "1" * 64, "final item"),
        ((_summary("1"),), "2" * 64, "final item"),
    ),
)
def test_epoch_page_rejects_order_and_cursor_counterexamples(
    items: tuple[ConfigEpochSummary, ...],
    cursor: str | None,
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        ConfigEpochPage(items, cursor)


def test_epoch_page_rejects_wrong_type_and_excess_cardinality() -> None:
    with pytest.raises(TypeError, match="exact tuple"):
        ConfigEpochPage(cast(tuple[ConfigEpochSummary, ...], [_summary("1")]), None)
    with pytest.raises(TypeError, match="exact epoch summaries"):
        ConfigEpochPage((cast(ConfigEpochSummary, object()),), None)
    with pytest.raises(ValueError, match="cardinality"):
        ConfigEpochPage(
            tuple(_summary(f"{index:064x}") for index in range(MAX_CONFIG_EPOCH_PAGE_SIZE + 1)),
            None,
        )


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: replace(value, epoch_id="A" * 64),
        lambda value: replace(value, source_format=cast(PolicySourceFormat, "toml")),
        lambda value: replace(value, source_hash="0" * 63),
        lambda value: replace(value, document_hash="g" * 64),
        lambda value: replace(value, epoch_hash=""),
        lambda value: replace(value, source_byte_count=-1),
        lambda value: replace(value, source_byte_count=0),
        lambda value: replace(value, source_byte_count=2_097_153),
        lambda value: replace(value, source_byte_count=True),
    ),
)
def test_epoch_summary_rejects_invalid_persisted_projections(
    mutate: Callable[[ConfigEpochSummary], ConfigEpochSummary],
) -> None:
    with pytest.raises(ValueError):
        mutate(_summary("1"))


def test_epoch_status_requires_exact_page_and_scope_consistency() -> None:
    page = ConfigEpochPage((_summary("1"),), None)
    active = ActiveConfigEpoch(_SCOPE, "1" * 64, 1)

    assert ConfigEpochStatus(_SCOPE, active, page).active == active
    with pytest.raises(TypeError, match="exact epoch page"):
        ConfigEpochStatus(_SCOPE, active, cast(ConfigEpochPage, object()))
    with pytest.raises(ValueError, match="scope does not match"):
        ConfigEpochStatus(
            _SCOPE,
            ActiveConfigEpoch(RepositoryScope(1, 3), "1" * 64, 1),
            page,
        )

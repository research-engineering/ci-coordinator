from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from itertools import pairwise

from ci_coordinator.ci_economics.collection import CollectionState
from ci_coordinator.ci_economics.model import MAX_ECONOMICS_PAGE_SIZE
from ci_coordinator.ci_economics.sources import ProviderRunCollectionSource
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import is_safe_json_integer

_DIGEST = re.compile(r"[0-9a-f]{64}")
_SOURCE_CURSOR = re.compile(r"([1-9][0-9]{0,15})\.([1-9][0-9]{0,15})")


@dataclass(frozen=True, slots=True)
class RecordedProviderSource:
    source: ProviderRunCollectionSource
    state: CollectionState

    def __post_init__(self) -> None:
        if type(self.source) is not ProviderRunCollectionSource or type(self.state) is not (
            CollectionState
        ):
            raise TypeError("source catalog requires exact provider provenance and state")
        if (
            self.source.source_id != self.state.subject_id
            or self.source.run_created_at != self.state.source_created_at
            or self.state.status == "expired"
        ):
            raise ValueError("source catalog state is expired or differs from its source")

    @property
    def key(self) -> tuple[int, int]:
        return self.source.attempt.workflow_run_id, self.source.attempt.run_attempt

    @property
    def cursor(self) -> str:
        return f"{self.key[0]}.{self.key[1]}"


@dataclass(frozen=True, slots=True)
class ProviderSourcePage:
    scope: RepositoryScope
    items: tuple[RecordedProviderSource, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        _require_scope(self.scope)
        if type(self.items) is not tuple or any(
            type(item) is not RecordedProviderSource for item in self.items
        ):
            raise TypeError("source catalog requires exact recorded sources")
        if len(self.items) > MAX_ECONOMICS_PAGE_SIZE:
            raise ValueError("source catalog exceeds its page bound")
        if any(item.source.attempt.scope != self.scope for item in self.items):
            raise ValueError("source catalog crosses its repository scope")
        if any(left.key <= right.key for left, right in pairwise(self.items)):
            raise ValueError("source catalog requires descending unique attempt keys")
        if self.next_cursor is not None and (
            not self.items or self.next_cursor != self.items[-1].cursor
        ):
            raise ValueError("source cursor must identify its final returned item")


@dataclass(frozen=True, slots=True)
class MeasurementReportPointer:
    report_id: str
    report_digest: str
    received_at: datetime
    retain_until: datetime

    def __post_init__(self) -> None:
        require_catalog_digest(self.report_id)
        require_catalog_digest(self.report_digest)
        for value in (self.received_at, self.retain_until):
            if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("report pointer requires timezone-aware instants")
        if self.received_at >= self.retain_until:
            raise ValueError("report pointer receipt must precede retention expiry")


@dataclass(frozen=True, slots=True)
class MeasurementReportPage:
    source: ProviderRunCollectionSource
    items: tuple[MeasurementReportPointer, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        if type(self.source) is not ProviderRunCollectionSource:
            raise TypeError("report catalog requires exact source provenance")
        if type(self.items) is not tuple or any(
            type(item) is not MeasurementReportPointer for item in self.items
        ):
            raise TypeError("report catalog requires exact pointers")
        if len(self.items) > MAX_ECONOMICS_PAGE_SIZE:
            raise ValueError("report catalog exceeds its page bound")
        if any(left.report_id >= right.report_id for left, right in pairwise(self.items)):
            raise ValueError("report catalog requires ascending unique report identities")
        if self.next_cursor is not None and (
            not self.items or self.next_cursor != self.items[-1].report_id
        ):
            raise ValueError("report cursor must identify its final returned item")


def decode_source_cursor(value: str) -> tuple[int, int]:
    match = _SOURCE_CURSOR.fullmatch(value) if type(value) is str else None
    if match is None:
        raise ValueError("source cursor requires a canonical run and attempt")
    run, attempt = map(int, match.groups())
    if not is_safe_json_integer(run) or not is_safe_json_integer(attempt):
        raise ValueError("source cursor IDs exceed the safe-integer bound")
    return run, attempt


def require_catalog_digest(value: str) -> None:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise ValueError("catalog identity requires a lowercase SHA-256 digest")


def require_catalog_page(scope: RepositoryScope, limit: int) -> None:
    _require_scope(scope)
    if type(limit) is not int or not 1 <= limit <= MAX_ECONOMICS_PAGE_SIZE:
        raise ValueError("catalog page size exceeds its bound")


def _require_scope(scope: RepositoryScope) -> None:
    if type(scope) is not RepositoryScope:
        raise TypeError("catalog read requires an exact repository scope")

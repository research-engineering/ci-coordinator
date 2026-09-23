"""Pure bounded read models for repository config epoch lifecycle."""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import pairwise
from typing import Final

from ci_coordinator.config_control import PolicySourceFormat, RepositoryScope
from ci_coordinator.config_control.limits import MAX_POLICY_SOURCE_BYTES
from ci_coordinator.config_epochs.contracts import ActiveConfigEpoch

MAX_CONFIG_EPOCH_PAGE_SIZE: Final = 100


@dataclass(frozen=True, slots=True)
class ConfigEpochSummary:
    epoch_id: str
    source_format: PolicySourceFormat
    source_hash: str
    document_hash: str
    epoch_hash: str
    source_byte_count: int

    def __post_init__(self) -> None:
        for name, value in (
            ("epoch_id", self.epoch_id),
            ("source_hash", self.source_hash),
            ("document_hash", self.document_hash),
            ("epoch_hash", self.epoch_hash),
        ):
            if type(value) is not str or re.fullmatch(r"[0-9a-f]{64}", value) is None:
                raise ValueError(f"{name} is not lowercase SHA-256 hexadecimal")
        if type(self.source_format) is not str or self.source_format not in {"json", "yaml-1.2"}:
            raise ValueError("source format is invalid")
        if (
            type(self.source_byte_count) is not int
            or not 1 <= self.source_byte_count <= MAX_POLICY_SOURCE_BYTES
        ):
            raise ValueError("source byte count is outside the admitted bound")


@dataclass(frozen=True, slots=True)
class ConfigEpochPage:
    items: tuple[ConfigEpochSummary, ...]
    next_cursor: str | None

    def __post_init__(self) -> None:
        if type(self.items) is not tuple:
            raise TypeError("config epoch page items must be an exact tuple")
        if not 0 <= len(self.items) <= MAX_CONFIG_EPOCH_PAGE_SIZE:
            raise ValueError("config epoch page exceeds its cardinality bound")
        if any(type(item) is not ConfigEpochSummary for item in self.items):
            raise TypeError("config epoch page requires exact epoch summaries")
        epoch_ids = tuple(item.epoch_id for item in self.items)
        if any(left >= right for left, right in pairwise(epoch_ids)):
            raise ValueError("config epoch page is not strictly ordered")
        if self.next_cursor is not None and (
            not self.items or self.next_cursor != self.items[-1].epoch_id
        ):
            raise ValueError("config epoch page cursor does not identify its final item")


@dataclass(frozen=True, slots=True)
class ConfigEpochStatus:
    scope: RepositoryScope
    active: ActiveConfigEpoch | None
    epochs: ConfigEpochPage

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("config epoch status requires an exact repository scope")
        if type(self.epochs) is not ConfigEpochPage:
            raise TypeError("config epoch status requires an exact epoch page")
        if self.active is not None and (
            type(self.active) is not ActiveConfigEpoch or self.active.scope != self.scope
        ):
            raise ValueError("active config epoch scope does not match status scope")

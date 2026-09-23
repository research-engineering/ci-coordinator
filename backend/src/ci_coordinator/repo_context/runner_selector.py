"""Static GitHub Actions runner selectors extracted from exact workflow bytes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ci_coordinator.kernel import utf16_sort_key

MAX_RUNNER_SELECTOR_LABELS: Final = 16
MAX_RUNNER_SELECTOR_TEXT_BYTES: Final = 256


@dataclass(frozen=True, slots=True)
class StaticRunnerSelector:
    """A bounded non-authorizing projection of one static ``runs-on`` value."""

    labels: tuple[str, ...]
    group: str | None

    def __post_init__(self) -> None:
        if type(self.labels) is not tuple or len(self.labels) > MAX_RUNNER_SELECTOR_LABELS:
            raise TypeError("runner selector labels must be a bounded exact tuple")
        if not self.labels and self.group is None:
            raise ValueError("runner selector requires at least one label or group")
        if any(not _bounded_ascii_label(label) or label != label.lower() for label in self.labels):
            raise ValueError("runner selector labels must be normalized bounded ASCII text")
        if tuple(sorted(set(self.labels), key=utf16_sort_key)) != self.labels:
            raise ValueError("runner selector labels must be canonical")
        if self.group is not None and not _bounded_text(self.group):
            raise ValueError("runner selector group must be absent or bounded text")

    def to_identity_mapping(self) -> dict[str, object]:
        return {"labels": list(self.labels), "group": self.group}


@dataclass(frozen=True, slots=True)
class WorkflowJobRunnerSelector:
    job_id: str
    selector: StaticRunnerSelector

    def __post_init__(self) -> None:
        if not _bounded_text(self.job_id):
            raise ValueError("runner selector job id must be bounded text")
        if type(self.selector) is not StaticRunnerSelector:
            raise TypeError("workflow job runner selector must be exact")

    def to_identity_mapping(self) -> dict[str, object]:
        return {"jobId": self.job_id, "selector": self.selector.to_identity_mapping()}


def static_runner_selector(
    *,
    labels: tuple[str, ...],
    group: str | None,
) -> StaticRunnerSelector | None:
    """Normalize the ASCII subset with provider-independent case semantics."""

    if type(labels) is not tuple or any(not _bounded_ascii_label(label) for label in labels):
        return None
    normalized = tuple(sorted({label.lower() for label in labels}, key=utf16_sort_key))
    if len(normalized) != len(labels) or (group is not None and not _bounded_text(group)):
        return None
    try:
        return StaticRunnerSelector(labels=normalized, group=group)
    except (TypeError, ValueError):
        return None


def _bounded_text(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and not any(
            ord(character) < 32 or ord(character) == 127 or 0xD800 <= ord(character) <= 0xDFFF
            for character in value
        )
        and len(value.encode("utf-8")) <= MAX_RUNNER_SELECTOR_TEXT_BYTES
    )


def _bounded_ascii_label(value: object) -> bool:
    if type(value) is not str:
        return False
    return value.isascii() and _bounded_text(value)

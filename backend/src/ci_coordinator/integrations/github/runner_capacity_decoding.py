"""Strict wire decoding for GitHub self-hosted runner capacity evidence."""

from __future__ import annotations

from dataclasses import dataclass

from ci_coordinator.integrations.github._response_decoding import (
    canonical_text_or_none,
    json_object_or_none,
    non_negative_safe_integer,
    object_or_none,
    positive_safe_integer,
)
from ci_coordinator.integrations.github._routes import GitHubPage
from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.runner_capacity import (
    MAX_OBSERVED_RUNNER_LABELS,
    ObservedSelfHostedRunner,
)

_MAX_RUNNER_TEXT_BYTES = 256


@dataclass(frozen=True, slots=True)
class DecodedPage[T]:
    total_count: int
    items: tuple[T, ...]


@dataclass(frozen=True, slots=True)
class RunnerGroupSummary:
    group_id: int
    name: str
    restricted_to_workflows: bool


def decode_runner_page(body: bytes) -> DecodedPage[ObservedSelfHostedRunner] | None:
    value = json_object_or_none(body)
    if value is None:
        return None
    total_count = non_negative_safe_integer(value.get("total_count"))
    raw_runners = value.get("runners")
    if total_count is None or type(raw_runners) is not list or len(raw_runners) > GitHubPage().size:
        return None

    runners: list[ObservedSelfHostedRunner] = []
    for raw_runner in raw_runners:
        runner = _decode_runner(raw_runner)
        if runner is None:
            return None
        runners.append(runner)
    return DecodedPage(total_count, tuple(runners))


def decode_runner_group_page(body: bytes) -> DecodedPage[RunnerGroupSummary] | None:
    value = json_object_or_none(body)
    if value is None:
        return None
    total_count = non_negative_safe_integer(value.get("total_count"))
    raw_groups = value.get("runner_groups")
    if total_count is None or type(raw_groups) is not list or len(raw_groups) > GitHubPage().size:
        return None

    groups: list[RunnerGroupSummary] = []
    for raw_group in raw_groups:
        group = object_or_none(raw_group)
        if group is None:
            return None
        group_id = positive_safe_integer(group.get("id"))
        name = canonical_text_or_none(group.get("name"), maximum_bytes=_MAX_RUNNER_TEXT_BYTES)
        restricted = group.get("restricted_to_workflows")
        if group_id is None or name is None or type(restricted) is not bool:
            return None
        groups.append(RunnerGroupSummary(group_id, name, restricted))
    return DecodedPage(total_count, tuple(groups))


def _decode_runner(value: object) -> ObservedSelfHostedRunner | None:
    runner = object_or_none(value)
    if runner is None:
        return None
    runner_id = positive_safe_integer(runner.get("id"))
    status = runner.get("status")
    busy = runner.get("busy")
    labels = _decode_labels(runner.get("labels"))
    if (
        runner_id is None
        or type(status) is not str
        or status not in {"online", "offline"}
        or type(busy) is not bool
        or labels is None
    ):
        return None
    try:
        return ObservedSelfHostedRunner(
            runner_id=runner_id,
            online=status == "online",
            busy=busy,
            labels=labels,
        )
    except (TypeError, ValueError):
        return None


def _decode_labels(value: object) -> tuple[str, ...] | None:
    if type(value) is not list or len(value) > MAX_OBSERVED_RUNNER_LABELS:
        return None
    labels: list[str] = []
    for raw_label in value:
        label = object_or_none(raw_label)
        if label is None:
            return None
        name = canonical_text_or_none(label.get("name"), maximum_bytes=_MAX_RUNNER_TEXT_BYTES)
        if name is None or not name.isascii():
            return None
        labels.append(name.lower())
    canonical = tuple(sorted(set(labels), key=utf16_sort_key))
    return canonical if len(canonical) == len(labels) else None

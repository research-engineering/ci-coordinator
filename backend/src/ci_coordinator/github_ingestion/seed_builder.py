from __future__ import annotations

from ci_coordinator.github_ingestion.events import (
    NormalizedMergeGroupEvent,
    NormalizedPullRequestEvent,
    NormalizedPushEvent,
)
from ci_coordinator.github_ingestion.results import NoopDelivery
from ci_coordinator.github_ingestion.seeds import (
    DynamicCiSeed,
    ExactShaRange,
    FullCiInvalidatingRange,
    MergeGroupSeed,
    PullRequestSeed,
    PushSeed,
    ShaRangeBinding,
)

type PlanningSeedEvent = (
    NormalizedPushEvent | NormalizedPullRequestEvent | NormalizedMergeGroupEvent
)
type SeedBuildResult = DynamicCiSeed | NoopDelivery


def build_seed(event: PlanningSeedEvent) -> SeedBuildResult:
    if isinstance(event, NormalizedPushEvent):
        return _build_push_seed(event)
    if isinstance(event, NormalizedPullRequestEvent):
        return _build_pull_request_seed(event)
    return _build_merge_group_seed(event)


def _build_push_seed(event: NormalizedPushEvent) -> SeedBuildResult:
    if event.deleted or _is_zero_sha(event.head_sha):
        return NoopDelivery(
            provenance=event.provenance,
            repository=event.repository,
            reason_code="non_live_push_ref",
        )
    return PushSeed(
        provenance=event.provenance,
        repository=event.repository,
        action=event.action,
        ref=event.ref,
        base_sha=event.base_sha,
        head_sha=event.head_sha,
        range_binding=_range_binding(event.base_sha, ()),
    )


def _build_pull_request_seed(event: NormalizedPullRequestEvent) -> SeedBuildResult:
    if _is_zero_sha(event.head_sha):
        return NoopDelivery(
            provenance=event.provenance,
            repository=event.repository,
            reason_code="non_live_pull_request_head",
        )
    return PullRequestSeed(
        provenance=event.provenance,
        repository=event.repository,
        action=event.action,
        ref=f"refs/pull/{event.pull_request_number}/merge",
        pull_request_number=event.pull_request_number,
        base_sha=event.base_sha,
        head_sha=event.head_sha,
        range_binding=_range_binding(event.base_sha, ()),
    )


def _build_merge_group_seed(event: NormalizedMergeGroupEvent) -> SeedBuildResult:
    if _is_zero_sha(event.head_sha):
        return NoopDelivery(
            provenance=event.provenance,
            repository=event.repository,
            reason_code="non_live_merge_group_head",
        )
    missing_ref = ("missing_merge_group_head_ref",) if event.head_ref is None else ()
    return MergeGroupSeed(
        provenance=event.provenance,
        repository=event.repository,
        action=event.action,
        ref=event.head_ref,
        merge_group_head_ref=event.head_ref,
        base_sha=event.base_sha,
        head_sha=event.head_sha,
        range_binding=_range_binding(event.base_sha, missing_ref),
    )


def _range_binding(base_sha: str, additional_reasons: tuple[str, ...]) -> ShaRangeBinding:
    reasons = additional_reasons
    if _is_zero_sha(base_sha):
        reasons = ("ambiguous_base_sha", *reasons)
    return FullCiInvalidatingRange(tuple(sorted(set(reasons)))) if reasons else ExactShaRange()


def _is_zero_sha(value: str) -> bool:
    return value == "0" * 40

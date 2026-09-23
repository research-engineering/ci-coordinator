from __future__ import annotations

from ci_coordinator.github_ingestion.event_common import (
    EventCommon,
    git_sha,
    object_field,
    optional_string,
)
from ci_coordinator.github_ingestion.events import NormalizedMergeGroupEvent
from ci_coordinator.github_ingestion.results import UnsupportedWebhook
from ci_coordinator.github_ingestion.strict_json import FrozenJsonObject


def normalize_merge_group(
    payload: FrozenJsonObject,
    common: EventCommon,
) -> NormalizedMergeGroupEvent | UnsupportedWebhook:
    merge_group = object_field(payload, "merge_group")
    base_sha = git_sha(merge_group, "base_sha")
    head_sha = git_sha(merge_group, "head_sha")
    if base_sha is None or head_sha is None:
        return UnsupportedWebhook(
            provenance=common.provenance,
            repository=common.repository,
            action=common.action,
            reason_code="incomplete_merge_group",
        )
    head_ref = optional_string(merge_group, "head_ref")
    if head_ref is None:
        head_ref = optional_string(merge_group, "ref")
    return NormalizedMergeGroupEvent(
        provenance=common.provenance,
        repository=common.repository,
        action=common.action,
        base_sha=base_sha,
        head_sha=head_sha,
        head_ref=head_ref,
    )

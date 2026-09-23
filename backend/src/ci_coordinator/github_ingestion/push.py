from __future__ import annotations

from ci_coordinator.github_ingestion.event_common import (
    EventCommon,
    git_sha,
    optional_boolean,
    optional_string,
)
from ci_coordinator.github_ingestion.events import NormalizedPushEvent
from ci_coordinator.github_ingestion.results import UnsupportedWebhook
from ci_coordinator.github_ingestion.strict_json import FrozenJsonObject


def normalize_push(
    payload: FrozenJsonObject,
    common: EventCommon,
) -> NormalizedPushEvent | UnsupportedWebhook:
    ref = optional_string(payload, "ref")
    base_sha = git_sha(payload, "before")
    head_sha = git_sha(payload, "after")
    if ref is None or base_sha is None or head_sha is None:
        return UnsupportedWebhook(
            provenance=common.provenance,
            repository=common.repository,
            action=common.action,
            reason_code="incomplete_push",
        )
    return NormalizedPushEvent(
        provenance=common.provenance,
        repository=common.repository,
        action=common.action,
        ref=ref,
        base_sha=base_sha,
        head_sha=head_sha,
        deleted=optional_boolean(payload, "deleted") is True,
    )

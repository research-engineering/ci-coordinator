from __future__ import annotations

from ci_coordinator.github_ingestion.event_common import (
    EventCommon,
    git_sha,
    object_field,
    positive_integer,
)
from ci_coordinator.github_ingestion.events import NormalizedPullRequestEvent
from ci_coordinator.github_ingestion.results import UnsupportedWebhook
from ci_coordinator.github_ingestion.strict_json import FrozenJsonObject


def normalize_pull_request(
    payload: FrozenJsonObject,
    common: EventCommon,
) -> NormalizedPullRequestEvent | UnsupportedWebhook:
    pull_request = object_field(payload, "pull_request")
    base = object_field(pull_request, "base")
    head = object_field(pull_request, "head")
    number = positive_integer(payload.get("number"))
    base_sha = git_sha(base, "sha")
    head_sha = git_sha(head, "sha")
    if number is None or base_sha is None or head_sha is None:
        return UnsupportedWebhook(
            provenance=common.provenance,
            repository=common.repository,
            action=common.action,
            reason_code="incomplete_pull_request",
        )
    return NormalizedPullRequestEvent(
        provenance=common.provenance,
        repository=common.repository,
        action=common.action,
        pull_request_number=number,
        base_sha=base_sha,
        head_sha=head_sha,
    )

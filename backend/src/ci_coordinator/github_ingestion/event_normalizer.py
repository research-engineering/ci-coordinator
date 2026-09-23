from __future__ import annotations

from ci_coordinator.github_ingestion.event_common import EventCommon
from ci_coordinator.github_ingestion.events import NormalizedGitHubEvent
from ci_coordinator.github_ingestion.merge_group import normalize_merge_group
from ci_coordinator.github_ingestion.payload_limits import WebhookIngestionProfile
from ci_coordinator.github_ingestion.pull_request import normalize_pull_request
from ci_coordinator.github_ingestion.push import normalize_push
from ci_coordinator.github_ingestion.results import NoopDelivery, UnsupportedWebhook
from ci_coordinator.github_ingestion.strict_json import FrozenJsonObject
from ci_coordinator.github_ingestion.workflow_job import normalize_workflow_job
from ci_coordinator.github_ingestion.workflow_run import normalize_workflow_run

type EventNormalizationResult = NormalizedGitHubEvent | NoopDelivery | UnsupportedWebhook


def normalize_event(
    payload: FrozenJsonObject,
    common: EventCommon,
    profile: WebhookIngestionProfile,
) -> EventNormalizationResult:
    action_decision = profile.action_decision(common.provenance.event_name, common.action)
    if action_decision == "unsupported":
        return UnsupportedWebhook(
            provenance=common.provenance,
            repository=common.repository,
            action=common.action,
            reason_code="unsupported_event",
        )
    if action_decision == "not_admitted":
        return NoopDelivery(
            provenance=common.provenance,
            repository=common.repository,
            reason_code="event_action_not_admitted",
        )
    match common.provenance.event_name:
        case "push":
            return normalize_push(payload, common)
        case "pull_request":
            return normalize_pull_request(payload, common)
        case "merge_group":
            return normalize_merge_group(payload, common)
        case "workflow_run":
            return normalize_workflow_run(payload, common)
        case "workflow_job":
            return normalize_workflow_job(payload, common)
        case _:
            raise AssertionError("admitted profile event has no normalizer")

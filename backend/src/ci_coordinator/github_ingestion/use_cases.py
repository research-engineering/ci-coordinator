from __future__ import annotations

from dataclasses import dataclass

from ci_coordinator.github_ingestion.event_common import extract_event_common
from ci_coordinator.github_ingestion.event_normalizer import normalize_event
from ci_coordinator.github_ingestion.events import (
    NormalizedGitHubEvent,
    NormalizedWorkflowJobEvent,
    NormalizedWorkflowRunEvent,
)
from ci_coordinator.github_ingestion.payload_limits import WebhookIngestionProfile
from ci_coordinator.github_ingestion.ping import normalize_ping
from ci_coordinator.github_ingestion.ports import (
    DeliveryClaimed,
    DeliveryConflict,
    DeliveryDuplicate,
    DeliveryIdempotencyKey,
    DeliveryStoreUnavailable,
    WebhookIngestionStore,
    prepare_delivery_claim,
)
from ci_coordinator.github_ingestion.provenance import (
    ProvenanceFailure,
    WebhookProvenance,
    admit_webhook_provenance,
)
from ci_coordinator.github_ingestion.results import (
    DeliveryUnavailable,
    DuplicateDelivery,
    IngestionRejection,
    NoopDelivery,
    PingDelivery,
    SeedIngestion,
    UnsupportedWebhook,
    WorkflowJobObservation,
    WorkflowRunObservation,
)
from ci_coordinator.github_ingestion.seed_builder import build_seed
from ci_coordinator.github_ingestion.strict_json import (
    FrozenJsonObject,
    JsonPayloadError,
    parse_json_object,
)
from ci_coordinator.identity_admission import TrustedWebhook
from ci_coordinator.kernel import sha256_hex

type IngestionResult = (
    SeedIngestion
    | WorkflowRunObservation
    | WorkflowJobObservation
    | NoopDelivery
    | PingDelivery
    | UnsupportedWebhook
    | DuplicateDelivery
    | DeliveryUnavailable
    | IngestionRejection
)
type PreparedWebhookOutcome = (
    NormalizedGitHubEvent | NoopDelivery | PingDelivery | UnsupportedWebhook
)


@dataclass(frozen=True, slots=True)
class PreparedWebhookIngestion:
    provenance: WebhookProvenance
    outcome: PreparedWebhookOutcome


type WebhookPreparationResult = PreparedWebhookIngestion | IngestionRejection


def prepare_trusted_webhook_ingestion(
    trusted_webhook: TrustedWebhook,
    raw_body: bytes,
    profile: WebhookIngestionProfile,
) -> WebhookPreparationResult:
    """Normalize the same immutable bytes admitted into ``trusted_webhook`` by the caller."""

    provenance = admit_webhook_provenance(trusted_webhook)
    if isinstance(provenance, ProvenanceFailure):
        return IngestionRejection(provenance.code)
    if type(raw_body) is not bytes or sha256_hex(raw_body) != provenance.body_sha256:
        return IngestionRejection("webhook_body_hash_mismatch")
    try:
        payload = parse_json_object(raw_body, profile.limits)
    except JsonPayloadError as error:
        return IngestionRejection(error.code)
    if provenance.event_name == "ping":
        if (
            not _event_header_matches_payload("ping", payload)
            or profile.action_decision("ping", None) != "admitted"
        ):
            return IngestionRejection("invalid_webhook_body")
        ping = normalize_ping(payload, provenance)
        return (
            ping
            if isinstance(ping, IngestionRejection)
            else PreparedWebhookIngestion(provenance, ping)
        )
    common = extract_event_common(provenance, payload)
    if isinstance(common, ProvenanceFailure):
        return IngestionRejection(common.code)
    if not _event_header_matches_payload(provenance.event_name, payload):
        return IngestionRejection("invalid_webhook_body")
    return PreparedWebhookIngestion(
        provenance=provenance,
        outcome=normalize_event(payload, common, profile),
    )


async def complete_webhook_ingestion(
    prepared: PreparedWebhookIngestion,
    ingestion_store: WebhookIngestionStore,
) -> IngestionResult:
    provenance = prepared.provenance
    try:
        prepared_claim = prepare_delivery_claim(provenance)
    except ValueError:
        return IngestionRejection("invalid_webhook_body")
    key = prepared_claim.key
    outcome = prepared.outcome
    durable_observation = (
        outcome
        if isinstance(outcome, NormalizedWorkflowJobEvent | NormalizedWorkflowRunEvent)
        and outcome.action == "completed"
        else None
    )
    delivery_result = await ingestion_store.commit(prepared_claim, durable_observation)
    if isinstance(delivery_result, DeliveryStoreUnavailable):
        return DeliveryUnavailable(provenance)
    if isinstance(delivery_result, DeliveryDuplicate):
        return _duplicate_result(provenance, key, delivery_result.key)
    if isinstance(delivery_result, DeliveryConflict):
        return _conflict_result(key, delivery_result)
    if not isinstance(delivery_result, DeliveryClaimed) or delivery_result.key != key:
        return IngestionRejection("delivery_idempotency_contract_violation")

    if isinstance(outcome, NoopDelivery | PingDelivery | UnsupportedWebhook):
        return outcome
    if isinstance(outcome, NormalizedWorkflowJobEvent):
        return WorkflowJobObservation(outcome)
    if isinstance(outcome, NormalizedWorkflowRunEvent):
        return WorkflowRunObservation(outcome)
    seed = build_seed(outcome)
    return seed if isinstance(seed, NoopDelivery) else SeedIngestion(seed)


def _event_header_matches_payload(event_name: str, payload: FrozenJsonObject) -> bool:
    keys = set(payload)
    detected: set[str] = set()
    if {"ref", "before", "after", "deleted"} <= keys:
        detected.add("push")
    if {"zen", "hook_id", "hook"} <= keys:
        detected.add("ping")
    for family in ("pull_request", "merge_group", "workflow_job", "workflow_run"):
        if family in keys:
            detected.add(family)
    if event_name in {
        "ping",
        "push",
        "pull_request",
        "merge_group",
        "workflow_job",
        "workflow_run",
    }:
        return detected == {event_name}
    return not detected


def _duplicate_result(
    provenance: WebhookProvenance,
    expected: DeliveryIdempotencyKey,
    received: DeliveryIdempotencyKey,
) -> DuplicateDelivery | IngestionRejection:
    if received == expected:
        return DuplicateDelivery(provenance=provenance)
    return IngestionRejection("delivery_idempotency_contract_violation")


def _conflict_result(
    expected: DeliveryIdempotencyKey,
    received: DeliveryConflict,
) -> IngestionRejection:
    if (
        received.delivery_id == expected.delivery_id
        and received.existing_body_sha256 != expected.body_sha256
    ):
        return IngestionRejection("delivery_id_conflict")
    return IngestionRejection("delivery_idempotency_contract_violation")

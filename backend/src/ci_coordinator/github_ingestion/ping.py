from __future__ import annotations

from ci_coordinator.github_ingestion.event_common import object_field, positive_integer
from ci_coordinator.github_ingestion.provenance import WebhookProvenance
from ci_coordinator.github_ingestion.results import IngestionRejection, PingDelivery
from ci_coordinator.github_ingestion.strict_json import FrozenJsonObject


def normalize_ping(
    payload: FrozenJsonObject,
    provenance: WebhookProvenance,
) -> PingDelivery | IngestionRejection:
    hook = object_field(payload, "hook")
    hook_id = positive_integer(payload.get("hook_id"))
    if (
        type(payload.get("zen")) is not str
        or payload.get("action") is not None
        or hook_id is None
        or hook is None
        or positive_integer(hook.get("id")) != hook_id
    ):
        return IngestionRejection("invalid_webhook_body")
    return PingDelivery(provenance)

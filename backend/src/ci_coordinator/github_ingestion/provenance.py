from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from ci_coordinator.identity_admission import TrustedWebhook

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class WebhookProvenance:
    delivery_id: str
    event_name: str
    body_sha256: str
    verified_at: datetime
    verifier_version: str


@dataclass(frozen=True, slots=True)
class GitHubRepository:
    installation_id: int
    repository_id: int
    owner: str
    name: str


@dataclass(frozen=True, slots=True)
class ProvenanceFailure:
    code: str


type ProvenanceAdmission = WebhookProvenance | ProvenanceFailure


def admit_webhook_provenance(trusted_webhook: TrustedWebhook) -> ProvenanceAdmission:
    delivery_id = _nonempty_scalar(trusted_webhook.delivery_id)
    if delivery_id is None:
        return ProvenanceFailure("missing_delivery_id")
    event_name = _nonempty_scalar(trusted_webhook.event_name)
    if event_name is None:
        return ProvenanceFailure("missing_event_name")
    if _SHA256_HEX.fullmatch(trusted_webhook.body_sha256) is None:
        return ProvenanceFailure("invalid_webhook_body")
    if not isinstance(trusted_webhook.verified_at, datetime):
        return ProvenanceFailure("invalid_webhook_body")
    verifier_version = _nonempty_scalar(trusted_webhook.verifier_version)
    if verifier_version is None:
        return ProvenanceFailure("invalid_webhook_body")
    return WebhookProvenance(
        delivery_id=delivery_id,
        event_name=event_name,
        body_sha256=trusted_webhook.body_sha256,
        verified_at=trusted_webhook.verified_at,
        verifier_version=verifier_version,
    )


def _nonempty_scalar(value: object) -> str | None:
    if type(value) is not str or not value:
        return None
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        return None
    return value

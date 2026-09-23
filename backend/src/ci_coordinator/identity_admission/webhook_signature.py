from __future__ import annotations

import hmac
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256

from ci_coordinator.identity_admission.rejection import RejectedIdentity
from ci_coordinator.kernel import Clock, sha256_hex

SIGNATURE_HEADER = "x-hub-signature-256"
DELIVERY_HEADER = "x-github-delivery"
EVENT_HEADER = "x-github-event"
SIGNATURE_PREFIX = "sha256="
_SIGNATURE_HEX_CHARACTERS = 64
VERIFIER_VERSION = "ci-coordinator.identity-admission.webhook.v1"

HeaderValue = str | Sequence[str]


@dataclass(frozen=True)
class TrustedWebhook:
    delivery_id: str | None
    event_name: str | None
    body_sha256: str
    verified_at: datetime
    verifier_version: str = VERIFIER_VERSION


def verify_webhook(
    headers: Mapping[str, HeaderValue],
    raw_body: bytes,
    secret: str,
    clock: Clock,
) -> TrustedWebhook | RejectedIdentity:
    return verify_webhook_at(headers, raw_body, secret, clock.now())


def verify_webhook_at(
    headers: Mapping[str, HeaderValue],
    raw_body: bytes,
    secret: str,
    verified_at: datetime,
) -> TrustedWebhook | RejectedIdentity:
    body_sha256 = sha256_hex(raw_body)
    # B105 is inapplicable: this branch rejects an empty caller-provided secret.
    if secret == "":  # nosec B105
        return _reject(
            "missing_webhook_secret",
            "webhook secret is missing",
            verified_at,
            body_sha256,
        )

    signature, duplicate_signature = _single_header_value(headers, SIGNATURE_HEADER)
    if duplicate_signature:
        return _reject(
            "ambiguous_webhook_signature",
            "webhook signature header is ambiguous",
            verified_at,
            body_sha256,
        )
    if signature is None:
        return _reject(
            "missing_webhook_signature",
            "webhook signature is missing",
            verified_at,
            body_sha256,
        )
    if not _is_canonical_signature(signature):
        return _reject(
            "malformed_webhook_signature",
            "webhook signature must be canonical sha256",
            verified_at,
            body_sha256,
        )

    expected = _sign(raw_body, secret)
    if not hmac.compare_digest(signature.encode("ascii"), expected.encode("ascii")):
        return _reject(
            "invalid_webhook_signature",
            "webhook signature does not match raw body",
            verified_at,
            body_sha256,
        )

    delivery_id, duplicate_delivery = _single_header_value(headers, DELIVERY_HEADER)
    if duplicate_delivery:
        return _reject(
            "ambiguous_webhook_delivery",
            "webhook delivery header is ambiguous",
            verified_at,
            body_sha256,
        )
    event_name, duplicate_event = _single_header_value(headers, EVENT_HEADER)
    if duplicate_event:
        return _reject(
            "ambiguous_webhook_event",
            "webhook event header is ambiguous",
            verified_at,
            body_sha256,
        )

    return TrustedWebhook(
        delivery_id=delivery_id,
        event_name=event_name,
        body_sha256=body_sha256,
        verified_at=verified_at,
    )


def _reject(
    reason_code: str,
    message: str,
    verified_at: datetime,
    body_sha256: str,
) -> RejectedIdentity:
    return RejectedIdentity(
        reason_code=reason_code,
        message=message,
        verified_at=verified_at,
        verifier_version=VERIFIER_VERSION,
        body_sha256=body_sha256,
    )


def _sign(raw_body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf8"), raw_body, sha256).hexdigest()
    return f"{SIGNATURE_PREFIX}{digest}"


def _is_canonical_signature(value: str) -> bool:
    if len(value) != len(SIGNATURE_PREFIX) + _SIGNATURE_HEX_CHARACTERS:
        return False
    if not value.startswith(SIGNATURE_PREFIX):
        return False
    digest = value[len(SIGNATURE_PREFIX) :]
    return digest.isascii() and all(character in "0123456789abcdef" for character in digest)


def _single_header_value(headers: Mapping[str, HeaderValue], name: str) -> tuple[str | None, bool]:
    values = _header_values(headers, name)
    if len(values) > 1:
        return None, True
    return values[0] if values else None, False


def _header_values(headers: Mapping[str, HeaderValue], name: str) -> list[str]:
    values: list[str] = []
    for key, value in headers.items():
        if key.lower() != name:
            continue
        if isinstance(value, str):
            values.append(value)
            continue
        values.extend(item for item in value if isinstance(item, str))
    return values

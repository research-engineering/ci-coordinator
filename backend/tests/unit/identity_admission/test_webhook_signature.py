from __future__ import annotations

import hmac
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from ci_coordinator.identity_admission import RejectedIdentity, TrustedWebhook, verify_webhook
from ci_coordinator.kernel import FixedClock, sha256_hex

SECRET = "webhook-secret"
BODY = b'{"repository":{"id":42},"action":"opened"}'
NOW = datetime(2026, 7, 9, 14, 30, tzinfo=UTC)


def test_verify_webhook_accepts_valid_raw_body_signature() -> None:
    trusted = verify_webhook(
        {
            "X-GitHub-Delivery": "delivery-1",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sign(BODY, SECRET),
        },
        BODY,
        SECRET,
        FixedClock(NOW),
    )

    assert isinstance(trusted, TrustedWebhook)
    assert trusted.delivery_id == "delivery-1"
    assert trusted.event_name == "pull_request"
    assert trusted.body_sha256 == sha256_hex(BODY)
    assert trusted.verified_at == NOW


def test_verify_webhook_rejects_signature_over_different_body_before_trusting_payload() -> None:
    malformed_body = b'{"repository":'
    rejected = verify_webhook(
        {"x-hub-signature-256": sign(BODY, SECRET)},
        malformed_body,
        SECRET,
        FixedClock(NOW),
    )

    assert isinstance(rejected, RejectedIdentity)
    assert rejected.reason_code == "invalid_webhook_signature"
    assert rejected.body_sha256 == sha256_hex(malformed_body)


def test_verify_webhook_rejects_missing_signature_with_stable_reason() -> None:
    rejected = verify_webhook({}, BODY, SECRET, FixedClock(NOW))

    assert isinstance(rejected, RejectedIdentity)
    assert rejected.reason_code == "missing_webhook_signature"
    assert rejected.verified_at == NOW


def test_verify_webhook_rejects_malformed_signature_scheme() -> None:
    rejected = verify_webhook(
        {"x-hub-signature-256": "sha1=bad"},
        BODY,
        SECRET,
        FixedClock(NOW),
    )

    assert isinstance(rejected, RejectedIdentity)
    assert rejected.reason_code == "malformed_webhook_signature"


@pytest.mark.parametrize(
    "signature",
    (
        "sha256=" + "a" * 63,
        "sha256=" + "A" * 64,
        "sha256=" + "\u00e9" * 64,
        "sha256=" + "a" * 64 + "0",
        "sha256=" + "a" * 10_000,
    ),
)
def test_verify_webhook_rejects_noncanonical_signature_before_comparison(
    signature: str,
) -> None:
    rejected = verify_webhook(
        {"x-hub-signature-256": signature},
        BODY,
        SECRET,
        FixedClock(NOW),
    )

    assert isinstance(rejected, RejectedIdentity)
    assert rejected.reason_code == "malformed_webhook_signature"


def test_verify_webhook_rejects_missing_secret_without_leaking_signature_material() -> None:
    rejected = verify_webhook(
        {"x-hub-signature-256": sign(BODY, SECRET)},
        BODY,
        "",
        FixedClock(NOW),
    )

    assert isinstance(rejected, RejectedIdentity)
    assert rejected.reason_code == "missing_webhook_secret"
    assert not hasattr(rejected, "raw_body")
    assert not hasattr(rejected, "signature")
    assert not hasattr(rejected, "secret")


def test_verify_webhook_rejects_duplicate_signature_header_values() -> None:
    rejected = verify_webhook(
        {"x-hub-signature-256": [sign(BODY, SECRET), "sha256=bad"]},
        BODY,
        SECRET,
        FixedClock(NOW),
    )

    assert isinstance(rejected, RejectedIdentity)
    assert rejected.reason_code == "ambiguous_webhook_signature"


@pytest.mark.parametrize(
    ("header", "reason_code"),
    (
        ("x-github-delivery", "ambiguous_webhook_delivery"),
        ("x-github-event", "ambiguous_webhook_event"),
    ),
)
def test_verify_webhook_rejects_duplicate_unsigned_metadata(
    header: str,
    reason_code: str,
) -> None:
    headers: dict[str, str | list[str]] = {
        "x-github-delivery": "delivery-1",
        "x-github-event": "push",
        "x-hub-signature-256": sign(BODY, SECRET),
    }
    headers[header] = ["first", "second"]

    rejected = verify_webhook(headers, BODY, SECRET, FixedClock(NOW))

    assert isinstance(rejected, RejectedIdentity)
    assert rejected.reason_code == reason_code


def sign(raw_body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf8"), raw_body, sha256).hexdigest()
    return f"sha256={digest}"

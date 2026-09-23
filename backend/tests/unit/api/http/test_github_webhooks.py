from __future__ import annotations

import hmac
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    GitHubWebhookRouteDependencies,
    HttpRouteDependencies,
)
from ci_coordinator.api.http.webhook_ingress import (
    WebhookAdmissionUnavailable,
    WebhookIngressCommand,
    WebhookIngressResult,
    WebhookPreparationOffered,
)
from ci_coordinator.github_ingestion import (
    ExactShaRange,
    IngestionRejection,
    PingDelivery,
    PushSeed,
    SeedIngestion,
)
from ci_coordinator.github_ingestion.provenance import GitHubRepository, WebhookProvenance
from ci_coordinator.identity_admission import RejectedIdentity, TrustedWebhook
from ci_coordinator.kernel import sha256_hex

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
SECRET = "webhook-test-secret"
BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40


@dataclass
class RecordingIngress:
    result: WebhookIngressResult
    commands: list[WebhookIngressCommand] = field(default_factory=list)

    async def __call__(self, command: WebhookIngressCommand, /) -> WebhookIngressResult:
        self.commands.append(command)
        return self.result


class ExplodingIngress:
    async def __call__(self, _: WebhookIngressCommand, /) -> WebhookIngressResult:
        raise RuntimeError("ingress secret must not be returned")


def test_preparation_response_discloses_only_the_best_effort_effect() -> None:
    app, _ = mounted_app(WebhookPreparationOffered())
    body = b"{}"
    with TestClient(app) as client:
        response = client.post("/webhooks/github", content=body, headers=signed_headers(body))
    document = app.openapi()
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "processed": True,
        "duplicate": False,
        "effect": "delivery_claim_recorded",
        "downstream": "best_effort_preparation",
    }
    schema = document["components"]["schemas"]["WebhookProcessedResponse"]
    assert set(schema["properties"]["downstream"]["enum"]) == {"none", "best_effort_preparation"}


def test_admitted_media_invokes_ingress_once_with_exact_headers_and_body() -> None:
    raw_body = b'{"ref":"refs/heads/main"}'
    app, ingress = mounted_app(seed_result(raw_body))

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/github",
            content=raw_body,
            headers=signed_headers(raw_body),
        )

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "processed": True,
        "duplicate": False,
        "effect": "delivery_claim_recorded",
        "downstream": "none",
    }
    assert len(ingress.commands) == 1
    assert ingress.commands[0].headers == (
        ("x-github-delivery", ("delivery-1",)),
        ("x-github-event", ("push",)),
        ("x-hub-signature-256", (webhook_signature(raw_body),)),
    )
    assert ingress.commands[0].raw_body == raw_body


def test_duplicate_signature_lines_are_preserved_for_identity_admission() -> None:
    raw_body = b'{"secret":"must-not-enter-ingestion"}'
    app, ingress = mounted_app(
        RejectedIdentity(
            reason_code="ambiguous_webhook_signature",
            message="redacted",
            verified_at=NOW,
            verifier_version="test",
        )
    )
    signature = webhook_signature(raw_body)
    headers = [
        ("content-type", "application/json"),
        ("x-github-delivery", "delivery-1"),
        ("x-github-event", "push"),
        ("x-hub-signature-256", signature),
        ("x-hub-signature-256", signature),
    ]

    with TestClient(app) as client:
        response = client.post("/webhooks/github", content=raw_body, headers=headers)

    assert response.status_code == 401
    assert response.json() == {"ok": False, "error": "invalid signature"}
    assert len(ingress.commands) == 1
    assert ingress.commands[0].headers[-1] == (
        "x-hub-signature-256",
        (signature, signature),
    )
    assert signature not in response.text
    assert "must-not-enter-ingestion" not in response.text


@pytest.mark.parametrize(
    "media_headers",
    (
        (),
        (("content-type", "text/plain"),),
        (("content-type", "application/json; charset=utf-8"),),
        (("content-type", "application/json"), ("content-type", "application/json")),
        (("content-type", "application/json"), ("content-encoding", "identity")),
    ),
)
def test_noncanonical_or_encoded_media_is_rejected_before_ingress(
    media_headers: tuple[tuple[str, str], ...],
) -> None:
    raw_body = b"{}"
    app, ingress = mounted_app(seed_result(raw_body))
    provider_headers = signed_headers(raw_body)[1:]

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/github",
            content=raw_body,
            headers=[*media_headers, *provider_headers],
        )

    assert response.status_code == 400
    assert response.json() == {"ok": False, "error": "invalid webhook"}
    assert ingress.commands == []


def test_worker_unavailability_is_a_redacted_service_failure() -> None:
    raw_body = b"{}"
    app, _ = mounted_app(WebhookAdmissionUnavailable())

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/github",
            content=raw_body,
            headers=signed_headers(raw_body),
        )

    assert response.status_code == 503
    assert response.json() == {"ok": False, "error": "webhook ingestion unavailable"}


def test_ping_acknowledgment_is_ignored_not_queued_or_planned() -> None:
    body = b'{"zen":"bounded","hook_id":7,"hook":{"id":7}}'
    ping = PingDelivery(WebhookProvenance("delivery-1", "ping", sha256_hex(body), NOW, "test"))
    app, ingress = mounted_app(ping)
    headers = [
        (name, "ping" if name == "x-github-event" else value)
        for name, value in signed_headers(body)
    ]
    with TestClient(app) as client:
        response = client.post("/webhooks/github", content=body, headers=headers)
    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "duplicate": False,
        "ignored": True,
        "reason": "ignored_webhook",
    }
    assert len(ingress.commands) == 1


def test_route_preserves_non_utf8_body_bytes_without_json_round_trip() -> None:
    raw_body = b'\x00\xff{"key":"value"}\r\n '
    app, ingress = mounted_app(seed_result(raw_body))

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/github",
            content=raw_body,
            headers=signed_headers(raw_body),
        )

    assert response.status_code == 200
    assert len(ingress.commands) == 1
    assert ingress.commands[0].raw_body == raw_body


def test_domain_rejection_is_redacted_to_a_stable_transport_error() -> None:
    raw_body = b'{"token":"body-secret"}'
    signature = webhook_signature(raw_body)
    rejection = IngestionRejection("invalid_json")
    app, ingress = mounted_app(rejection)

    with TestClient(app) as client:
        response = client.post(
            "/webhooks/github",
            content=raw_body,
            headers=signed_headers(raw_body),
        )

    assert response.status_code == 400
    assert response.json() == {"ok": False, "error": "invalid webhook"}
    assert len(ingress.commands) == 1
    assert "invalid_json" not in response.text
    assert "body-secret" not in response.text
    assert "domain-secret" not in response.text
    assert signature not in response.text


def test_unexpected_ingress_failure_uses_the_generic_internal_error_contract() -> None:
    raw_body = b'{"token":"body-secret"}'
    app = create_app(
        HttpRouteDependencies(
            webhook=GitHubWebhookRouteDependencies(
                webhook_ingress=ExplodingIngress(),
            )
        )
    )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/webhooks/github",
            content=raw_body,
            headers=signed_headers(raw_body),
        )

    assert response.status_code == 500
    assert response.json() == {"code": "internal_error"}
    assert "body-secret" not in response.text


def test_idempotency_contract_violation_uses_the_generic_internal_error_contract() -> None:
    raw_body = b'{"token":"body-secret"}'
    app, _ = mounted_app(IngestionRejection("delivery_idempotency_contract_violation"))

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post(
            "/webhooks/github",
            content=raw_body,
            headers=signed_headers(raw_body),
        )

    assert response.status_code == 500
    assert response.json() == {"code": "internal_error"}
    assert response.headers["cache-control"] == "no-store"
    assert "body-secret" not in response.text


def test_openapi_exposes_only_the_exact_github_webhook_route_shape() -> None:
    app, _ = mounted_app(seed_result(b"{}"))

    schema = app.openapi()
    operation = schema["paths"]["/webhooks/github"]["post"]

    assert list(schema["paths"]) == ["/webhooks/github"]
    assert operation["operationId"] == "receive_github_webhook"
    assert {parameter["name"] for parameter in operation["parameters"]} == {
        "X-GitHub-Delivery",
        "X-GitHub-Event",
        "X-Hub-Signature-256",
    }
    assert operation["requestBody"]["required"] is True
    assert set(operation["responses"]) == {"200", "400", "401", "409", "413", "500", "503"}
    assert operation["responses"]["500"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorBody"
    }


@pytest.mark.parametrize(
    "path",
    ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"),
)
def test_runtime_does_not_serve_api_description_or_documentation(path: str) -> None:
    app, _ = mounted_app(seed_result(b"{}"))

    with TestClient(app) as client:
        response = client.get(path)

    assert response.status_code == 404
    assert "/webhooks/github" in app.openapi()["paths"]


def mounted_app(result: WebhookIngressResult) -> tuple[FastAPI, RecordingIngress]:
    ingress = RecordingIngress(result=result)
    app = create_app(
        HttpRouteDependencies(
            webhook=GitHubWebhookRouteDependencies(
                webhook_ingress=ingress,
            )
        )
    )
    return app, ingress


def signed_headers(raw_body: bytes) -> list[tuple[str, str]]:
    return [
        ("content-type", "application/json"),
        ("x-github-delivery", "delivery-1"),
        ("x-github-event", "push"),
        ("x-hub-signature-256", webhook_signature(raw_body)),
    ]


def webhook_signature(raw_body: bytes) -> str:
    digest = hmac.new(SECRET.encode("utf8"), raw_body, sha256).hexdigest()
    return f"sha256={digest}"


def trusted_webhook(raw_body: bytes) -> TrustedWebhook:
    return TrustedWebhook(
        delivery_id="delivery-1",
        event_name="push",
        body_sha256=sha256_hex(raw_body),
        verified_at=NOW,
    )


def seed_result(raw_body: bytes) -> SeedIngestion:
    trusted = trusted_webhook(raw_body)
    provenance = WebhookProvenance(
        delivery_id="delivery-1",
        event_name="push",
        body_sha256=trusted.body_sha256,
        verified_at=trusted.verified_at,
        verifier_version=trusted.verifier_version,
    )
    repository = GitHubRepository(
        installation_id=100,
        repository_id=200,
        owner="example-org",
        name="ci-coordinator",
    )
    return SeedIngestion(
        seed=PushSeed(
            provenance=provenance,
            repository=repository,
            action=None,
            base_sha=BASE_SHA,
            head_sha=HEAD_SHA,
            range_binding=ExactShaRange(),
            ref="refs/heads/main",
        )
    )

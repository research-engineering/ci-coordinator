from __future__ import annotations

import asyncio
import hmac
import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from threading import Event
from typing import cast

import httpx2 as httpx
import pytest
from app.test_candidate_planning import _admitted_draft
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from integrations.test_github_repository_context import (
    _contents_response,
    _file,
    _graph,
    _repository_identity,
)
from sqlalchemy import delete, insert, select

from ci_coordinator.config_control import ValidatedEpochDraft, admit_policy_document
from ci_coordinator.integrations.github import GitHubAppTransportFactory
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.repository_context import DEPENDENCY_GRAPH_PATH
from ci_coordinator.kernel import SystemClock
from ci_coordinator.observability import RuntimeMetrics
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.schema import active_config_epochs, webhook_deliveries
from ci_coordinator.runtime import composition
from ci_coordinator.runtime.application import RuntimeApplication, compose_runtime_application

from ._runtime_ingress_issuance_support import _insert_epoch
from .test_runtime_composition import (
    _AvailableJwksProvider,
    _key_set,
    _plan_request_body,
    _settings,
    _token,
)

pytestmark = pytest.mark.persistence


@pytest.mark.parametrize("change", ["none", "repository_drift", "policy_removed"])
def test_connected_webhook_preparation_keeps_current_request_authority(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    prepared = Event()
    requests: list[str] = []
    changed = False
    graph = json.loads(_graph())
    graph["globalRiskPaths"] = [".github/workflows/**"]

    observe_preparation = RuntimeMetrics.planning_preparation

    def planning_preparation(metrics: RuntimeMetrics, outcome: str) -> None:
        observe_preparation(metrics, outcome)
        if outcome == "prepared":
            prepared.set()

    def provider(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requests.append(path)
        if path == "/app/installations/1001/access_tokens":
            return httpx.Response(
                201,
                headers={
                    "content-type": "application/json",
                    "x-github-api-version-selected": GITHUB_API_VERSION,
                },
                stream=httpx.ByteStream(
                    json.dumps(
                        {
                            "token": "test-installation-token",
                            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
                        }
                    ).encode()
                ),
            )
        if path == "/repositories/2002":
            body = _repository_identity(
                repository_id=2002,
                owner="example",
                name="renamed" if changed and change == "repository_drift" else "ci",
            )
        elif path.startswith("/repos/example/ci/compare/"):
            body = json.dumps({"files": [_file("src/module.py")]}).encode()
        elif path == f"/repos/example/ci/contents/{DEPENDENCY_GRAPH_PATH}":
            body = _contents_response(DEPENDENCY_GRAPH_PATH, json.dumps(graph).encode())
        else:
            return httpx.Response(503, stream=httpx.ByteStream(b"{}"))
        return httpx.Response(
            200,
            stream=httpx.ByteStream(body),
            headers={
                "content-type": "application/json",
                "x-github-api-version-selected": GITHUB_API_VERSION,
            },
        )

    factory_type = GitHubAppTransportFactory

    def factory(**kwargs: object) -> GitHubAppTransportFactory:
        # Only the network boundary is replaced; credentials and provider decoding remain real.
        return factory_type(
            app_id=cast(str, kwargs["app_id"]),
            private_key_pem=cast(str, kwargs["private_key_pem"]),
            clock=SystemClock(),
            transport=httpx.MockTransport(provider),
        )

    oidc_key = rsa.generate_private_key(65537, 2048)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(composition, "GitHubAppTransportFactory", factory)
    monkeypatch.setattr(RuntimeMetrics, "planning_preparation", planning_preparation)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        composition,
        "GitHubActionsJwksProvider",
        lambda *_, **__: _AvailableJwksProvider(_key_set(oidc_key)),
    )

    source = json.loads(_admitted_draft().source_bytes)
    source["repository"].update(installationId=1001, repositoryId=2002, owner="example", name="ci")
    source["repository"]["dynamicCi"]["dependencyGraph"]["source"] = "generated"
    draft = admit_policy_document(json.dumps(source).encode(), "json")
    assert isinstance(draft, ValidatedEpochDraft)

    async def configure(*, remove: bool = False) -> None:
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.begin() as connection:
                if remove:
                    await connection.execute(delete(active_config_epochs))
                else:
                    await _insert_epoch(connection, draft)
                    await connection.execute(
                        insert(active_config_epochs).values(
                            installation_id=1001,
                            repository_id=2002,
                            epoch_id=draft.epoch_id,
                            revision=1,
                        )
                    )
        finally:
            await engine.dispose()

    async def claimed() -> bool:
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.connect() as connection:
                return (
                    await connection.scalar(select(webhook_deliveries.c.delivery_id)) == "prepare-1"
                )
        finally:
            await engine.dispose()

    asyncio.run(configure())
    runtime = compose_runtime_application(_settings(runtime_postgres_database_url))
    assert isinstance(runtime, RuntimeApplication)
    body = json.dumps(
        {
            "installation": {"id": 1001},
            "repository": {"id": 2002, "name": "ci", "owner": {"login": "example"}},
            "ref": "refs/heads/main",
            "before": "a" * 40,
            "after": "b" * 40,
            "deleted": False,
        }
    ).encode()
    signature = hmac.new(b"w" * 32, body, sha256).hexdigest()
    headers = {
        "content-type": "application/json",
        "x-github-delivery": "prepare-1",
        "x-github-event": "push",
        "x-hub-signature-256": f"sha256={signature}",
    }
    with TestClient(runtime.app) as client:
        accepted = client.post("/webhooks/github", content=body, headers=headers)
        assert accepted.status_code == 200
        assert accepted.json()["downstream"] == "best_effort_preparation"
        assert asyncio.run(claimed())
        assert prepared.wait(timeout=10), (
            "the connected preparation worker did not publish a context"
        )
        replay = client.post("/webhooks/github", content=body, headers=headers)
        assert replay.json()["reason"] == "duplicate_delivery"
        if change == "policy_removed":
            asyncio.run(configure(remove=True))
        changed = True
        requests.clear()
        request = _plan_request_body()
        request.update(
            eventName="push", ref="refs/heads/main", pullRequestNumber=None, executionSha="b" * 40
        )
        result = client.post(
            "/api/v1/dynamic-ci/plan",
            json=request,
            headers={"authorization": f"Bearer {_token(oidc_key, request)}"},
        )
        metrics = client.get("/metrics", headers={"authorization": "Bearer " + "m" * 32})
        assert result.status_code == 200
        assert result.json()["payload"]["execution"]["mode"] == "full-ci"
        assert result.json()["payload"]["verifiedPlanId"] is None
        if change == "none":
            assert 'outcome="cache_hit"} 1.0' in metrics.text
            assert not any("/compare/" in path or "/contents/" in path for path in requests)
        else:
            assert 'outcome="cache_hit"} 0.0' in metrics.text
        if change == "policy_removed":
            assert requests == []

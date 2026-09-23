from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.dev_environment.compose import LocalEndpoints
from scripts.dev_environment.identity import derive_instance_identity
from scripts.dev_environment.secrets import ensure_instance_state
from scripts.dev_environment.smoke import HttpResponse, SmokeError, verify_local_stack


def test_connected_witness_admits_anonymous_boundary_and_no_secret_disclosure(
    tmp_path: Path,
) -> None:
    identity = derive_instance_identity(
        tmp_path,
        state_home=tmp_path.parent / f"{tmp_path.name}-user-state",
    )
    ensure_instance_state(identity)
    endpoints = LocalEndpoints(
        api="http://127.0.0.1:49151",
        ui="http://127.0.0.1:49152",
        postgres="postgresql://127.0.0.1:49153",
    )
    responses = {
        f"{endpoints.api}/healthz": _json({"ok": True, "status": "alive"}),
        f"{endpoints.ui}/workbench": HttpResponse(
            200,
            "text/html; charset=utf-8",
            b'<div id="root"></div>',
        ),
        f"{endpoints.ui}/api/v1/workbench/installations": _json(
            {"error": "unauthenticated", "ok": False, "retryAfterSeconds": None},
            status=401,
        ),
        f"{endpoints.ui}/api/v1/workbench/repositories/1/1?limit=10": _json(
            {"error": "unauthenticated", "ok": False},
            status=401,
        ),
        f"{endpoints.ui}/api/v1/workbench/repositories/1/1?limit=10&limit=10": HttpResponse(
            404,
            "",
            b"",
        ),
    }

    verify_local_stack(
        identity,
        endpoints,
        "services: {}\n",
        requester=lambda url, _accept: responses[url],
    )


def test_connected_witness_rejects_secret_in_render_without_echoing_it(
    tmp_path: Path,
) -> None:
    identity = derive_instance_identity(
        tmp_path,
        state_home=tmp_path.parent / f"{tmp_path.name}-user-state",
    )
    ensure_instance_state(identity)
    secret = (identity.secrets_directory / "break-glass-bearer-token").read_text().strip()
    endpoints = LocalEndpoints("http://127.0.0.1:1", "http://127.0.0.1:2", "unused")

    with pytest.raises(SmokeError, match="disclosed") as caught:
        verify_local_stack(identity, endpoints, f"token: {secret}")

    assert secret not in str(caught.value)


def test_connected_witness_rejects_development_proxy_credential_injection(
    tmp_path: Path,
) -> None:
    identity = derive_instance_identity(
        tmp_path,
        state_home=tmp_path.parent / f"{tmp_path.name}-user-state",
    )
    ensure_instance_state(identity)
    endpoints = LocalEndpoints(
        api="http://127.0.0.1:49151",
        ui="http://127.0.0.1:49152",
        postgres="postgresql://127.0.0.1:49153",
    )
    responses = {
        f"{endpoints.api}/healthz": _json({"ok": True, "status": "alive"}),
        f"{endpoints.ui}/workbench": HttpResponse(200, "text/html", b'<div id="root"></div>'),
        f"{endpoints.ui}/api/v1/workbench/installations": _json(
            {"complete": True, "failures": [], "installations": [], "ok": True},
        ),
        f"{endpoints.ui}/api/v1/workbench/repositories/1/1?limit=10": _json(
            {"error": "unauthenticated", "ok": False},
            status=401,
        ),
    }

    with pytest.raises(SmokeError, match="anonymous-boundary"):
        verify_local_stack(
            identity,
            endpoints,
            "services: {}\n",
            requester=lambda url, _accept: responses[url],
        )


def _json(value: object, *, status: int = 200) -> HttpResponse:
    return HttpResponse(status, "application/json", json.dumps(value).encode())

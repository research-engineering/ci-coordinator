"""Bounded connected-stack witness for one admitted local instance."""

from __future__ import annotations

import http.client
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit

from scripts.dev_environment.compose import LocalEndpoints
from scripts.dev_environment.identity import InstanceIdentity
from scripts.dev_environment.private_files import PrivateFileError, read_private_text

_MAX_RESPONSE_BYTES: Final = 2 * 1024 * 1024


class SmokeError(RuntimeError):
    """The connected development stack failed a bounded witness."""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    content_type: str
    body: bytes


type HttpRequester = Callable[[str, str], HttpResponse]


def verify_local_stack(
    identity: InstanceIdentity,
    endpoints: LocalEndpoints,
    rendered_config: str,
    *,
    requester: HttpRequester | None = None,
) -> None:
    _verify_secret_non_disclosure(identity, rendered_config)
    request = _request if requester is None else requester
    health = request(f"{endpoints.api}/healthz", "application/json")
    if health.status != 200 or _json_object(health) != {"ok": True, "status": "alive"}:
        raise SmokeError("backend health witness failed")
    document = request(f"{endpoints.ui}/workbench", "text/html")
    if (
        document.status != 200
        or "text/html" not in document.content_type
        or b'<div id="root"></div>' not in document.body
    ):
        raise SmokeError("frontend document witness failed")
    catalog_response = request(
        f"{endpoints.ui}/api/v1/workbench/installations",
        "application/json",
    )
    if catalog_response.status != 401:
        raise SmokeError("provider inventory anonymous-boundary witness failed")
    catalog = _json_object(catalog_response, expected_status=401)
    if catalog != {
        "error": "unauthenticated",
        "ok": False,
        "retryAfterSeconds": None,
    }:
        raise SmokeError("provider inventory anonymous-boundary witness failed")
    snapshot_response = request(
        f"{endpoints.ui}/api/v1/workbench/repositories/1/1?limit=10",
        "application/json",
    )
    if snapshot_response.status != 401:
        raise SmokeError("workbench anonymous-boundary witness failed")
    snapshot = _json_object(snapshot_response, expected_status=401)
    if snapshot != {"error": "unauthenticated", "ok": False}:
        raise SmokeError("workbench anonymous-boundary witness failed")
    unprivileged = request(
        f"{endpoints.ui}/api/v1/workbench/repositories/1/1?limit=10&limit=10",
        "application/json",
    )
    if unprivileged.status != 404:
        raise SmokeError("workbench proxy authority witness failed")


def _verify_secret_non_disclosure(
    identity: InstanceIdentity,
    rendered_config: str,
) -> None:
    try:
        values = tuple(
            read_private_text(path).strip() for path in identity.secrets_directory.iterdir()
        )
    except (OSError, PrivateFileError) as error:
        raise SmokeError("secret disclosure witness could not read admitted state") from error
    if any(value and value in rendered_config for value in values):
        raise SmokeError("rendered Compose configuration disclosed a local secret")


def _json_object(
    response: HttpResponse,
    *,
    expected_status: int = 200,
) -> Mapping[str, object]:
    if response.status != expected_status or "application/json" not in response.content_type:
        raise SmokeError("JSON endpoint witness failed")
    try:
        value: object = json.loads(response.body)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise SmokeError("JSON endpoint returned an invalid document") from error
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise SmokeError("JSON endpoint returned a non-object document")
    return value


def _request(url: str, accept: str) -> HttpResponse:
    endpoint = urlsplit(url)
    if endpoint.scheme != "http" or endpoint.hostname != "127.0.0.1" or endpoint.port is None:
        raise SmokeError("local endpoint escaped the admitted loopback boundary")
    path = endpoint.path or "/"
    if endpoint.query:
        path = f"{path}?{endpoint.query}"
    connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=5)
    try:
        connection.request("GET", path, headers={"accept": accept})
        response = connection.getresponse()
        body = response.read(_MAX_RESPONSE_BYTES + 1)
        if len(body) > _MAX_RESPONSE_BYTES:
            raise SmokeError("local endpoint response exceeded the admitted bound")
        return HttpResponse(
            status=response.status,
            content_type=response.getheader("content-type", ""),
            body=body,
        )
    except (OSError, http.client.HTTPException) as error:
        raise SmokeError("local endpoint is unavailable") from error
    finally:
        connection.close()

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Literal, cast

import httpx2 as httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from ci_coordinator.integrations.keycloak.discovery import KeycloakProviderMetadata

ISSUER = "https://auth.example.test/realms/coordinator"
BROWSER_CLIENT_ID = "ci-coordinator-admin-ui"
API_CLIENT_ID = "ci-coordinator-admin-api"
REDIRECT_URI = "https://coordinator.example.test/auth/callback"
POST_LOGOUT_REDIRECT_URI = "https://coordinator.example.test/"
AUTHORIZATION_ENDPOINT = f"{ISSUER}/protocol/openid-connect/auth"
TOKEN_ENDPOINT = f"{ISSUER}/protocol/openid-connect/token"
JWKS_URI = f"{ISSUER}/protocol/openid-connect/certs"
END_SESSION_ENDPOINT = f"{ISSUER}/protocol/openid-connect/logout"
DISCOVERY_URL = f"{ISSUER}/.well-known/openid-configuration"
KID = "signing-key"
OPAQUE = "A" * 43
NOW = 2_000_000_000
TokenKind = Literal["id", "access", "logout"]


@dataclass(slots=True)
class ManualMonotonicClock:
    value: float = 0.0

    def now(self) -> float:
        return self.value


@dataclass(slots=True)
class DocumentClient:
    documents: dict[str, bytes]
    calls: list[tuple[str, int]] = field(default_factory=list)
    started: asyncio.Event | None = None
    release: asyncio.Event | None = None
    finished: asyncio.Event | None = None
    failure: BaseException | None = None

    async def get_document(self, url: str, *, maximum_bytes: int) -> bytes:
        self.calls.append((url, maximum_bytes))
        finished = self.finished
        if finished is not None:
            task = asyncio.current_task()
            assert task is not None
            task.add_done_callback(lambda _completed: finished.set())
        if self.started is not None:
            self.started.set()
        if self.release is not None:
            await self.release.wait()
        if self.failure is not None:
            raise self.failure
        return self.documents[url]


def metadata() -> KeycloakProviderMetadata:
    return KeycloakProviderMetadata(
        issuer=ISSUER,
        authorization_endpoint=AUTHORIZATION_ENDPOINT,
        token_endpoint=TOKEN_ENDPOINT,
        jwks_uri=JWKS_URI,
        end_session_endpoint=END_SESSION_ENDPOINT,
    )


def discovery_document(**overrides: object) -> bytes:
    value: dict[str, object] = {
        "issuer": ISSUER,
        "authorization_endpoint": AUTHORIZATION_ENDPOINT,
        "token_endpoint": TOKEN_ENDPOINT,
        "jwks_uri": JWKS_URI,
        "end_session_endpoint": END_SESSION_ENDPOINT,
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_basic"],
        "id_token_signing_alg_values_supported": ["RS256"],
    }
    value.update(overrides)
    return json_bytes(value)


def json_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def public_jwk(
    *,
    key: rsa.RSAPrivateKey = PRIVATE_KEY,
    kid: str = KID,
) -> dict[str, object]:
    numbers = key.public_key().public_numbers()
    return {
        "alg": "RS256",
        "e": _integer_base64url(numbers.e),
        "key_ops": ["verify"],
        "kid": kid,
        "kty": "RSA",
        "n": _integer_base64url(numbers.n),
        "use": "sig",
    }


def private_jwk_member(name: str) -> str:
    numbers = PRIVATE_KEY.private_numbers()
    values = {
        "d": numbers.d,
        "p": numbers.p,
        "q": numbers.q,
        "dp": numbers.dmp1,
        "dq": numbers.dmq1,
        "qi": numbers.iqmp,
    }
    return _integer_base64url(values.get(name, 1))


def claims(kind: TokenKind) -> dict[str, object]:
    common: dict[str, object] = {
        "iss": ISSUER,
        "sub": "subject-1",
        "iat": NOW,
        "exp": NOW + 60,
    }
    if kind == "id":
        return {
            **common,
            "typ": "ID",
            "aud": BROWSER_CLIENT_ID,
            "azp": BROWSER_CLIENT_ID,
            "nonce": OPAQUE,
            "sid": "session-1",
            "preferred_username": "operator",
            "name": "example Operator",
            "resource_access": {API_CLIENT_ID: {"roles": ["read", "configure"]}},
        }
    if kind == "access":
        return {
            **common,
            "typ": "Bearer",
            "aud": [API_CLIENT_ID, "account"],
            "azp": "automation-client",
            "resource_access": {API_CLIENT_ID: {"roles": ["read", "audit"]}},
        }
    return {
        **common,
        "typ": "Logout",
        "aud": BROWSER_CLIENT_ID,
        "jti": "logout-1",
        "sid": "session-1",
        "events": {"http://schemas.openid.net/event/backchannel-logout": {}},
    }


def token(
    kind: TokenKind,
    *,
    claim_mutation: Callable[[dict[str, object]], None] | None = None,
    header_mutation: Callable[[dict[str, object]], None] | None = None,
    key: rsa.RSAPrivateKey = PRIVATE_KEY,
) -> str:
    payload = deepcopy(claims(kind))
    if claim_mutation is not None:
        claim_mutation(payload)
    header: dict[str, object] = {
        "alg": "RS256",
        "kid": KID,
        "typ": "logout+jwt" if kind == "logout" else "JWT",
    }
    if kind == "access":
        header["typ"] = "at+jwt"
    if header_mutation is not None:
        header_mutation(header)
    encoded_header = _base64url(json_bytes(header))
    encoded_payload = _base64url(json_bytes(payload))
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{encoded_header}.{encoded_payload}.{_base64url(signature)}"


def response(
    status: int = 200,
    *,
    body: bytes = b"{}",
    headers: dict[str, str] | list[tuple[str, str]] | None = None,
) -> httpx.Response:
    admitted_headers: dict[str, str] | list[tuple[str, str]] = (
        {"content-type": "application/json"} if headers is None else headers
    )
    return httpx.Response(status, headers=admitted_headers, content=body)


def replace(value: dict[str, object], field: str, replacement: object) -> None:
    value[field] = replacement


def remove(value: dict[str, object], field: str) -> None:
    value.pop(field, None)


def replace_roles(value: dict[str, object], roles: object) -> None:
    resources = cast(dict[str, object], value["resource_access"])
    resources[API_CLIENT_ID] = {"roles": roles}


def _integer_base64url(value: int) -> str:
    size = max(1, (value.bit_length() + 7) // 8)
    return _base64url(value.to_bytes(size, "big"))


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")

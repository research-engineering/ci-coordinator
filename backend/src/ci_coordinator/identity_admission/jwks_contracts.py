"""Typed contracts for bounded GitHub Actions JWKS retrieval."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from ci_coordinator.identity_admission.oidc_verifier import ActionsOidcJwkSet

type JwksTransportFailureKind = Literal[
    "cancelled",
    "redirected",
    "timeout",
    "unavailable",
    "response_oversize",
]
type JwksUnavailableKind = Literal[
    "fetch_cancelled",
    "fetch_timeout",
    "fetch_unavailable",
    "response_invalid",
    "response_oversize",
    "response_redirected",
    "refresh_throttled",
    "signing_key_unavailable",
]

_TRANSPORT_FAILURE_KINDS = frozenset[JwksTransportFailureKind](
    {"cancelled", "redirected", "timeout", "unavailable", "response_oversize"}
)
_UNAVAILABLE_KINDS = frozenset[JwksUnavailableKind](
    {
        "fetch_cancelled",
        "fetch_timeout",
        "fetch_unavailable",
        "response_invalid",
        "response_oversize",
        "response_redirected",
        "refresh_throttled",
        "signing_key_unavailable",
    }
)


@dataclass(frozen=True, slots=True)
class JwksFetchResponse:
    status: int
    content_type: str | None
    body: bytes

    def __post_init__(self) -> None:
        if type(self.status) is not int or not 100 <= self.status <= 599:
            raise ValueError("JWKS response status must be an HTTP status")
        if type(self.body) is not bytes:
            raise ValueError("JWKS response body must be exact bytes")
        if self.content_type is not None and type(self.content_type) is not str:
            raise ValueError("JWKS content type must be text or absent")


@dataclass(frozen=True, slots=True)
class JwksTransportFailure:
    kind: JwksTransportFailureKind
    message: str

    def __post_init__(self) -> None:
        if self.kind not in _TRANSPORT_FAILURE_KINDS:
            raise ValueError("JWKS transport failure kind is not admitted")
        if type(self.message) is not str or not self.message:
            raise ValueError("JWKS transport failure message is required")


@dataclass(frozen=True, slots=True)
class JwksUnavailable:
    kind: JwksUnavailableKind
    message: str

    def __post_init__(self) -> None:
        if self.kind not in _UNAVAILABLE_KINDS:
            raise ValueError("JWKS unavailable kind is not admitted")
        if type(self.message) is not str or not self.message:
            raise ValueError("JWKS unavailable message is required")


@dataclass(frozen=True, slots=True)
class JwksProviderConfig:
    maximum_cache_age_seconds: float
    minimum_refresh_interval_seconds: float
    failure_backoff_seconds: float
    maximum_response_bytes: int
    required_content_type_prefix: str = "application/json"

    def __post_init__(self) -> None:
        for value, name in (
            (self.maximum_cache_age_seconds, "maximum cache age"),
            (self.minimum_refresh_interval_seconds, "minimum refresh interval"),
            (self.failure_backoff_seconds, "failure backoff"),
        ):
            if type(value) not in {int, float} or value <= 0:
                raise ValueError(f"JWKS {name} must be a positive number")
        if self.minimum_refresh_interval_seconds > self.maximum_cache_age_seconds:
            raise ValueError("JWKS refresh interval must not exceed cache age")
        if type(self.maximum_response_bytes) is not int or self.maximum_response_bytes < 1:
            raise ValueError("JWKS response byte bound must be positive")
        if (
            type(self.required_content_type_prefix) is not str
            or not self.required_content_type_prefix
        ):
            raise ValueError("JWKS content type prefix is required")


class JwksTransport(Protocol):
    async def fetch(self) -> JwksFetchResponse | JwksTransportFailure: ...


class JwksProvider(Protocol):
    async def get_key_set(self, key_id: str) -> ActionsOidcJwkSet | JwksUnavailable: ...

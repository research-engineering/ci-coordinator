"""Constant-time authentication for the deployment-owned metrics capability."""

from __future__ import annotations

import hmac
from hashlib import sha256

from fastapi import Request

from ci_coordinator.runtime_settings.contracts import is_metrics_bearer_token


class StaticMetricsBearerAuthenticator:
    def __init__(self, bearer_token: str) -> None:
        if not is_metrics_bearer_token(bearer_token):
            raise ValueError("metrics bearer token must be bounded header-safe authentication text")
        self._bearer_token_digest = sha256(bearer_token.encode("ascii")).digest()

    def authenticate(self, request: Request) -> bool:
        values = tuple(
            value
            for name, value in request.scope.get("headers", ())
            if name.lower() == b"authorization"
        )
        if len(values) != 1:
            return False
        authorization = values[0]
        if not 7 + 32 <= len(authorization) <= 7 + 4_096:
            return False
        if authorization[:7].lower() != b"bearer ":
            return False
        token = authorization[7:]
        return (
            token.isascii()
            and not any(character in b" \t\n\r\v\f" for character in token)
            and hmac.compare_digest(sha256(token).digest(), self._bearer_token_digest)
        )

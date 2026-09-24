"""Pure protected-header admission shared by OIDC key retrieval and verification."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Final, Literal

from joserfc import jws
from joserfc.errors import JoseError
from joserfc.jws import JWSRegistry

from ci_coordinator.kernel import JsonResourceLimits, load_strict_json

_ALGORITHM: Final = "RS256"
_OPTIONAL_HEADER_FIELDS: Final = frozenset({"typ", "x5t"})
_JWS_REGISTRY = JWSRegistry(algorithms=[_ALGORITHM], strict_check_header=True)
_HEADER_LIMITS = JsonResourceLimits(max_depth=2, max_nodes=16)


@dataclass(frozen=True, slots=True)
class ActionsOidcHeader:
    key_id: str

    def __post_init__(self) -> None:
        if type(self.key_id) is not str or not self.key_id:
            raise ValueError("OIDC JWT key id is not admitted")


@dataclass(frozen=True, slots=True)
class RejectedActionsOidcHeader:
    reason_code: Literal["oidc_token_malformed", "oidc_header_not_admitted"]
    message: str


def admit_actions_oidc_header(token: object) -> ActionsOidcHeader | RejectedActionsOidcHeader:
    """Admit the fixed protected-header vocabulary without trusting token claims."""
    if type(token) is not str or not token:
        return RejectedActionsOidcHeader(
            reason_code="oidc_token_malformed",
            message="OIDC token must be a compact JWT",
        )
    try:
        token_bytes = token.encode("ascii")
        header_segment = token_bytes.partition(b".")[0]
        _JWS_REGISTRY.validate_header_size(header_segment)
        header = load_strict_json(
            base64.b64decode(
                header_segment + b"=" * (-len(header_segment) % 4),
                altchars=b"-_",
                validate=True,
            ),
            max_bytes=_JWS_REGISTRY.max_header_length,
            resource_limits=_HEADER_LIMITS,
        )
        if type(header) is not dict:
            raise ValueError("OIDC JWT header must be an object")
        extracted = jws.extract_compact(
            token_bytes,
            registry=_JWS_REGISTRY,
        ).protected
        if extracted != header:
            raise ValueError("OIDC JWT header decoding disagrees")
    except (JoseError, UnicodeError, TypeError, ValueError):
        return RejectedActionsOidcHeader(
            reason_code="oidc_token_malformed",
            message="OIDC token header is malformed",
        )
    if (
        not {"alg", "kid"}.issubset(header)
        or not set(header).issubset({"alg", "kid", *_OPTIONAL_HEADER_FIELDS})
        or header.get("alg") != _ALGORITHM
        or ("typ" in header and header["typ"] != "JWT")
        or ("x5t" in header and not _canonical_sha1_thumbprint(header["x5t"]))
    ):
        return RejectedActionsOidcHeader(
            reason_code="oidc_header_not_admitted",
            message="OIDC JWT header is not admitted",
        )
    key_id = header.get("kid")
    if type(key_id) is not str or not key_id:
        return RejectedActionsOidcHeader(
            reason_code="oidc_header_not_admitted",
            message="OIDC JWT key id is not admitted",
        )
    return ActionsOidcHeader(key_id=key_id)


def _canonical_sha1_thumbprint(value: object) -> bool:
    if type(value) is not str or len(value) != 27:
        return False
    try:
        decoded = base64.b64decode(value.encode("ascii") + b"=", altchars=b"-_", validate=True)
    except (UnicodeError, ValueError):
        return False
    return (
        len(decoded) == 20
        and base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") == value
    )

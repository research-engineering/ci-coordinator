"""Pure protected-header admission shared by OIDC key retrieval and verification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from joserfc import jws
from joserfc.errors import JoseError
from joserfc.jws import JWSRegistry

_ALGORITHM: Final = "RS256"
_OPTIONAL_HEADER_FIELDS: Final = frozenset({"typ"})
_JWS_REGISTRY = JWSRegistry(algorithms=[_ALGORITHM], strict_check_header=True)


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
        header = jws.extract_compact(
            token.encode("ascii"),
            registry=_JWS_REGISTRY,
        ).protected
    except (JoseError, UnicodeError):
        return RejectedActionsOidcHeader(
            reason_code="oidc_token_malformed",
            message="OIDC token header is malformed",
        )
    if (
        not {"alg", "kid"}.issubset(header)
        or not set(header).issubset({"alg", "kid", *_OPTIONAL_HEADER_FIELDS})
        or header.get("alg") != _ALGORITHM
        or ("typ" in header and header["typ"] != "JWT")
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

"""Cryptographic GitHub Actions OIDC verification before claim admission."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import Final

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey
from joserfc import jwt as jose_jwt
from joserfc.errors import JoseError, SecurityWarning
from joserfc.jwk import RSAKey

from ci_coordinator.identity_admission.actions_oidc import (
    VERIFIER_VERSION,
    ExpectedActionsOidcClaims,
    TrustedActionsRun,
    admit_verified_actions_oidc_claims,
)
from ci_coordinator.identity_admission.oidc_header import (
    RejectedActionsOidcHeader,
    admit_actions_oidc_header,
)
from ci_coordinator.identity_admission.rejection import RejectedIdentity
from ci_coordinator.kernel import Clock

_ALGORITHM: Final = "RS256"
MAXIMUM_ACTIONS_OIDC_JWKS_KEYS: Final = 64
MINIMUM_ACTIONS_OIDC_RSA_MODULUS_BITS: Final = 2048
_PRIVATE_JWK_MEMBERS: Final = frozenset({"d", "p", "q", "dp", "dq", "qi", "oth"})
_STRING_JWK_MEMBERS: Final = ("alg", "e", "kid", "kty", "n", "use")


@dataclass(frozen=True, slots=True)
class ActionsOidcJwkSet:
    """A bounded, caller-fetched public-key set for a single issuer."""

    keys: tuple[Mapping[str, object], ...]

    def __post_init__(self) -> None:
        if not self.keys:
            raise ValueError("OIDC JWK set must contain at least one key")
        if len(self.keys) > MAXIMUM_ACTIONS_OIDC_JWKS_KEYS:
            raise ValueError("OIDC JWK set exceeds the admitted key limit")
        frozen_keys = tuple(
            admitted for key in self.keys if (admitted := _admit_jwk(key)) is not None
        )
        if not frozen_keys:
            raise ValueError("OIDC JWK set must contain at least one admitted key")
        seen_key_ids: set[str] = set()
        for key in frozen_keys:
            key_id = _read_key_id(key)
            if key_id in seen_key_ids:
                raise ValueError("OIDC JWK set must not contain duplicate key ids")
            seen_key_ids.add(key_id)
        object.__setattr__(self, "keys", frozen_keys)


def verify_actions_oidc(
    token: str,
    expected: ExpectedActionsOidcClaims,
    key_set: ActionsOidcJwkSet,
    clock: Clock,
) -> TrustedActionsRun | RejectedIdentity:
    """Verify an RS256 JWT, then apply the already-owned claim predicate."""
    verified_at = clock.now()
    header = admit_actions_oidc_header(token)
    if isinstance(header, RejectedActionsOidcHeader):
        return _reject(header.reason_code, header.message, verified_at)
    jwk = _find_key(key_set, header.key_id)
    if jwk is None:
        return _reject(
            "oidc_signing_key_unavailable",
            "OIDC signing key is unavailable",
            verified_at,
        )
    try:
        public_key = _import_rsa_public_key(jwk)
        verified = jose_jwt.decode(
            token,
            key=public_key,
            algorithms=[_ALGORITHM],
        )
        claims = verified.claims
    except (JoseError, SecurityWarning, TypeError, ValueError):
        return _reject("oidc_signature_invalid", "OIDC JWT signature is invalid", verified_at)
    if type(claims) is not dict:
        return _reject("oidc_claims_not_json", "OIDC JWT claims are not a JSON object", verified_at)
    if not {"iss", "aud", "exp"}.issubset(claims):
        return _reject("oidc_signature_invalid", "OIDC JWT signature is invalid", verified_at)
    return admit_verified_actions_oidc_claims(claims, expected, clock)


def _find_key(key_set: ActionsOidcJwkSet, key_id: str) -> Mapping[str, object] | None:
    for candidate in key_set.keys:
        if candidate.get("kid") == key_id:
            return candidate
    return None


def _admit_jwk(value: Mapping[str, object]) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        return None
    if _PRIVATE_JWK_MEMBERS.intersection(value):
        return None
    snapshot: dict[str, object] = {}
    for key in _STRING_JWK_MEMBERS:
        item = value.get(key)
        if type(item) is not str:
            return None
        snapshot[key] = item
    key_operations = value.get("key_ops")
    if key_operations is not None:
        if type(key_operations) is not list or any(
            type(operation) is not str for operation in key_operations
        ):
            return None
        snapshot["key_ops"] = tuple(key_operations)
    admitted = MappingProxyType(snapshot)
    try:
        _read_key_id(admitted)
    except ValueError:
        return None
    return admitted


def _read_key_id(key: Mapping[str, object]) -> str:
    if not isinstance(key, Mapping):
        raise ValueError("OIDC JWK must be an object")
    key_id = key.get("kid")
    if type(key_id) is not str or not key_id:
        raise ValueError("OIDC JWK key id is required")
    if key.get("kty") != "RSA" or key.get("alg") != _ALGORITHM or key.get("use") != "sig":
        raise ValueError("OIDC JWK must be an RS256 signature key")
    key_operations = key.get("key_ops")
    if key_operations is not None and key_operations != ("verify",):
        raise ValueError("OIDC JWK must permit only signature verification")
    if _PRIVATE_JWK_MEMBERS.intersection(key):
        raise ValueError("OIDC JWK must not contain private key material")
    if type(key.get("n")) is not str or type(key.get("e")) is not str:
        raise ValueError("OIDC JWK must contain an RSA public key")
    try:
        public_key = _import_rsa_public_key(key).public_key
    except (JoseError, SecurityWarning, TypeError, ValueError):
        raise ValueError("OIDC JWK RSA public key is invalid") from None
    if not isinstance(public_key, RSAPublicKey):
        raise ValueError("OIDC JWK must contain an RSA public key")
    if public_key.key_size < MINIMUM_ACTIONS_OIDC_RSA_MODULUS_BITS:
        raise ValueError("OIDC JWK RSA modulus is below the admitted size")
    return key_id


def _import_rsa_public_key(key: Mapping[str, object]) -> RSAKey:
    projection: dict[str, str | list[str]] = {}
    for member in _STRING_JWK_MEMBERS:
        value = key.get(member)
        if type(value) is not str:
            raise ValueError("OIDC JWK member is invalid")
        projection[member] = value
    key_operations = key.get("key_ops")
    if key_operations is not None:
        if not isinstance(key_operations, (tuple, list)) or any(
            type(operation) is not str for operation in key_operations
        ):
            raise ValueError("OIDC JWK key operations are invalid")
        projection["key_ops"] = list(key_operations)
    imported = RSAKey.import_key(projection)
    if imported.is_private:
        raise ValueError("OIDC JWK must contain only public key material")
    return imported


def _reject(reason_code: str, message: str, verified_at: datetime) -> RejectedIdentity:
    return RejectedIdentity(
        reason_code=reason_code,
        message=message,
        verified_at=verified_at,
        verifier_version=VERIFIER_VERSION,
    )

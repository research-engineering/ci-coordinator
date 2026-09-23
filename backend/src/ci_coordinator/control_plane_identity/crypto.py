"""Domain-separated OAuth transaction, session, and CSRF cryptography."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Final, cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity.model import (
    BrowserOAuthTransaction,
    ControlPlaneSessionRecord,
    KeycloakBrowserEvidence,
    KeycloakHumanPrincipal,
    ReviewerOAuthTransaction,
    ReviewerStepUpBinding,
    derive_human_actor_id,
)
from ci_coordinator.kernel import canonical_json, load_strict_json

_BROWSER_TRANSACTION_AAD = b"ci-coordinator/keycloak-browser-transaction/v1"
_REVIEWER_TRANSACTION_AAD = b"ci-coordinator/github-reviewer-transaction/v1"
_BROWSER_TRANSACTION_KEY_INFO = b"ci-coordinator/keycloak-browser-transaction-key/v1"
_REVIEWER_TRANSACTION_KEY_INFO = b"ci-coordinator/github-reviewer-transaction-key/v1"
_CSRF_KEY_INFO = b"ci-coordinator/control-plane-csrf-key/v1"
_SESSION_HANDLE_KEY_INFO = b"ci-coordinator/control-plane-session-handle-key/v1"
_NONCE_BYTES = 12
_RANDOM_BYTES = 32
_MAX_TRANSACTION_COOKIE_BYTES = 4_096


@dataclass(frozen=True, slots=True)
class BrowserAuthorizationRequest:
    state: str = field(repr=False)
    nonce: str = field(repr=False)
    code_challenge: str
    transaction_cookie: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class ReviewerAuthorizationRequest:
    state: str = field(repr=False)
    transaction_digest: bytes = field(repr=False)
    code_challenge: str
    transaction_cookie: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.transaction_digest) is not bytes or len(self.transaction_digest) != 32:
            raise ValueError("reviewer transaction digest must be exactly 32 bytes")


class ControlPlaneIdentityCrypto:
    def __init__(
        self,
        master_key: bytes,
        *,
        entropy: Callable[[int], bytes] = secrets.token_bytes,
    ) -> None:
        if type(master_key) is not bytes or len(master_key) != 32:
            raise ValueError("control-plane identity master key must be exactly 32 bytes")
        self._browser_transaction = AESGCM(_derive(master_key, _BROWSER_TRANSACTION_KEY_INFO))
        self._reviewer_transaction = AESGCM(_derive(master_key, _REVIEWER_TRANSACTION_KEY_INFO))
        self._csrf_key = _derive(master_key, _CSRF_KEY_INFO)
        self._session_handle_key = _derive(master_key, _SESSION_HANDLE_KEY_INFO)
        self._entropy = entropy

    def new_browser_transaction(
        self,
        *,
        issued_at: datetime,
        authority_profile_digest: str,
    ) -> BrowserAuthorizationRequest:
        state = _encode(self._random_bytes())
        nonce_value = _encode(self._random_bytes())
        verifier = _encode(self._random_bytes())
        transaction = BrowserOAuthTransaction(
            state_digest=hashlib.sha256(state.encode("ascii")).digest(),
            nonce=nonce_value,
            code_verifier=verifier,
            authority_profile_digest=authority_profile_digest,
            issued_at=issued_at,
        )
        payload = canonical_json(
            {
                "authorityProfileDigest": transaction.authority_profile_digest,
                "issuedAt": transaction.issued_at.isoformat(),
                "nonce": transaction.nonce,
                "stateDigest": transaction.state_digest.hex(),
                "verifier": transaction.code_verifier,
            }
        )
        return BrowserAuthorizationRequest(
            state=state,
            nonce=nonce_value,
            code_challenge=_pkce_challenge(verifier),
            transaction_cookie=self._seal(
                self._browser_transaction,
                payload,
                _BROWSER_TRANSACTION_AAD,
            ),
        )

    def open_browser_transaction(self, cookie: str | None) -> BrowserOAuthTransaction | None:
        value = self._open(self._browser_transaction, cookie, _BROWSER_TRANSACTION_AAD)
        if value is None or set(value) != {
            "authorityProfileDigest",
            "issuedAt",
            "nonce",
            "stateDigest",
            "verifier",
        }:
            return None
        try:
            return BrowserOAuthTransaction(
                state_digest=bytes.fromhex(_exact_text(value, "stateDigest")),
                nonce=_exact_text(value, "nonce"),
                code_verifier=_exact_text(value, "verifier"),
                authority_profile_digest=_exact_text(value, "authorityProfileDigest"),
                issued_at=_instant(value, "issuedAt"),
            )
        except (TypeError, ValueError):
            return None

    def new_reviewer_transaction(
        self,
        binding: ReviewerStepUpBinding,
    ) -> ReviewerAuthorizationRequest:
        if type(binding) is not ReviewerStepUpBinding:
            raise TypeError("reviewer transaction binding must be exact")
        state = _encode(self._random_bytes())
        verifier = _encode(self._random_bytes())
        transaction = ReviewerOAuthTransaction(
            state_digest=hashlib.sha256(state.encode("ascii")).digest(),
            code_verifier=verifier,
            binding=binding,
        )
        payload = canonical_json(
            {
                "binding": _reviewer_binding_mapping(transaction.binding),
                "stateDigest": transaction.state_digest.hex(),
                "verifier": transaction.code_verifier,
            }
        )
        return ReviewerAuthorizationRequest(
            state=state,
            transaction_digest=transaction.state_digest,
            code_challenge=_pkce_challenge(verifier),
            transaction_cookie=self._seal(
                self._reviewer_transaction,
                payload,
                _REVIEWER_TRANSACTION_AAD,
            ),
        )

    def open_reviewer_transaction(self, cookie: str | None) -> ReviewerOAuthTransaction | None:
        value = self._open(self._reviewer_transaction, cookie, _REVIEWER_TRANSACTION_AAD)
        if value is None or set(value) != {"binding", "stateDigest", "verifier"}:
            return None
        binding_value = value.get("binding")
        if type(binding_value) is not dict:
            return None
        binding = cast(dict[str, object], binding_value)
        if set(binding) != {
            "authorityProfileDigest",
            "expiresAt",
            "expectedActiveEpochId",
            "expectedActiveRevision",
            "initiatingActor",
            "installationId",
            "issuedAt",
            "operationId",
            "proposalDigest",
            "proposalManifestId",
            "repositoryId",
            "revision",
            "sessionHandleDigest",
        }:
            return None
        try:
            return ReviewerOAuthTransaction(
                state_digest=bytes.fromhex(_exact_text(value, "stateDigest")),
                code_verifier=_exact_text(value, "verifier"),
                binding=ReviewerStepUpBinding(
                    session_handle_digest=bytes.fromhex(
                        _exact_text(binding, "sessionHandleDigest")
                    ),
                    initiating_actor=_exact_text(binding, "initiatingActor"),
                    scope=RepositoryScope(
                        _exact_int(binding, "installationId"),
                        _exact_int(binding, "repositoryId"),
                    ),
                    operation_id=_exact_text(binding, "operationId"),
                    proposal_manifest_id=_exact_text(binding, "proposalManifestId"),
                    revision=_exact_text(binding, "revision"),
                    proposal_digest=_exact_text(binding, "proposalDigest"),
                    expected_active_epoch_id=_optional_exact_text(
                        binding,
                        "expectedActiveEpochId",
                    ),
                    expected_active_revision=_optional_exact_int(
                        binding,
                        "expectedActiveRevision",
                    ),
                    authority_profile_digest=_exact_text(
                        binding,
                        "authorityProfileDigest",
                    ),
                    issued_at=_instant(binding, "issuedAt"),
                    expires_at=_instant(binding, "expiresAt"),
                ),
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def state_matches(
        transaction: BrowserOAuthTransaction | ReviewerOAuthTransaction,
        state: str | None,
    ) -> bool:
        if type(transaction) not in {BrowserOAuthTransaction, ReviewerOAuthTransaction}:
            return False
        if not _is_handle(state):
            return False
        admitted_state = cast(str, state)
        digest = hashlib.sha256(admitted_state.encode("ascii")).digest()
        return hmac.compare_digest(transaction.state_digest, digest)

    def issue_session(
        self,
        *,
        evidence: KeycloakBrowserEvidence,
        issued_at: datetime,
        expires_at: datetime,
        authority_profile_digest: str,
    ) -> tuple[str, ControlPlaneSessionRecord]:
        if type(evidence) is not KeycloakBrowserEvidence:
            raise TypeError("session issuance evidence must be exact")
        handle = _encode(self._random_bytes())
        handle_digest = self.handle_digest(handle)
        if handle_digest is None:
            raise RuntimeError("generated session handle is invalid")
        return handle, ControlPlaneSessionRecord(
            handle_digest=handle_digest,
            issuer=evidence.issuer,
            subject=evidence.subject,
            keycloak_session_id=evidence.keycloak_session_id,
            actor_id=derive_human_actor_id(evidence.issuer, evidence.subject),
            roles=evidence.roles,
            authority_profile_digest=authority_profile_digest,
            issued_at=issued_at,
            expires_at=expires_at,
            display=evidence.display,
        )

    def restore_human_principal(
        self,
        handle: str,
        record: ControlPlaneSessionRecord,
    ) -> KeycloakHumanPrincipal | None:
        if type(record) is not ControlPlaneSessionRecord:
            return None
        digest = self.handle_digest(handle)
        if digest is None or not hmac.compare_digest(digest, record.handle_digest):
            return None
        try:
            return KeycloakHumanPrincipal(
                issuer=record.issuer,
                subject=record.subject,
                keycloak_session_id=record.keycloak_session_id,
                roles=record.roles,
                session_handle=handle,
                expires_at=record.expires_at,
                authority_profile_digest=record.authority_profile_digest,
                display=record.display,
            )
        except (TypeError, ValueError):
            return None

    def handle_digest(self, handle: str | None) -> bytes | None:
        if not _is_handle(handle):
            return None
        admitted_handle = cast(str, handle)
        return hmac.digest(self._session_handle_key, admitted_handle.encode("ascii"), "sha256")

    def csrf_token(self, handle: str) -> str:
        if not _is_handle(handle):
            raise ValueError("session handle is invalid")
        return _encode(hmac.digest(self._csrf_key, handle.encode("ascii"), "sha256"))

    def csrf_matches(self, handle: str, candidate: str | None) -> bool:
        if not _is_handle(candidate):
            return False
        try:
            expected = self.csrf_token(handle)
        except (TypeError, ValueError):
            return False
        admitted_candidate = cast(str, candidate)
        return hmac.compare_digest(
            expected.encode("ascii"),
            admitted_candidate.encode("ascii"),
        )

    def _seal(self, cipher: AESGCM, payload: bytes, aad: bytes) -> str:
        nonce = self._random_nonce()
        return _encode(nonce + cipher.encrypt(nonce, payload, aad))

    @staticmethod
    def _open(cipher: AESGCM, cookie: str | None, aad: bytes) -> dict[str, object] | None:
        encoded = _decode(cookie, maximum_bytes=_MAX_TRANSACTION_COOKIE_BYTES)
        if encoded is None or len(encoded) <= _NONCE_BYTES + 16:
            return None
        nonce, ciphertext = encoded[:_NONCE_BYTES], encoded[_NONCE_BYTES:]
        try:
            value = load_strict_json(cipher.decrypt(nonce, ciphertext, aad))
        except (InvalidTag, TypeError, ValueError, json.JSONDecodeError):
            return None
        if type(value) is not dict:
            return None
        return cast(dict[str, object], value)

    def _random_bytes(self) -> bytes:
        return self._entropy_bytes(_RANDOM_BYTES, "value")

    def _random_nonce(self) -> bytes:
        return self._entropy_bytes(_NONCE_BYTES, "nonce")

    def _entropy_bytes(self, size: int, name: str) -> bytes:
        value = self._entropy(size)
        if type(value) is not bytes or len(value) != size:
            raise RuntimeError(f"control-plane entropy returned an invalid {name}")
        return value


def _derive(master_key: bytes, info: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=info,
    ).derive(master_key)


def _reviewer_binding_mapping(binding: ReviewerStepUpBinding) -> dict[str, object]:
    return {
        "authorityProfileDigest": binding.authority_profile_digest,
        "expiresAt": binding.expires_at.isoformat(),
        "expectedActiveEpochId": binding.expected_active_epoch_id,
        "expectedActiveRevision": binding.expected_active_revision,
        "initiatingActor": binding.initiating_actor,
        "installationId": binding.scope.installation_id,
        "issuedAt": binding.issued_at.isoformat(),
        "operationId": binding.operation_id,
        "proposalDigest": binding.proposal_digest,
        "proposalManifestId": binding.proposal_manifest_id,
        "repositoryId": binding.scope.repository_id,
        "revision": binding.revision,
        "sessionHandleDigest": binding.session_handle_digest.hex(),
    }


def _pkce_challenge(verifier: str) -> str:
    return _encode(hashlib.sha256(verifier.encode("ascii")).digest())


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: object, *, maximum_bytes: int) -> bytes | None:
    if type(value) is not str or not value or len(value) > maximum_bytes * 2:
        return None
    try:
        encoded = value.encode("ascii")
        decoded = base64.b64decode(
            encoded + b"=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
        return decoded if len(decoded) <= maximum_bytes and _encode(decoded) == value else None
    except (UnicodeEncodeError, binascii.Error, ValueError):
        return None


def _is_handle(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 43
        and all(character in _URL_SAFE for character in value)
    )


def _exact_text(value: Mapping[str, object], key: str) -> str:
    candidate = value.get(key)
    if type(candidate) is not str:
        raise ValueError(f"{key} must be exact text")
    return candidate


def _exact_int(value: Mapping[str, object], key: str) -> int:
    candidate = value.get(key)
    if type(candidate) is not int:
        raise ValueError(f"{key} must be an exact integer")
    return candidate


def _optional_exact_text(value: Mapping[str, object], key: str) -> str | None:
    candidate = value.get(key)
    if candidate is not None and type(candidate) is not str:
        raise ValueError(f"{key} must be exact optional text")
    return candidate


def _optional_exact_int(value: Mapping[str, object], key: str) -> int | None:
    candidate = value.get(key)
    if candidate is not None and type(candidate) is not int:
        raise ValueError(f"{key} must be an exact optional integer")
    return candidate


def _instant(value: Mapping[str, object], key: str) -> datetime:
    instant = datetime.fromisoformat(_exact_text(value, key))
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError(f"{key} must be timezone-aware")
    return instant.astimezone(UTC)


_URL_SAFE: Final = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")

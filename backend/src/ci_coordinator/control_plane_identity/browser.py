"""Keycloak browser-session and back-channel logout transitions."""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol, cast
from urllib.parse import urlsplit

from ci_coordinator.control_plane_identity.activity import IdentityActivityObserver, observe_login
from ci_coordinator.control_plane_identity.crypto import ControlPlaneIdentityCrypto
from ci_coordinator.control_plane_identity.model import (
    BackChannelLogoutEvidence,
    IdentityRejected,
    IdentityUnavailable,
    KeycloakBrowserEvidence,
    KeycloakHumanPrincipal,
    is_canonical_oidc_issuer,
)
from ci_coordinator.control_plane_identity.ports import (
    BackChannelLogoutApplied,
    BackChannelLogoutReplay,
    BackChannelLogoutStore,
    BackChannelLogoutStoreUnavailable,
    ControlPlaneSessionStore,
    ControlPlaneSessionStoreRejected,
    ControlPlaneSessionStoreUnavailable,
    KeycloakBrowserPort,
    KeycloakEvidenceRejected,
    KeycloakUnavailable,
)
from ci_coordinator.kernel import Clock, NoQueueAdmission

MAXIMUM_CONCURRENT_SESSION_LOOKUPS = 16
_MAX_CODE_BYTES = 1_024
_SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class BrowserIdentityPolicy:
    issuer: str
    authority_profile_digest: str
    session_maximum_seconds: int = 900
    transaction_maximum_seconds: int = 300
    clock_skew_seconds: int = 60
    logout_token_maximum_age_seconds: int = 120

    def __post_init__(self) -> None:
        if not is_canonical_oidc_issuer(self.issuer):
            raise ValueError("browser identity issuer must be a canonical HTTPS URL")
        if (
            type(self.authority_profile_digest) is not str
            or _SHA256.fullmatch(self.authority_profile_digest) is None
        ):
            raise ValueError("authority profile digest must be lowercase SHA-256 hexadecimal")
        _bounded_integer(self.session_maximum_seconds, "session maximum", 60, 900)
        _bounded_integer(self.transaction_maximum_seconds, "transaction maximum", 60, 300)
        _bounded_integer(self.clock_skew_seconds, "clock skew", 0, 120)
        _bounded_integer(
            self.logout_token_maximum_age_seconds,
            "logout token maximum age",
            1,
            120,
        )


@dataclass(frozen=True, slots=True)
class BrowserLoginStart:
    authorization_url: str
    transaction_cookie: str = field(repr=False)
    expires_at: datetime
    lifetime_seconds: int

    def __post_init__(self) -> None:
        if type(self.authorization_url) is not str or len(self.authorization_url) > 2_048:
            raise ValueError("authorization URL is outside its bound")
        parsed = urlsplit(self.authorization_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("authorization URL must use HTTPS")
        if type(self.transaction_cookie) is not str or not self.transaction_cookie:
            raise ValueError("transaction cookie must be non-empty")
        object.__setattr__(
            self,
            "expires_at",
            _aware_utc(self.expires_at, "transaction expiry"),
        )
        _bounded_integer(self.lifetime_seconds, "transaction lifetime", 60, 300)


@dataclass(frozen=True, slots=True)
class BrowserLoginCompleted:
    principal: KeycloakHumanPrincipal

    def __post_init__(self) -> None:
        if type(self.principal) is not KeycloakHumanPrincipal:
            raise TypeError("browser login principal must be exact")


@dataclass(frozen=True, slots=True)
class BrowserLogoutCompleted:
    remote_redirect_url: str | None

    def __post_init__(self) -> None:
        if self.remote_redirect_url is None:
            return
        if type(self.remote_redirect_url) is not str or len(self.remote_redirect_url) > 2_048:
            raise ValueError("remote logout URL is outside its bound")
        parsed = urlsplit(self.remote_redirect_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("remote logout URL must use HTTPS")


@dataclass(frozen=True, slots=True)
class BackChannelLogoutCompleted:
    deleted_session_count: int

    def __post_init__(self) -> None:
        if type(self.deleted_session_count) is not int or self.deleted_session_count < 0:
            raise ValueError("deleted session count must be non-negative")


type BrowserLoginResult = BrowserLoginCompleted | IdentityRejected | IdentityUnavailable
type BrowserAuthenticationResult = KeycloakHumanPrincipal | IdentityRejected | IdentityUnavailable
type BrowserLogoutResult = BrowserLogoutCompleted | IdentityUnavailable
type BackChannelLogoutResult = BackChannelLogoutCompleted | IdentityRejected | IdentityUnavailable


class BrowserIdentityUseCase(Protocol):
    def start_login(self) -> BrowserLoginStart | IdentityUnavailable: ...

    async def complete_login(
        self,
        *,
        code: str | None,
        state: str | None,
        transaction_cookie: str | None,
        previous_session_handle: str | None,
    ) -> BrowserLoginResult: ...

    async def authenticate(self, session_handle: str | None) -> BrowserAuthenticationResult: ...

    async def logout(self, session_handle: str | None) -> BrowserLogoutResult: ...

    async def apply_back_channel_logout(
        self,
        evidence: BackChannelLogoutEvidence,
    ) -> BackChannelLogoutResult: ...

    def csrf_token(self, principal: KeycloakHumanPrincipal) -> str: ...

    def csrf_matches(self, principal: KeycloakHumanPrincipal, candidate: str | None) -> bool: ...


class BrowserIdentityService:
    def __init__(
        self,
        *,
        provider: KeycloakBrowserPort,
        sessions: ControlPlaneSessionStore,
        back_channel_logouts: BackChannelLogoutStore,
        crypto: ControlPlaneIdentityCrypto,
        clock: Clock,
        policy: BrowserIdentityPolicy,
        activity: IdentityActivityObserver | None = None,
    ) -> None:
        if type(crypto) is not ControlPlaneIdentityCrypto:
            raise TypeError("browser identity crypto must be exact")
        if type(policy) is not BrowserIdentityPolicy:
            raise TypeError("browser identity policy must be exact")
        self._provider = provider
        self._sessions = sessions
        self._back_channel_logouts = back_channel_logouts
        self._crypto = crypto
        self._clock = clock
        self._policy = policy
        self._activity = activity
        self._session_lookup_admission = NoQueueAdmission(MAXIMUM_CONCURRENT_SESSION_LOOKUPS)

    def start_login(self) -> BrowserLoginStart | IdentityUnavailable:
        now = _clock_now(self._clock)
        request = self._crypto.new_browser_transaction(
            issued_at=now,
            authority_profile_digest=self._policy.authority_profile_digest,
        )
        try:
            authorization_url = self._provider.authorization_url(
                state=request.state,
                nonce=request.nonce,
                code_challenge=request.code_challenge,
            )
            return BrowserLoginStart(
                authorization_url=authorization_url,
                transaction_cookie=request.transaction_cookie,
                expires_at=now + timedelta(seconds=self._policy.transaction_maximum_seconds),
                lifetime_seconds=self._policy.transaction_maximum_seconds,
            )
        except (KeycloakUnavailable, TypeError, ValueError):
            return IdentityUnavailable("identity_provider_unavailable")

    async def complete_login(
        self,
        *,
        code: str | None,
        state: str | None,
        transaction_cookie: str | None,
        previous_session_handle: str | None,
    ) -> BrowserLoginResult:
        result = await self._complete_login(
            code=code,
            state=state,
            transaction_cookie=transaction_cookie,
            previous_session_handle=previous_session_handle,
        )
        if isinstance(result, IdentityRejected):
            await observe_login(self._activity, "login_rejected")
        elif isinstance(result, IdentityUnavailable):
            await observe_login(self._activity, "login_unavailable")
        return result

    async def _complete_login(
        self,
        *,
        code: str | None,
        state: str | None,
        transaction_cookie: str | None,
        previous_session_handle: str | None,
    ) -> BrowserLoginResult:
        transaction = self._crypto.open_browser_transaction(transaction_cookie)
        now = _clock_now(self._clock)
        if transaction is None or not self._transaction_is_current(transaction.issued_at, now):
            return IdentityRejected("invalid_login")
        if transaction.authority_profile_digest != self._policy.authority_profile_digest:
            return IdentityRejected("authority_profile_changed")
        if not _bounded_code(code) or not self._crypto.state_matches(transaction, state):
            return IdentityRejected("invalid_login")
        admitted_code = cast(str, code)
        try:
            evidence = await self._provider.exchange_code(
                code=admitted_code,
                code_verifier=transaction.code_verifier,
                expected_nonce=transaction.nonce,
            )
        except asyncio.CancelledError:
            raise
        except KeycloakEvidenceRejected:
            return IdentityRejected("invalid_login")
        except KeycloakUnavailable:
            return IdentityUnavailable("identity_provider_unavailable")
        if type(evidence) is not KeycloakBrowserEvidence or evidence.issuer != self._policy.issuer:
            return IdentityRejected("invalid_login")
        application_now = _clock_now(self._clock)
        skew = timedelta(seconds=self._policy.clock_skew_seconds)
        if evidence.issued_at > application_now + skew or evidence.expires_at <= application_now:
            return IdentityRejected("invalid_login")
        try:
            durable_now = _aware_utc(await self._sessions.current_time(), "durable time")
            expires_at = min(
                evidence.expires_at,
                durable_now + timedelta(seconds=self._policy.session_maximum_seconds),
            )
            if expires_at <= durable_now or evidence.expires_at <= _clock_now(self._clock):
                return IdentityRejected("invalid_login")
            handle, record = self._crypto.issue_session(
                evidence=evidence,
                issued_at=durable_now,
                expires_at=expires_at,
                authority_profile_digest=self._policy.authority_profile_digest,
            )
            await self._sessions.replace(
                previous_handle_digest=self._crypto.handle_digest(previous_session_handle),
                record=record,
            )
        except asyncio.CancelledError:
            raise
        except ControlPlaneSessionStoreRejected:
            return IdentityRejected("invalid_login")
        except ControlPlaneSessionStoreUnavailable:
            return IdentityUnavailable("session_store_unavailable")
        principal = self._crypto.restore_human_principal(handle, record)
        if principal is None:
            raise RuntimeError("new control-plane session could not be restored")
        return BrowserLoginCompleted(principal)

    async def authenticate(self, session_handle: str | None) -> BrowserAuthenticationResult:
        digest = self._crypto.handle_digest(session_handle)
        if digest is None or session_handle is None:
            return IdentityRejected("unauthenticated")
        lease = self._session_lookup_admission.try_acquire()
        if lease is None:
            return IdentityRejected("overloaded")
        try:
            try:
                record = await self._sessions.load(digest)
            except asyncio.CancelledError:
                raise
            except ControlPlaneSessionStoreUnavailable:
                return IdentityUnavailable("session_store_unavailable")
            if record is None:
                return IdentityRejected("unauthenticated")
            if record.authority_profile_digest != self._policy.authority_profile_digest:
                await self._best_effort_delete(digest)
                return IdentityRejected("authority_profile_changed")
            if record.issuer != self._policy.issuer or record.expires_at <= _clock_now(self._clock):
                await self._best_effort_delete(digest)
                return IdentityRejected("unauthenticated")
            principal = self._crypto.restore_human_principal(session_handle, record)
            if principal is None:
                await self._best_effort_delete(digest)
                return IdentityRejected("unauthenticated")
            return principal
        finally:
            lease.release()

    async def logout(self, session_handle: str | None) -> BrowserLogoutResult:
        digest = self._crypto.handle_digest(session_handle)
        if digest is not None:
            try:
                await self._sessions.delete(digest)
            except asyncio.CancelledError:
                raise
            except ControlPlaneSessionStoreUnavailable:
                return IdentityUnavailable("session_store_unavailable")
        try:
            remote_url = await self._provider.logout_url()
            return BrowserLogoutCompleted(remote_url)
        except (KeycloakUnavailable, TypeError, ValueError):
            return BrowserLogoutCompleted(None)

    async def apply_back_channel_logout(
        self,
        evidence: BackChannelLogoutEvidence,
    ) -> BackChannelLogoutResult:
        if (
            type(evidence) is not BackChannelLogoutEvidence
            or evidence.issuer != self._policy.issuer
        ):
            return IdentityRejected("invalid_logout")
        now = _clock_now(self._clock)
        skew = timedelta(seconds=self._policy.clock_skew_seconds)
        maximum_age = timedelta(seconds=self._policy.logout_token_maximum_age_seconds)
        if (
            now < evidence.issued_at - skew
            or now > evidence.expires_at + skew
            or now > evidence.issued_at + maximum_age + skew
        ):
            return IdentityRejected("invalid_logout")
        replay_retained_until = (
            max(
                evidence.expires_at,
                evidence.issued_at + maximum_age,
            )
            + skew
        )
        try:
            mutation = await self._back_channel_logouts.consume_and_delete(
                evidence=evidence,
                replay_retained_until=replay_retained_until,
            )
        except asyncio.CancelledError:
            raise
        except BackChannelLogoutStoreUnavailable:
            return IdentityUnavailable("logout_store_unavailable")
        if type(mutation) is BackChannelLogoutReplay:
            return IdentityRejected("logout_replayed")
        if type(mutation) is BackChannelLogoutApplied:
            return BackChannelLogoutCompleted(mutation.deleted_session_count)
        raise RuntimeError("back-channel logout result algebra is incomplete")

    def csrf_token(self, principal: KeycloakHumanPrincipal) -> str:
        if type(principal) is not KeycloakHumanPrincipal:
            raise TypeError("CSRF token requires an exact human principal")
        return self._crypto.csrf_token(principal.session_handle)

    def csrf_matches(self, principal: KeycloakHumanPrincipal, candidate: str | None) -> bool:
        return type(principal) is KeycloakHumanPrincipal and self._crypto.csrf_matches(
            principal.session_handle,
            candidate,
        )

    def _transaction_is_current(self, issued_at: datetime, now: datetime) -> bool:
        age = now - issued_at
        return timedelta() <= age <= timedelta(seconds=self._policy.transaction_maximum_seconds)

    async def _best_effort_delete(self, digest: bytes) -> None:
        try:
            await self._sessions.delete(digest, reason="revoked")
        except ControlPlaneSessionStoreUnavailable:
            return


def _bounded_integer(value: object, name: str, minimum: int, maximum: int) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be in [{minimum}, {maximum}]")


def _bounded_code(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and "\0" not in value
        and not any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        and len(value.encode("utf-8")) <= _MAX_CODE_BYTES
    )


def _clock_now(clock: Clock) -> datetime:
    return _aware_utc(clock.now(), "identity clock")


def _aware_utc(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)

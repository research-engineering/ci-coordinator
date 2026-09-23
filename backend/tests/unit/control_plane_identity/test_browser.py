from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta, timezone

import pytest

from ci_coordinator.control_plane_identity import (
    BackChannelLogoutApplied,
    BackChannelLogoutCompleted,
    BackChannelLogoutEvidence,
    BackChannelLogoutMutation,
    BackChannelLogoutReplay,
    BackChannelLogoutStoreUnavailable,
    BackChannelLogoutTarget,
    BrowserIdentityPolicy,
    BrowserIdentityService,
    BrowserLoginCompleted,
    BrowserLoginStart,
    BrowserLogoutCompleted,
    ControlPlaneIdentityCrypto,
    ControlPlaneSessionRecord,
    ControlPlaneSessionStoreRejected,
    ControlPlaneSessionStoreUnavailable,
    DisplayMetadata,
    IdentityRejected,
    IdentityUnavailable,
    KeycloakBrowserEvidence,
    KeycloakEvidenceRejected,
    KeycloakUnavailable,
)
from ci_coordinator.control_plane_identity.activity import (
    ActivityPrincipal,
    IdentityActivityObserver,
    SessionEndReason,
)
from ci_coordinator.kernel import FixedClock

_ISSUER = "https://auth.example.test/realms/coordinator"
_PROFILE = "a" * 64
_NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)


class _Entropy:
    def __init__(self) -> None:
        self._counter = 0

    def __call__(self, size: int) -> bytes:
        self._counter += 1
        return bytes([self._counter]) * size


def test_login_persists_only_token_free_identity_and_roles() -> None:
    provider = _Provider()
    sessions = _Sessions()
    service = _service(provider=provider, sessions=sessions)
    start = service.start_login()
    assert isinstance(start, BrowserLoginStart)

    result = asyncio.run(
        service.complete_login(
            code="authorization-code",
            state=provider.state,
            transaction_cookie=start.transaction_cookie,
            previous_session_handle=None,
        )
    )

    assert isinstance(result, BrowserLoginCompleted)
    assert result.principal.actor_id.startswith("keycloak-human:v1:")
    assert result.principal.roles == frozenset({"read", "configure"})
    assert provider.exchanges == [("authorization-code", provider.nonce)]
    record = next(iter(sessions.records.values()))
    assert "token" not in repr(record).lower()
    assert record.expires_at == _NOW + timedelta(minutes=15)


def test_login_start_normalizes_aware_expiry_to_utc() -> None:
    offset = timezone(timedelta(hours=2))

    start = BrowserLoginStart(
        authorization_url="https://auth.example.test/authorize",
        transaction_cookie="sealed-transaction",
        expires_at=datetime(2026, 9, 2, 14, 5, tzinfo=offset),
        lifetime_seconds=300,
    )

    assert start.expires_at == _NOW + timedelta(minutes=5)
    assert start.expires_at.tzinfo is UTC


def test_malformed_unicode_code_is_rejected_before_provider_exchange() -> None:
    provider = _Provider()
    service = _service(provider=provider)
    start = service.start_login()
    assert isinstance(start, BrowserLoginStart)

    result = asyncio.run(
        service.complete_login(
            code="\ud800",
            state=provider.state,
            transaction_cookie=start.transaction_cookie,
            previous_session_handle=None,
        )
    )

    assert result == IdentityRejected("invalid_login")
    assert provider.exchanges == []


def test_login_start_maps_provider_failure_to_typed_unavailability() -> None:
    provider = _Provider(authorization_error=KeycloakUnavailable("provider unavailable"))

    assert _service(provider=provider).start_login() == IdentityUnavailable(
        "identity_provider_unavailable"
    )


@pytest.mark.parametrize(
    ("provider_error", "store_error", "expected"),
    [
        (
            KeycloakEvidenceRejected("evidence rejected"),
            None,
            IdentityRejected("invalid_login"),
        ),
        (
            KeycloakUnavailable("provider unavailable"),
            None,
            IdentityUnavailable("identity_provider_unavailable"),
        ),
        (
            None,
            ControlPlaneSessionStoreRejected("session rejected"),
            IdentityRejected("invalid_login"),
        ),
        (
            None,
            ControlPlaneSessionStoreUnavailable("session unavailable"),
            IdentityUnavailable("session_store_unavailable"),
        ),
    ],
    ids=("provider-rejected", "provider-unavailable", "store-rejected", "store-unavailable"),
)
def test_login_preserves_provider_and_session_failure_algebra(
    provider_error: KeycloakEvidenceRejected | KeycloakUnavailable | None,
    store_error: ControlPlaneSessionStoreRejected | ControlPlaneSessionStoreUnavailable | None,
    expected: IdentityRejected | IdentityUnavailable,
) -> None:
    provider = _Provider(exchange_error=provider_error)
    sessions = _Sessions(replace_error=store_error)
    service = _service(provider=provider, sessions=sessions)
    start = service.start_login()
    assert isinstance(start, BrowserLoginStart)

    result = asyncio.run(
        service.complete_login(
            code="authorization-code",
            state=provider.state,
            transaction_cookie=start.transaction_cookie,
            previous_session_handle=None,
        )
    )

    assert result == expected
    assert sessions.records == {}


@pytest.mark.parametrize(
    "evidence",
    [
        KeycloakBrowserEvidence(
            issuer="https://other.example.test/realms/coordinator",
            subject="subject",
            keycloak_session_id="sid-17",
            roles=frozenset({"read"}),
            issued_at=_NOW,
            expires_at=_NOW + timedelta(hours=1),
            display=DisplayMetadata(None, None),
        ),
        KeycloakBrowserEvidence(
            issuer=_ISSUER,
            subject="subject",
            keycloak_session_id="sid-17",
            roles=frozenset({"read"}),
            issued_at=_NOW + timedelta(seconds=61),
            expires_at=_NOW + timedelta(hours=1),
            display=DisplayMetadata(None, None),
        ),
        KeycloakBrowserEvidence(
            issuer=_ISSUER,
            subject="subject",
            keycloak_session_id="sid-17",
            roles=frozenset({"read"}),
            issued_at=_NOW - timedelta(hours=1),
            expires_at=_NOW,
            display=DisplayMetadata(None, None),
        ),
    ],
    ids=("issuer", "future-issued-at", "expired"),
)
def test_login_rejects_noncanonical_or_stale_provider_evidence(
    evidence: KeycloakBrowserEvidence,
) -> None:
    provider = _Provider(evidence=evidence)
    service = _service(provider=provider)
    start = service.start_login()
    assert isinstance(start, BrowserLoginStart)

    result = asyncio.run(
        service.complete_login(
            code="authorization-code",
            state=provider.state,
            transaction_cookie=start.transaction_cookie,
            previous_session_handle=None,
        )
    )

    assert result == IdentityRejected("invalid_login")


@pytest.mark.parametrize(
    ("state", "profile_digest", "expected"),
    [
        ("wrong-state", _PROFILE, IdentityRejected("invalid_login")),
        ("provider-state", "b" * 64, IdentityRejected("authority_profile_changed")),
    ],
)
def test_callback_fails_before_exchange_on_changed_transaction_authority(
    state: str,
    profile_digest: str,
    expected: IdentityRejected,
) -> None:
    provider = _Provider()
    original = _service(provider=provider)
    start = original.start_login()
    assert isinstance(start, BrowserLoginStart)
    candidate_state = provider.state if state == "provider-state" else state
    service = _service(provider=provider, profile_digest=profile_digest)

    result = asyncio.run(
        service.complete_login(
            code="authorization-code",
            state=candidate_state,
            transaction_cookie=start.transaction_cookie,
            previous_session_handle=None,
        )
    )

    assert result == expected
    assert provider.exchanges == []


def test_session_authentication_invalidates_profile_change() -> None:
    sessions = _Sessions()
    provider = _Provider()
    original = _service(provider=provider, sessions=sessions)
    start = original.start_login()
    assert isinstance(start, BrowserLoginStart)
    completed = asyncio.run(
        original.complete_login(
            code="authorization-code",
            state=provider.state,
            transaction_cookie=start.transaction_cookie,
            previous_session_handle=None,
        )
    )
    assert isinstance(completed, BrowserLoginCompleted)

    changed = _service(provider=provider, sessions=sessions, profile_digest="b" * 64)
    result = asyncio.run(changed.authenticate(completed.principal.session_handle))

    assert result == IdentityRejected("authority_profile_changed")
    assert sessions.records == {}


@pytest.mark.parametrize(
    ("session_handle", "load_error", "expected"),
    [
        (None, None, IdentityRejected("unauthenticated")),
        ("A" * 43, None, IdentityRejected("unauthenticated")),
        (
            "A" * 43,
            ControlPlaneSessionStoreUnavailable("session unavailable"),
            IdentityUnavailable("session_store_unavailable"),
        ),
    ],
    ids=("missing", "unknown", "store-unavailable"),
)
def test_session_authentication_preserves_absence_and_store_failure(
    session_handle: str | None,
    load_error: ControlPlaneSessionStoreUnavailable | None,
    expected: IdentityRejected | IdentityUnavailable,
) -> None:
    sessions = _Sessions(load_error=load_error)

    assert asyncio.run(_service(sessions=sessions).authenticate(session_handle)) == expected


def test_logout_deletes_local_authority_before_remote_failure() -> None:
    sessions = _Sessions()
    provider = _Provider()
    service = _service(provider=provider, sessions=sessions)
    start = service.start_login()
    assert isinstance(start, BrowserLoginStart)
    completed = asyncio.run(
        service.complete_login(
            code="authorization-code",
            state=provider.state,
            transaction_cookie=start.transaction_cookie,
            previous_session_handle=None,
        )
    )
    assert isinstance(completed, BrowserLoginCompleted)

    def fail_after_local_delete() -> None:
        assert sessions.records == {}
        raise KeycloakUnavailable("remote logout unavailable")

    provider.on_logout = fail_after_local_delete
    result = asyncio.run(service.logout(completed.principal.session_handle))

    assert result == BrowserLogoutCompleted(None)
    assert sessions.records == {}


def test_back_channel_logout_is_atomic_replay_safe_and_retained_through_skew() -> None:
    logouts = _Logouts(BackChannelLogoutApplied(2))
    service = _service(back_channel_logouts=logouts)
    evidence = BackChannelLogoutEvidence(
        issuer=_ISSUER,
        token_id="logout-17",
        issued_at=_NOW - timedelta(seconds=30),
        expires_at=_NOW + timedelta(seconds=30),
        target=BackChannelLogoutTarget(keycloak_session_id="sid-17"),
    )

    result = asyncio.run(service.apply_back_channel_logout(evidence))

    assert result == BackChannelLogoutCompleted(2)
    assert logouts.calls == [
        (
            evidence,
            evidence.issued_at + timedelta(seconds=180),
        )
    ]

    logouts.result = BackChannelLogoutReplay()
    assert asyncio.run(service.apply_back_channel_logout(evidence)) == IdentityRejected(
        "logout_replayed"
    )


def test_expired_back_channel_logout_never_reaches_durable_mutation() -> None:
    logouts = _Logouts(BackChannelLogoutApplied(1))
    service = _service(back_channel_logouts=logouts)
    evidence = BackChannelLogoutEvidence(
        issuer=_ISSUER,
        token_id="logout-17",
        issued_at=_NOW - timedelta(minutes=4),
        expires_at=_NOW - timedelta(minutes=2),
        target=BackChannelLogoutTarget(subject="subject"),
    )

    result = asyncio.run(service.apply_back_channel_logout(evidence))

    assert result == IdentityRejected("invalid_logout")
    assert logouts.calls == []


def test_back_channel_logout_rejects_wrong_issuer_and_types_store_failure() -> None:
    wrong_issuer = BackChannelLogoutEvidence(
        issuer="https://other.example.test/realms/coordinator",
        token_id="logout-17",
        issued_at=_NOW - timedelta(seconds=30),
        expires_at=_NOW + timedelta(seconds=30),
        target=BackChannelLogoutTarget(subject="subject"),
    )
    unavailable = BackChannelLogoutStoreUnavailable("logout store unavailable")
    logouts = _Logouts(BackChannelLogoutApplied(0), error=unavailable)
    service = _service(back_channel_logouts=logouts)

    assert asyncio.run(service.apply_back_channel_logout(wrong_issuer)) == IdentityRejected(
        "invalid_logout"
    )
    accepted_issuer = BackChannelLogoutEvidence(
        issuer=_ISSUER,
        token_id="logout-18",
        issued_at=_NOW - timedelta(seconds=30),
        expires_at=_NOW + timedelta(seconds=30),
        target=BackChannelLogoutTarget(subject="subject"),
    )
    assert asyncio.run(service.apply_back_channel_logout(accepted_issuer)) == IdentityUnavailable(
        "logout_store_unavailable"
    )


@dataclass
class _Activity:
    calls: list[str] = field(default_factory=list)

    async def login_diagnostic(self, action: str) -> None:
        self.calls.append(action)

    async def principal_diagnostic(self, principal: ActivityPrincipal, action: str) -> None:
        self.calls.append(action)


@pytest.mark.parametrize("store_failure", [False, True])
def test_login_diagnostics_never_receive_credentials_or_assert_commit(store_failure: bool) -> None:
    activity, provider = _Activity(), _Provider()
    sessions = _Sessions(
        replace_error=ControlPlaneSessionStoreUnavailable("secret-exception")
        if store_failure
        else None
    )
    service = _service(provider=provider, sessions=sessions, activity=activity)
    start = service.start_login()
    assert isinstance(start, BrowserLoginStart)
    result = asyncio.run(
        service.complete_login(
            code="secret-code",
            state=provider.state,
            transaction_cookie=start.transaction_cookie,
            previous_session_handle="secret-session",
        )
    )
    if store_failure:
        assert result == IdentityUnavailable("session_store_unavailable")
        assert activity.calls == ["login_unavailable"]
        assert sessions.records == {}
    else:
        assert isinstance(result, BrowserLoginCompleted)
        assert activity.calls == []
    rejected = asyncio.run(
        service.complete_login(
            code="secret-code",
            state="secret-state",
            transaction_cookie=None,
            previous_session_handle="secret-session",
        )
    )
    assert isinstance(rejected, IdentityRejected)
    assert activity.calls[-1] == "login_rejected"
    assert "secret" not in repr(activity)


def _service(
    *,
    provider: _Provider | None = None,
    sessions: _Sessions | None = None,
    back_channel_logouts: _Logouts | None = None,
    profile_digest: str = _PROFILE,
    activity: IdentityActivityObserver | None = None,
) -> BrowserIdentityService:
    return BrowserIdentityService(
        provider=provider or _Provider(),
        sessions=sessions or _Sessions(),
        back_channel_logouts=back_channel_logouts or _Logouts(BackChannelLogoutApplied(0)),
        crypto=ControlPlaneIdentityCrypto(bytes(range(32)), entropy=_Entropy()),
        clock=FixedClock(_NOW),
        activity=activity,
        policy=BrowserIdentityPolicy(
            issuer=_ISSUER,
            authority_profile_digest=profile_digest,
        ),
    )


@dataclass
class _Provider:
    state: str = ""
    nonce: str = ""
    exchanges: list[tuple[str, str]] = field(default_factory=list)
    on_logout: Callable[[], None] | None = None
    authorization_error: KeycloakUnavailable | None = None
    exchange_error: KeycloakEvidenceRejected | KeycloakUnavailable | None = None
    evidence: KeycloakBrowserEvidence | None = None

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str) -> str:
        if self.authorization_error is not None:
            raise self.authorization_error
        assert len(code_challenge) == 43
        self.state = state
        self.nonce = nonce
        return f"https://auth.example.test/authorize?state={state}"

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        expected_nonce: str,
    ) -> KeycloakBrowserEvidence:
        if self.exchange_error is not None:
            raise self.exchange_error
        assert len(code_verifier) == 43
        self.exchanges.append((code, expected_nonce))
        return self.evidence or KeycloakBrowserEvidence(
            issuer=_ISSUER,
            subject="subject",
            keycloak_session_id="sid-17",
            roles=frozenset({"read", "configure"}),
            issued_at=_NOW,
            expires_at=_NOW + timedelta(hours=1),
            display=DisplayMetadata("maintainer", "Maintainer"),
        )

    async def logout_url(self) -> str | None:
        if self.on_logout is not None:
            self.on_logout()
        return "https://auth.example.test/logout"


@dataclass
class _Sessions:
    now: datetime = _NOW
    records: dict[bytes, ControlPlaneSessionRecord] = field(default_factory=dict)
    replace_error: ControlPlaneSessionStoreRejected | ControlPlaneSessionStoreUnavailable | None = (
        None
    )
    load_error: ControlPlaneSessionStoreUnavailable | None = None

    async def current_time(self) -> datetime:
        return self.now

    async def replace(
        self,
        *,
        previous_handle_digest: bytes | None,
        record: ControlPlaneSessionRecord,
    ) -> None:
        if self.replace_error is not None:
            raise self.replace_error
        if previous_handle_digest is not None:
            self.records.pop(previous_handle_digest, None)
        self.records[record.handle_digest] = record

    async def load(self, handle_digest: bytes) -> ControlPlaneSessionRecord | None:
        if self.load_error is not None:
            raise self.load_error
        return self.records.get(handle_digest)

    async def delete(self, handle_digest: bytes, *, reason: SessionEndReason = "logout") -> None:
        self.records.pop(handle_digest, None)


@dataclass
class _Logouts:
    result: BackChannelLogoutMutation
    calls: list[tuple[BackChannelLogoutEvidence, datetime]] = field(default_factory=list)
    error: BackChannelLogoutStoreUnavailable | None = None

    async def consume_and_delete(
        self,
        *,
        evidence: BackChannelLogoutEvidence,
        replay_retained_until: datetime,
    ) -> BackChannelLogoutMutation:
        self.calls.append((evidence, replay_retained_until))
        if self.error is not None:
            raise self.error
        return self.result

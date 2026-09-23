from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import (
    ControlPlaneIdentityCrypto,
    DisplayMetadata,
    KeycloakBrowserEvidence,
    ReviewerStepUpBinding,
    derive_human_actor_id,
)

_ISSUER = "https://auth.example.test/realms/coordinator"
_PROFILE = "a" * 64
_NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)


class _Entropy:
    def __init__(self) -> None:
        self._counter = 0

    def __call__(self, size: int) -> bytes:
        self._counter += 1
        return bytes([self._counter]) * size


def _crypto() -> ControlPlaneIdentityCrypto:
    return ControlPlaneIdentityCrypto(bytes(range(32)), entropy=_Entropy())


def test_browser_transaction_is_aead_pkce_nonce_state_and_profile_bound() -> None:
    crypto = _crypto()

    request = crypto.new_browser_transaction(
        issued_at=_NOW,
        authority_profile_digest=_PROFILE,
    )
    opened = crypto.open_browser_transaction(request.transaction_cookie)

    assert opened is not None
    assert opened.authority_profile_digest == _PROFILE
    assert opened.nonce == request.nonce
    assert crypto.state_matches(opened, request.state)
    assert not crypto.state_matches(opened, "A" * 43)
    assert len(request.code_challenge) == 43
    assert crypto.open_browser_transaction(_tamper(request.transaction_cookie)) is None


def test_browser_and_reviewer_transaction_domains_are_not_interchangeable() -> None:
    crypto = _crypto()
    browser = crypto.new_browser_transaction(
        issued_at=_NOW,
        authority_profile_digest=_PROFILE,
    )
    reviewer = crypto.new_reviewer_transaction(_reviewer_binding(crypto))

    assert crypto.open_reviewer_transaction(browser.transaction_cookie) is None
    assert crypto.open_browser_transaction(reviewer.transaction_cookie) is None


def test_reviewer_transaction_round_trip_preserves_every_binding() -> None:
    crypto = _crypto()
    binding = _reviewer_binding(crypto)

    request = crypto.new_reviewer_transaction(binding)
    opened = crypto.open_reviewer_transaction(request.transaction_cookie)

    assert opened is not None
    assert opened.binding == binding
    assert crypto.state_matches(opened, request.state)
    assert len(request.code_challenge) == 43
    assert crypto.open_reviewer_transaction(_tamper(request.transaction_cookie)) is None
    assert crypto.open_reviewer_transaction(request.transaction_cookie + "=") is None


def test_session_handle_restores_no_provider_credential_and_binds_csrf() -> None:
    crypto = _crypto()
    handle, record = crypto.issue_session(
        evidence=_browser_evidence(),
        issued_at=_NOW,
        expires_at=_NOW + timedelta(minutes=15),
        authority_profile_digest=_PROFILE,
    )

    principal = crypto.restore_human_principal(handle, record)

    assert principal is not None
    assert principal.actor_id == derive_human_actor_id(_ISSUER, "subject")
    assert "token" not in repr(record).lower()
    assert "token" not in repr(principal).lower()
    assert crypto.restore_human_principal("B" * 43, record) is None
    assert crypto.csrf_matches(handle, crypto.csrf_token(handle))
    assert not crypto.csrf_matches(handle, crypto.csrf_token("B" * 43))


def test_master_key_rotation_revokes_existing_session_handles() -> None:
    previous = ControlPlaneIdentityCrypto(bytes(range(32)), entropy=_Entropy())
    rotated = ControlPlaneIdentityCrypto(bytes(reversed(range(32))), entropy=_Entropy())
    handle, record = previous.issue_session(
        evidence=_browser_evidence(),
        issued_at=_NOW,
        expires_at=_NOW + timedelta(minutes=15),
        authority_profile_digest=_PROFILE,
    )

    assert previous.restore_human_principal(handle, record) is not None
    assert rotated.handle_digest(handle) != record.handle_digest
    assert rotated.restore_human_principal(handle, record) is None


def _reviewer_binding(crypto: ControlPlaneIdentityCrypto) -> ReviewerStepUpBinding:
    session_digest = crypto.handle_digest("S" * 43)
    assert session_digest is not None
    return ReviewerStepUpBinding(
        session_handle_digest=session_digest,
        initiating_actor=derive_human_actor_id(_ISSUER, "subject"),
        scope=RepositoryScope(7, 11),
        operation_id="4f776986-e879-4e3c-8fc8-b79f4c026669",
        proposal_manifest_id="proposal:" + "d" * 32,
        revision="b" * 40,
        proposal_digest="c" * 64,
        expected_active_epoch_id="e" * 64,
        expected_active_revision=3,
        authority_profile_digest=_PROFILE,
        issued_at=_NOW,
        expires_at=_NOW + timedelta(minutes=5),
    )


def _browser_evidence() -> KeycloakBrowserEvidence:
    return KeycloakBrowserEvidence(
        issuer=_ISSUER,
        subject="subject",
        keycloak_session_id="sid-17",
        roles=frozenset({"read", "configure"}),
        issued_at=_NOW,
        expires_at=_NOW + timedelta(hours=1),
        display=DisplayMetadata("maintainer", "Maintainer"),
    )


def _tamper(value: str) -> str:
    return value[:-1] + ("A" if value[-1] != "A" else "B")

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import (
    ControlPlaneIdentityCrypto,
    DisplayMetadata,
    KeycloakBrowserEvidence,
    LoginStartAnchor,
    RegisteredEpochStepUpBinding,
    ReviewerStepUpBinding,
    derive_human_actor_id,
)
from ci_coordinator.control_plane_identity import crypto as identity_crypto
from ci_coordinator.kernel import canonical_json

_ISSUER = "https://auth.example.test/realms/coordinator"
_PROFILE = "a" * 64
_NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)
_LOGIN_STARTED_AT = datetime(2026, 9, 2, 13, 59, 58, 125_000, tzinfo=timezone(timedelta(hours=2)))
_LOGIN_START = LoginStartAnchor(fence_generation=41, started_at=_LOGIN_STARTED_AT)


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
        login_start=_LOGIN_START,
        authority_profile_digest=_PROFILE,
    )
    opened = crypto.open_browser_transaction(request.transaction_cookie)

    assert opened is not None
    assert opened.authority_profile_digest == _PROFILE
    assert opened.issued_at == _NOW
    assert opened.login_start == _LOGIN_START
    assert opened.login_start.started_at.tzinfo is UTC
    assert opened.nonce == request.nonce
    assert crypto.state_matches(opened, request.state)
    assert not crypto.state_matches(opened, "A" * 43)
    assert len(request.code_challenge) == 43
    assert crypto.open_browser_transaction(_tamper(request.transaction_cookie)) is None


def test_browser_and_reviewer_transaction_domains_are_not_interchangeable() -> None:
    crypto = _crypto()
    browser = crypto.new_browser_transaction(
        issued_at=_NOW,
        login_start=_LOGIN_START,
        authority_profile_digest=_PROFILE,
    )
    reviewer = crypto.new_reviewer_transaction(_reviewer_binding(crypto))

    assert crypto.open_reviewer_transaction(browser.transaction_cookie) is None
    assert crypto.open_browser_transaction(reviewer.transaction_cookie) is None


def test_sealed_browser_transaction_without_an_exact_login_start_anchor_is_rejected() -> None:
    crypto = _crypto()
    request = crypto.new_browser_transaction(
        issued_at=_NOW,
        login_start=_LOGIN_START,
        authority_profile_digest=_PROFILE,
    )
    opened = crypto.open_browser_transaction(request.transaction_cookie)
    assert opened is not None
    predecessor_shape: dict[str, object] = {
        "authorityProfileDigest": _PROFILE,
        "issuedAt": _NOW.isoformat(),
        "nonce": opened.nonce,
        "stateDigest": opened.state_digest.hex(),
        "verifier": opened.code_verifier,
    }

    def sealed(payload: dict[str, object]) -> str:
        # noinspection PyProtectedMember
        return crypto._seal(
            crypto._browser_transaction,
            canonical_json(payload),
            identity_crypto._BROWSER_TRANSACTION_AAD,
        )

    exact_anchor: dict[str, object] = {
        **predecessor_shape,
        "loginFenceGeneration": 41,
        "loginStartedAt": _LOGIN_STARTED_AT.isoformat(),
    }
    # The first predecessor release sealed only a wall-clock anchor.
    time_only = {key: value for key, value in exact_anchor.items() if key != "loginFenceGeneration"}
    for rejected in (
        predecessor_shape,
        time_only,
        {**exact_anchor, "loginStartedAt": "2026-09-02T11:59:58"},
        {**exact_anchor, "loginFenceGeneration": -1},
        {**exact_anchor, "loginFenceGeneration": True},
        {**exact_anchor, "loginFenceGeneration": "41"},
    ):
        assert crypto.open_browser_transaction(sealed(rejected)) is None
    restored = crypto.open_browser_transaction(sealed(exact_anchor))
    assert restored is not None
    assert restored.login_start == _LOGIN_START


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


def test_registered_epoch_transaction_round_trip_preserves_every_binding() -> None:
    crypto = _crypto()
    binding = _registered_epoch_binding(crypto)

    request = crypto.new_registered_epoch_reviewer_transaction(binding)
    opened = crypto.open_registered_epoch_reviewer_transaction(request.transaction_cookie)

    assert opened is not None
    assert opened.binding == binding
    assert crypto.state_matches(opened, request.state)
    assert len(request.code_challenge) == 43
    assert (
        crypto.open_registered_epoch_reviewer_transaction(_tamper(request.transaction_cookie))
        is None
    )


def test_proposal_and_registered_epoch_cookies_are_not_interchangeable() -> None:
    # Kills: sealing the registered-epoch subject under the proposal associated data.
    crypto = _crypto()
    proposal = crypto.new_reviewer_transaction(_reviewer_binding(crypto))
    registered = crypto.new_registered_epoch_reviewer_transaction(_registered_epoch_binding(crypto))

    assert crypto.open_registered_epoch_reviewer_transaction(proposal.transaction_cookie) is None
    assert crypto.open_reviewer_transaction(registered.transaction_cookie) is None
    assert crypto.open_browser_transaction(registered.transaction_cookie) is None


def test_registered_epoch_cookie_requires_its_exact_subject_kind_and_key_set() -> None:
    # Kills: dropping the explicit subjectKind discriminator or the exact key-set check.
    crypto = _crypto()
    request = crypto.new_registered_epoch_reviewer_transaction(_registered_epoch_binding(crypto))
    opened = crypto.open_registered_epoch_reviewer_transaction(request.transaction_cookie)
    assert opened is not None
    exact = identity_crypto._registered_epoch_binding_mapping(opened.binding)

    def sealed(binding: dict[str, object]) -> str:
        # noinspection PyProtectedMember
        return crypto._seal(
            crypto._reviewer_transaction,
            canonical_json(
                {
                    "binding": binding,
                    "stateDigest": opened.state_digest.hex(),
                    "verifier": opened.code_verifier,
                }
            ),
            identity_crypto._REGISTERED_EPOCH_REVIEWER_TRANSACTION_AAD,
        )

    assert crypto.open_registered_epoch_reviewer_transaction(sealed(exact)) is not None
    for rejected in (
        {**exact, "subjectKind": "proposal"},
        {key: value for key, value in exact.items() if key != "subjectKind"},
        {**exact, "proposalManifestId": "proposal:" + "d" * 32},
    ):
        assert crypto.open_registered_epoch_reviewer_transaction(sealed(rejected)) is None


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


def _registered_epoch_binding(crypto: ControlPlaneIdentityCrypto) -> RegisteredEpochStepUpBinding:
    session_digest = crypto.handle_digest("S" * 43)
    assert session_digest is not None
    return RegisteredEpochStepUpBinding(
        session_handle_digest=session_digest,
        initiating_actor=derive_human_actor_id(_ISSUER, "subject"),
        scope=RepositoryScope(7, 11),
        operation_id="registered-review-1",
        target_epoch_id="f" * 64,
        source_hash="1" * 64,
        revision="b" * 40,
        inventory_digest="2" * 64,
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

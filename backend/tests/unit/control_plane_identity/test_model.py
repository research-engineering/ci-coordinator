from __future__ import annotations

from dataclasses import fields
from datetime import UTC, datetime, timedelta, timezone

import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import (
    BackChannelLogoutEvidence,
    BackChannelLogoutTarget,
    BreakGlassPrincipal,
    ControlPlaneRole,
    ControlPlaneSessionRecord,
    DisplayMetadata,
    GitHubReviewerPrincipal,
    IdentityRejected,
    KeycloakHumanPrincipal,
    KeycloakWorkloadPrincipal,
    ReviewerStepUpBinding,
    RoleAdmissionGranted,
    admit_control_plane_roles,
    derive_human_actor_id,
    derive_workload_actor_id,
    is_administrator_actor_id,
    is_break_glass_actor_id,
)

_ISSUER = "https://auth.example.test/realms/coordinator"
_PROFILE = "a" * 64
_NOW = datetime(2026, 9, 2, 12, tzinfo=UTC)
_HANDLE = "A" * 43


def test_actor_coordinates_are_stable_and_domain_separated() -> None:
    human = derive_human_actor_id(_ISSUER, "subject")
    workload = derive_workload_actor_id(_ISSUER, "client", "subject")

    assert human == derive_human_actor_id(_ISSUER, "subject")
    assert human.startswith("keycloak-human:v1:")
    assert workload.startswith("keycloak-workload:v1:")
    assert human.removeprefix("keycloak-human:v1:") != workload.removeprefix(
        "keycloak-workload:v1:"
    )
    assert derive_workload_actor_id(
        _ISSUER,
        "client-a",
        "subject",
    ) != derive_workload_actor_id(_ISSUER, "client-b", "subject")
    assert is_administrator_actor_id(human)
    assert is_administrator_actor_id(workload)
    assert not is_administrator_actor_id("break-glass:v1:dev1")
    assert is_break_glass_actor_id("break-glass:v1:dev1")
    assert not is_break_glass_actor_id(human)


def test_workload_principal_normalizes_aware_instants_to_utc() -> None:
    offset = timezone(timedelta(hours=2))
    principal = KeycloakWorkloadPrincipal(
        issuer=_ISSUER,
        authorized_party="workflow-bot",
        subject="service-account-workflow-bot",
        roles=frozenset({"read"}),
        issued_at=datetime(2026, 9, 2, 14, tzinfo=offset),
        expires_at=datetime(2026, 9, 2, 14, 5, tzinfo=offset),
        authority_profile_digest=_PROFILE,
    )

    assert principal.issued_at == _NOW
    assert principal.issued_at.tzinfo is UTC
    assert principal.expires_at == _NOW + timedelta(minutes=5)
    assert principal.expires_at.tzinfo is UTC


@pytest.mark.parametrize(
    "required_roles",
    [
        frozenset(),
        frozenset({"read"}),
        frozenset({"read", "configure"}),
    ],
)
def test_role_admission_is_exact_and_monotonic(
    required_roles: frozenset[ControlPlaneRole],
) -> None:
    principal = _human(roles=frozenset({"read", "configure"}))

    result = admit_control_plane_roles(
        principal,
        required_roles,
        at=_NOW,
    )

    assert isinstance(result, RoleAdmissionGranted)
    assert result.effective_roles == required_roles


def test_role_admission_rejects_missing_expired_and_wrong_plane_authority() -> None:
    missing = admit_control_plane_roles(
        _human(roles=frozenset({"read"})),
        frozenset({"read", "activate"}),
        at=_NOW,
    )
    expired = admit_control_plane_roles(
        _human(expires_at=_NOW),
        frozenset({"read"}),
        at=_NOW,
    )
    break_glass = admit_control_plane_roles(
        BreakGlassPrincipal("dev1"),
        frozenset(),
        at=_NOW,
    )

    assert missing == IdentityRejected("forbidden", ("activate",))
    assert expired == IdentityRejected("unauthenticated")
    assert break_glass == IdentityRejected("forbidden")


def test_session_record_is_token_free_and_actor_bound() -> None:
    record = _session()

    names = {item.name for item in fields(record)}

    assert not names & {
        "access_token",
        "client_secret",
        "encrypted_access_token",
        "id_token",
        "refresh_token",
    }
    assert record.actor_id == derive_human_actor_id(_ISSUER, "subject")
    assert _HANDLE not in repr(record)

    with pytest.raises(ValueError, match="actor does not match"):
        _session(actor_id=derive_human_actor_id(_ISSUER, "other"))


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("user_id", True),
        ("user_id", 0),
        ("login", "bad_login"),
        ("permission", "write"),
    ],
)
def test_reviewer_principal_rejects_type_and_authority_confusion(
    field_name: str,
    value: object,
) -> None:
    arguments: dict[str, object] = {
        "user_id": 17,
        "login": "maintainer",
        "permission": "maintain",
        "observed_at": _NOW,
        "expires_at": _NOW + timedelta(minutes=5),
    }
    arguments[field_name] = value

    with pytest.raises((TypeError, ValueError)):
        GitHubReviewerPrincipal(**arguments)  # type: ignore[arg-type]


def test_reviewer_step_up_binds_exact_session_scope_revision_and_proposal() -> None:
    binding = ReviewerStepUpBinding(
        session_handle_digest=b"s" * 32,
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

    assert binding.scope == RepositoryScope(7, 11)

    with pytest.raises(ValueError, match="human Keycloak actor"):
        ReviewerStepUpBinding(
            session_handle_digest=b"s" * 32,
            initiating_actor="github-reviewer:v1:17",
            scope=binding.scope,
            operation_id=binding.operation_id,
            proposal_manifest_id=binding.proposal_manifest_id,
            revision=binding.revision,
            proposal_digest=binding.proposal_digest,
            expected_active_epoch_id=binding.expected_active_epoch_id,
            expected_active_revision=binding.expected_active_revision,
            authority_profile_digest=binding.authority_profile_digest,
            issued_at=binding.issued_at,
            expires_at=binding.expires_at,
        )


def test_back_channel_logout_requires_at_least_one_target_and_bounded_lifetime() -> None:
    evidence = BackChannelLogoutEvidence(
        issuer=_ISSUER,
        token_id="logout-17",
        issued_at=_NOW,
        expires_at=_NOW + timedelta(seconds=120),
        target=BackChannelLogoutTarget(keycloak_session_id="sid-17"),
    )

    assert evidence.target.subject is None

    assert BackChannelLogoutTarget(
        keycloak_session_id="sid",
        subject="subject",
    ) == BackChannelLogoutTarget(keycloak_session_id="sid", subject="subject")
    with pytest.raises(ValueError, match="sid, subject, or both"):
        BackChannelLogoutTarget()
    with pytest.raises(ValueError, match="lifetime maximum"):
        BackChannelLogoutEvidence(
            issuer=_ISSUER,
            token_id="logout-17",
            issued_at=_NOW,
            expires_at=_NOW + timedelta(seconds=121),
            target=BackChannelLogoutTarget(subject="subject"),
        )


def _human(
    *,
    roles: frozenset[ControlPlaneRole] = frozenset({"read"}),
    expires_at: datetime = _NOW + timedelta(minutes=15),
) -> KeycloakHumanPrincipal:
    return KeycloakHumanPrincipal(
        issuer=_ISSUER,
        subject="subject",
        keycloak_session_id="sid-17",
        roles=roles,
        session_handle=_HANDLE,
        expires_at=expires_at,
        authority_profile_digest=_PROFILE,
        display=DisplayMetadata("maintainer", "Maintainer"),
    )


def _session(*, actor_id: str | None = None) -> ControlPlaneSessionRecord:
    return ControlPlaneSessionRecord(
        handle_digest=b"h" * 32,
        issuer=_ISSUER,
        subject="subject",
        keycloak_session_id="sid-17",
        actor_id=actor_id or derive_human_actor_id(_ISSUER, "subject"),
        roles=frozenset({"read"}),
        authority_profile_digest=_PROFILE,
        issued_at=_NOW,
        expires_at=_NOW + timedelta(minutes=15),
        display=DisplayMetadata("maintainer", "Maintainer"),
    )

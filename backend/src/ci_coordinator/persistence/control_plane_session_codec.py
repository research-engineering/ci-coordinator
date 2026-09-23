from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from ci_coordinator.control_plane_identity.model import (
    ControlPlaneRole,
    ControlPlaneSessionRecord,
    DisplayMetadata,
)

_ROLE_BITS: tuple[tuple[ControlPlaneRole, int], ...] = (
    ("activate", 1),
    ("audit", 2),
    ("configure", 4),
    ("override", 8),
    ("read", 16),
)


def record_values(record: ControlPlaneSessionRecord) -> dict[str, object]:
    return {
        "handle_digest": record.handle_digest,
        "issuer": record.issuer,
        "subject": record.subject,
        "keycloak_sid": record.keycloak_session_id,
        "actor_id": record.actor_id,
        "roles": _roles_to_bits(record.roles),
        "preferred_username": record.display.preferred_username,
        "display_name": record.display.display_name,
        "profile_digest": record.authority_profile_digest,
        "issued_at": record.issued_at,
        "expires_at": record.expires_at,
    }


def record_from_row(row: Mapping[str, object]) -> ControlPlaneSessionRecord:
    handle_digest = row["handle_digest"]
    issuer = row["issuer"]
    subject = row["subject"]
    keycloak_sid = row["keycloak_sid"]
    actor_id = row["actor_id"]
    roles = row["roles"]
    preferred_username = row["preferred_username"]
    display_name = row["display_name"]
    profile_digest = row["profile_digest"]
    issued_at = row["issued_at"]
    expires_at = row["expires_at"]
    if (
        type(handle_digest) is not bytes
        or type(issuer) is not str
        or type(subject) is not str
        or type(keycloak_sid) is not str
        or type(actor_id) is not str
        or type(roles) is not int
        or (preferred_username is not None and type(preferred_username) is not str)
        or (display_name is not None and type(display_name) is not str)
        or type(profile_digest) is not str
        or type(issued_at) is not datetime
        or type(expires_at) is not datetime
    ):
        raise TypeError("stored control-plane session row has invalid primitive types")
    return ControlPlaneSessionRecord(
        handle_digest=handle_digest,
        issuer=issuer,
        subject=subject,
        keycloak_session_id=keycloak_sid,
        actor_id=actor_id,
        roles=_roles_from_bits(roles),
        authority_profile_digest=profile_digest,
        issued_at=issued_at,
        expires_at=expires_at,
        display=DisplayMetadata(
            preferred_username=preferred_username,
            display_name=display_name,
        ),
    )


def _roles_to_bits(roles: frozenset[ControlPlaneRole]) -> int:
    return sum(bit for role, bit in _ROLE_BITS if role in roles)


def _roles_from_bits(value: int) -> frozenset[ControlPlaneRole]:
    if not 0 <= value <= 31:
        raise ValueError("stored control-plane role bits are invalid")
    return frozenset(role for role, bit in _ROLE_BITS if value & bit)

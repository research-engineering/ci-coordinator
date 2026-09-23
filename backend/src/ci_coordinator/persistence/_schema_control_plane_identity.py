from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    Index,
    LargeBinary,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Table,
)

from ci_coordinator.persistence._schema_core import APPLICATION_SCHEMA, metadata

control_plane_sessions = Table(
    "control_plane_sessions",
    metadata,
    Column("handle_digest", LargeBinary(32), nullable=False),
    Column("issuer", String(512), nullable=False),
    Column("subject", String(512), nullable=False),
    Column("keycloak_sid", String(512), nullable=False),
    Column("actor_id", String(82), nullable=False),
    Column("roles", SmallInteger, nullable=False),
    Column("preferred_username", String(256)),
    Column("display_name", String(512)),
    Column("profile_digest", String(64), nullable=False),
    Column("issued_at", DateTime(timezone=True), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "octet_length(handle_digest) = 32",
        name="ck_control_plane_sessions_handle_digest",
    ),
    CheckConstraint(
        "octet_length(issuer) BETWEEN 1 AND 512",
        name="ck_control_plane_sessions_issuer",
    ),
    CheckConstraint(
        "octet_length(subject) BETWEEN 1 AND 512",
        name="ck_control_plane_sessions_subject",
    ),
    CheckConstraint(
        "octet_length(keycloak_sid) BETWEEN 1 AND 512",
        name="ck_control_plane_sessions_keycloak_sid",
    ),
    CheckConstraint(
        "actor_id ~ '^keycloak-human:v1:[0-9a-f]{64}$'",
        name="ck_control_plane_sessions_actor_id",
    ),
    CheckConstraint(
        "roles BETWEEN 0 AND 31",
        name="ck_control_plane_sessions_roles",
    ),
    CheckConstraint(
        "preferred_username IS NULL OR octet_length(preferred_username) BETWEEN 1 AND 256",
        name="ck_control_plane_sessions_preferred_username",
    ),
    CheckConstraint(
        "display_name IS NULL OR octet_length(display_name) BETWEEN 1 AND 512",
        name="ck_control_plane_sessions_display_name",
    ),
    CheckConstraint(
        "profile_digest ~ '^[0-9a-f]{64}$'",
        name="ck_control_plane_sessions_profile_digest",
    ),
    CheckConstraint(
        "isfinite(issued_at) AND isfinite(expires_at) "
        "AND expires_at > issued_at "
        "AND expires_at <= issued_at + INTERVAL '900 seconds'",
        name="ck_control_plane_sessions_time_order",
    ),
    PrimaryKeyConstraint("handle_digest", name="pk_control_plane_sessions"),
    schema=APPLICATION_SCHEMA,
)

Index(
    "ix_control_plane_sessions_expires_at",
    control_plane_sessions.c.expires_at,
    control_plane_sessions.c.handle_digest,
)
Index(
    "ix_control_plane_sessions_identity_issued",
    control_plane_sessions.c.issuer,
    control_plane_sessions.c.subject,
    control_plane_sessions.c.issued_at.desc(),
    control_plane_sessions.c.handle_digest.desc(),
)
Index(
    "ix_control_plane_sessions_issuer_sid",
    control_plane_sessions.c.issuer,
    control_plane_sessions.c.keycloak_sid,
)

control_plane_logout_replays = Table(
    "control_plane_logout_replays",
    metadata,
    Column("issuer", String(512), nullable=False),
    Column("jti", String(512), nullable=False),
    Column("retain_until", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "octet_length(issuer) BETWEEN 1 AND 512",
        name="ck_control_plane_logout_replays_issuer",
    ),
    CheckConstraint(
        "octet_length(jti) BETWEEN 1 AND 512",
        name="ck_control_plane_logout_replays_jti",
    ),
    CheckConstraint(
        "isfinite(retain_until)",
        name="ck_control_plane_logout_replays_retain_until",
    ),
    PrimaryKeyConstraint(
        "issuer",
        "jti",
        name="pk_control_plane_logout_replays",
    ),
    schema=APPLICATION_SCHEMA,
)

Index(
    "ix_control_plane_logout_replays_retain_until",
    control_plane_logout_replays.c.retain_until,
    control_plane_logout_replays.c.issuer,
    control_plane_logout_replays.c.jti,
)

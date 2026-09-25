"""PostgreSQL lifecycle for token-free control-plane identity state."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, func, insert, literal, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql.dml import ReturningInsert

from ci_coordinator.control_plane_identity import (
    BackChannelLogoutApplied,
    BackChannelLogoutEvidence,
    BackChannelLogoutReplay,
    BackChannelLogoutStoreUnavailable,
    ControlPlaneSessionRecord,
    ControlPlaneSessionStoreRejected,
    ControlPlaneSessionStoreUnavailable,
)
from ci_coordinator.control_plane_identity.activity import SessionEndReason
from ci_coordinator.persistence.activity_capability import ADMINISTRATOR_ACTIVITY
from ci_coordinator.persistence.activity_write import (
    cleanup_activity,
    delete_sessions_with_activity,
    lock_activity,
    record_session_login,
)
from ci_coordinator.persistence.compatibility_admission import admit_schema_dependent_operation
from ci_coordinator.persistence.compatibility_contracts import CapabilityDeclaration
from ci_coordinator.persistence.compatibility_fence import (
    CompatibilityFenceMode,
    acquire_compatibility_fence,
)
from ci_coordinator.persistence.compatibility_profile import (
    CompatibilityProfile,
    load_bundled_profile,
)
from ci_coordinator.persistence.connection import configure_read_committed, verify_read_committed
from ci_coordinator.persistence.control_plane_session_codec import record_from_row, record_values
from ci_coordinator.persistence.errors import PersistenceError
from ci_coordinator.persistence.schema import (
    control_plane_logout_replays,
    control_plane_sessions,
)
from ci_coordinator.persistence.schema_capabilities import (
    control_plane_identity_state_requirements,
)

_MAX_SESSIONS_PER_IDENTITY = 8
_EXPIRED_CLEANUP_LIMIT = 128


class PostgresControlPlaneSessionStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._profile = load_bundled_profile()
        self._required_capabilities = control_plane_identity_state_requirements(self._profile)

    async def current_time(self) -> datetime:
        try:
            async with _identity_transaction(
                self._engine,
                self._profile,
                self._required_capabilities,
            ) as connection:
                return await _database_time(connection)
        except asyncio.CancelledError:
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            raise ControlPlaneSessionStoreUnavailable(
                "control-plane session clock is unavailable"
            ) from error

    async def replace(
        self,
        *,
        previous_handle_digest: bytes | None,
        record: ControlPlaneSessionRecord,
    ) -> None:
        if type(record) is not ControlPlaneSessionRecord:
            raise TypeError("control-plane session record must be exact")
        if previous_handle_digest is not None:
            _require_digest(previous_handle_digest, "previous control-plane session digest")
        try:
            async with _identity_transaction(
                self._engine,
                self._profile,
                self._required_capabilities,
            ) as connection:
                await lock_activity(connection)
                await connection.execute(
                    text("SELECT pg_catalog.pg_advisory_xact_lock(:identity_lock)"),
                    {"identity_lock": _identity_lock_key(record.actor_id)},
                )
                database_now = await _database_time(connection)
                if not record.issued_at <= database_now < record.expires_at:
                    raise ControlPlaneSessionStoreRejected(
                        "control-plane session is outside the database clock window"
                    )
                await _cleanup_expired_sessions(connection)
                if previous_handle_digest is not None:
                    await delete_sessions_with_activity(
                        connection,
                        delete(control_plane_sessions).where(
                            control_plane_sessions.c.handle_digest == previous_handle_digest
                        ),
                        "replaced",
                    )
                overflow = (
                    select(control_plane_sessions.c.handle_digest)
                    .where(
                        control_plane_sessions.c.issuer == record.issuer,
                        control_plane_sessions.c.subject == record.subject,
                    )
                    .order_by(
                        control_plane_sessions.c.issued_at.desc(),
                        control_plane_sessions.c.handle_digest.desc(),
                    )
                    .offset(_MAX_SESSIONS_PER_IDENTITY - 1)
                )
                await delete_sessions_with_activity(
                    connection,
                    delete(control_plane_sessions).where(
                        control_plane_sessions.c.handle_digest.in_(overflow)
                    ),
                    "replaced",
                )
                inserted = await connection.execute(_guarded_session_insert(record))
                if inserted.scalar_one_or_none() is None:
                    raise ControlPlaneSessionStoreRejected(
                        "control-plane session expired before insertion"
                    )
                await record_session_login(connection, record)
                await cleanup_activity(connection)
        except asyncio.CancelledError:
            raise
        except ControlPlaneSessionStoreRejected:
            raise
        except IntegrityError as error:
            raise ControlPlaneSessionStoreRejected(
                "control-plane session conflicts with retained state"
            ) from error
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            raise ControlPlaneSessionStoreUnavailable(
                "control-plane session replacement failed"
            ) from error

    async def load(self, handle_digest: bytes) -> ControlPlaneSessionRecord | None:
        _require_digest(handle_digest, "control-plane session digest")
        try:
            async with _identity_transaction(
                self._engine,
                self._profile,
                self._required_capabilities,
            ) as connection:
                result = await connection.execute(
                    select(
                        control_plane_sessions,
                        (control_plane_sessions.c.expires_at > func.statement_timestamp()).label(
                            "is_current"
                        ),
                    ).where(control_plane_sessions.c.handle_digest == handle_digest)
                )
                row = result.mappings().one_or_none()
                if row is None:
                    return None
                row_values = dict(row)
                if row_values.pop("is_current") is not True:
                    await lock_activity(connection)
                    await delete_sessions_with_activity(
                        connection,
                        delete(control_plane_sessions).where(
                            control_plane_sessions.c.handle_digest == handle_digest,
                            control_plane_sessions.c.expires_at <= func.statement_timestamp(),
                        ),
                        "expired",
                    )
                    return None
                try:
                    return record_from_row(row_values)
                except (TypeError, ValueError):
                    await connection.execute(
                        delete(control_plane_sessions).where(
                            control_plane_sessions.c.handle_digest == handle_digest
                        )
                    )
                    return None
        except asyncio.CancelledError:
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            raise ControlPlaneSessionStoreUnavailable(
                "control-plane session lookup failed"
            ) from error

    async def delete(self, handle_digest: bytes, *, reason: SessionEndReason = "logout") -> None:
        _require_digest(handle_digest, "control-plane session digest")
        if reason not in ("logout", "expired", "revoked", "replaced"):
            raise ValueError("invalid session end reason")
        try:
            async with _identity_transaction(
                self._engine,
                self._profile,
                self._required_capabilities,
            ) as connection:
                await lock_activity(connection)
                await delete_sessions_with_activity(
                    connection,
                    delete(control_plane_sessions).where(
                        control_plane_sessions.c.handle_digest == handle_digest
                    ),
                    reason,
                )
        except asyncio.CancelledError:
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            raise ControlPlaneSessionStoreUnavailable(
                "control-plane session deletion failed"
            ) from error


class PostgresBackChannelLogoutStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._profile = load_bundled_profile()
        self._required_capabilities = control_plane_identity_state_requirements(self._profile)

    async def consume_and_delete(
        self,
        *,
        evidence: BackChannelLogoutEvidence,
        replay_retained_until: datetime,
    ) -> BackChannelLogoutApplied | BackChannelLogoutReplay:
        if type(evidence) is not BackChannelLogoutEvidence:
            raise TypeError("back-channel logout evidence must be exact")
        retained_until = _aware_utc(replay_retained_until, "logout replay retention")
        if retained_until < evidence.expires_at:
            raise ValueError("logout replay retention cannot precede token expiry")
        try:
            async with _identity_transaction(
                self._engine,
                self._profile,
                self._required_capabilities,
            ) as connection:
                await lock_activity(connection)
                await _cleanup_expired_replays(connection)
                await connection.execute(
                    delete(control_plane_logout_replays).where(
                        control_plane_logout_replays.c.issuer == evidence.issuer,
                        control_plane_logout_replays.c.jti == evidence.token_id,
                        control_plane_logout_replays.c.retain_until <= func.statement_timestamp(),
                    )
                )
                inserted = await connection.execute(
                    _guarded_replay_insert(evidence, retained_until)
                )
                if inserted.scalar_one_or_none() is None:
                    return BackChannelLogoutReplay()
                deleted_count = await _delete_target_sessions(connection, evidence)
                return BackChannelLogoutApplied(deleted_count)
        except asyncio.CancelledError:
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            raise BackChannelLogoutStoreUnavailable(
                "back-channel logout persistence failed"
            ) from error


@asynccontextmanager
async def _identity_transaction(
    engine: AsyncEngine,
    profile: CompatibilityProfile,
    required_capabilities: tuple[CapabilityDeclaration, ...],
) -> AsyncIterator[AsyncConnection]:
    async with engine.connect() as raw_connection:
        connection = await configure_read_committed(raw_connection, profile)
        async with connection.begin():
            await verify_read_committed(connection, profile)
            await acquire_compatibility_fence(
                connection,
                profile,
                CompatibilityFenceMode.PARTICIPANT,
            )
            await admit_schema_dependent_operation(
                connection,
                profile,
                tuple(sorted({*required_capabilities, ADMINISTRATOR_ACTIVITY.declaration()})),
            )
            yield connection


async def _database_time(connection: AsyncConnection) -> datetime:
    value = await connection.scalar(select(func.statement_timestamp()))
    return _aware_utc(value, "database identity clock")


async def _cleanup_expired_sessions(connection: AsyncConnection) -> None:
    expired = (
        select(control_plane_sessions.c.handle_digest)
        .where(control_plane_sessions.c.expires_at <= func.statement_timestamp())
        .order_by(
            control_plane_sessions.c.expires_at,
            control_plane_sessions.c.handle_digest,
        )
        .limit(_EXPIRED_CLEANUP_LIMIT)
    )
    await delete_sessions_with_activity(
        connection,
        delete(control_plane_sessions).where(control_plane_sessions.c.handle_digest.in_(expired)),
        "expired",
    )


async def _cleanup_expired_replays(connection: AsyncConnection) -> None:
    expired = (
        select(
            control_plane_logout_replays.c.issuer,
            control_plane_logout_replays.c.jti,
        )
        .where(control_plane_logout_replays.c.retain_until <= func.statement_timestamp())
        .order_by(
            control_plane_logout_replays.c.retain_until,
            control_plane_logout_replays.c.issuer,
            control_plane_logout_replays.c.jti,
        )
        .limit(_EXPIRED_CLEANUP_LIMIT)
    )
    await connection.execute(
        delete(control_plane_logout_replays).where(
            tuple_(
                control_plane_logout_replays.c.issuer,
                control_plane_logout_replays.c.jti,
            ).in_(expired)
        )
    )


def _guarded_session_insert(record: ControlPlaneSessionRecord) -> ReturningInsert[Any]:
    values = record_values(record)
    columns = tuple(values)
    source = select(
        *(
            literal(values[name], type_=control_plane_sessions.c[name].type).label(name)
            for name in columns
        )
    ).where(
        func.statement_timestamp() >= record.issued_at,
        func.statement_timestamp() < record.expires_at,
    )
    return (
        insert(control_plane_sessions)
        .from_select(columns, source)
        .returning(control_plane_sessions.c.handle_digest)
    )


def _guarded_replay_insert(
    evidence: BackChannelLogoutEvidence,
    retained_until: datetime,
) -> ReturningInsert[Any]:
    values = {
        "issuer": evidence.issuer,
        "jti": evidence.token_id,
        "retain_until": retained_until,
    }
    columns = tuple(values)
    source = select(
        *(
            literal(values[name], type_=control_plane_logout_replays.c[name].type).label(name)
            for name in columns
        )
    ).where(func.statement_timestamp() < retained_until)
    return (
        postgresql_insert(control_plane_logout_replays)
        .from_select(columns, source)
        .on_conflict_do_nothing(
            index_elements=(
                control_plane_logout_replays.c.issuer,
                control_plane_logout_replays.c.jti,
            )
        )
        .returning(literal(True).label("inserted"))
    )


async def _delete_target_sessions(
    connection: AsyncConnection,
    evidence: BackChannelLogoutEvidence,
) -> int:
    predicates = [control_plane_sessions.c.issuer == evidence.issuer]
    if evidence.target.keycloak_session_id is not None:
        predicates.append(
            control_plane_sessions.c.keycloak_sid == evidence.target.keycloak_session_id
        )
    if evidence.target.subject is not None:
        predicates.append(control_plane_sessions.c.subject == evidence.target.subject)
    return await delete_sessions_with_activity(
        connection, delete(control_plane_sessions).where(*predicates), "revoked"
    )


def _identity_lock_key(actor_id: str) -> int:
    digest_prefix = bytes.fromhex(actor_id.removeprefix("keycloak-human:v1:")[:16])
    if len(digest_prefix) != 8:
        raise ValueError("control-plane actor identity is invalid")
    return int.from_bytes(digest_prefix, byteorder="big", signed=True)


def _require_digest(value: object, name: str) -> None:
    if type(value) is not bytes or len(value) != 32:
        raise ValueError(f"{name} must be 32 bytes")


def _aware_utc(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)

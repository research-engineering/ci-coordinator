"""PostgreSQL lifecycle for token-free control-plane identity state."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

from sqlalchemy import and_, delete, func, insert, literal, or_, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql.dml import ReturningInsert

from ci_coordinator.control_plane_identity import (
    LOGOUT_FENCE_RETENTION_SECONDS,
    BackChannelLogoutApplied,
    BackChannelLogoutEvidence,
    BackChannelLogoutReplay,
    BackChannelLogoutStoreUnavailable,
    ControlPlaneSessionRecord,
    ControlPlaneSessionStoreRejected,
    ControlPlaneSessionStoreUnavailable,
    LoginStartAnchor,
)
from ci_coordinator.control_plane_identity.activity import SessionDeletionReason
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
    control_plane_logout_fence_generation,
    control_plane_logout_fences,
    control_plane_logout_replays,
    control_plane_sessions,
)
from ci_coordinator.persistence.schema_capabilities import (
    control_plane_identity_state_requirements,
    control_plane_session_lifecycle_requirements,
)

_MAX_SESSIONS_PER_IDENTITY = 8
_EXPIRED_CLEANUP_LIMIT = 128
_LOGOUT_FENCE_RETENTION: Final = timedelta(seconds=LOGOUT_FENCE_RETENTION_SECONDS)
_LOGOUT_FENCE_RETENTION_INTERVAL: Final = text(
    f"INTERVAL '{LOGOUT_FENCE_RETENTION_SECONDS} seconds'"
)
# Fence writers hold this lock exclusively and advance the transactional generation under it;
# login-start reads and session insertion share it. A login start therefore reads a generation
# that every earlier fence write has committed and every later one exceeds.
_EXCLUSIVE_LOGOUT_FENCE_LOCK: Final = text(
    "SELECT pg_catalog.pg_advisory_xact_lock("
    "pg_catalog.hashtextextended('control-plane-logout-fence/v1', 0))"
)
_SHARED_LOGOUT_FENCE_LOCK: Final = text(
    "SELECT pg_catalog.pg_advisory_xact_lock_shared("
    "pg_catalog.hashtextextended('control-plane-logout-fence/v1', 0))"
)

type LogoutFenceTarget = Literal["subject", "sid"]


class PostgresControlPlaneSessionStore:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._profile = load_bundled_profile()
        self._required_capabilities = control_plane_identity_state_requirements(self._profile)
        # Reads of existing sessions do not depend on the fence; login and logout do.
        self._lifecycle_capabilities = control_plane_session_lifecycle_requirements(self._profile)

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

    async def login_start_anchor(self) -> LoginStartAnchor:
        try:
            async with _identity_transaction(
                self._engine,
                self._profile,
                self._lifecycle_capabilities,
            ) as connection:
                await _lock_logout_fence(connection, exclusive=False)
                row = (
                    await connection.execute(
                        select(
                            control_plane_logout_fence_generation.c.current_generation,
                            func.statement_timestamp().label("started_at"),
                        )
                    )
                ).one()
                return LoginStartAnchor(
                    fence_generation=row.current_generation,
                    started_at=row.started_at,
                )
        except asyncio.CancelledError:
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            raise ControlPlaneSessionStoreUnavailable(
                "control-plane login start anchor is unavailable"
            ) from error

    async def replace(
        self,
        *,
        previous_handle_digest: bytes | None,
        record: ControlPlaneSessionRecord,
        login_start: LoginStartAnchor,
    ) -> None:
        if type(record) is not ControlPlaneSessionRecord:
            raise TypeError("control-plane session record must be exact")
        if type(login_start) is not LoginStartAnchor:
            raise TypeError("control-plane login start anchor must be exact")
        if previous_handle_digest is not None:
            _require_digest(previous_handle_digest, "previous control-plane session digest")
        started_at = login_start.started_at
        try:
            async with _identity_transaction(
                self._engine,
                self._profile,
                self._lifecycle_capabilities,
            ) as connection:
                await _lock_logout_fence(connection, exclusive=False)
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
                # Liveness bound only; the generation comparison below carries the safety.
                if not started_at <= database_now < started_at + _LOGOUT_FENCE_RETENTION:
                    raise ControlPlaneSessionStoreRejected(
                        "control-plane login start is outside the login age bound"
                    )
                if await _logout_fence_covers(connection, record, login_start.fence_generation):
                    raise ControlPlaneSessionStoreRejected(
                        "control-plane login evidence predates a logout"
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

    async def logout(self, handle_digest: bytes, *, issuer: str, subject: str) -> None:
        _require_digest(handle_digest, "control-plane session digest")
        if type(issuer) is not str or type(subject) is not str:
            raise TypeError("logout fence identity must be exact text")
        try:
            async with _identity_transaction(
                self._engine,
                self._profile,
                self._lifecycle_capabilities,
            ) as connection:
                await _lock_logout_fence(connection, exclusive=True)
                await lock_activity(connection)
                await delete_sessions_with_activity(
                    connection,
                    delete(control_plane_sessions).where(
                        control_plane_sessions.c.handle_digest == handle_digest
                    ),
                    "logout",
                )
                # A concurrent replacement may already have deleted the authenticated handle;
                # the caller's authenticated identity still fences later insertions.
                await _advance_logout_fence(
                    connection,
                    issuer=issuer,
                    target_kind="subject",
                    target_value=subject,
                )
        except asyncio.CancelledError:
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            raise ControlPlaneSessionStoreUnavailable(
                "control-plane session logout failed"
            ) from error

    async def delete(self, handle_digest: bytes, *, reason: SessionDeletionReason) -> None:
        _require_digest(handle_digest, "control-plane session digest")
        if reason not in ("expired", "revoked", "replaced"):
            raise ValueError("invalid session deletion reason")
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
        self._required_capabilities = control_plane_session_lifecycle_requirements(self._profile)

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
                await _lock_logout_fence(connection, exclusive=True)
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
                target_kind, target_value = _back_channel_fence_target(evidence)
                await _advance_logout_fence(
                    connection,
                    issuer=evidence.issuer,
                    target_kind=target_kind,
                    target_value=target_value,
                )
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


async def _lock_logout_fence(connection: AsyncConnection, *, exclusive: bool) -> None:
    await connection.execute(
        _EXCLUSIVE_LOGOUT_FENCE_LOCK if exclusive else _SHARED_LOGOUT_FENCE_LOCK
    )


async def _logout_fence_covers(
    connection: AsyncConnection,
    record: ControlPlaneSessionRecord,
    login_fence_generation: int,
) -> bool:
    # Cleanup erased fences up to the floor; evidence older than it cannot be proven unfenced.
    floor = (
        await connection.execute(
            select(control_plane_logout_fence_generation.c.deleted_generation_floor)
        )
    ).scalar_one()
    if floor > login_fence_generation:
        return True
    fences = control_plane_logout_fences
    covering = await connection.scalar(
        select(fences.c.issuer)
        .where(
            fences.c.issuer == record.issuer,
            fences.c.generation > login_fence_generation,
            or_(
                and_(fences.c.target_kind == "subject", fences.c.target_value == record.subject),
                and_(
                    fences.c.target_kind == "sid",
                    fences.c.target_value == record.keycloak_session_id,
                ),
            ),
        )
        .limit(1)
    )
    return covering is not None


async def _advance_logout_fence(
    connection: AsyncConnection,
    *,
    issuer: str,
    target_kind: LogoutFenceTarget,
    target_value: str,
) -> None:
    fences = control_plane_logout_fences
    counter = control_plane_logout_fence_generation
    expired = (
        select(fences.c.issuer, fences.c.target_kind, fences.c.target_value)
        .where(fences.c.fenced_at <= func.statement_timestamp() - _LOGOUT_FENCE_RETENTION_INTERVAL)
        .order_by(fences.c.fenced_at, fences.c.issuer, fences.c.target_kind, fences.c.target_value)
        .limit(_EXPIRED_CLEANUP_LIMIT)
    )
    erased = await connection.execute(
        delete(fences)
        .where(tuple_(fences.c.issuer, fences.c.target_kind, fences.c.target_value).in_(expired))
        .returning(fences.c.generation)
    )
    erased_floor = max(erased.scalars().all(), default=0)
    # Wall time selects what to erase; the floor keeps erased fences effective for older logins.
    generation = (
        await connection.execute(
            update(counter)
            .values(
                current_generation=counter.c.current_generation + 1,
                deleted_generation_floor=func.greatest(
                    counter.c.deleted_generation_floor,
                    literal(erased_floor, type_=counter.c.deleted_generation_floor.type),
                ),
            )
            .returning(counter.c.current_generation)
        )
    ).scalar_one()
    statement = postgresql_insert(fences).values(
        issuer=issuer,
        target_kind=target_kind,
        target_value=target_value,
        generation=generation,
        fenced_at=func.statement_timestamp(),
    )
    await connection.execute(
        statement.on_conflict_do_update(
            index_elements=(fences.c.issuer, fences.c.target_kind, fences.c.target_value),
            set_={
                "generation": statement.excluded.generation,
                "fenced_at": statement.excluded.fenced_at,
            },
        )
    )


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


def _back_channel_fence_target(
    evidence: BackChannelLogoutEvidence,
) -> tuple[LogoutFenceTarget, str]:
    # A sid names one Keycloak session, so a sid-bearing token fences no other sessions.
    if evidence.target.keycloak_session_id is not None:
        return "sid", evidence.target.keycloak_session_id
    if evidence.target.subject is not None:
        return "subject", evidence.target.subject
    raise ValueError("back-channel logout target is empty")


def _require_digest(value: object, name: str) -> None:
    if type(value) is not bytes or len(value) != 32:
        raise ValueError(f"{name} must be 32 bytes")


def _aware_utc(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)

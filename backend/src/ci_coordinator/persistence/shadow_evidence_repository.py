"""PostgreSQL immutable terminal-shadow-evidence repository."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.canonical_row import CanonicalRowCodecError
from ci_coordinator.persistence.errors import PersistenceInvariantViolation, StoreUnavailable
from ci_coordinator.persistence.schema import shadow_evidence
from ci_coordinator.persistence.shadow_evidence_codec import (
    decode_shadow_evidence_row,
    encode_shadow_evidence_row,
    validate_shadow_profile_id,
)
from ci_coordinator.persistence.shadow_reconciliation_state_profile import (
    ShadowReconciliationStateProfile,
)
from ci_coordinator.shadow_mode import (
    ShadowEvidenceConflict,
    ShadowEvidenceDuplicate,
    ShadowEvidenceRecord,
    ShadowEvidenceStored,
    ShadowEvidenceWrite,
)


class _PostgresShadowEvidenceRepository:
    def __init__(
        self,
        connection: AsyncConnection,
        profile: ShadowReconciliationStateProfile,
        ensure_active: Callable[[], None],
        mark_rollback_required: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._profile = profile
        self._ensure_active = ensure_active
        self._mark_rollback_required = mark_rollback_required

    async def record(self, record: ShadowEvidenceRecord) -> ShadowEvidenceWrite:
        self._ensure_active()
        try:
            attempted = encode_shadow_evidence_row(record, self._profile)
            inserted = await self._connection.scalar(
                postgres_insert(shadow_evidence)
                .values(attempted)
                .on_conflict_do_nothing(
                    index_elements=[
                        shadow_evidence.c.profile_id,
                        shadow_evidence.c.repository,
                        shadow_evidence.c.event,
                        shadow_evidence.c.surface,
                    ]
                )
                .returning(shadow_evidence.c.profile_id)
            )
            if inserted == attempted["profile_id"]:
                return ShadowEvidenceStored(record)
            existing = await self._load_exact(attempted)
            if existing is None:
                raise PersistenceInvariantViolation("shadow evidence conflict row is unavailable")
            if existing.has_same_semantics_as(record):
                return ShadowEvidenceDuplicate(existing)
            return ShadowEvidenceConflict(existing)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except CanonicalRowCodecError as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("shadow evidence row is invalid") from error
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("shadow evidence persistence is unavailable") from error

    async def list_records(self, profile_id: str) -> tuple[ShadowEvidenceRecord, ...]:
        self._ensure_active()
        try:
            admitted_profile_id = validate_shadow_profile_id(profile_id, self._profile)
            result = await self._connection.execute(
                select(shadow_evidence)
                .where(shadow_evidence.c.profile_id == admitted_profile_id)
                .order_by(
                    shadow_evidence.c.repository,
                    shadow_evidence.c.event,
                    shadow_evidence.c.surface,
                )
            )
            return tuple(
                decode_shadow_evidence_row(dict(row), self._profile) for row in result.mappings()
            )
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except CanonicalRowCodecError as error:
            self._mark_rollback_required()
            raise PersistenceInvariantViolation("shadow evidence row is invalid") from error
        except SQLAlchemyError as error:
            self._mark_rollback_required()
            raise StoreUnavailable("shadow evidence load is unavailable") from error

    async def _load_exact(self, key: dict[str, object]) -> ShadowEvidenceRecord | None:
        result = await self._connection.execute(
            select(shadow_evidence).where(
                shadow_evidence.c.profile_id == key["profile_id"],
                shadow_evidence.c.repository == key["repository"],
                shadow_evidence.c.event == key["event"],
                shadow_evidence.c.surface == key["surface"],
            )
        )
        row = result.mappings().one_or_none()
        return None if row is None else decode_shadow_evidence_row(dict(row), self._profile)

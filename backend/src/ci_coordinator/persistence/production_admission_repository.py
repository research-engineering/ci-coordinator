"""Idempotent durable registration of verified production authorities."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.engine import RowMapping
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.persistence.errors import PersistenceInvariantViolation, StoreUnavailable
from ci_coordinator.persistence.schema import (
    production_admission_authorities,
    production_admission_scope_bindings,
)
from ci_coordinator.production_admission import ProductionAdmissionRegistration


class ProductionAdmissionRegistrationUnavailable(StoreUnavailable):
    """A verified authority could not be durably registered."""


class _PostgresProductionAdmissionRepository:
    def __init__(
        self,
        connection: AsyncConnection,
        ensure_active: Callable[[], None],
        mark_rollback_required: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._ensure_active = ensure_active
        self._mark_rollback_required = mark_rollback_required

    async def register(self, registration: ProductionAdmissionRegistration) -> None:
        self._ensure_active()
        if type(registration) is not ProductionAdmissionRegistration:
            raise TypeError("production admission registration must be exact")
        try:
            now = await self._connection.scalar(select(func.clock_timestamp()))
            if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
                raise PersistenceInvariantViolation(
                    "database clock did not return an aware instant"
                )
            if now >= registration.expires_at:
                raise PersistenceInvariantViolation(
                    "production admission authority already expired"
                )
            await self._connection.execute(
                postgres_insert(production_admission_authorities)
                .values(_authority_row(registration))
                .on_conflict_do_nothing(
                    index_elements=[production_admission_authorities.c.authority_id]
                )
            )
            for binding in registration.scope_bindings:
                await self._connection.execute(
                    postgres_insert(production_admission_scope_bindings)
                    .values(
                        authority_id=registration.authority_id,
                        installation_id=binding.scope.installation_id,
                        repository_id=binding.scope.repository_id,
                        admission_subject_digest=binding.admission_subject_digest,
                        config_epoch_id=binding.config_epoch_id,
                        target_registry_hash=binding.target_registry_hash,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            production_admission_scope_bindings.c.authority_id,
                            production_admission_scope_bindings.c.installation_id,
                            production_admission_scope_bindings.c.repository_id,
                        ]
                    )
                )
            await self._assert_exact_registration(registration)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (PersistenceInvariantViolation, SQLAlchemyError) as error:
            self._mark_rollback_required()
            raise ProductionAdmissionRegistrationUnavailable(
                "production admission registration is unavailable"
            ) from error

    async def _assert_exact_registration(
        self,
        registration: ProductionAdmissionRegistration,
    ) -> None:
        stored_authority = (
            (
                await self._connection.execute(
                    select(production_admission_authorities).where(
                        production_admission_authorities.c.authority_id == registration.authority_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if stored_authority is None or _normalized_authority(stored_authority) != _authority_row(
            registration
        ):
            raise PersistenceInvariantViolation("production admission authority conflicts")
        stored_bindings = tuple(
            (
                row.authority_id,
                row.installation_id,
                row.repository_id,
                row.admission_subject_digest,
                row.config_epoch_id,
                row.target_registry_hash,
            )
            for row in (
                await self._connection.execute(
                    select(production_admission_scope_bindings)
                    .where(
                        production_admission_scope_bindings.c.authority_id
                        == registration.authority_id
                    )
                    .order_by(
                        production_admission_scope_bindings.c.installation_id,
                        production_admission_scope_bindings.c.repository_id,
                    )
                )
            )
        )
        expected_bindings = tuple(
            (
                registration.authority_id,
                binding.scope.installation_id,
                binding.scope.repository_id,
                binding.admission_subject_digest,
                binding.config_epoch_id,
                binding.target_registry_hash,
            )
            for binding in registration.scope_bindings
        )
        if stored_bindings != expected_bindings:
            raise PersistenceInvariantViolation("production admission scope bindings conflict")


def _authority_row(registration: ProductionAdmissionRegistration) -> dict[str, object]:
    return {
        "authority_id": registration.authority_id,
        "key_id": registration.key_id,
        "public_key_spki_der": registration.public_key_spki_der,
        "issued_at": registration.issued_at,
        "expires_at": registration.expires_at,
        "envelope_canonical_json": registration.envelope_canonical_json,
    }


def _normalized_authority(row: RowMapping) -> dict[str, object]:
    mapping = dict(row)
    mapping["public_key_spki_der"] = _database_bytes(mapping["public_key_spki_der"])
    mapping["envelope_canonical_json"] = _database_bytes(mapping["envelope_canonical_json"])
    return mapping


def _database_bytes(value: object) -> bytes:
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value)
    raise PersistenceInvariantViolation("production admission binary column is invalid")

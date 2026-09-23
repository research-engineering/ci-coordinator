from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import insert, text
from sqlalchemy.exc import IntegrityError

from ci_coordinator.persistence import (
    PostgresIngressIssuanceUnitOfWork,
)
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.issued_plan_codec import encode_envelope
from ci_coordinator.persistence.production_cutover_schema_attestation import (
    runtime_ingress_issuance_v2_schema_matches_contract,
)
from ci_coordinator.persistence.runtime_state_profile import load_bundled_runtime_state_profile
from ci_coordinator.persistence.schema import (
    issued_plan_envelopes,
    production_admission_authorities,
)

from ._runtime_ingress_issuance_support import (
    _config_draft,
    _selected_record_and_guard,
)

pytestmark = pytest.mark.persistence


def test_catalog_attestation_rejects_a_same_name_weakened_runtime_constraint(
    postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(postgres_database_url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                try:
                    assert await runtime_ingress_issuance_v2_schema_matches_contract(connection)
                    await connection.execute(
                        text(
                            "ALTER TABLE ci_coordinator.issued_plan_envelopes DROP CONSTRAINT "
                            "ck_issued_plan_envelopes_envelope_byte_limit"
                        )
                    )
                    await connection.execute(
                        text(
                            "ALTER TABLE ci_coordinator.issued_plan_envelopes ADD CONSTRAINT "
                            "ck_issued_plan_envelopes_envelope_byte_limit CHECK "
                            "(octet_length(envelope_canonical_json) <= 1048576)"
                        )
                    )
                    assert not await runtime_ingress_issuance_v2_schema_matches_contract(connection)
                finally:
                    await transaction.rollback()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_database_constraints_reject_cross_scope_authority_and_overlong_lifetime(
    postgres_database_url: str,
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        now = datetime.now(UTC)
        draft = _config_draft("main")
        record, guard, registration = await _selected_record_and_guard(now, draft)
        admin_engine = create_postgres_engine(postgres_database_url)
        runtime_engine = create_postgres_engine(runtime_postgres_database_url)
        try:
            async with PostgresIngressIssuanceUnitOfWork(runtime_engine) as unit_of_work:
                await unit_of_work.production_admissions.register(registration)
                await unit_of_work.commit()
            async with admin_engine.begin() as connection:
                with pytest.raises(IntegrityError) as cross_scope:
                    async with connection.begin_nested():
                        await connection.execute(
                            insert(issued_plan_envelopes).values(
                                idempotency_key="cross-scope-selected",
                                record_id="issued_plan_" + "f" * 32,
                                request_hash="f" * 64,
                                installation_id=guard.scope.installation_id + 1,
                                repository_id=guard.scope.repository_id,
                                production_admission_authority_id=registration.authority_id,
                                issued_at=record.envelope.issued_at,
                                expires_at=record.envelope.expires_at,
                                envelope_canonical_json=encode_envelope(
                                    record.envelope, load_bundled_runtime_state_profile()
                                ),
                            )
                        )
                assert getattr(cross_scope.value.orig, "sqlstate", None) == "23503"
                with pytest.raises(IntegrityError) as lifetime:
                    async with connection.begin_nested():
                        await connection.execute(
                            insert(production_admission_authorities).values(
                                authority_id="production_admission_" + "f" * 32,
                                key_id="test-key",
                                public_key_spki_der=(
                                    bytes.fromhex("302a300506032b6570032100") + b"\x00" * 32
                                ),
                                issued_at=now,
                                expires_at=now + timedelta(days=8),
                                envelope_canonical_json=b"{}",
                            )
                        )
                assert getattr(lifetime.value.orig, "sqlstate", None) == "23514"
        finally:
            await runtime_engine.dispose()
            await admin_engine.dispose()

    asyncio.run(scenario())

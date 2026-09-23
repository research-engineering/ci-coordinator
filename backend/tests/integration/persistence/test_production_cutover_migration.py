from __future__ import annotations

import asyncio

import pytest
from alembic import command
from sqlalchemy import create_engine, text

from ci_coordinator.persistence import PostgresIngressIssuanceUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine
from ci_coordinator.persistence.errors import DatabaseCapabilityUnavailable
from ci_coordinator.persistence.issued_plan_codec import IssuedPlanCodecError, encode_envelope
from ci_coordinator.persistence.production_cutover_schema_attestation import (
    production_cutover_schema_matches_contract,
    runtime_ingress_issuance_v2_schema_matches_contract,
    shadow_reconciliation_v2_schema_matches_contract,
)
from ci_coordinator.persistence.runtime_state_profile import load_bundled_runtime_state_profile

from ._runtime_ingress_issuance_support import _record
from .conftest import alembic_config

_PREDECESSOR = "20260904_0003"
_RETIRED = "20260906_0004"
_SUCCESSOR = "20260906_0005"

pytestmark = pytest.mark.persistence


def test_forward_cutover_preserves_the_exact_legacy_expiry_and_bytes(
    unmigrated_database_url: str,
) -> None:
    config = alembic_config(unmigrated_database_url)
    command.upgrade(config, _PREDECESSOR)
    record = _record("before-cutover", "legacy-request")
    envelope_bytes = encode_envelope(record.envelope, load_bundled_runtime_state_profile())
    engine = create_engine(unmigrated_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ci_coordinator.issued_plan_envelopes "
                    "(idempotency_key, record_id, request_hash, installation_id, repository_id, "
                    "issued_at, envelope_canonical_json) "
                    "VALUES (:key, :record, :request, 100, 200, :issued, :envelope)"
                ),
                {
                    "key": record.idempotency_key,
                    "record": record.record_id,
                    "request": record.request_hash,
                    "issued": record.envelope.issued_at,
                    "envelope": envelope_bytes,
                },
            )
        command.upgrade(config, _SUCCESSOR)
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM public.alembic_version"))
                == _SUCCESSOR
            )
            saved = connection.execute(
                text(
                    "SELECT issued_at, expires_at, envelope_canonical_json "
                    "FROM ci_coordinator.issued_plan_envelopes"
                )
            ).one()
            assert saved.issued_at == record.envelope.issued_at
            assert saved.expires_at == record.envelope.expires_at
            assert bytes(saved.envelope_canonical_json) == envelope_bytes
            transitions = connection.execute(
                text(
                    "SELECT revision_id, transition_kind "
                    "FROM ci_coordinator.database_compatibility_declarations "
                    "WHERE generation >= 4 ORDER BY generation"
                )
            ).all()
            assert [tuple(row) for row in transitions] == [
                (_RETIRED, "contract"),
                (_SUCCESSOR, "expand"),
            ]
            predecessor = set(
                connection.scalars(
                    text(
                        "SELECT capability_id "
                        "FROM ci_coordinator.database_compatibility_capabilities "
                        "WHERE revision_id = :revision"
                    ),
                    {"revision": _PREDECESSOR},
                )
            )
            current = set(
                connection.scalars(
                    text(
                        "SELECT capability_id "
                        "FROM ci_coordinator.database_compatibility_capabilities "
                        "WHERE revision_id = :revision"
                    ),
                    {"revision": _SUCCESSOR},
                )
            )
            assert current == predecessor - {
                "runtime-ingress-issuance-state/v1",
                "runtime-shadow-reconciliation-state/v1",
            } | {
                "runtime-ingress-issuance-state/v2",
                "runtime-shadow-reconciliation-state/v2",
                "production-generation-cutover/v1",
            }
        asyncio.run(_attest(unmigrated_database_url))
        with pytest.raises(RuntimeError, match="production cutover requires forward repair"):
            command.downgrade(config, _PREDECESSOR)
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM public.alembic_version"))
                == _SUCCESSOR
            )
            assert (
                connection.scalar(text("SELECT count(*) FROM ci_coordinator.issued_plan_envelopes"))
                == 1
            )
    finally:
        engine.dispose()


@pytest.mark.parametrize("malformation", ("invalid-json-shape", "projection-identity"))
def test_malformed_legacy_plan_rolls_back_both_forward_revisions(
    unmigrated_database_url: str,
    malformation: str,
) -> None:
    config = alembic_config(unmigrated_database_url)
    command.upgrade(config, _PREDECESSOR)
    record = _record("malformed-legacy", "legacy-request")
    envelope_bytes = encode_envelope(record.envelope, load_bundled_runtime_state_profile())
    engine = create_engine(unmigrated_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ci_coordinator.issued_plan_envelopes "
                    "(idempotency_key, record_id, request_hash, installation_id, repository_id, "
                    "issued_at, envelope_canonical_json) "
                    "VALUES (:key, :record, :request, 100, 200, :issued, :envelope)"
                ),
                {
                    "key": record.idempotency_key,
                    "record": record.record_id,
                    "request": "f" * 64
                    if malformation == "projection-identity"
                    else record.request_hash,
                    "issued": record.envelope.issued_at,
                    "envelope": b"{}" if malformation == "invalid-json-shape" else envelope_bytes,
                },
            )
        with pytest.raises(IssuedPlanCodecError):
            command.upgrade(config, _SUCCESSOR)
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM public.alembic_version"))
                == _PREDECESSOR
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT max(generation) "
                        "FROM ci_coordinator.database_compatibility_declarations"
                    )
                )
                == 3
            )
            assert (
                connection.scalar(
                    text("SELECT to_regclass('ci_coordinator.production_scope_states')")
                )
                is None
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_schema='ci_coordinator' "
                        "AND table_name='issued_plan_envelopes' "
                        "AND column_name='expires_at'"
                    )
                )
                == 0
            )
            assert (
                connection.scalar(text("SELECT count(*) FROM ci_coordinator.issued_plan_envelopes"))
                == 1
            )
    finally:
        engine.dispose()


def test_retirement_is_not_a_runtime_admission_point(unmigrated_database_url: str) -> None:
    config = alembic_config(unmigrated_database_url)
    command.upgrade(config, _RETIRED)

    async def reject() -> None:
        engine = create_postgres_engine(unmigrated_database_url)
        try:
            with pytest.raises(
                DatabaseCapabilityUnavailable,
                match="current database declaration does not provide every required capability",
            ):
                async with PostgresIngressIssuanceUnitOfWork(engine):
                    pytest.fail("retired capability must reject before repository exposure")
        finally:
            await engine.dispose()

    asyncio.run(reject())
    command.upgrade(config, _SUCCESSOR)
    asyncio.run(_attest(unmigrated_database_url))


async def _attest(url: str) -> None:
    engine = create_postgres_engine(url)
    try:
        async with engine.connect() as connection:
            assert await runtime_ingress_issuance_v2_schema_matches_contract(connection)
            assert await shadow_reconciliation_v2_schema_matches_contract(connection)
            assert await production_cutover_schema_matches_contract(connection)
    finally:
        await engine.dispose()

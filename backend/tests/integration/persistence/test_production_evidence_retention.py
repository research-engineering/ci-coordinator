from __future__ import annotations

import asyncio
from hashlib import sha256

import pytest
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from ci_coordinator.persistence.schema import (
    production_evidence_bundles,
    production_scope_states,
    production_staged_grants,
)
from ci_coordinator.production_admission.cutover_commands import ProductionCutoverRejected
from ci_coordinator.production_admission.limits import MAX_RETAINED_PRODUCTION_BUNDLES
from ci_coordinator.production_admission.ports import ProductionCutoverUnavailable

from ._production_cutover_support import cutover_database

pytestmark = pytest.mark.persistence


def test_referenced_evidence_cannot_be_deleted_and_runtime_cannot_rewrite_or_delete_it(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            await db.stage()
            async with db.admin.begin() as connection:
                with pytest.raises(IntegrityError):
                    async with connection.begin_nested():
                        await connection.execute(delete(production_evidence_bundles))
            async with db.runtime.begin() as connection:
                for statement in (
                    delete(production_evidence_bundles),
                    update(production_evidence_bundles).values(canonical_json=b"{}"),
                    delete(production_staged_grants),
                    update(production_staged_grants).values(generation=2),
                    delete(production_scope_states),
                ):
                    with pytest.raises(DBAPIError) as caught:
                        async with connection.begin_nested():
                            await connection.execute(statement)
                    assert getattr(caught.value.orig, "sqlstate", None) == "42501"
            assert (
                await db.store.load_evidence(
                    db.fixture.draft.scope, db.fixture.signed.grant.authority_id
                )
                == db.fixture.staged.canonical_bytes
            )

    asyncio.run(scenario())


def test_a_same_digest_different_bytes_store_conflict_is_not_overwritten_or_staged(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            content = b"{}\n"
            async with db.admin.begin() as connection:
                await connection.execute(
                    insert(production_evidence_bundles).values(
                        bundle_digest=db.fixture.staged.relation.evidence_bundle_digest,
                        content_sha256=sha256(content).hexdigest(),
                        canonical_json=content,
                        byte_count=len(content),
                        retained_at=func.clock_timestamp(),
                    )
                )
            with pytest.raises(ProductionCutoverUnavailable):
                await db.store.stage(
                    db.stage_command(), grant=db.fixture.signed.grant, evidence=db.fixture.staged
                )
            assert await db.store.inspect(db.fixture.draft.scope) is None
            assert await db.store.resolve(db.stage_command()) is None
            async with db.admin.connect() as connection:
                row = (
                    (await connection.execute(select(production_evidence_bundles))).mappings().one()
                )
                assert bytes(row["canonical_json"]) == content
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(production_staged_grants)
                    )
                    == 0
                )

    asyncio.run(scenario())


def test_exact_bundle_count_ceiling_rejects_without_eviction_or_partial_registration(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            content = b"{}\n"
            async with db.admin.begin() as connection:
                await connection.execute(
                    insert(production_evidence_bundles),
                    [
                        {
                            "bundle_digest": f"{index:064x}",
                            "content_sha256": sha256(content).hexdigest(),
                            "canonical_json": content,
                            "byte_count": len(content),
                            "retained_at": db.fixture.now,
                        }
                        for index in range(MAX_RETAINED_PRODUCTION_BUNDLES)
                    ],
                )
            result = await db.store.stage(
                db.stage_command(), grant=db.fixture.signed.grant, evidence=db.fixture.staged
            )
            assert result == ProductionCutoverRejected("capacity_exhausted")
            assert await db.store.inspect(db.fixture.draft.scope) is None
            assert await db.store.resolve(db.stage_command()) is None
            async with db.admin.begin() as connection:
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(production_evidence_bundles)
                    )
                    == MAX_RETAINED_PRODUCTION_BUNDLES
                )
                assert (
                    await connection.scalar(
                        select(func.count()).select_from(production_staged_grants)
                    )
                    == 0
                )
                with pytest.raises(IntegrityError) as caught:
                    async with connection.begin_nested():
                        await connection.execute(
                            insert(production_evidence_bundles).values(
                                bundle_digest="f" * 64,
                                content_sha256=sha256(content).hexdigest(),
                                canonical_json=content,
                                byte_count=len(content),
                                retained_at=func.clock_timestamp(),
                            )
                        )
                assert getattr(caught.value.orig, "sqlstate", None) == "23514"

    asyncio.run(scenario())


def test_database_guard_rejects_a_staged_generation_not_in_the_signed_scope(
    postgres_database_url: str, runtime_postgres_database_url: str
) -> None:
    async def scenario() -> None:
        async with cutover_database(postgres_database_url, runtime_postgres_database_url) as db:
            await db.stage()
            async with db.admin.begin() as connection:
                with pytest.raises(IntegrityError) as caught:
                    async with connection.begin_nested():
                        await connection.execute(
                            text(
                                "INSERT INTO ci_coordinator.production_staged_grants "
                                "SELECT installation_id, repository_id, authority_id, "
                                "generation + 1, "
                                "bundle_digest, lookup_canonical_json, staged_at "
                                "FROM ci_coordinator.production_staged_grants"
                            )
                        )
                assert "production staged grant differs from its signed scope" in str(
                    caught.value.orig
                )

    asyncio.run(scenario())

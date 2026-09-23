from dataclasses import replace
from typing import Final

from sqlalchemy import and_, delete, func, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics.archive_retention import expire_archive_detail
from ci_coordinator.ci_economics.history_configuration import MAX_HISTORY_DATASETS, HistoryUsage
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence._schema_ci_history_archive import (
    ci_history_attempts as attempts,
)
from ci_coordinator.persistence._schema_ci_history_archive import (
    ci_history_details as details,
)
from ci_coordinator.persistence._schema_ci_history_control import ci_history_datasets as datasets
from ci_coordinator.persistence.ci_history_retention_codec import decode_history_retention
from ci_coordinator.persistence.ci_history_state_store import (
    HistoryWriteConflict,
    history_database_time,
    history_scope_predicate,
    load_history_dataset,
    lock_history_scope,
    write_history_dataset,
)

MAX_HISTORY_DETAIL_CLEANUP: Final = 100


async def expire_history_details(connection: AsyncConnection) -> int:
    candidates = (
        await connection.execute(
            select(
                attempts.c.installation_id,
                attempts.c.repository_id,
                func.min(attempts.c.detail_expires_at).label("due_at"),
            )
            .select_from(
                attempts.join(
                    datasets,
                    and_(
                        attempts.c.installation_id == datasets.c.installation_id,
                        attempts.c.repository_id == datasets.c.repository_id,
                        attempts.c.generation == datasets.c.generation,
                    ),
                )
            )
            .where(
                datasets.c.state.in_(("active", "paused")),
                attempts.c.detail_state == "retained",
                attempts.c.detail_expires_at <= func.statement_timestamp(),
            )
            .group_by(attempts.c.installation_id, attempts.c.repository_id)
            .order_by("due_at", attempts.c.installation_id, attempts.c.repository_id)
            .limit(MAX_HISTORY_DATASETS + 1)
        )
    ).all()
    if len(candidates) > MAX_HISTORY_DATASETS:
        raise ValueError("history cleanup population exceeds the dataset bound")
    for installation_id, repository_id, _ in candidates:
        expired = await expire_history_details_in_scope(
            connection, RepositoryScope(installation_id, repository_id)
        )
        if expired:
            return expired
    return 0


async def expire_history_details_in_scope(
    connection: AsyncConnection,
    scope: RepositoryScope,
    *,
    limit: int = MAX_HISTORY_DETAIL_CLEANUP,
) -> int:
    if type(limit) is not int or not 1 <= limit <= MAX_HISTORY_DETAIL_CLEANUP:
        raise ValueError("history detail cleanup requires a bounded positive limit")
    async with connection.begin_nested():
        if not await lock_history_scope(connection, scope, try_only=True):
            return 0
        dataset = await load_history_dataset(connection, scope, locked=True, skip_locked=True)
        if dataset is None or dataset.state not in {"active", "paused"}:
            return 0
        now = await history_database_time(connection)
        rows = (
            (
                await connection.execute(
                    select(
                        attempts.c.workflow_run_id,
                        attempts.c.run_attempt,
                        attempts.c.detail_state,
                        attempts.c.detail_first_imported_at,
                        attempts.c.detail_policy_canonical,
                        attempts.c.detail_policy_source,
                        attempts.c.detail_policy_revision,
                        attempts.c.detail_expires_at,
                    )
                    .where(
                        history_scope_predicate(attempts, scope),
                        attempts.c.generation == dataset.generation,
                        attempts.c.detail_state == "retained",
                        attempts.c.detail_expires_at <= now,
                    )
                    .order_by(
                        attempts.c.detail_expires_at,
                        attempts.c.workflow_run_id,
                        attempts.c.run_attempt,
                    )
                    .limit(limit)
                    .with_for_update()
                )
            )
            .mappings()
            .all()
        )
        if not rows:
            return 0
        for row in rows:
            prior = decode_history_retention(row)
            successor = expire_archive_detail(prior, now=now)
            if prior.state != "retained" or successor.state != "expired":
                raise ValueError("history detail expiry projection disagrees with its policy")
        keys = {(row["workflow_run_id"], row["run_attempt"]) for row in rows}
        removed = (
            await connection.execute(
                delete(details)
                .where(
                    history_scope_predicate(details, scope),
                    details.c.generation == dataset.generation,
                    tuple_(details.c.workflow_run_id, details.c.run_attempt).in_(sorted(keys)),
                )
                .returning(
                    details.c.workflow_run_id,
                    details.c.run_attempt,
                    func.octet_length(details.c.detail_canonical),
                )
            )
        ).all()
        if {(run_id, attempt) for run_id, attempt, _ in removed} != keys or any(
            type(size) is not int or size <= 0 for _, _, size in removed
        ):
            raise ValueError("retained history detail has no stored payload")
        changed = await connection.execute(
            update(attempts)
            .where(
                history_scope_predicate(attempts, scope),
                attempts.c.generation == dataset.generation,
                tuple_(attempts.c.workflow_run_id, attempts.c.run_attempt).in_(sorted(keys)),
                attempts.c.detail_state == "retained",
            )
            .values(detail_state="expired")
        )
        if changed.rowcount != len(keys):
            raise HistoryWriteConflict("history detail expiry lost its locked parents")
        usage = dataset.usage.release(
            HistoryUsage(
                attempts=0, jobs=0, gaps=0, canonicalBytes=sum(size for _, _, size in removed)
            )
        )
        await write_history_dataset(
            connection,
            dataset,
            replace(dataset, usage=usage, data_revision=dataset.data_revision + 1),
        )
        return len(rows)

from datetime import datetime
from typing import Literal

from sqlalchemy import and_, delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement

from ci_coordinator.ci_economics.history_configuration import HistoryDataset
from ci_coordinator.ci_economics.history_rechecks import (
    MAX_HISTORY_RECHECK_ACQUISITIONS,
    MAX_HISTORY_RECHECK_RUNS,
    HistoryRecheckHint,
    HistoryRecheckState,
    initial_history_recheck,
    merge_history_recheck_hint,
    recheck_capacity_available,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence._schema_ci_history_control import ci_history_rechecks as rechecks
from ci_coordinator.persistence.ci_history_recheck_codec import (
    decode_history_recheck,
    encode_history_recheck,
)
from ci_coordinator.persistence.ci_history_state_store import (
    HistoryWriteConflict,
    history_cas_time,
    history_database_time,
    history_scope_predicate,
    load_history_dataset,
    lock_history_scope,
)

type RecheckAdmission = Literal[
    "admitted", "replayed", "inactive", "not_ready", "unselected", "capacity_reached"
]
type RecheckCasAuthority = Literal["hint", "acquire", "holder", "recovery"]


async def enqueue_history_recheck(
    connection: AsyncConnection, hint: HistoryRecheckHint
) -> RecheckAdmission:
    if type(hint) is not HistoryRecheckHint:
        raise TypeError("history queue admission requires an exact hint")
    scope = hint.cursor.scope
    await lock_history_scope(connection, scope)
    dataset = await load_history_dataset(connection, scope, locked=True)
    if dataset is None:
        return "inactive"
    return await admit_history_recheck(
        connection, dataset, hint, now=await history_database_time(connection)
    )


async def load_history_recheck(
    connection: AsyncConnection, scope: RepositoryScope, generation: int, workflow_run_id: int
) -> HistoryRecheckState | None:
    row = (
        (
            await connection.execute(
                select(rechecks)
                .where(
                    history_scope_predicate(rechecks, scope),
                    rechecks.c.generation == generation,
                    rechecks.c.workflow_run_id == workflow_run_id,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    return None if row is None else decode_history_recheck(row)


async def admit_history_recheck(
    connection: AsyncConnection,
    dataset: HistoryDataset,
    hint: HistoryRecheckHint,
    *,
    now: datetime,
    revisit_completed: bool = False,
) -> RecheckAdmission:
    if type(revisit_completed) is not bool or (revisit_completed and hint.source != "repair"):
        raise ValueError("only a parent-bound historical repair may revisit completed work")
    if dataset.state != "active":
        return "inactive"
    if now < max(dataset.configured_at, hint.run_created_at):
        return "not_ready"
    initial = initial_history_recheck(dataset, hint, now)
    if initial is None:
        return "unselected"
    prior = await load_history_recheck(
        connection, dataset.scope, dataset.generation, hint.cursor.workflow_run_id
    )
    if prior is not None:
        successor = merge_history_recheck_hint(prior, hint, revisit_completed=revisit_completed)
        if successor == prior:
            return "replayed"
        await write_history_recheck(
            connection,
            prior,
            successor,
            authority="hint",
            hint=hint,
            revisit_completed=revisit_completed,
        )
        return "admitted"
    # The caller holds the dataset scope lock, which serializes every queue admission.
    sources = (
        (
            await connection.execute(
                select(rechecks.c.source)
                .where(history_scope_predicate(rechecks, dataset.scope))
                .limit(MAX_HISTORY_RECHECK_RUNS + 1)
            )
        )
        .scalars()
        .all()
    )
    if any(source not in {"recent", "repair"} for source in sources):
        raise ValueError("history recheck capacity contains an unknown source")
    if not recheck_capacity_available(len(sources), sources.count("repair"), hint.source):
        return "capacity_reached"
    await connection.execute(insert(rechecks).values(**encode_history_recheck(initial)))
    return "admitted"


async def write_history_recheck(
    connection: AsyncConnection,
    prior: HistoryRecheckState,
    successor: HistoryRecheckState | None,
    *,
    authority: RecheckCasAuthority,
    hint: HistoryRecheckHint | None = None,
    revisit_completed: bool = False,
) -> None:
    if type(prior) is not HistoryRecheckState:
        raise TypeError("history recheck CAS requires an exact prior state")
    if successor is not None and (
        type(successor) is not HistoryRecheckState
        or (
            successor.hint.cursor.scope,
            successor.generation,
            successor.hint.cursor.workflow_run_id,
            successor.hint.workflow_id,
            successor.hint.run_created_at,
            successor.revision,
        )
        != (
            prior.hint.cursor.scope,
            prior.generation,
            prior.hint.cursor.workflow_run_id,
            prior.hint.workflow_id,
            prior.hint.run_created_at,
            prior.revision + 1,
        )
    ):
        raise ValueError("history recheck CAS must preserve identity and advance one revision")
    predicates = [
        history_scope_predicate(rechecks, prior.hint.cursor.scope),
        rechecks.c.generation == prior.generation,
        rechecks.c.workflow_run_id == prior.hint.cursor.workflow_run_id,
        rechecks.c.revision == prior.revision,
        rechecks.c.state_canonical == encode_history_recheck(prior)["state_canonical"],
    ]
    predicates.extend(_time_authority(prior, successor, authority, hint, revisit_completed))
    if successor is None:
        result = await connection.execute(delete(rechecks).where(*predicates))
    else:
        mutable = {
            name: value
            for name, value in encode_history_recheck(successor).items()
            if name
            not in {
                "installation_id",
                "repository_id",
                "generation",
                "workflow_run_id",
                "workflow_id",
            }
        }
        result = await connection.execute(update(rechecks).where(*predicates).values(**mutable))
    if result.rowcount != 1:
        raise HistoryWriteConflict("history recheck CAS lost or its lease authority expired")


def _time_authority(
    prior: HistoryRecheckState,
    successor: HistoryRecheckState | None,
    authority: RecheckCasAuthority,
    hint: HistoryRecheckHint | None,
    revisit_completed: bool,
) -> list[ColumnElement[bool]]:
    if authority == "hint":
        if (
            hint is None
            or successor is None
            or merge_history_recheck_hint(prior, hint, revisit_completed=revisit_completed)
            != successor
        ):
            raise ValueError("history hint CAS requires its exact monotonic merge")
        return []
    if hint is not None or revisit_completed is not False:
        raise ValueError("lease transitions cannot carry a historical repair hint")
    cas_time = history_cas_time()
    lease = prior.lease
    if authority == "acquire":
        if (
            successor is None
            or successor.lease is None
            or successor.acquisition_count != prior.acquisition_count + 1
        ):
            raise ValueError("history acquisition CAS requires its counted fresh lease")
        predicates = [
            cas_time >= successor.lease.acquired_at,
            cas_time < successor.lease.expires_at,
        ]
        if lease is not None:
            predicates.append(cas_time >= lease.expires_at)
        return predicates
    if lease is None or (successor is not None and successor.lease is not None):
        raise ValueError("history terminal CAS requires prior lease and an unleased successor")
    exact = and_(
        rechecks.c.lease_worker_id == lease.worker_id,
        rechecks.c.lease_token == lease.token,
        rechecks.c.lease_acquired_at == lease.acquired_at,
        rechecks.c.lease_expires_at == lease.expires_at,
    )
    if authority == "holder":
        return [exact, cas_time >= lease.acquired_at, cas_time < lease.expires_at]
    if authority == "recovery" and prior.acquisition_count == MAX_HISTORY_RECHECK_ACQUISITIONS:
        return [exact, cas_time >= lease.expires_at]
    raise ValueError("unknown or inapplicable history recheck CAS authority")

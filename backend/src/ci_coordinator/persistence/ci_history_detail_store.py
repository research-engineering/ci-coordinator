from dataclasses import replace
from datetime import datetime
from typing import Literal

from sqlalchemy import func, insert, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.ci_economics.archive_detail import (
    MAX_HISTORY_DETAIL_BYTES,
    ArchivedAttemptDetail,
    encode_archive_detail,
    validate_history_detail,
)
from ci_coordinator.ci_economics.archive_encoding import (
    MAX_HISTORY_STATISTICS_BYTES as MAX_STORED_HISTORY_DETAIL_BYTES,
)
from ci_coordinator.ci_economics.archive_retention import (
    ArchiveDetailRetention,
    record_first_detail_import,
)
from ci_coordinator.ci_economics.archive_statistics import ArchivedAttemptStatistics
from ci_coordinator.ci_economics.history_configuration import HistoryDataset, HistoryUsage
from ci_coordinator.persistence._schema_ci_history_archive import (
    ci_history_attempts,
    ci_history_details,
)
from ci_coordinator.persistence.ci_history_codec import decode_archive_statistics
from ci_coordinator.persistence.ci_history_configuration_store import load_history_defaults
from ci_coordinator.persistence.ci_history_retention_codec import (
    decode_history_retention,
    encode_history_retention,
)
from ci_coordinator.persistence.ci_history_state_store import (
    history_scope_predicate,
    write_history_dataset,
)
from ci_coordinator.persistence.ci_history_statistics_store import (
    history_attempt_predicate,
    load_history_jobs,
)

_DETAIL_KEY = (
    "installation_id",
    "repository_id",
    "generation",
    "workflow_run_id",
    "run_attempt",
)

type DetailStoreOutcome = Literal["recorded", "not_imported", "capacity_reached"]


async def store_history_detail(
    connection: AsyncConnection,
    dataset: HistoryDataset,
    statistics: ArchivedAttemptStatistics,
    detail: ArchivedAttemptDetail,
    *,
    now: datetime,
) -> tuple[HistoryDataset, DetailStoreOutcome]:
    """Admit one already-fetched detail sidecar beside its statistics parent.

    A malformed optional sidecar is unavailable detail, not a reason to
    discard independently valid permanent statistics. Database and quota
    failures remain exceptions or ``capacity_reached`` so the caller can roll
    back the whole contribution savepoint.
    """

    statistics = ArchivedAttemptStatistics.model_validate(statistics)
    if dataset.state != "active":
        raise ValueError("history detail requires an active dataset")
    identity = statistics.attempt.to_attempt()
    if dataset.scope != identity.scope or not dataset.configuration.selects(statistics.workflow_id):
        raise ValueError("history detail substitutes its current dataset scope")

    predicate = history_attempt_predicate(ci_history_attempts, dataset.generation, identity)
    parent = (
        (await connection.execute(select(ci_history_attempts).where(predicate).with_for_update()))
        .mappings()
        .one_or_none()
    )
    if parent is None:
        raise ValueError("history detail has no statistics parent")
    if type(parent["has_conflict"]) is not bool or parent["has_conflict"] is not False:
        raise ValueError("history detail requires a non-conflicting statistics parent")
    retention = decode_history_retention(parent)
    existing_size = await connection.scalar(
        select(func.octet_length(ci_history_details.c.detail_canonical)).where(
            history_scope_predicate(ci_history_details, identity.scope),
            ci_history_details.c.generation == dataset.generation,
            ci_history_details.c.workflow_run_id == identity.workflow_run_id,
            ci_history_details.c.run_attempt == identity.run_attempt,
        )
    )
    _validate_existing_detail(retention, existing_size)
    if retention.state != "not_imported":
        return dataset, "not_imported"

    encoded_detail = _validate_and_encode_detail(statistics, detail)
    if encoded_detail is None:
        return dataset, "not_imported"
    stored_statistics = decode_archive_statistics(
        parent, await load_history_jobs(connection, dataset.generation, identity)
    )
    encoded_detail = _validate_and_encode_detail(stored_statistics, detail)
    if encoded_detail is None or stored_statistics.population != "complete":
        return dataset, "not_imported"

    defaults = await load_history_defaults(connection)
    if now < dataset.configured_at or now < defaults.updated_at:
        raise ValueError("history detail import precedes its policy authority")
    policy, reference = dataset.resolve_detail_policy(defaults)
    imported = record_first_detail_import(retention, policy, reference, now=now)
    if imported is retention:
        return dataset, "not_imported"
    if imported.state != "retained":
        raise ValueError("enabled detail policy did not produce retained detail")

    addition = HistoryUsage(
        attempts=0,
        jobs=0,
        gaps=0,
        canonicalBytes=len(encoded_detail),
    )
    usage = dataset.usage.reserve(addition, dataset.configuration.quota)
    if usage is None:
        return dataset, "capacity_reached"

    key = {name: parent[name] for name in _DETAIL_KEY}
    await connection.execute(
        insert(ci_history_details).values(**key, detail_canonical=encoded_detail)
    )
    changed_parent = await connection.execute(
        update(ci_history_attempts).where(predicate).values(**encode_history_retention(imported))
    )
    if changed_parent.rowcount != 1:
        raise ValueError("history detail parent disappeared during import")
    changed_dataset = replace(
        dataset,
        usage=usage,
        data_revision=dataset.data_revision + 1,
    )
    await write_history_dataset(connection, dataset, changed_dataset)
    return changed_dataset, "recorded"


def _validate_existing_detail(retention: ArchiveDetailRetention, existing_size: object) -> None:
    if existing_size is not None and (
        type(existing_size) is not int or not 1 <= existing_size <= MAX_STORED_HISTORY_DETAIL_BYTES
    ):
        raise ValueError("stored history detail has an invalid positive bounded size")
    if retention.state == "not_imported" and existing_size is not None:
        raise ValueError("not-imported history detail has a child payload")
    if retention.state == "retained" and existing_size is None:
        raise ValueError("retained history detail has no child payload")
    if retention.state == "expired" and existing_size is not None:
        raise ValueError("expired history detail still has a child payload")


def _validate_and_encode_detail(
    statistics: ArchivedAttemptStatistics, detail: ArchivedAttemptDetail
) -> bytes | None:
    try:
        validate_history_detail(statistics, detail)
        encoded = encode_archive_detail(detail)
    except (TypeError, ValueError, OverflowError):
        return None
    if type(encoded) is not bytes or not 1 <= len(encoded) <= MAX_HISTORY_DETAIL_BYTES:
        return None
    return encoded

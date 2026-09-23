"""PostgreSQL adapter for one bounded post-fence workbench statement snapshot."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.compatibility_admission import admit_schema_dependent_operation
from ci_coordinator.persistence.compatibility_fence import (
    CompatibilityFenceMode,
    acquire_compatibility_fence,
)
from ci_coordinator.persistence.compatibility_profile import (
    CompatibilityProfile,
    load_bundled_profile,
)
from ci_coordinator.persistence.errors import PersistenceError
from ci_coordinator.persistence.operator_override_repository import (
    admit_operator_override_state,
)
from ci_coordinator.persistence.runtime_state_profile import load_bundled_runtime_state_profile
from ci_coordinator.persistence.schema_capabilities import workbench_read_requirements
from ci_coordinator.persistence.shadow_reconciliation_state_profile import (
    load_bundled_shadow_reconciliation_state_profile,
)
from ci_coordinator.persistence.workbench_projection import (
    project_audit_events,
    project_config_epochs,
    project_overrides,
    project_plans,
    project_runs,
)
from ci_coordinator.persistence.workbench_queries import (
    WORKBENCH_FAMILIES,
    WorkbenchFamily,
    workbench_snapshot_statement,
)
from ci_coordinator.workbench_read_models import (
    MAX_WORKBENCH_SECTION_ITEMS,
    RepositoryDataSnapshot,
    TruncationView,
    WorkbenchReadError,
)


class PostgresWorkbenchRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._compatibility_profile = load_bundled_profile()
        self._runtime_profile = load_bundled_runtime_state_profile()
        self._reconciliation_profile = load_bundled_shadow_reconciliation_state_profile()

    async def load(self, scope: RepositoryScope, *, limit: int) -> RepositoryDataSnapshot:
        if type(scope) is not RepositoryScope:
            raise TypeError("workbench scope must be exact")
        if type(limit) is not int or not 1 <= limit <= MAX_WORKBENCH_SECTION_ITEMS:
            raise ValueError("workbench section limit is outside its admitted interval")
        try:
            raw = await self._load_rows(scope, limit)
            return RepositoryDataSnapshot(
                scope=scope,
                observed_at=raw.observed_at,
                ledger_revision=raw.ledger_revision,
                plans=project_plans(raw.plans[:limit], self._runtime_profile),
                runs=project_runs(
                    raw.runs[:limit],
                    self._reconciliation_profile,
                    raw.observed_at,
                ),
                overrides=project_overrides(raw.overrides[:limit], raw.observed_at),
                config_epochs=project_config_epochs(raw.config_epochs[:limit]),
                audit_events=project_audit_events(raw.audit_events[:limit]),
                truncated=TruncationView(
                    plans=len(raw.plans) > limit,
                    runs=len(raw.runs) > limit,
                    overrides=len(raw.overrides) > limit,
                    config_epochs=len(raw.config_epochs) > limit,
                    audit_events=len(raw.audit_events) > limit,
                ),
            )
        except asyncio.CancelledError:
            raise
        except (PersistenceError, SQLAlchemyError, TypeError, ValueError) as error:
            raise WorkbenchReadError("repository workbench snapshot is unavailable") from error

    async def _load_rows(self, scope: RepositoryScope, limit: int) -> _WorkbenchRows:
        async with self._engine.connect() as raw_connection:
            connection = await _configure_snapshot_read(
                raw_connection,
                self._compatibility_profile,
            )
            async with connection.begin():
                await connection.execute(
                    text(self._compatibility_profile.snapshot_read_only_statement)
                )
                await _verify_read_snapshot_transaction(
                    connection,
                    self._compatibility_profile,
                )
                await acquire_compatibility_fence(
                    connection,
                    self._compatibility_profile,
                    CompatibilityFenceMode.PARTICIPANT,
                )
                await admit_schema_dependent_operation(
                    connection,
                    self._compatibility_profile,
                    workbench_read_requirements(self._compatibility_profile),
                )
                await admit_operator_override_state(connection, self._compatibility_profile)
                result = await connection.execute(workbench_snapshot_statement(scope, limit))
                rows = result.mappings().all()
                if len(rows) != limit + 1:
                    raise ValueError("workbench snapshot positions are invalid")
                observed_at = rows[0]["observed_at"]
                ledger_revision = rows[0]["ledger_revision"]
                if type(observed_at) is not datetime or observed_at.tzinfo is None:
                    raise ValueError("workbench snapshot timestamp is invalid")
                if type(ledger_revision) is not int or ledger_revision < 0:
                    raise ValueError("workbench audit revision is invalid")
                groups: dict[WorkbenchFamily, list[Mapping[str, object]]] = {
                    name: [] for name in WORKBENCH_FAMILIES
                }
                for position, row in enumerate(rows, start=1):
                    if row["position"] != position:
                        raise ValueError("workbench snapshot order is invalid")
                    for name in WORKBENCH_FAMILIES:
                        marker = row[f"{name}__position"]
                        if marker is None:
                            continue
                        if marker != position:
                            raise ValueError("workbench family position is invalid")
                        prefix = f"{name}_"
                        groups[name].append(
                            {
                                key.removeprefix(prefix): value
                                for key, value in row.items()
                                if key.startswith(prefix) and key != f"{name}__position"
                            }
                        )
                return _WorkbenchRows(
                    observed_at=observed_at,
                    ledger_revision=ledger_revision,
                    plans=tuple(groups["plans"]),
                    runs=tuple(groups["runs"]),
                    overrides=tuple(groups["overrides"]),
                    config_epochs=tuple(groups["config_epochs"]),
                    audit_events=tuple(groups["audit_events"]),
                )


@dataclass(frozen=True, slots=True)
class _WorkbenchRows:
    observed_at: datetime
    ledger_revision: int
    plans: tuple[Mapping[str, object], ...]
    runs: tuple[Mapping[str, object], ...]
    overrides: tuple[Mapping[str, object], ...]
    config_epochs: tuple[Mapping[str, object], ...]
    audit_events: tuple[Mapping[str, object], ...]


async def _configure_snapshot_read(
    connection: AsyncConnection,
    profile: CompatibilityProfile,
) -> AsyncConnection:
    return await connection.execution_options(isolation_level=profile.snapshot_isolation_level)


async def _verify_read_snapshot_transaction(
    connection: AsyncConnection,
    profile: CompatibilityProfile,
) -> None:
    isolation = await connection.scalar(text("SHOW transaction_isolation"))
    read_only = await connection.scalar(text("SHOW transaction_read_only"))
    if (
        isolation != profile.snapshot_isolation_expected_value
        or read_only != profile.snapshot_read_only_expected_value
    ):
        raise ValueError("workbench snapshot transaction is not read-committed and read-only")

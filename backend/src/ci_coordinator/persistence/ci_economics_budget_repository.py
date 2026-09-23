from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import Insert, Update, func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.audit_replay import AuditAppendAppended, AuditEventInput, prepare_audit_event
from ci_coordinator.audit_replay.event import JsonValue
from ci_coordinator.ci_economics.budget_commands import (
    BUDGET_POLICY_EVENT_TYPE,
    BudgetPolicyCommitted,
    BudgetPolicyConflict,
    BudgetPolicyWriteResult,
    ConfigureBudgetPolicy,
)
from ci_coordinator.ci_economics.budget_policy import (
    MAX_BUDGET_POLICIES_PER_REPOSITORY,
    BudgetPolicySnapshot,
)
from ci_coordinator.ci_economics.ports import CiEconomicsStoreUnavailable
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.ci_economics_budget_codec import (
    budget_policy_from_mapping,
    decode_budget_policy,
    encode_budget_policy,
)
from ci_coordinator.persistence.ci_economics_budget_lock import lock_budget_scope
from ci_coordinator.persistence.errors import PersistenceError
from ci_coordinator.persistence.schema import ci_economics_budget_policies


class _PostgresBudgetPolicyRepository:
    def __init__(
        self,
        connection: AsyncConnection,
        audit: _PostgresAuditEventRepository,
        ensure_active: Callable[[], None],
        mark_rollback_required: Callable[[], None],
    ) -> None:
        self._connection = connection
        self._audit = audit
        self._ensure_active = ensure_active
        self._mark_rollback_required = mark_rollback_required

    async def list_policies(self, scope: RepositoryScope) -> tuple[BudgetPolicySnapshot, ...]:
        self._ensure_active()
        if type(scope) is not RepositoryScope:
            raise TypeError("policy read requires exact scope")
        table = ci_economics_budget_policies
        try:
            rows = (
                (
                    await self._connection.execute(
                        select(table)
                        .where(
                            table.c.installation_id == scope.installation_id,
                            table.c.repository_id == scope.repository_id,
                        )
                        .limit(MAX_BUDGET_POLICIES_PER_REPOSITORY + 1)
                    )
                )
                .mappings()
                .all()
            )
            if len(rows) > MAX_BUDGET_POLICIES_PER_REPOSITORY:
                raise ValueError("stored budget policy quota is violated")
            policies = []
            for row in rows:
                policy = decode_budget_policy(row["policy_canonical"])
                if (
                    policy.scope != scope
                    or policy.policy_key != row["policy_key"]
                    or policy.revision != row["revision"]
                    or policy.policy_digest != row["policy_digest"]
                ):
                    raise ValueError("budget policy identity contradicts storage")
                policies.append(policy)
            return tuple(sorted(policies, key=lambda policy: policy.policy_key))
        except asyncio.CancelledError:
            raise
        except (SQLAlchemyError, TypeError, ValueError) as error:
            raise CiEconomicsStoreUnavailable("budget policies unavailable") from error

    async def configure_policy(self, command: ConfigureBudgetPolicy) -> BudgetPolicyWriteResult:
        self._ensure_active()
        if type(command) is not ConfigureBudgetPolicy:
            raise TypeError("policy write requires exact command")
        try:
            await lock_budget_scope(self._connection, command.scope)
            existing = await self._audit._find_pair_owned(
                command.audit_key, BUDGET_POLICY_EVENT_TYPE, command.scope
            )
            if existing is not None:
                payload = existing.payload
                if type(payload) is not dict or set(payload) != {"commandDigest", "policy"}:
                    raise ValueError("budget operation has invalid audit evidence")
                policy = budget_policy_from_mapping(payload["policy"])
                if policy.scope != command.scope:
                    raise ValueError("budget operation crosses repository scope")
                if payload["commandDigest"] != command.command_digest:
                    return BudgetPolicyConflict("operation_conflict")
                if policy != command.next_policy or existing.actor != command.actor:
                    raise ValueError("budget operation contradicts its command")
                return BudgetPolicyCommitted(policy, replayed=True)
            policies = await self.list_policies(command.scope)
            prior = next((item for item in policies if item.policy_key == command.policy_key), None)
            if (0 if prior is None else prior.revision) != command.expected_revision:
                return BudgetPolicyConflict("revision_conflict")
            if prior is None and len(policies) == MAX_BUDGET_POLICIES_PER_REPOSITORY:
                return BudgetPolicyConflict("capacity_reached")
            policy = command.next_policy
            now = await self._connection.scalar(select(func.clock_timestamp()))
            if type(now) is not datetime:
                raise ValueError("database clock is unavailable")
            event = prepare_audit_event(
                AuditEventInput(
                    idempotency_key=command.audit_key,
                    subject_type="policy-decision",
                    subject_id=f"ci-budget:{command.scope.installation_id}:{command.scope.repository_id}:{command.policy_key}",
                    event_type=BUDGET_POLICY_EVENT_TYPE,
                    created_at=now.astimezone(UTC)
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z"),
                    actor=command.actor,
                    installation_id=command.scope.installation_id,
                    repository_id=command.scope.repository_id,
                    payload=cast(
                        JsonValue,
                        {
                            "commandDigest": command.command_digest,
                            "policy": policy.canonical_mapping(),
                        },
                    ),
                )
            )
            appended = await self._audit._append_pair_owned(event, command.scope)
            if not isinstance(appended, AuditAppendAppended):
                raise ValueError("budget operation contradicts its audit slot")
            table = ci_economics_budget_policies
            values = dict(
                revision=policy.revision,
                policy_digest=policy.policy_digest,
                policy_canonical=encode_budget_policy(policy),
            )
            statement: Insert | Update
            if prior is None:
                statement = insert(table).values(
                    **values,
                    installation_id=command.scope.installation_id,
                    repository_id=command.scope.repository_id,
                    policy_key=command.policy_key,
                )
            else:
                statement = (
                    update(table)
                    .where(
                        table.c.installation_id == command.scope.installation_id,
                        table.c.repository_id == command.scope.repository_id,
                        table.c.policy_key == command.policy_key,
                        table.c.revision == command.expected_revision,
                    )
                    .values(**values)
                )
            written = await self._connection.scalar(statement.returning(table.c.revision))
            if written != policy.revision:
                raise ValueError("budget policy CAS did not commit its paired audit")
            return BudgetPolicyCommitted(policy, replayed=False)
        except asyncio.CancelledError:
            self._mark_rollback_required()
            raise
        except (
            PersistenceError,
            SQLAlchemyError,
            TypeError,
            ValueError,
            CiEconomicsStoreUnavailable,
        ) as error:
            self._mark_rollback_required()
            raise CiEconomicsStoreUnavailable("budget policy write unavailable") from error

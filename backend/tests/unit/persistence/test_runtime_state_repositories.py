from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from sqlalchemy.sql.dml import Delete
from sqlalchemy.sql.selectable import Select

from ci_coordinator.github_ingestion.ports import prepare_delivery_claim
from ci_coordinator.github_ingestion.provenance import WebhookProvenance
from ci_coordinator.persistence import (
    control_plane_session_repository,
    runtime_issuance_repository,
)
from ci_coordinator.persistence.audit_repository import _PostgresAuditEventRepository
from ci_coordinator.persistence.compatibility_profile import CompatibilityProfile
from ci_coordinator.persistence.config_epoch_repository import _PostgresConfigEpochRepository
from ci_coordinator.persistence.control_plane_session_repository import (
    PostgresControlPlaneSessionStore,
)
from ci_coordinator.persistence.operator_override_repository import (
    PostgresOperatorOverrideRepository,
)
from ci_coordinator.persistence.runtime_delivery_repository import (
    _PostgresWebhookIngestionRepository,
)
from ci_coordinator.persistence.runtime_issuance_repository import _PostgresIssuanceRepository
from ci_coordinator.persistence.runtime_state_profile import RuntimeIngressIssuanceStateProfile
from ci_coordinator.persistence.webhook_delivery_audit import WebhookDeliveryAuditAppender
from ci_coordinator.plan_issuance import IssuedPlanRecord


class _CancelledScalarConnection:
    async def scalar(self, _statement: object) -> object:
        raise asyncio.CancelledError("database operation cancelled")


class _UnusedAuditAppender:
    async def append_delivery_claim(self, _event: object) -> object:
        raise AssertionError("cancelled delivery insert must not reach audit append")


class _SessionLookupResult:
    def __init__(self, row: Mapping[str, object] | None) -> None:
        self._row = row
        self.closed = False

    async def close(self) -> None:
        self.closed = True

    def mappings(self) -> _SessionLookupResult:
        return self

    def one_or_none(self) -> Mapping[str, object] | None:
        return self._row

    def __iter__(self) -> Iterator[Mapping[str, object]]:
        return iter([] if self._row is None else [self._row])

    def scalars(self) -> _SessionLookupResult:
        return self

    async def partitions(self, size: int) -> AsyncIterator[list[bytes]]:
        assert size == 128
        if self._row is not None:
            yield [b"x" * 32]


class _SessionLookupConnection:
    def __init__(self, row: Mapping[str, object] | None) -> None:
        self._row = row
        self.statements: list[object] = []
        self.streamed: list[object] = []
        self.stream_result: _SessionLookupResult | None = None

    async def stream(
        self, statement: object, *, execution_options: dict[str, int]
    ) -> _SessionLookupResult:
        assert execution_options == {"yield_per": 128}
        self.streamed.append(statement)
        self.stream_result = _SessionLookupResult(self._row)
        return self.stream_result

    async def execute(self, statement: object) -> _SessionLookupResult:
        self.statements.append(statement)
        if isinstance(statement, Select):
            return _SessionLookupResult(self._row)
        return _SessionLookupResult(None)


def test_delivery_claim_cancellation_marks_rollback_and_propagates() -> None:
    async def scenario() -> None:
        rollback_required = False

        def mark_rollback_required() -> None:
            nonlocal rollback_required
            rollback_required = True

        repository = _PostgresWebhookIngestionRepository(
            cast(AsyncConnection, _CancelledScalarConnection()),
            cast(WebhookDeliveryAuditAppender, _UnusedAuditAppender()),
            lambda: None,
            mark_rollback_required,
        )
        prepared = prepare_delivery_claim(
            WebhookProvenance(
                delivery_id="delivery-1",
                event_name="push",
                body_sha256="a" * 64,
                verified_at=datetime(2026, 7, 15, 12, 0, tzinfo=UTC),
                verifier_version="test-verifier/v1",
            )
        )
        with pytest.raises(asyncio.CancelledError, match="database operation cancelled"):
            await repository.commit(prepared, None)
        assert rollback_required

    asyncio.run(scenario())


def test_issuance_save_cancellation_marks_rollback_and_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        rollback_required = False

        def mark_rollback_required() -> None:
            nonlocal rollback_required
            rollback_required = True

        monkeypatch.setattr(runtime_issuance_repository, "_row_for", lambda _record, _profile: {})
        repository = _PostgresIssuanceRepository(
            cast(AsyncConnection, _CancelledScalarConnection()),
            cast(_PostgresAuditEventRepository, object()),
            cast(_PostgresConfigEpochRepository, object()),
            cast(PostgresOperatorOverrideRepository, object()),
            cast(CompatibilityProfile, object()),
            cast(RuntimeIngressIssuanceStateProfile, object()),
            lambda: None,
            mark_rollback_required,
        )
        with pytest.raises(asyncio.CancelledError, match="database operation cancelled"):
            await repository.save(
                cast(
                    IssuedPlanRecord,
                    SimpleNamespace(
                        envelope=SimpleNamespace(
                            payload=SimpleNamespace(verified_plan_id=None),
                            expires_at=datetime(2026, 7, 15, 13, 0, tzinfo=UTC),
                        )
                    ),
                )
            )
        assert rollback_required

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("row", "expected_deletes"),
    [
        (None, 0),
        ({"is_current": False}, 1),
    ],
    ids=("physical-miss", "expired-row"),
)
def test_control_plane_session_lookup_mutates_only_a_proved_expired_row(
    monkeypatch: pytest.MonkeyPatch,
    row: Mapping[str, object] | None,
    expected_deletes: int,
) -> None:
    connection = _SessionLookupConnection(row)
    store = PostgresControlPlaneSessionStore(cast(AsyncEngine, object()))

    @asynccontextmanager
    async def transaction(*_args: object) -> AsyncIterator[AsyncConnection]:
        yield cast(AsyncConnection, connection)

    monkeypatch.setattr(control_plane_session_repository, "_identity_transaction", transaction)

    assert asyncio.run(store.load(b"x" * 32)) is None
    assert sum(isinstance(statement, Select) for statement in connection.statements) == 1
    assert sum(isinstance(statement, Delete) for statement in connection.statements) == (
        expected_deletes
    )
    assert len(connection.streamed) == expected_deletes
    if expected_deletes:
        assert connection.stream_result is not None and connection.stream_result.closed
    else:
        assert connection.stream_result is None
    for statement in connection.streamed:
        assert isinstance(statement, Select)
        assert "expires_at <= statement_timestamp()" in str(statement)
    for statement in connection.statements:
        if isinstance(statement, Delete):
            assert statement.compile().params == {"handle_digest_1": [b"x" * 32]}

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

import pytest
from sqlalchemy import Select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection

from ci_coordinator.audit_replay import (
    AuditAppendAppended,
    AuditAppendConflict,
    AuditAppendResult,
    AuditEventInput,
    PreparedAuditEvent,
    build_audit_event,
    build_prepared_audit_event,
)
from ci_coordinator.github_ingestion import (
    DeliveryClaimed,
    DeliveryDuplicate,
    DeliveryIdempotencyKey,
    DeliveryStoreUnavailable,
)
from ci_coordinator.github_ingestion.events import NormalizedWorkflowJobEvent
from ci_coordinator.github_ingestion.ports import PreparedDeliveryClaim, prepare_delivery_claim
from ci_coordinator.github_ingestion.provenance import GitHubRepository, WebhookProvenance
from ci_coordinator.kernel import sha256_hex
from ci_coordinator.persistence.errors import PersistenceInvariantViolation, StoreUnavailable
from ci_coordinator.persistence.runtime_delivery_repository import (
    _PostgresWebhookIngestionRepository,
)
from ci_coordinator.persistence.webhook_delivery_audit import (
    WebhookDeliveryAuditAppender,
    _PostgresWebhookDeliveryAuditRepository,
    prepare_existing_webhook_delivery_claim_audit,
    prepare_webhook_delivery_claim_audit,
)

_NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
_BODY = b'{"secret":"body-secret"}'
_BODY_SHA256 = sha256_hex(_BODY)


@dataclass
class _RollbackMarker:
    required: bool = False

    def __call__(self) -> None:
        self.required = True


class _ForbiddenConnection:
    async def scalar(self, _statement: object) -> object:
        raise AssertionError("connection must not be used")

    async def execute(self, _statement: object) -> object:
        raise AssertionError("connection must not be used")


class _InsertedConnection:
    async def scalar(self, _statement: object) -> object:
        return "sensitive-delivery-token"

    async def execute(self, _statement: object) -> object:
        raise AssertionError("inserted delivery must not be re-read")


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def one_or_none(self) -> object:
        return self._value

    def mappings(self) -> _ScalarResult:
        return self


class _ExistingConnection:
    def __init__(self, *, guard_available: bool = True) -> None:
        self._results = iter(((_BODY_SHA256,), None))
        self.guard_keys: list[str] = []
        self.guard_available = guard_available

    async def scalar(self, _statement: object) -> object:
        return None

    async def execute(
        self, statement: object, parameters: dict[str, str] | None = None
    ) -> _ScalarResult | tuple[tuple[str, bool], ...]:
        assert parameters is None
        if "pg_try_advisory_xact_lock" in str(statement):
            assert isinstance(statement, Select)
            keys = tuple(
                value
                for value in statement.compile().params.values()
                if isinstance(value, str) and value.startswith("ci-workflow-observation/v1:")
            )
            assert len(keys) == 1
            self.guard_keys.append(keys[0])
            return ((keys[0].removeprefix("ci-workflow-observation/v1:"), self.guard_available),)
        return _ScalarResult(next(self._results))


class _BodyReplayConnection(_ExistingConnection):
    def __init__(self) -> None:
        super().__init__()
        self._results = iter((None, ("original-delivery",), None))


class _SqlFailureConnection:
    async def scalar(self, _statement: object) -> object:
        raise SQLAlchemyError("sensitive database details")


class _AuditAppender:
    def __init__(
        self,
        outcome: Literal["appended", "conflict", "cancelled", "unavailable"],
    ) -> None:
        self._outcome = outcome
        self.events: list[PreparedAuditEvent] = []

    async def append_delivery_claim(self, event: PreparedAuditEvent) -> AuditAppendResult:
        self.events.append(event)
        if self._outcome == "cancelled":
            raise asyncio.CancelledError("audit append cancelled")
        if self._outcome == "unavailable":
            raise StoreUnavailable("sensitive audit storage details")
        if self._outcome == "conflict":
            existing = build_audit_event(
                AuditEventInput(
                    idempotency_key=event.idempotency_key,
                    subject_type="webhook-delivery",
                    subject_id="webhook-delivery:conflict",
                    event_type="webhook-delivery.conflict-fixture",
                    created_at=event.created_at,
                    actor="test",
                    payload={"outcome": "conflict"},
                ),
                None,
            )
            attempted = build_prepared_audit_event(event, None)
            return AuditAppendConflict(existing, attempted, "semantic conflict")
        return AuditAppendAppended(build_prepared_audit_event(event, None))


def test_audit_projection_is_redacted_and_replay_stable_across_verification_time() -> None:
    first_claim = _claim(_NOW)
    replay_claim = _claim(_NOW + timedelta(minutes=5))

    first = prepare_webhook_delivery_claim_audit(first_claim)
    replay = prepare_webhook_delivery_claim_audit(replay_claim)
    serialized = b"|".join(
        (
            first.idempotency_key.encode(),
            first.subject_id.encode(),
            first.payload_canonical_bytes,
        )
    )

    assert first.input_hash == replay.input_hash
    assert first.created_at != replay.created_at
    assert first.event_type == "webhook-delivery.claimed/v1"
    for forbidden in (
        b"sensitive-delivery-token",
        b"sensitive-event-name",
        b"sensitive-verifier-version",
        b"body-secret",
        _BODY_SHA256.encode(),
    ):
        assert forbidden not in serialized


def test_prepared_claim_cannot_be_forged_through_its_public_constructor() -> None:
    with pytest.raises(TypeError, match="cannot be constructed directly"):
        PreparedDeliveryClaim(
            object(),
            delivery_id="delivery",
            event_name="push",
            body_sha256="a" * 64,
            verified_at=_NOW,
            verifier_version="test-verifier/v1",
        )


def test_unprepared_key_fails_closed_before_sql_or_audit() -> None:
    async def scenario() -> None:
        rollback = _RollbackMarker()
        audit = _AuditAppender("appended")
        repository = _repository(_ForbiddenConnection(), audit, rollback)

        result = await repository.commit(
            cast(
                PreparedDeliveryClaim,
                DeliveryIdempotencyKey("sensitive-delivery-token", "a" * 64),
            ),
            None,
        )

        assert isinstance(result, DeliveryStoreUnavailable)
        assert rollback.required
        assert audit.events == []

    asyncio.run(scenario())


def test_observation_from_another_verified_webhook_fails_before_sql_or_audit() -> None:
    async def scenario() -> None:
        rollback = _RollbackMarker()
        audit = _AuditAppender("appended")
        repository = _repository(_ForbiddenConnection(), audit, rollback)
        provenance = WebhookProvenance(
            delivery_id="workflow-delivery",
            event_name="workflow_job",
            body_sha256="a" * 64,
            verified_at=_NOW,
            verifier_version="test-verifier/v1",
        )
        observation = _job_observation(replace(provenance, body_sha256="b" * 64))

        result = await repository.commit(prepare_delivery_claim(provenance), observation)

        assert isinstance(result, DeliveryStoreUnavailable)
        assert rollback.required
        assert audit.events == []

    asyncio.run(scenario())


def test_new_claim_appends_its_audit_before_reporting_success() -> None:
    async def scenario() -> None:
        rollback = _RollbackMarker()
        audit = _AuditAppender("appended")
        repository = _repository(_InsertedConnection(), audit, rollback)

        result = await repository.commit(_claim(_NOW), None)

        assert isinstance(result, DeliveryClaimed)
        assert result.key == DeliveryIdempotencyKey(
            "sensitive-delivery-token",
            _BODY_SHA256,
        )
        assert len(audit.events) == 1
        assert not rollback.required

    asyncio.run(scenario())


@pytest.mark.parametrize("guard_available", [False, True])
def test_exact_delivery_replay_repairs_a_missing_audit_counterpart(guard_available: bool) -> None:
    async def scenario() -> None:
        rollback = _RollbackMarker()
        audit = _AuditAppender("appended")
        connection = _ExistingConnection(guard_available=guard_available)
        repository = _repository(connection, audit, rollback)

        result = await repository.commit(_claim(_NOW + timedelta(minutes=1)), None)

        assert isinstance(
            result, DeliveryDuplicate if guard_available else DeliveryStoreUnavailable
        )
        assert len(audit.events) == 1
        assert connection.guard_keys == ["ci-workflow-observation/v1:sensitive-delivery-token"]
        assert rollback.required is not guard_available

    asyncio.run(scenario())


def test_same_body_under_a_new_delivery_id_repairs_the_original_audit_pair() -> None:
    async def scenario() -> None:
        rollback = _RollbackMarker()
        audit = _AuditAppender("appended")
        connection = _BodyReplayConnection()
        repository = _repository(connection, audit, rollback)
        claim = _claim(_NOW + timedelta(minutes=1))

        result = await repository.commit(claim, None)

        assert isinstance(result, DeliveryDuplicate)
        assert result.key == DeliveryIdempotencyKey(
            "sensitive-delivery-token",
            _BODY_SHA256,
        )
        assert len(audit.events) == 1
        assert connection.guard_keys == ["ci-workflow-observation/v1:original-delivery"]
        expected = prepare_existing_webhook_delivery_claim_audit(
            claim,
            delivery_id="original-delivery",
        )
        assert _prepared_event_observables(audit.events[0]) == (
            _prepared_event_observables(expected)
        )
        assert not rollback.required

    asyncio.run(scenario())


def _prepared_event_observables(event: PreparedAuditEvent) -> tuple[object, ...]:
    return (
        event.idempotency_key,
        event.installation_id,
        event.repository_id,
        event.subject_type,
        event.subject_id,
        event.event_type,
        event.created_at,
        event.actor,
        event.payload_canonical_bytes,
        event.payload_hash,
        event.input_hash,
    )


def test_audit_semantic_conflict_invalidates_the_whole_claim_transaction() -> None:
    async def scenario() -> None:
        rollback = _RollbackMarker()
        repository = _repository(
            _InsertedConnection(),
            _AuditAppender("conflict"),
            rollback,
        )

        result = await repository.commit(_claim(_NOW), None)

        assert isinstance(result, DeliveryStoreUnavailable)
        assert rollback.required

    asyncio.run(scenario())


def test_sql_failure_is_a_redacted_unavailable_result() -> None:
    async def scenario() -> None:
        rollback = _RollbackMarker()
        repository = _repository(
            _SqlFailureConnection(),
            _AuditAppender("appended"),
            rollback,
        )

        result = await repository.commit(_claim(_NOW), None)

        assert isinstance(result, DeliveryStoreUnavailable)
        assert "sensitive" not in repr(result)
        assert rollback.required

    asyncio.run(scenario())


def test_audit_store_failure_is_a_redacted_unavailable_result_and_rolls_back() -> None:
    async def scenario() -> None:
        rollback = _RollbackMarker()
        repository = _repository(
            _InsertedConnection(),
            _AuditAppender("unavailable"),
            rollback,
        )

        result = await repository.commit(_claim(_NOW), None)

        assert isinstance(result, DeliveryStoreUnavailable)
        assert "sensitive" not in repr(result)
        assert rollback.required

    asyncio.run(scenario())


def test_audit_cancellation_marks_rollback_and_propagates() -> None:
    async def scenario() -> None:
        rollback = _RollbackMarker()
        repository = _repository(
            _InsertedConnection(),
            _AuditAppender("cancelled"),
            rollback,
        )

        with pytest.raises(asyncio.CancelledError, match="audit append cancelled"):
            await repository.commit(_claim(_NOW), None)
        assert rollback.required

    asyncio.run(scenario())


def test_generic_audit_append_rejects_the_pair_owned_event() -> None:
    async def scenario() -> None:
        rollback = _RollbackMarker()
        repository = _PostgresWebhookDeliveryAuditRepository(
            cast(AsyncConnection, _ForbiddenConnection()),
            lambda: None,
            rollback,
        )

        with pytest.raises(
            PersistenceInvariantViolation,
            match="requires its owning state transition",
        ):
            await repository.append(prepare_webhook_delivery_claim_audit(_claim(_NOW)))
        assert rollback.required

    asyncio.run(scenario())


def _claim(verified_at: datetime) -> PreparedDeliveryClaim:
    return prepare_delivery_claim(
        WebhookProvenance(
            delivery_id="sensitive-delivery-token",
            event_name="sensitive-event-name",
            body_sha256=_BODY_SHA256,
            verified_at=verified_at,
            verifier_version="sensitive-verifier-version",
        )
    )


def _repository(
    connection: object,
    audit: _AuditAppender,
    rollback: _RollbackMarker,
) -> _PostgresWebhookIngestionRepository:
    return _PostgresWebhookIngestionRepository(
        cast(AsyncConnection, connection),
        cast(WebhookDeliveryAuditAppender, audit),
        lambda: None,
        rollback,
    )


def _job_observation(provenance: WebhookProvenance) -> NormalizedWorkflowJobEvent:
    return NormalizedWorkflowJobEvent(
        provenance=provenance,
        repository=GitHubRepository(101, 202, "acme", "service"),
        action="completed",
        workflow_run_id=303,
        run_attempt=2,
        head_sha="b" * 40,
        workflow_job_id=404,
        job_name="tests",
        status="completed",
        conclusion="success",
        created_at=_NOW,
        started_at=_NOW,
        completed_at=_NOW,
        labels=("linux",),
        runner=None,
    )

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from ci_economics.factories import ATTEMPT
from ci_economics.report_ingestion_support import (
    BINDING,
    IDENTITY,
    REPORT,
    SOURCE,
    IngestionBoundary,
)

from ci_coordinator.ci_economics.model import AttemptIdentity
from ci_coordinator.ci_economics.ports import (
    CiEconomicsStoreUnavailable,
    MeasurementReportWriteResult,
    ProviderAttemptDeferred,
)
from ci_coordinator.ci_economics.report_ingestion import ProviderReportJobBinding
from ci_coordinator.ci_economics.reports import MeasurementReportOrigin
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.identity_admission import TrustedActionsRun


@pytest.mark.parametrize(
    "result",
    [
        "recorded",
        "replayed",
        "report_conflict",
        "source_unavailable",
        "outside_retention",
        "capacity_reached",
    ],
)
async def test_admitted_report_preserves_every_durable_outcome(
    result: MeasurementReportWriteResult,
) -> None:
    boundary = IngestionBoundary(result=result)
    assert await boundary.service().record_report(REPORT, IDENTITY, page_number=2) == result
    assert boundary.calls == [
        ("resolve", (ATTEMPT.scope, 303, 2)),
        ("bind", (REPORT, "acme/service", 2)),
        ("write", (SOURCE, REPORT, MeasurementReportOrigin("a" * 64, "f" * 64))),
    ]
    assert IDENTITY.execution_sha != REPORT.attempt.head_sha


@pytest.mark.parametrize(
    "identity",
    [
        replace(IDENTITY, repository_id=999),
        replace(IDENTITY, run_id=999),
        replace(IDENTITY, run_attempt=3),
        replace(IDENTITY, check_run_id="404"),
        replace(IDENTITY, check_run_id=None),
        replace(IDENTITY, check_run_id="0405"),
        replace(IDENTITY, claim_hash=None),
        replace(IDENTITY, claim_hash="A" * 64),
    ],
)
async def test_claim_mismatch_is_rejected_before_any_io(identity: TrustedActionsRun) -> None:
    boundary = IngestionBoundary()
    assert await boundary.service().record_report(REPORT, identity, page_number=1) == "forbidden"
    assert not boundary.calls


@pytest.mark.parametrize("scopes", [frozenset(), frozenset({RepositoryScope(102, 202)})])
async def test_scope_authority_is_required_before_provider_or_store(
    scopes: frozenset[RepositoryScope],
) -> None:
    boundary = IngestionBoundary()
    service = boundary.service(allowed_scopes=scopes)
    assert await service.record_report(REPORT, IDENTITY, page_number=1) == "forbidden"
    assert not boundary.calls


@pytest.mark.parametrize(
    "attempt",
    [
        replace(ATTEMPT, scope=RepositoryScope(102, 202)),
        replace(ATTEMPT, scope=RepositoryScope(101, 203)),
        replace(ATTEMPT, workflow_run_id=304),
        replace(ATTEMPT, run_attempt=3),
        replace(ATTEMPT, head_sha="c" * 40),
    ],
)
async def test_source_mismatch_prevents_job_lookup(attempt: AttemptIdentity) -> None:
    boundary = IngestionBoundary(source=replace(SOURCE, attempt=attempt))
    assert await boundary.service().record_report(REPORT, IDENTITY, page_number=1) == "unavailable"
    assert [name for name, _ in boundary.calls] == ["resolve"]


@pytest.mark.parametrize(
    "binding",
    [
        replace(BINDING, attempt=replace(ATTEMPT, scope=RepositoryScope(102, 202))),
        replace(BINDING, attempt=replace(ATTEMPT, scope=RepositoryScope(101, 203))),
        replace(BINDING, attempt=replace(ATTEMPT, workflow_run_id=304)),
        replace(BINDING, attempt=replace(ATTEMPT, run_attempt=3)),
        replace(BINDING, attempt=replace(ATTEMPT, head_sha="c" * 40)),
        replace(BINDING, repository="acme/other"),
        replace(BINDING, provider_job_id=999),
        replace(BINDING, check_run_id=999),
        ProviderAttemptDeferred("provider_incomplete"),
        ProviderAttemptDeferred("provider_binding_mismatch"),
    ],
)
async def test_provider_binding_is_rebound_before_storage(
    binding: ProviderReportJobBinding | ProviderAttemptDeferred,
) -> None:
    boundary = IngestionBoundary(binding=binding)
    assert await boundary.service().record_report(REPORT, IDENTITY, page_number=1) == "unavailable"
    assert [name for name, _ in boundary.calls] == ["resolve", "bind"]


@pytest.mark.parametrize("field", ["source", "binding", "result"])
async def test_cancellation_and_programmer_failure_are_not_hidden(field: str) -> None:
    for error in (asyncio.CancelledError(), RuntimeError("bug")):
        boundary = IngestionBoundary()
        setattr(boundary, field, error)
        with pytest.raises(type(error)):
            await boundary.service().record_report(REPORT, IDENTITY, page_number=1)


async def test_unavailable_store_is_closed_without_retry_or_registration() -> None:
    boundary = IngestionBoundary(result=CiEconomicsStoreUnavailable())
    assert await boundary.service().record_report(REPORT, IDENTITY, page_number=1) == "unavailable"
    assert [name for name, _ in boundary.calls] == ["resolve", "bind", "write"]


@pytest.mark.parametrize("page", [0, 21, True])
async def test_invalid_page_never_reaches_provider(page: int) -> None:
    boundary = IngestionBoundary()
    with pytest.raises(ValueError):
        await boundary.service().record_report(REPORT, IDENTITY, page_number=page)
    assert not boundary.calls

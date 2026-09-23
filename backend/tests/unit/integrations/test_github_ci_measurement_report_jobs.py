from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from ci_economics.report_factories import measurement_report
from ci_economics.report_ingestion_support import IDENTITY, IngestionBoundary

from ci_coordinator.app.ci_measurement_report_ingestion import MeasurementReportIngestionService
from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.ci_economics.report_ingestion import ProviderReportJobBinding
from ci_coordinator.integrations.github.ci_measurement_report_jobs import (
    GitHubMeasurementReportJobs,
)
from ci_coordinator.integrations.github.contracts import GitHubTransportFailure

from ._economics_source_support import ATTEMPT, PATH, RUN_ID, SCOPE, Provider, repository, response

REPORT = measurement_report()


def _job() -> dict[str, object]:
    return {
        "id": REPORT.provider_job_id,
        "run_id": RUN_ID,
        "head_sha": REPORT.attempt.head_sha,
        "check_run_url": f"https://api.github.com/repos/acme/service/check-runs/{REPORT.check_run_id}",
        "status": "in_progress",
    }


def _bind(
    provider: Provider, *, page: int = 1
) -> ProviderReportJobBinding | ProviderAttemptDeferred:
    return asyncio.run(
        GitHubMeasurementReportJobs(provider).bind_job(
            REPORT, repository="acme/service", page_number=page
        )
    )


@pytest.mark.parametrize("page", [1, 2, 20])
def test_exact_attempt_membership_needs_one_page_not_job_completion(page: int) -> None:
    provider = Provider(
        [response(repository()), response({"total_count": (page - 1) * 100 + 1, "jobs": [_job()]})]
    )
    result = _bind(provider, page=page)
    assert isinstance(result, ProviderReportJobBinding)
    assert result.attempt == REPORT.attempt
    assert result.repository == "acme/service"
    assert (result.provider_job_id, result.check_run_id) == (404, 405)
    assert len(result.evidence_digest) == 64
    assert provider.installations == [SCOPE.installation_id]
    assert [request.path for request in provider.requests] == ["/repositories/202", PATH + "/jobs"]
    assert [(pair.name, pair.value) for pair in provider.requests[1].query] == [
        ("page", str(page)),
        ("per_page", "100"),
    ]
    assert REPORT.attempt.run_attempt == ATTEMPT


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("run_id", RUN_ID + 1),
        ("run_id", True),
        ("head_sha", "c" * 40),
        ("check_run_url", "https://api.github.com/repos/acme/service/check-runs/404"),
        ("check_run_url", "https://api.github.com/repos/acme/other/check-runs/405"),
        ("check_run_url", "https://example.org/repos/acme/service/check-runs/405"),
        ("check_run_url", "https://api.github.com/repos/acme/service/check-runs/405?x=1"),
    ],
)
def test_every_selected_job_binding_operand_is_required(field: str, value: object) -> None:
    job = {**_job(), field: value}
    provider = Provider([response(repository()), response({"total_count": 1, "jobs": [job]})])
    assert _bind(provider) == ProviderAttemptDeferred("provider_binding_mismatch")


@pytest.mark.parametrize(
    ("page", "body", "reason"),
    [
        (1, {}, "provider_malformed"),
        (1, {"total_count": True, "jobs": []}, "provider_malformed"),
        (1, {"total_count": 0, "jobs": []}, "provider_incomplete"),
        (1, {"total_count": 1, "jobs": [{"id": 999}]}, "provider_incomplete"),
        (1, {"total_count": 1, "jobs": [{"id": True}]}, "provider_malformed"),
        (1, {"total_count": 1, "jobs": [None]}, "provider_malformed"),
        (1, {"total_count": 0, "jobs": [_job()]}, "provider_malformed"),
        (2, {"total_count": 100, "jobs": [_job()]}, "provider_malformed"),
        (1, {"total_count": 2, "jobs": [_job(), _job()]}, "provider_malformed"),
        (1, {"total_count": 101, "jobs": [_job()] * 101}, "provider_malformed"),
    ],
)
def test_missing_and_contradictory_pages_never_bind(page: int, body: object, reason: str) -> None:
    provider = Provider([response(repository()), response(body)])
    result = _bind(provider, page=page)
    assert isinstance(result, ProviderAttemptDeferred)
    assert result.reason == reason


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("shared_check_run", [False, True])
def test_check_run_membership_is_unambiguous_across_distinct_job_ids(
    reverse: bool, shared_check_run: bool
) -> None:
    other = {**_job(), "id": REPORT.provider_job_id + 2}
    if not shared_check_run:
        other["check_run_url"] = "https://api.github.com/repos/acme/service/check-runs/999"
    jobs = [other, _job()] if reverse else [_job(), other]
    provider = Provider([response(repository()), response({"total_count": 2, "jobs": jobs})])
    result = _bind(provider)
    if shared_check_run:
        assert result == ProviderAttemptDeferred("provider_binding_mismatch")
    else:
        assert isinstance(result, ProviderReportJobBinding)
        assert result.provider_job_id == REPORT.provider_job_id
    assert len(provider.requests) == 2


@pytest.mark.parametrize("field,value", [("id", 999), ("full_name", "acme/other")])
def test_repository_resolution_cannot_be_relabelled(field: str, value: object) -> None:
    provider = Provider([response({**repository(), field: value})])
    assert isinstance(_bind(provider), ProviderAttemptDeferred)
    assert len(provider.requests) == 1


@pytest.mark.parametrize("job_id", [404, 406])
async def test_ambiguous_provider_page_never_reaches_the_report_store(job_id: int) -> None:
    provider = Provider(
        [
            response(repository()),
            response({"total_count": 2, "jobs": [_job(), {**_job(), "id": 406}]}),
        ]
    )
    boundary = IngestionBoundary()
    service = MeasurementReportIngestionService(
        allowed_scopes=frozenset({SCOPE}),
        sources=boundary,
        jobs=GitHubMeasurementReportJobs(provider),
        store=boundary,
    )
    assert (
        await service.record_report(
            replace(REPORT, provider_job_id=job_id), IDENTITY, page_number=1
        )
        == "unavailable"
    )
    assert [name for name, _ in boundary.calls] == ["resolve"]
    assert len(provider.requests) == 2


@pytest.mark.parametrize("page", [0, 21, True])
def test_invalid_page_is_rejected_before_provider_access(page: int) -> None:
    provider = Provider([])
    with pytest.raises(ValueError):
        _bind(provider, page=page)
    assert not provider.requests and not provider.installations


@pytest.mark.parametrize("first", [True, False])
def test_failure_and_cancellation_do_not_become_positive_membership(first: bool) -> None:
    prefix = [] if first else [response(repository())]
    provider = Provider([*prefix, GitHubTransportFailure("unavailable", "offline")])
    assert _bind(provider) == ProviderAttemptDeferred("provider_unavailable")
    with pytest.raises(asyncio.CancelledError):
        _bind(Provider([*prefix, asyncio.CancelledError()]))


@pytest.mark.parametrize(
    "body",
    [b'{"jobs":[],"jobs":[]}', b"[" * 200, b"x" * 1_048_577],
    ids=("duplicate-key", "excessive-nesting", "oversized-body"),
)
def test_raw_page_admission_precedes_membership(body: bytes) -> None:
    provider = Provider([response(repository()), replace(response({}), body=body)])
    assert _bind(provider) == ProviderAttemptDeferred("provider_malformed")

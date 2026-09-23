import asyncio
import json
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import cast
from weakref import ReferenceType, ref

import pytest

from ci_coordinator.app.ci_history_collection import (
    HISTORY_LANES,
    CiHistoryCollectionService,
    HistoryLane,
)
from ci_coordinator.ci_economics.archive_detail import (
    MAX_HISTORY_DETAIL_BYTES,
    ArchivedAttemptDetail,
    ArchivedJobDetail,
    encode_archive_detail,
)
from ci_coordinator.ci_economics.archive_encoding import (
    MAX_HISTORY_HEADER_BYTES,
    encode_archive_job,
)
from ci_coordinator.ci_economics.archive_statistics import ArchivedAttemptStatistics
from ci_coordinator.ci_economics.history_attempts import HistoryAttemptCursor
from ci_coordinator.ci_economics.history_ports import (
    HistoryAttemptNotFound,
    HistoryAttemptObservation,
    HistoryAttemptResult,
)
from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.integrations.github import ci_history_provider
from ci_coordinator.integrations.github.ci_history_decoding import (
    HistoryJobPage,
    decode_history_job_page,
)
from ci_coordinator.integrations.github.ci_history_detail_decoding import (
    HistoryDetailPage,
    decode_history_detail_page,
)
from ci_coordinator.integrations.github.ci_history_provider import GitHubHistoryProvider
from ci_coordinator.integrations.github.contracts import GitHubTransportFailure

from ._economics_source_support import PATH, SCOPE, Provider, repository, response
from ._history_provider_support import (
    CURSOR,
    JOBS_PATH,
    RUN_CREATED_AT,
    history_job,
    history_run,
    job_page,
)


def _load(provider: Provider) -> HistoryAttemptResult:
    return asyncio.run(
        GitHubHistoryProvider(provider).load_history_attempt(CURSOR, run_created_at=RUN_CREATED_AT)
    )


def _detail_step(number: int = 1, **overrides: object) -> dict[str, object]:
    return {
        "number": number,
        "status": "completed",
        "conclusion": "success",
        "started_at": None,
        "completed_at": None,
        **overrides,
    }


@pytest.mark.parametrize("terminal_count,active_count", [(0, 0), (1, 0), (0, 3), (2, 3)])
def test_completed_attempt_keeps_only_terminal_child_facts(
    terminal_count: int, active_count: int
) -> None:
    jobs = [history_job(index + 1) for index in range(terminal_count)]
    jobs.extend(
        {**history_job(terminal_count + index + 1), "status": "queued", "conclusion": None}
        for index in range(active_count)
    )
    provider = Provider(
        [response(repository()), response(history_run()), job_page(jobs), response(history_run())]
    )
    result = _load(provider)
    assert isinstance(result, ArchivedAttemptStatistics)
    assert result.population == ("partial" if active_count else "complete")
    assert result.provider_job_total == terminal_count + active_count
    assert tuple(job.provider_job_id for job in result.jobs) == tuple(range(1, terminal_count + 1))
    assert result.attempt.to_attempt().scope == SCOPE
    assert result.run_created_at == RUN_CREATED_AT
    assert len(provider.requests) == 4 and not provider.responses


def test_valid_complete_steps_return_a_bounded_optional_detail_sidecar() -> None:
    job = {
        **history_job(),
        "steps": [
            _detail_step(
                started_at="2026-09-07T12:00:04Z",
                completed_at="2026-09-07T12:00:01Z",
                name="private-step-name",
                unknown="discarded",
            )
        ],
    }
    provider = Provider(
        [response(repository()), response(history_run()), job_page([job]), response(history_run())]
    )
    result = _load(provider)
    assert isinstance(result, HistoryAttemptObservation)
    assert result.detail is not None
    assert result.detail.jobs[0].provider_job_id == 1
    step = result.detail.jobs[0].steps[0]
    assert step.number == 1
    assert step.started_at is not None and step.completed_at is not None
    assert step.started_at > step.completed_at
    encoded = encode_archive_detail(result.detail)
    assert len(encoded) <= 262_144
    assert b"private-step-name" not in encoded and b"unknown" not in encoded
    assert len(provider.requests) == 4 and not provider.responses


def test_explicit_empty_steps_are_distinct_from_missing_steps() -> None:
    provider = Provider(
        [
            response(repository()),
            response(history_run()),
            job_page([{**history_job(), "steps": []}]),
            response(history_run()),
        ]
    )
    result = _load(provider)
    assert isinstance(result, HistoryAttemptObservation)
    assert result.detail is not None and result.detail.jobs[0].steps == ()

    missing = history_job()
    del missing["steps"]
    provider = Provider(
        [
            response(repository()),
            response(history_run()),
            job_page([missing]),
            response(history_run()),
        ]
    )
    assert isinstance(_load(provider), ArchivedAttemptStatistics)


@pytest.mark.parametrize(
    "job_ids",
    [(1, 2, 3), (3, 1, 2), (3, 2, 1), (*range(2, 102), 1)],
    ids=["ordered", "permuted", "reversed", "cross-page"],
)
def test_complete_detail_is_independent_of_provider_job_order(job_ids: tuple[int, ...]) -> None:
    jobs = [{**history_job(job_id), "steps": [_detail_step(number=job_id)]} for job_id in job_ids]
    pages = [
        job_page(jobs[offset : offset + 100], total=len(jobs), number=offset // 100 + 1)
        for offset in range(0, len(jobs), 100)
    ]
    provider = Provider(
        [response(repository()), response(history_run()), *pages, response(history_run())]
    )
    result = _load(provider)
    assert isinstance(result, HistoryAttemptObservation) and result.detail is not None
    expected = tuple(range(1, len(job_ids) + 1))
    assert tuple(job.provider_job_id for job in result.statistics.jobs) == expected
    assert tuple((job.provider_job_id, job.steps[0].number) for job in result.detail.jobs) == tuple(
        (job_id, job_id) for job_id in expected
    )
    assert len(provider.requests) == 3 + len(pages) and not provider.responses


def test_detail_normalization_does_not_remove_duplicate_job_evidence() -> None:
    job = {**history_job(), "steps": [_detail_step()]}
    provider = Provider([response(repository()), response(history_run()), job_page([job, job])])
    assert _load(provider) == ProviderAttemptDeferred("provider_unstable")
    assert len(provider.requests) == 3 and not provider.responses


@pytest.mark.parametrize(
    "steps",
    [
        [_detail_step(status="queued")],
        [_detail_step(number=True)],
        [_detail_step(number=2), _detail_step(number=1)],
        [_detail_step(number=1), _detail_step(number=1)],
        [_detail_step(number=index) for index in range(1, 258)],
    ],
)
def test_invalid_optional_detail_keeps_independently_valid_statistics(
    steps: list[dict[str, object]],
) -> None:
    job = {**history_job(), "steps": steps}
    provider = Provider(
        [response(repository()), response(history_run()), job_page([job]), response(history_run())]
    )
    result = _load(provider)
    assert isinstance(result, ArchivedAttemptStatistics)
    assert result.population == "complete"


def test_partial_job_population_never_returns_detail_sidecar() -> None:
    terminal = {**history_job(), "steps": [_detail_step()]}
    active = {**history_job(2), "status": "queued", "conclusion": None}
    provider = Provider(
        [
            response(repository()),
            response(history_run()),
            job_page([terminal, active], total=2),
            response(history_run()),
        ]
    )
    result = _load(provider)
    assert isinstance(result, ArchivedAttemptStatistics)
    assert result.population == "partial"


def test_detail_byte_cap_keeps_independently_valid_statistics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ci_coordinator.ci_economics.archive_detail.MAX_HISTORY_DETAIL_BYTES", 1)
    job = {**history_job(), "steps": [_detail_step()]}
    provider = Provider(
        [response(repository()), response(history_run()), job_page([job]), response(history_run())]
    )
    result = _load(provider)
    assert isinstance(result, ArchivedAttemptStatistics)
    assert result.population == "complete"


def test_detail_cap_releases_prior_pages_and_skips_later_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detail_calls: list[int] = []
    retained: list[ReferenceType[ArchivedJobDetail]] = []
    statistics_pages = 0

    def detail_page(
        body: bytes, cursor: HistoryAttemptCursor, *, head_sha: str, remaining_bytes: int
    ) -> HistoryDetailPage | None:
        detail_calls.append(remaining_bytes)
        assert len(detail_calls) <= 2
        decoded = decode_history_detail_page(
            body, cursor, head_sha=head_sha, remaining_bytes=remaining_bytes
        )
        if len(detail_calls) == 1:
            assert decoded is not None and len(decoded.jobs) == 100
            retained.extend(ref(job) for job in decoded.jobs)
        else:
            assert decoded is None
        return decoded

    def statistics_page(
        body: bytes, cursor: HistoryAttemptCursor, *, head_sha: str
    ) -> HistoryJobPage | ProviderAttemptDeferred:
        nonlocal statistics_pages
        statistics_pages += 1
        if statistics_pages == 3:
            assert retained and all(job() is None for job in retained)
        return decode_history_job_page(body, cursor, head_sha=head_sha)

    monkeypatch.setattr(ci_history_provider, "decode_history_detail_page", detail_page)
    monkeypatch.setattr(ci_history_provider, "decode_history_job_page", statistics_page)
    pages = [
        job_page(
            [
                {
                    **history_job(job_id),
                    "steps": [_detail_step(number) for number in range(1, step_count + 1)],
                }
                for job_id in range(first, last)
            ],
            total=201,
            number=page_number,
        )
        for page_number, first, last, step_count in (
            (1, 1, 101, 10),
            (2, 101, 201, 20),
            (3, 201, 202, 1),
        )
    ]
    provider = Provider(
        [response(repository()), response(history_run()), *pages, response(history_run())]
    )
    result = _load(provider)
    assert isinstance(result, ArchivedAttemptStatistics)
    assert result.population == "complete" and result.provider_job_total == 201
    assert tuple(job.provider_job_id for job in result.jobs) == tuple(range(1, 202))
    assert all(job.started_at == RUN_CREATED_AT + timedelta(seconds=1) for job in result.jobs)
    assert statistics_pages == 3 and len(detail_calls) == 2
    assert 0 < detail_calls[1] < detail_calls[0] < MAX_HISTORY_DETAIL_BYTES
    assert [request.path for request in provider.requests] == [
        "/repositories/202",
        PATH,
        JOBS_PATH,
        JOBS_PATH,
        JOBS_PATH,
        PATH,
    ]
    assert not provider.responses


@pytest.mark.parametrize("margin", [-1, 0, 1])
def test_incremental_detail_budget_counts_envelope_and_cross_page_separators(
    monkeypatch: pytest.MonkeyPatch, margin: int
) -> None:
    expected = ArchivedAttemptDetail.model_validate(
        {
            "schemaVersion": "ci-economics-archive-detail/v1",
            "attempt": {
                "installationId": SCOPE.installation_id,
                "repositoryId": SCOPE.repository_id,
                "workflowRunId": CURSOR.workflow_run_id,
                "runAttempt": CURSOR.next_attempt,
                "headSha": "b" * 40,
            },
            "jobs": tuple({"providerJobId": job_id, "steps": ()} for job_id in range(1, 102)),
        }
    )
    expected_bytes = encode_archive_detail(expected)
    monkeypatch.setattr(
        ci_history_provider, "MAX_HISTORY_DETAIL_BYTES", len(expected_bytes) + margin
    )
    provider = Provider(
        [
            response(repository()),
            response(history_run()),
            job_page([{**history_job(job_id), "steps": []} for job_id in range(1, 101)], total=101),
            job_page([{**history_job(101), "steps": []}], total=101, number=2),
            response(history_run()),
        ]
    )
    result = _load(provider)
    if margin < 0:
        assert isinstance(result, ArchivedAttemptStatistics)
        assert result.population == "complete" and len(result.jobs) == 101
    else:
        assert isinstance(result, HistoryAttemptObservation) and result.detail is not None
        assert encode_archive_detail(result.detail) == expected_bytes
    assert len(provider.requests) == 5 and not provider.responses


@pytest.mark.parametrize("repeat_skipped_id", [False, True])
def test_full_active_page_keeps_pagination_and_cross_page_identity(
    repeat_skipped_id: bool,
) -> None:
    active = [
        {**history_job(index + 1), "status": "queued", "conclusion": None} for index in range(100)
    ]
    last = history_job(1 if repeat_skipped_id else 101)
    provider = Provider(
        [
            response(repository()),
            response(history_run()),
            job_page(active, total=101),
            job_page([last], total=101, number=2),
            response(history_run()),
        ]
    )
    result = _load(provider)
    if repeat_skipped_id:
        assert result == ProviderAttemptDeferred("provider_unstable")
        assert len(provider.requests) == 4 and len(provider.responses) == 1
    else:
        assert isinstance(result, ArchivedAttemptStatistics)
        assert result.population == "partial" and result.provider_job_total == 101
        assert tuple(job.provider_job_id for job in result.jobs) == (101,)
        assert len(provider.requests) == 5 and not provider.responses


@pytest.mark.parametrize("second_active", [False, True])
def test_duplicate_active_identity_within_page_cannot_be_hidden(second_active: bool) -> None:
    active = {**history_job(), "status": "queued", "conclusion": None}
    provider = Provider(
        [
            response(repository()),
            response(history_run()),
            job_page([active, active if second_active else history_job()]),
            response(history_run()),
        ]
    )
    assert _load(provider) == ProviderAttemptDeferred("provider_unstable")
    assert len(provider.requests) == 3 and len(provider.responses) == 1


@pytest.mark.parametrize("field,value", [("id", True), ("run_id", 999), ("run_attempt", 1)])
def test_partial_capture_does_not_bypass_active_child_identity(field: str, value: object) -> None:
    active = {**history_job(), "status": "queued", "conclusion": None, field: value}
    provider = Provider(
        [
            response(repository()),
            response(history_run()),
            job_page([active]),
            response(history_run()),
        ]
    )
    result = _load(provider)
    assert result == ProviderAttemptDeferred(
        "provider_binding_mismatch" if field == "run_attempt" else "provider_malformed"
    )
    assert len(provider.requests) == 3 and len(provider.responses) == 1


@pytest.mark.parametrize("delay", [timedelta(0), timedelta(seconds=1), timedelta(days=30)])
def test_archive_uses_run_creation_without_equating_it_to_attempt_creation(
    delay: timedelta,
) -> None:
    header = {
        **history_run(),
        "created_at": (RUN_CREATED_AT + delay).isoformat().replace("+00:00", "Z"),
    }
    provider = Provider(
        [response(repository()), response(header), job_page([history_job()]), response(header)]
    )
    result = _load(provider)
    assert isinstance(result, ArchivedAttemptStatistics)
    assert result.run_created_at == RUN_CREATED_AT
    assert result.attempt.run_attempt == CURSOR.next_attempt
    assert result.jobs[0].started_at == RUN_CREATED_AT + timedelta(seconds=1)
    assert len(provider.requests) == 4 and not provider.responses


@pytest.mark.parametrize("created_at", [None, True, "2026-09-07T12:00:00Z", datetime(2026, 9, 7)])
def test_invalid_run_source_time_is_rejected_before_provider_io(created_at: object) -> None:
    provider = Provider([])
    with pytest.raises(ValueError):
        asyncio.run(
            GitHubHistoryProvider(provider).load_history_attempt(
                CURSOR, run_created_at=cast(datetime, created_at)
            )
        )
    assert provider.requests == [] and provider.installations == []


def test_equivalent_source_offset_is_normalized_without_changing_the_instant() -> None:
    provider = Provider(
        [response(repository()), response(history_run()), job_page([]), response(history_run())]
    )
    result = asyncio.run(
        GitHubHistoryProvider(provider).load_history_attempt(
            CURSOR, run_created_at=RUN_CREATED_AT.astimezone(timezone(timedelta(hours=2)))
        )
    )
    assert isinstance(result, ArchivedAttemptStatistics)
    assert result.run_created_at.isoformat() == RUN_CREATED_AT.isoformat()


@pytest.mark.parametrize("lane", HISTORY_LANES)
@pytest.mark.parametrize("partial_runner", [False, True])
async def test_real_adapter_and_application_preserve_source_time_for_every_lane(
    lane: HistoryLane,
    partial_runner: bool,
) -> None:
    from app._history_collection_support import STATISTICS, WORKER, CollectionBoundary

    boundary = CollectionBoundary.for_lane(lane)
    created = STATISTICS.run_created_at
    header = {
        **history_run(),
        "run_attempt": 1,
        "head_sha": "a" * 40,
        "created_at": (created + timedelta(seconds=1)).isoformat().replace("+00:00", "Z"),
    }
    job = {**history_job(), "run_attempt": 1, "head_sha": "a" * 40}
    if partial_runner:
        job.update(runner_group_id=None, runner_group_name="GitHub Actions")
    provider = Provider(
        [response(repository()), response(header), job_page([job]), response(header)]
    )
    service = CiHistoryCollectionService(
        store=boundary,
        discovery=boundary,
        attempts=GitHubHistoryProvider(provider),
        repository_access=boundary,
        worker_id=WORKER,
        metrics=boundary.metrics,
    )
    assert (await service.run(asyncio.Event()))[HISTORY_LANES.index(lane)] == "applied"
    writes = [
        value for stage, value in boundary.calls if stage in {"statistics", "recheck_statistics"}
    ]
    assert len(writes) == 1
    _, stored = cast(tuple[object, ArchivedAttemptStatistics], writes[0])
    assert stored.run_created_at == created and stored.attempt.run_attempt == 1
    assert stored.population == "complete" and len(stored.jobs) == 1
    assert stored.jobs[0].runner_id == 50
    assert stored.jobs[0].runner_group_id == (None if partial_runner else 60)
    assert boundary.count("provider_results", lane, "complete") == 1
    assert len(provider.requests) == 4 and not provider.responses


def test_partial_rerun_runner_metadata_preserves_inconsistent_timing() -> None:
    job = {
        **history_job(),
        "runner_group_id": None,
        "runner_group_name": "GitHub Actions",
        "created_at": "2026-09-07T12:10:00Z",
    }
    provider = Provider(
        [response(repository()), response(history_run()), job_page([job]), response(history_run())]
    )
    result = _load(provider)
    assert isinstance(result, ArchivedAttemptStatistics)
    assert result.population == "complete" and result.provider_job_total == 1
    archived = result.jobs[0]
    assert archived.runner_id == 50 and archived.runner_group_id is None
    assert archived.timing is None and archived.timing_quality == "inconsistent"
    assert archived.created_at == RUN_CREATED_AT + timedelta(minutes=10)
    assert archived.completed_at == RUN_CREATED_AT + timedelta(seconds=4)
    assert "GitHub Actions" not in result.model_dump_json()
    assert len(provider.requests) == 4 and not provider.responses


@pytest.mark.parametrize(
    "count,numeric",
    [(0, False), (1, False), (101, False), (101, True), (2_000, False), (2_001, False)],
)
def test_attempt_collection_is_bounded_and_distinguishes_complete_empty_and_partial(
    count: int,
    numeric: bool,
) -> None:
    pages = [
        job_page(
            [history_job(index + 1) for index in range(start, min(start + 100, count))],
            total=count,
            number=start // 100 + 1,
        )
        for start in range(0, min(count, 2_000), 100)
    ] or [job_page([])]
    if numeric:
        pages = [
            replace(
                page,
                pagination=replace(
                    page.pagination,
                    next_page=page.pagination.next_page.replace(
                        "/repos/acme/service/", "/repositories/202/"
                    ),
                ),
            )
            if page.pagination.next_page
            else page
            for page in pages
        ]
    provider = Provider(
        [response(repository()), response(history_run()), *pages, response(history_run())]
    )
    result = _load(provider)
    assert isinstance(result, ArchivedAttemptStatistics)
    assert result.population == ("partial" if count > 2_000 else "complete")
    assert result.provider_job_total == count and len(result.jobs) == min(count, 2_000)
    assert provider.installations == [SCOPE.installation_id]
    assert [request.path for request in provider.requests] == [
        "/repositories/202",
        PATH,
        *[JOBS_PATH for _ in pages],
        PATH,
    ]
    assert all(request.method == "GET" and request.body is None for request in provider.requests)
    assert len(provider.requests) <= 23 and provider.responses == []
    assert "private-" not in result.model_dump_json()


@pytest.mark.parametrize("edge", ["repository", "attempt", "jobs"])
def test_404_meaning_depends_on_its_exact_edge(edge: str) -> None:
    missing = replace(response({"message": "Not Found"}), status=404)
    if edge == "repository":
        provider = Provider([missing])
    elif edge == "attempt":
        provider = Provider([response(repository()), missing])
    else:
        provider = Provider(
            [response(repository()), response(history_run()), missing, response(history_run())]
        )
    result = _load(provider)
    if edge == "repository":
        assert result == ProviderAttemptDeferred("provider_unavailable")
    elif edge == "attempt":
        assert result == HistoryAttemptNotFound(CURSOR)
    else:
        assert isinstance(result, ArchivedAttemptStatistics)
        assert (
            result.population == "unavailable"
            and result.provider_job_total is None
            and result.jobs == ()
        )
    assert not provider.responses


@pytest.mark.parametrize("conclusion", [None, "failure"])
def test_two_terminal_headers_preserve_unknown_conclusion_in_complete_empty_population(
    conclusion: str | None,
) -> None:
    header = {**history_run(), "conclusion": conclusion}
    provider = Provider(
        [
            response(repository()),
            response(header),
            job_page([]),
            response(header),
        ]
    )
    result = _load(provider)
    assert isinstance(result, ArchivedAttemptStatistics)
    assert result.conclusion == conclusion and result.population == "complete"
    assert result.provider_job_total == 0 and result.jobs == ()
    assert not provider.responses


@pytest.mark.parametrize("first,second", [(None, "failure"), ("failure", None)])
def test_changed_knownness_between_terminal_headers_is_unstable(
    first: str | None,
    second: str | None,
) -> None:
    provider = Provider(
        [
            response(repository()),
            response({**history_run(), "conclusion": first}),
            job_page([]),
            response({**history_run(), "conclusion": second}),
        ]
    )
    assert _load(provider) == ProviderAttemptDeferred("provider_unstable")
    assert not provider.responses


@pytest.mark.parametrize(
    "field,value",
    [
        ("conclusion", "success"),
        ("workflow_id", 405),
        ("path", ".github/workflows/changed.yml"),
        ("head_sha", "c" * 40),
        ("created_at", "2026-09-07T12:00:01Z"),
        ("event", "pull_request"),
    ],
)
def test_changed_bracketing_header_is_not_a_stable_archive(field: str, value: object) -> None:
    provider = Provider(
        [
            response(repository()),
            response(history_run()),
            job_page([history_job()]),
            response({**history_run(), field: value}),
        ]
    )
    assert _load(provider) == ProviderAttemptDeferred("provider_unstable")


@pytest.mark.parametrize("fault", ["duplicate", "changed_total", "missing_final_row", "short_page"])
def test_moving_or_incomplete_job_pages_do_not_synthesize_completeness(fault: str) -> None:
    first = [history_job(index + 1) for index in range(100)]
    second = [history_job(101)]
    total = 101
    if fault == "duplicate":
        second = [history_job(1)]
    elif fault == "changed_total":
        total = 102
    elif fault == "missing_final_row":
        second = []
    else:
        first.pop()
    provider = Provider(
        [
            response(repository()),
            response(history_run()),
            job_page(first, total=101),
            job_page(second, total=total, number=2),
        ]
    )
    result = _load(provider)
    assert isinstance(result, ProviderAttemptDeferred)
    assert result.reason == (
        "provider_unstable"
        if fault in {"duplicate", "changed_total"}
        else "provider_incomplete"
        if fault == "missing_final_row"
        else "provider_malformed"
    )


def test_provider_transport_failure_is_not_converted_to_missing_history() -> None:
    provider = Provider(
        [response(repository()), GitHubTransportFailure("timeout", "bounded provider timeout")]
    )
    assert _load(provider) == ProviderAttemptDeferred("provider_unavailable")


def test_partial_prior_pages_survive_a_later_job_404_without_claiming_deletion() -> None:
    provider = Provider(
        [
            response(repository()),
            response(history_run()),
            job_page([history_job(index + 1) for index in range(100)], total=101),
            replace(response({}), status=404),
            response(history_run()),
        ]
    )
    result = _load(provider)
    assert isinstance(result, ArchivedAttemptStatistics)
    assert (
        result.population == "partial"
        and len(result.jobs) == 100
        and result.provider_job_total == 101
    )


@pytest.mark.parametrize("margin", [-1, 0, 1])
def test_canonical_byte_budget_returns_partial_before_exceeding_storage_admission(
    monkeypatch: pytest.MonkeyPatch, margin: int
) -> None:
    raw_job = history_job()
    decoded = decode_history_job_page(
        json.dumps({"total_count": 1, "jobs": [raw_job]}).encode(), CURSOR, head_sha="b" * 40
    )
    assert not isinstance(decoded, ProviderAttemptDeferred)
    job_size = len(encode_archive_job(decoded.terminal_jobs[0]))
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        ci_history_provider,
        "MAX_HISTORY_STATISTICS_BYTES",
        MAX_HISTORY_HEADER_BYTES + job_size + margin,
    )
    provider = Provider(
        [
            response(repository()),
            response(history_run()),
            job_page([raw_job]),
            response(history_run()),
        ]
    )
    result = _load(provider)
    assert isinstance(result, ArchivedAttemptStatistics)
    assert result.population == ("partial" if margin < 0 else "complete")
    assert len(result.jobs) == (0 if margin < 0 else 1)
    assert result.provider_job_total == 1 and not provider.responses


def test_byte_cap_does_not_hide_a_contradictory_job_on_the_same_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        ci_history_provider, "MAX_HISTORY_STATISTICS_BYTES", MAX_HISTORY_HEADER_BYTES
    )
    provider = Provider(
        [response(repository()), response(history_run()), job_page([history_job(), history_job()])]
    )
    assert _load(provider) == ProviderAttemptDeferred("provider_unstable")

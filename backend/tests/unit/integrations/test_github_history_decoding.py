from dataclasses import replace
from datetime import UTC, datetime
from itertools import product

import pytest

from ci_coordinator.ci_economics.ports import ProviderAttemptDeferred
from ci_coordinator.integrations.github.ci_history_decoding import (
    HistoryAttemptHeader,
    HistoryJobPage,
    decode_history_header,
    decode_history_job_page,
)
from ci_coordinator.integrations.github.reconciliation_observer_decoding import decode_job_page

from ._economics_source_support import response
from ._history_provider_support import CURSOR, RUN_CREATED_AT, history_job, history_run


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("id", True, "provider_malformed"),
        ("id", 304, "provider_binding_mismatch"),
        ("repository", {"id": 203}, "provider_binding_mismatch"),
        ("run_attempt", 3, "provider_binding_mismatch"),
        ("run_attempt", "2", "provider_malformed"),
        ("workflow_id", 0, "provider_malformed"),
        ("path", 10, "provider_malformed"),
        ("head_sha", "c", "provider_malformed"),
        ("created_at", "2026-02-31T00:00:00Z", "provider_malformed"),
        ("created_at", "2026-09-07T12:00:00.1234567Z", "provider_malformed"),
        ("event", "", "provider_malformed"),
        ("status", "unrecognized", "provider_malformed"),
        ("status", [], "provider_malformed"),
        ("status", "in_progress", "provider_malformed"),
        ("conclusion", "unrecognized", "provider_malformed"),
    ],
)
def test_header_admission_rejects_invalid_or_foreign_facts(
    field: str, value: object, reason: str
) -> None:
    result = decode_history_header(
        response({**history_run(), field: value}).body, CURSOR, run_created_at=RUN_CREATED_AT
    )
    assert isinstance(result, ProviderAttemptDeferred) and result.reason == reason


def test_active_run_is_explicitly_deferred_without_job_collection() -> None:
    body = response({**history_run(), "status": "in_progress", "conclusion": None}).body
    assert decode_history_header(
        body, CURSOR, run_created_at=RUN_CREATED_AT
    ) == ProviderAttemptDeferred("provider_not_terminal")


def test_completed_run_preserves_explicitly_unknown_conclusion() -> None:
    body = response({**history_run(), "conclusion": None}).body
    result = decode_history_header(body, CURSOR, run_created_at=RUN_CREATED_AT)
    assert isinstance(result, HistoryAttemptHeader) and result.statistics.conclusion is None


def test_missing_conclusion_field_does_not_masquerade_as_explicit_unknown() -> None:
    raw = history_run()
    del raw["conclusion"]
    assert decode_history_header(
        response(raw).body, CURSOR, run_created_at=RUN_CREATED_AT
    ) == ProviderAttemptDeferred("provider_malformed")


def test_header_projects_only_admitted_statistics_and_no_invented_workflow_hash() -> None:
    body = response({**history_run(), "actor": {"login": "private-actor"}}).body
    result = decode_history_header(body, CURSOR, run_created_at=RUN_CREATED_AT)
    assert isinstance(result, HistoryAttemptHeader)
    statistics = result.statistics
    assert statistics.attempt.to_attempt().scope == CURSOR.scope
    assert statistics.workflow_id == 404 and statistics.workflow_blob_sha is None
    assert statistics.population == "unavailable" and statistics.jobs == ()
    assert statistics.run_created_at == RUN_CREATED_AT
    assert result.attempt_created_at == datetime(2026, 9, 7, 12, tzinfo=UTC)
    assert "private-actor" not in statistics.model_dump_json()


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("run_attempt", 1, "provider_binding_mismatch"),
        ("run_attempt", True, "provider_binding_mismatch"),
        ("run_id", 304, "provider_malformed"),
        ("head_sha", "c" * 40, "provider_malformed"),
        ("created_at", "2026-09-07T12:00:00.1234567Z", "provider_malformed"),
        ("created_at", True, "provider_malformed"),
        ("started_at", "2026-09-07T12:00:01.1234567Z", "provider_malformed"),
        ("completed_at", "2026-09-07T12:00:04.1234567Z", "provider_malformed"),
        ("labels", ["linux", "linux"], "provider_malformed"),
        ("id", True, "provider_malformed"),
    ],
)
def test_job_admission_preserves_attempt_identity_and_time_precision(
    field: str, value: object, reason: str
) -> None:
    body = response({"total_count": 1, "jobs": [{**history_job(), field: value}]}).body
    result = decode_history_job_page(body, CURSOR, head_sha="b" * 40)
    assert isinstance(result, ProviderAttemptDeferred) and result.reason == reason


def test_missing_optional_job_fields_remain_unknown_and_private_detail_is_discarded() -> None:
    job = history_job()
    del job["run_attempt"], job["created_at"]
    body = response({"total_count": 1, "jobs": [job]}).body
    result = decode_history_job_page(body, CURSOR, head_sha="b" * 40)
    assert isinstance(result, HistoryJobPage)
    total, jobs = result.total, result.terminal_jobs
    assert total == 1 and len(jobs) == 1 and jobs[0].created_at is None
    assert jobs[0].runner_id == 50 and jobs[0].runner_group_id == 60
    assert jobs[0].labels == ("linux", "self-hosted")
    assert jobs[0].timing is not None
    assert jobs[0].timing.started_at == datetime(2026, 9, 7, 12, 0, 1, tzinfo=UTC)
    assert jobs[0].timing.completed_at == datetime(2026, 9, 7, 12, 0, 4, tzinfo=UTC)
    assert "private-" not in jobs[0].model_dump_json()


@pytest.mark.parametrize("started", ["2026-09-10T12:31:08Z", "2026-09-10T12:46:01Z"])
def test_real_rerun_time_shape_is_not_a_malformed_attempt(started: str) -> None:
    raw = {
        **history_job(),
        "created_at": "2026-09-10T12:46:00Z",
        "started_at": started,
        "completed_at": "2026-09-10T12:44:33Z",
    }
    result = decode_history_job_page(
        response({"total_count": 1, "jobs": [raw]}).body, CURSOR, head_sha="b" * 40
    )
    assert isinstance(result, HistoryJobPage)
    total, jobs = result.total, result.terminal_jobs
    assert total == len(jobs) == 1
    assert jobs[0].timing is None and jobs[0].timing_quality == "inconsistent"
    assert jobs[0].created_at == datetime(2026, 9, 10, 12, 46, tzinfo=UTC)
    assert jobs[0].completed_at == datetime(2026, 9, 10, 12, 44, 33, tzinfo=UTC)
    strict = decode_job_page(
        response({"total_count": 1, "jobs": [raw]}).body,
        expected_run_id=CURSOR.workflow_run_id,
        expected_head_sha="b" * 40,
    )
    assert (strict is None) == (started == "2026-09-10T12:46:01Z")


@pytest.mark.parametrize("body", [b"{}", b"[]", b'{"id":1,"id":1}', b"\xff"])
def test_header_rejects_non_object_duplicate_or_invalid_json(body: bytes) -> None:
    assert decode_history_header(
        body, CURSOR, run_created_at=RUN_CREATED_AT
    ) == ProviderAttemptDeferred("provider_malformed")


@pytest.mark.parametrize("group_id", [None, 0, 60])
@pytest.mark.parametrize("names", [{}, {"runner_group_name": "GitHub Actions"}])
def test_archive_numeric_identity_does_not_require_discarded_display_names(
    group_id: int | None, names: dict[str, object]
) -> None:
    job = history_job()
    del job["runner_name"], job["runner_group_name"]
    job.update(runner_group_id=group_id, **names)
    body = response({"total_count": 1, "jobs": [job]}).body
    result = decode_history_job_page(body, CURSOR, head_sha="b" * 40)
    assert isinstance(result, HistoryJobPage)
    (archived,) = result.terminal_jobs
    assert archived.runner_id == 50 and archived.runner_group_id == (group_id or None)
    assert (
        "runnerName" not in archived.model_dump()
        and "GitHub Actions" not in archived.model_dump_json()
    )
    assert (
        decode_job_page(body, expected_run_id=CURSOR.workflow_run_id, expected_head_sha="b" * 40)
        is None
    )


@pytest.mark.parametrize("runner_id,group_id", tuple(product([None, 0], repeat=2)))
def test_unassigned_runner_is_unknown_even_with_provider_display_names(
    runner_id: int | None, group_id: int | None
) -> None:
    job = {**history_job(), "runner_id": runner_id, "runner_group_id": group_id}
    result = decode_history_job_page(
        response({"total_count": 1, "jobs": [job]}).body, CURSOR, head_sha="b" * 40
    )
    assert isinstance(result, HistoryJobPage)
    (archived,) = result.terminal_jobs
    assert archived.runner_id is archived.runner_group_id is None


@pytest.mark.parametrize(
    "field,value",
    [
        *product(["runner_id", "runner_group_id", "id", "run_id"], [True, 1.0, -1, 2**53, "1"]),
        ("runner_id", None),
        ("runner_id", 0),
        ("id", 0),
        ("run_attempt", None),
        ("run_attempt", 2.0),
        ("labels", None),
        ("labels", "linux"),
        ("labels", [True]),
        ("labels", [""]),
        ("labels", ["x" * 129]),
        ("labels", [f"label-{index}" for index in range(33)]),
        ("labels", ["linux", "linux"]),
        ("name", ""),
        ("name", "x" * 513),
        ("name", "bad\x00name"),
        ("status", []),
        ("status", "unrecognized"),
        ("conclusion", None),
        ("conclusion", "unrecognized"),
    ],
)
def test_raw_archive_job_rejects_each_invalid_retained_operand(field: str, value: object) -> None:
    body = response({"total_count": 1, "jobs": [{**history_job(), field: value}]}).body
    result = decode_history_job_page(body, CURSOR, head_sha="b" * 40)
    assert isinstance(result, ProviderAttemptDeferred)


@pytest.mark.parametrize(
    "field", ["id", "run_id", "head_sha", "name", "status", "conclusion", "labels"]
)
def test_missing_required_archive_job_operand_rejects_the_whole_page(field: str) -> None:
    invalid = history_job(2)
    del invalid[field]
    body = response({"total_count": 2, "jobs": [history_job(), invalid]}).body
    assert decode_history_job_page(body, CURSOR, head_sha="b" * 40) == ProviderAttemptDeferred(
        "provider_malformed"
    )


@pytest.mark.parametrize(
    "page",
    [
        {},
        {"total_count": True, "jobs": []},
        {"total_count": 1.0, "jobs": []},
        {"total_count": -1, "jobs": []},
        {"total_count": 2**53, "jobs": []},
        {"total_count": 0, "jobs": None},
        {"total_count": 101, "jobs": [history_job(index) for index in range(1, 102)]},
        {"total_count": 1, "jobs": [None]},
    ],
)
def test_raw_archive_page_rejects_invalid_shape_or_bounds(page: object) -> None:
    assert decode_history_job_page(
        response(page).body, CURSOR, head_sha="b" * 40
    ) == ProviderAttemptDeferred("provider_malformed")


@pytest.mark.parametrize("status", ["queued", "in_progress", "waiting", "requested", "pending"])
def test_active_job_keeps_identity_without_archived_terminal_statistics(status: str) -> None:
    job = {**history_job(7), "status": status, "conclusion": None}
    assert decode_history_job_page(
        response({"total_count": 1, "jobs": [job]}).body, CURSOR, head_sha="b" * 40
    ) == HistoryJobPage(1, (7,), ())


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("id", None, "provider_malformed"),
        ("id", True, "provider_malformed"),
        ("id", 1.0, "provider_malformed"),
        ("id", 0, "provider_malformed"),
        ("run_id", 304, "provider_malformed"),
        ("head_sha", "c" * 40, "provider_malformed"),
        ("run_attempt", 1, "provider_binding_mismatch"),
        ("conclusion", "success", "provider_malformed"),
        ("status", "unknown", "provider_malformed"),
    ],
)
def test_active_job_omission_cannot_bypass_identity_or_status_admission(
    field: str, value: object, reason: str
) -> None:
    job = {**history_job(), "status": "queued", "conclusion": None, field: value}
    result = decode_history_job_page(
        response({"total_count": 1, "jobs": [job]}).body, CURSOR, head_sha="b" * 40
    )
    assert isinstance(result, ProviderAttemptDeferred) and result.reason == reason


def test_archive_labels_have_stable_canonical_order() -> None:
    first = decode_history_job_page(
        response({"total_count": 1, "jobs": [history_job()]}).body, CURSOR, head_sha="b" * 40
    )
    second = decode_history_job_page(
        response(
            {"total_count": 1, "jobs": [{**history_job(), "labels": ["linux", "self-hosted"]}]}
        ).body,
        CURSOR,
        head_sha="b" * 40,
    )
    assert isinstance(first, HistoryJobPage) and first == second
    assert first.terminal_jobs[0].labels == ("linux", "self-hosted")


@pytest.mark.parametrize("group_name", [None, "GitHub Actions"])
def test_only_the_group_name_relation_distinguishes_strict_from_archive_admission(
    group_name: str | None,
) -> None:
    job = {**history_job(), "runner_group_id": None, "runner_group_name": group_name}
    body = response({"total_count": 1, "jobs": [job]}).body
    archived = decode_history_job_page(body, CURSOR, head_sha="b" * 40)
    assert isinstance(archived, HistoryJobPage) and archived.terminal_jobs[0].runner_id == 50
    assert archived.terminal_jobs[0].runner_group_id is None
    strict = decode_job_page(
        body, expected_run_id=CURSOR.workflow_run_id, expected_head_sha="b" * 40
    )
    assert (strict is not None) == (group_name is None)


@pytest.mark.parametrize(
    "expected,raw_id,accepted",
    [(1, 1, True), (1, True, False), (303, 303, True), (303, 303.0, False)],
)
def test_run_identity_type_is_checked_even_when_numeric_equality_holds(
    expected: int, raw_id: object, accepted: bool
) -> None:
    cursor = replace(CURSOR, workflow_run_id=expected)
    job = {**history_job(), "run_id": raw_id}
    result = decode_history_job_page(
        response({"total_count": 1, "jobs": [job]}).body, cursor, head_sha="b" * 40
    )
    if accepted:
        assert isinstance(result, HistoryJobPage)
        assert result.total == len(result.terminal_jobs) == len(result.observed_ids) == 1
    else:
        assert result == ProviderAttemptDeferred("provider_malformed")

from datetime import timedelta

import pytest
from ci_economics.archive_factories import ARCHIVE_TIME, archived_statistics
from ci_economics.gap_recovery_factories import recheck_gap

from ci_coordinator.ci_economics.archive_detail import (
    ArchivedAttemptDetail,
    ArchivedJobDetail,
    ArchiveStepDetail,
    encode_archive_detail,
)
from ci_coordinator.ci_economics.archive_retention import (
    ArchiveDetailRetention,
    DetailPolicyReference,
    DetailRetentionPolicy,
)
from ci_coordinator.ci_economics.archive_statistics import ArchivePopulation
from ci_coordinator.ci_economics.history_read import HistoryReadPage, HistoryReadQuery
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.persistence.canonical_row import encode_canonical_object
from ci_coordinator.persistence.ci_history_codec import encode_archive_statistics
from ci_coordinator.persistence.ci_history_read_codec import (
    read_history_detail,
    read_history_detail_payload,
    read_history_gap,
    read_history_job,
    read_history_summary,
)
from ci_coordinator.persistence.ci_history_retention_codec import encode_history_retention


def _detail_rows() -> tuple[dict[str, object], dict[str, object], ArchivedAttemptDetail]:
    statistics = archived_statistics()
    encoded = encode_archive_statistics(statistics, generation=1)
    retention = ArchiveDetailRetention(
        "retained",
        ARCHIVE_TIME - timedelta(days=1),
        DetailRetentionPolicy("days", 30),
        DetailPolicyReference("repository_override", 2),
    )
    parent = {
        **encoded.attempt,
        **encode_history_retention(retention),
        "has_conflict": False,
        "first_imported_at": ARCHIVE_TIME,
    }
    payload = ArchivedAttemptDetail(
        schemaVersion="ci-economics-archive-detail/v1",
        attempt=statistics.attempt,
        jobs=tuple(
            ArchivedJobDetail(
                providerJobId=job.provider_job_id,
                steps=(
                    ArchiveStepDetail(
                        number=1,
                        status="completed",
                        conclusion=job.conclusion,
                        startedAt=job.started_at,
                        completedAt=job.completed_at,
                    ),
                ),
            )
            for job in statistics.jobs
        ),
    )
    detail = {**encoded.attempt, "detail_canonical": encode_archive_detail(payload)}
    return parent, detail, payload


def test_detail_payload_admits_exact_parent_and_job_bindings() -> None:
    parent, detail, payload = _detail_rows()
    statistics = archived_statistics()
    assert (
        read_history_detail_payload(
            parent,
            detail,
            statistics,
            now=ARCHIVE_TIME,
        )
        == payload
    )
    for change in (
        {"repository_id": 203},
        {"head_sha": "b" * 40},
        {"workflow_run_id": 999},
    ):
        assert (
            read_history_detail_payload({**parent, **change}, detail, statistics, now=ARCHIVE_TIME)
            is None
        )
    wrong_job = payload.model_copy(
        update={"jobs": (payload.jobs[0].model_copy(update={"provider_job_id": 999}),)}
    )
    wrong_job_bytes = encode_archive_detail(wrong_job)
    assert ArchivedAttemptDetail.model_validate_json(wrong_job_bytes).jobs[0].provider_job_id == 999
    assert (
        read_history_detail_payload(
            parent,
            {**detail, "detail_canonical": wrong_job_bytes},
            statistics,
            now=ARCHIVE_TIME,
        )
        is None
    )


@pytest.mark.parametrize(
    "field",
    ["installation_id", "repository_id", "generation", "workflow_run_id", "run_attempt"],
)
def test_detail_payload_rejects_each_foreign_child_key(field: str) -> None:
    parent, detail, payload = _detail_rows()
    statistics = archived_statistics()
    assert read_history_detail_payload(parent, detail, statistics, now=ARCHIVE_TIME) == payload
    value = detail[field]
    assert type(value) is int
    assert (
        read_history_detail_payload(
            parent, {**detail, field: value + 1}, statistics, now=ARCHIVE_TIME
        )
        is None
    )


@pytest.mark.parametrize("conflict", [True, None, 0, "false"])
def test_detail_payload_requires_an_explicit_conflict_free_parent(conflict: object) -> None:
    parent, detail, payload = _detail_rows()
    statistics = archived_statistics()
    assert read_history_detail_payload(parent, detail, statistics, now=ARCHIVE_TIME) == payload
    assert (
        read_history_detail_payload(
            {**parent, "has_conflict": conflict}, detail, statistics, now=ARCHIVE_TIME
        )
        is None
    )


@pytest.mark.parametrize(
    "raw",
    [b"not-json", b'{"schemaVersion":"ci-economics-archive-detail/v0"}', b"x" * 262_145],
)
def test_detail_payload_unknown_corrupt_or_oversized_is_unavailable(raw: bytes) -> None:
    parent, detail, _ = _detail_rows()
    statistics = archived_statistics()
    assert (
        read_history_detail_payload(
            parent,
            {**detail, "detail_canonical": raw},
            statistics,
            now=ARCHIVE_TIME,
        )
        is None
    )


def test_detail_payload_expiry_suppresses_content_but_not_metadata() -> None:
    parent, detail, _ = _detail_rows()
    statistics = archived_statistics()
    assert (
        read_history_detail_payload(
            parent,
            detail,
            statistics,
            now=ARCHIVE_TIME + timedelta(days=30),
        )
        is None
    )


@pytest.mark.parametrize("offset,state", [(-1, "retained"), (0, "expired"), (1, "expired")])
def test_detail_expiry_is_logical_at_the_exact_boundary(offset: int, state: str) -> None:
    imported = ARCHIVE_TIME - timedelta(days=365)
    retention = ArchiveDetailRetention(
        "retained",
        imported,
        DetailRetentionPolicy.default(),
        DetailPolicyReference("service_default", 1),
    )
    row = encode_history_retention(retention)
    result = read_history_detail(row, now=ARCHIVE_TIME + timedelta(microseconds=offset))
    assert result.state == state and result.first_imported_at == imported
    assert result.content == ("unavailable_format" if state == "retained" else "expired")
    assert row == encode_history_retention(retention)


@pytest.mark.parametrize(
    "field,value",
    [
        ("repository_id", 203),
        ("installation_id", 202),
        ("workflow_run_id", 999),
        ("workflow_id", 405),
        ("head_sha", "b" * 40),
        ("job_count", 0),
    ],
)
def test_small_header_read_rejects_contradictory_sql_projection(field: str, value: object) -> None:
    row = {
        **encode_archive_statistics(archived_statistics(), generation=1).attempt,
        **encode_history_retention(ArchiveDetailRetention()),
        "has_conflict": False,
        "first_imported_at": ARCHIVE_TIME,
        field: value,
    }
    with pytest.raises(ValueError):
        read_history_summary(row, now=ARCHIVE_TIME)


def test_header_projection_preserves_conflict_without_loading_jobs() -> None:
    encoded = encode_archive_statistics(archived_statistics(), generation=1)
    row = {
        **encoded.attempt,
        **encode_history_retention(ArchiveDetailRetention()),
        "has_conflict": True,
        "first_imported_at": ARCHIVE_TIME,
    }
    result = read_history_summary(row, now=ARCHIVE_TIME)
    assert result.has_conflict and result.job_count == 1
    assert "jobs" not in result.header.model_dump()
    assert result.detail.state == "not_imported"
    job = read_history_job(encoded.jobs[0])
    assert job == archived_statistics().jobs[0]
    with pytest.raises(ValueError):
        read_history_job({**encoded.jobs[0], "provider_job_id": 2})


@pytest.mark.parametrize(
    "population,total,conflict,expected",
    [
        ("complete", 1, False, "retained_complete"),
        ("partial", 2, False, "retained_incomplete"),
        ("complete", 1, True, "retained_conflicting"),
    ],
)
def test_gap_projection_joins_only_exact_current_retained_identity(
    population: ArchivePopulation,
    total: int,
    conflict: bool,
    expected: str,
) -> None:
    source = recheck_gap()
    row = {
        "gap_id": source.gap_id,
        "recorded_at": ARCHIVE_TIME,
        "installation_id": 101,
        "repository_id": 202,
        "generation": 1,
        "gap_canonical": encode_canonical_object(
            source.model_dump(mode="json"), maximum_bytes=4096, context="fixture"
        ),
    }
    encoded = encode_archive_statistics(
        archived_statistics(population=population, provider_total=total), generation=1
    )
    retained = read_history_summary(
        {
            **encoded.attempt,
            **encode_history_retention(ArchiveDetailRetention()),
            "has_conflict": conflict,
            "first_imported_at": ARCHIVE_TIME,
        },
        now=ARCHIVE_TIME,
    )
    view = read_history_gap(row, source.scope, 1, retained=retained)
    assert view.resolution == expected and view.retry_supported and view.retained == retained
    assert read_history_gap(row, source.scope, 1).resolution == "missing"
    for key, value in (("workflowRunId", 304), ("runAttempt", 2)):
        foreign = type(retained).model_validate(
            {
                **retained.model_dump(),
                "header": {
                    **retained.header.model_dump(),
                    "attempt": {**retained.header.attempt.model_dump(), key: value},
                },
            }
        )
        with pytest.raises(ValueError):
            read_history_gap(row, source.scope, 1, retained=foreign)
    for scope, generation in ((RepositoryScope(101, 999), 1), (source.scope, 2)):
        with pytest.raises(ValueError):
            read_history_gap(row, scope, generation, retained=retained)
    with pytest.raises(ValueError):
        read_history_gap({**row, "gap_id": "f" * 64}, source.scope, 1, retained=retained)


@pytest.mark.parametrize(
    "recorded_offset,observed_offset,valid",
    [(0, 0, True), (0, 1, True), (-1, 1, False), (1, 0, False)],
)
def test_missing_source_creation_recording_and_observation_share_one_order(
    recorded_offset: int,
    observed_offset: int,
    valid: bool,
) -> None:
    source = recheck_gap()
    row = {
        "gap_id": source.gap_id,
        "recorded_at": ARCHIVE_TIME + timedelta(microseconds=recorded_offset),
        "installation_id": 101,
        "repository_id": 202,
        "generation": 1,
        "gap_canonical": encode_canonical_object(
            source.model_dump(mode="json"), maximum_bytes=4096, context="fixture"
        ),
    }

    def page() -> HistoryReadPage:
        view = read_history_gap(row, source.scope, 1)
        return HistoryReadPage(
            query=HistoryReadQuery(installationId=101, repositoryId=202, generation=1, kind="gaps"),
            configurationRevision=1,
            dataRevision=1,
            observedAt=ARCHIVE_TIME + timedelta(microseconds=observed_offset),
            gaps=(view,),
        )

    if valid:
        assert page().gaps[0].resolution == "missing"
    else:
        with pytest.raises(ValueError):
            page()

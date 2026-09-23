from collections.abc import Mapping
from datetime import timedelta

import pytest
from ci_economics.archive_factories import ARCHIVE_TIME, archived_job, archived_statistics

from ci_coordinator.ci_economics.archive_encoding import (
    MAX_HISTORY_HEADER_BYTES,
    MAX_HISTORY_JOB_BYTES,
)
from ci_coordinator.persistence.canonical_row import encode_canonical_object
from ci_coordinator.persistence.ci_history_codec import (
    decode_archive_statistics,
    encode_archive_statistics,
)


@pytest.mark.parametrize("job_count", [0, 1, 3])
def test_header_and_children_round_trip_without_duplicating_job_payloads(job_count: int) -> None:
    statistics = archived_statistics(
        jobs=tuple(archived_job(index + 1) for index in range(job_count)), provider_total=job_count
    )
    encoded = encode_archive_statistics(statistics, generation=1)
    assert decode_archive_statistics(encoded.attempt, encoded.jobs) == statistics
    header = encoded.attempt["header_canonical"]
    assert isinstance(header, bytes) and b'"jobs"' not in header
    child_lengths: list[int] = []
    for row in encoded.jobs:
        raw = row["job_canonical"]
        assert isinstance(raw, bytes)
        child_lengths.append(len(raw))
    assert encoded.canonical_bytes == len(header) + sum(child_lengths)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("installation_id", 102),
        ("repository_id", 203),
        ("generation", True),
        ("generation", 2),
        ("workflow_run_id", 304),
        ("run_attempt", True),
        ("head_sha", "b" * 40),
        ("workflow_id", 405),
        ("run_created_at", ARCHIVE_TIME + timedelta(seconds=1)),
        ("statistics_digest", "f" * 64),
        ("job_count", True),
        ("job_count", 0),
        ("statistics_bytes", 1),
        ("statistics_bytes", True),
    ],
)
def test_every_header_projection_is_checked_against_canonical_facts(
    field: str, value: object
) -> None:
    encoded = encode_archive_statistics(archived_statistics(), generation=1)
    row = {**encoded.attempt, field: value}
    with pytest.raises(ValueError):
        decode_archive_statistics(row, encoded.jobs)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("installation_id", 102),
        ("repository_id", 203),
        ("generation", 2),
        ("workflow_run_id", 304),
        ("run_attempt", True),
        ("provider_job_id", True),
        ("name", "Other job"),
        ("conclusion", "failure"),
        ("created_at", None),
        ("started_at", None),
        ("completed_at", None),
    ],
)
def test_every_job_projection_is_bound_to_the_same_attempt_and_metric_operands(
    field: str, value: object
) -> None:
    encoded = encode_archive_statistics(archived_statistics(), generation=1)
    rows = ({**encoded.jobs[0], field: value},)
    with pytest.raises(ValueError):
        decode_archive_statistics(encoded.attempt, rows)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "reordered"])
def test_job_population_changes_cannot_survive_header_digest_validation(mutation: str) -> None:
    statistics = archived_statistics(jobs=(archived_job(1), archived_job(2)), provider_total=2)
    encoded = encode_archive_statistics(statistics, generation=1)
    rows: tuple[Mapping[str, object], ...]
    if mutation == "missing":
        rows = encoded.jobs[:1]
    elif mutation == "duplicate":
        rows = (encoded.jobs[0], encoded.jobs[0])
    else:
        rows = tuple(reversed(encoded.jobs))
    with pytest.raises(ValueError):
        decode_archive_statistics(encoded.attempt, rows)


@pytest.mark.parametrize("target", ["header", "job"])
@pytest.mark.parametrize("raw", [b"{}", b'{"x":1,"x":2}', b" { }", b"[]", b"\xff"])
def test_stored_bytes_are_closed_canonical_objects(target: str, raw: bytes) -> None:
    encoded = encode_archive_statistics(archived_statistics(), generation=1)
    parent = dict(encoded.attempt)
    child = dict(encoded.jobs[0])
    if target == "header":
        parent["header_canonical"] = raw
    else:
        child["job_canonical"] = raw
    with pytest.raises(ValueError):
        decode_archive_statistics(parent, (child,))


@pytest.mark.parametrize("target", ["header", "job"])
def test_payload_bounds_precede_untrusted_json_parsing(target: str) -> None:
    encoded = encode_archive_statistics(archived_statistics(), generation=1)
    parent = dict(encoded.attempt)
    child = dict(encoded.jobs[0])
    if target == "header":
        parent["header_canonical"] = b" " * (MAX_HISTORY_HEADER_BYTES + 1)
    else:
        child["job_canonical"] = b" " * (MAX_HISTORY_JOB_BYTES + 1)
    with pytest.raises(ValueError, match="byte bound"):
        decode_archive_statistics(parent, (child,))


def test_equivalent_but_noncanonical_timestamp_representation_is_rejected() -> None:
    statistics = archived_statistics()
    encoded = encode_archive_statistics(statistics, generation=1)
    header = statistics.canonical_mapping()
    del header["jobs"]
    header["runCreatedAt"] = "2020-01-01T01:00:00+01:00"
    parent = dict(encoded.attempt)
    parent["header_canonical"] = encode_canonical_object(
        header, maximum_bytes=MAX_HISTORY_HEADER_BYTES, context="test archive header"
    )
    with pytest.raises(ValueError, match="header_canonical"):
        decode_archive_statistics(parent, encoded.jobs)


def test_database_memoryview_payloads_preserve_exact_round_trip() -> None:
    statistics = archived_statistics()
    encoded = encode_archive_statistics(statistics, generation=1)
    parent = dict(encoded.attempt)
    child = dict(encoded.jobs[0])
    for row, field in [(parent, "header_canonical"), (child, "job_canonical")]:
        raw = row[field]
        assert isinstance(raw, bytes)
        row[field] = memoryview(raw)
    assert decode_archive_statistics(parent, (child,)) == statistics

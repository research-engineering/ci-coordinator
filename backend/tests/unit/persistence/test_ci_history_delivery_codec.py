import json
from datetime import timedelta, timezone
from typing import cast

import pytest
from ci_economics.archive_factories import ARCHIVE_TIME

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.github_ingestion.events import NormalizedWorkflowJobEvent
from ci_coordinator.github_ingestion.provenance import GitHubRepository, WebhookProvenance
from ci_coordinator.kernel import canonical_json, hash_object
from ci_coordinator.persistence.ci_economics_codec import encode_workflow_observation
from ci_coordinator.persistence.ci_history_delivery_codec import decode_history_delivery

_IDENTITIES = (
    ("installation_id", "installationId", 102),
    ("repository_id", "repositoryId", 203),
    ("workflow_run_id", "workflowRunId", 304),
    ("run_attempt", "runAttempt", 2),
    ("head_sha", "headSha", "b" * 40),
)


def _run_row() -> dict[str, object]:
    instant = ARCHIVE_TIME.isoformat().replace("+00:00", "Z")
    mapping = {
        "schemaVersion": "ci-workflow-run-observation/v1",
        "installationId": 101,
        "repositoryId": 202,
        "workflowRunId": 303,
        "runAttempt": 3,
        "workflowId": 404,
        "workflowFile": ".github/workflows/fullcheck.yml",
        "workflowName": "Full Check",
        "headBranch": "feature/change",
        "headSha": "a" * 40,
        "workflowEvent": "pull_request",
        "status": "completed",
        "conclusion": "success",
        "createdAt": instant,
        "runStartedAt": instant,
        "updatedAt": instant,
    }
    return {
        "delivery_id": "delivery-run-1",
        "observation_kind": "workflow_run",
        "installation_id": 101,
        "repository_id": 202,
        "workflow_run_id": 303,
        "run_attempt": 3,
        "head_sha": "a" * 40,
        "provider_job_id": None,
        "semantic_hash": hash_object(mapping),
        "observation_canonical_json": canonical_json(mapping),
        "recorded_at": ARCHIVE_TIME + timedelta(minutes=1),
        "retain_until": ARCHIVE_TIME + timedelta(minutes=1, days=90),
    }


def _replace_body(row: dict[str, object], changes: dict[str, object]) -> None:
    raw = cast(bytes, row["observation_canonical_json"])
    mapping = cast(dict[str, object], json.loads(raw))
    mapping.update(changes)
    row["observation_canonical_json"] = canonical_json(mapping)
    row["semantic_hash"] = hash_object(mapping)


def _job_row() -> dict[str, object]:
    event = NormalizedWorkflowJobEvent(
        provenance=WebhookProvenance(
            "delivery-job-1", "workflow_job", "a" * 64, ARCHIVE_TIME, "v1"
        ),
        repository=GitHubRepository(101, 202, "acme", "service"),
        action="completed",
        workflow_run_id=303,
        run_attempt=3,
        head_sha="a" * 40,
        workflow_job_id=505,
        job_name="Lint",
        status="completed",
        conclusion="success",
        created_at=ARCHIVE_TIME,
        started_at=ARCHIVE_TIME,
        completed_at=ARCHIVE_TIME,
        labels=("linux",),
        runner=None,
    )
    return {
        **encode_workflow_observation(event, delivery_id="delivery-job-1"),
        "recorded_at": ARCHIVE_TIME,
        "retain_until": ARCHIVE_TIME + timedelta(days=90),
    }


@pytest.mark.parametrize("memoryview_input", [False, True])
def test_exact_run_source_produces_only_a_scoped_attempt_hint(memoryview_input: bool) -> None:
    row = _run_row()
    if memoryview_input:
        row["observation_canonical_json"] = memoryview(
            cast(bytes, row["observation_canonical_json"])
        )
    source = decode_history_delivery(row)
    assert source.delivery_id == "delivery-run-1"
    assert source.scope == RepositoryScope(101, 202)
    assert source.workflow_run_id == 303
    assert source.recorded_at == row["recorded_at"]
    assert source.retain_until == row["retain_until"]
    assert source.unsupported is None
    assert source.hint is not None
    assert source.hint.cursor.canonical_mapping() == {
        "installationId": 101,
        "repositoryId": 202,
        "workflowRunId": 303,
        "latestAttempt": 3,
        "nextAttempt": 1,
    }
    assert (source.hint.workflow_id, source.hint.run_created_at, source.hint.source) == (
        404,
        ARCHIVE_TIME,
        "recent",
    )
    assert source.source_fingerprint == decode_history_delivery(_run_row()).source_fingerprint


@pytest.mark.parametrize(("column", "field", "replacement"), _IDENTITIES)
@pytest.mark.parametrize("change_body", [False, True])
def test_source_query_and_canonical_identity_must_agree(
    column: str, field: str, replacement: object, change_body: bool
) -> None:
    row = _run_row()
    assert decode_history_delivery(row).hint is not None
    if change_body:
        _replace_body(row, {field: replacement})
    else:
        row[column] = replacement
    with pytest.raises(ValueError):
        decode_history_delivery(row)


@pytest.mark.parametrize(
    ("column", "replacement"),
    [
        ("semantic_hash", "f" * 64),
        ("provider_job_id", 505),
        ("observation_kind", "workflow_job"),
        ("delivery_id", ""),
        ("delivery_id", "x" * 129),
        ("repository_id", True),
        ("run_attempt", 3.0),
        ("installation_id", "101"),
        ("workflow_run_id", 0),
        ("head_sha", "a" * 39),
        ("recorded_at", ARCHIVE_TIME.replace(tzinfo=None)),
        ("retain_until", ARCHIVE_TIME),
        ("unexpected", True),
    ],
)
def test_invalid_source_column_never_becomes_a_hint(column: str, replacement: object) -> None:
    row = _run_row()
    row[column] = replacement
    with pytest.raises(ValueError):
        decode_history_delivery(row)


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("schemaVersion", "ci-workflow-run-observation/v2"),
        ("status", "in_progress"),
        ("workflowId", True),
        ("runAttempt", "3"),
        ("createdAt", None),
        ("createdAt", 0),
        ("createdAt", "2020-01-01T00:00:00"),
        ("updatedAt", "not-a-time"),
        ("headBranch", []),
        ("conclusion", "invented"),
        ("unexpected", 1),
    ],
)
def test_resealed_invalid_source_body_never_becomes_a_hint(field: str, replacement: object) -> None:
    row = _run_row()
    _replace_body(row, {field: replacement})
    with pytest.raises(ValueError):
        decode_history_delivery(row)


@pytest.mark.parametrize("raw", [b"{} ", b"[]", b'{"a":1,"a":2}', b"\xff"])
def test_noncanonical_source_is_not_admitted(raw: bytes) -> None:
    row = _run_row()
    row["observation_canonical_json"] = raw
    with pytest.raises(ValueError):
        decode_history_delivery(row)


def test_source_body_limit_is_checked_before_projection() -> None:
    row = _run_row()
    _replace_body(row, {"workflowName": "x" * 16_384})
    with pytest.raises(ValueError):
        decode_history_delivery(row)


@pytest.mark.parametrize(("column", "field", "replacement"), _IDENTITIES)
def test_coherent_identity_change_also_changes_the_delivery_fingerprint(
    column: str, field: str, replacement: object
) -> None:
    row = _run_row()
    original = decode_history_delivery(row)
    row[column] = replacement
    _replace_body(row, {field: replacement})
    assert decode_history_delivery(row).source_fingerprint != original.source_fingerprint


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("workflowName", "Renamed"),
        ("conclusion", None),
        ("workflowFile", "changed.yml"),
        ("createdAt", "2020-01-01T00:00:00.123Z"),
        ("workflowId", None),
    ],
)
def test_every_changed_canonical_operand_changes_source_fingerprint(
    field: str, value: object
) -> None:
    row = _run_row()
    original = decode_history_delivery(row)
    _replace_body(row, {field: value})
    assert decode_history_delivery(row).source_fingerprint != original.source_fingerprint


@pytest.mark.parametrize("change_time", [False, True])
def test_delivery_identity_and_original_retention_are_fingerprint_operands(
    change_time: bool,
) -> None:
    row = _run_row()
    original = decode_history_delivery(row)
    if change_time:
        row["recorded_at"] = ARCHIVE_TIME
        row["retain_until"] = ARCHIVE_TIME + timedelta(days=90)
    else:
        row["delivery_id"] = "new-delivery"
    assert decode_history_delivery(row).source_fingerprint != original.source_fingerprint


def test_equivalent_database_timezone_does_not_change_source_fingerprint() -> None:
    row = _run_row()
    original = decode_history_delivery(row)
    zone = timezone(timedelta(hours=2))
    row["recorded_at"] = original.recorded_at.astimezone(zone)
    row["retain_until"] = original.retain_until.astimezone(zone)
    assert decode_history_delivery(row) == original


def test_unknown_workflow_is_explicitly_unsupported_without_invented_provenance() -> None:
    row = _run_row()
    _replace_body(row, {"workflowId": None, "conclusion": None})
    source = decode_history_delivery(row)
    assert source.hint is None
    assert source.unsupported == "workflow_unknown"


def test_job_only_source_reuses_existing_decoder_without_inventing_a_workflow() -> None:
    source = decode_history_delivery(_job_row())
    assert source.scope == RepositoryScope(101, 202)
    assert source.workflow_run_id == 303
    assert source.hint is None
    assert source.unsupported == "job_only"


@pytest.mark.parametrize("column", ["semantic_hash", "head_sha", "provider_job_id"])
def test_invalid_job_source_cannot_be_acknowledged_as_merely_unsupported(column: str) -> None:
    row = _job_row()
    row[column] = {"semantic_hash": "b" * 64, "head_sha": "b" * 40, "provider_job_id": 506}[column]
    with pytest.raises(ValueError):
        decode_history_delivery(row)

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import replace
from itertools import permutations
from pathlib import Path
from typing import cast

import pytest
from scripts.release_artifact_identity import (
    MAX_GATE_RESPONSE_BYTES,
    GateExpectation,
    GateRun,
    ReleaseAdmissionError,
    ReleaseCoordinates,
    admit_gate_response,
    derive_release_identity,
)

REPOSITORY = "research-engineering/ci-coordinator"
REPOSITORY_ID = 1001
SOURCE_COMMIT = "a" * 40
SOURCE_REF = "refs/heads/master"
WORKFLOW_ID = 2001
WORKFLOW_PATH = ".github/workflows/python-persistence.yml"


def _expectation() -> GateExpectation:
    return GateExpectation(
        repository=REPOSITORY,
        repository_id=REPOSITORY_ID,
        source_commit=SOURCE_COMMIT,
        source_ref=SOURCE_REF,
        workflow_id=WORKFLOW_ID,
        workflow_name="Full Check",
        workflow_path=WORKFLOW_PATH,
    )


def _run() -> dict[str, object]:
    repository = {"id": REPOSITORY_ID, "full_name": REPOSITORY}
    return {
        "id": 8_001,
        "run_attempt": 2,
        "workflow_id": WORKFLOW_ID,
        "name": "Full Check",
        "path": WORKFLOW_PATH,
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "head_branch": "master",
        "head_sha": SOURCE_COMMIT,
        "repository": repository,
        "head_repository": repository,
    }


def _write_response(path: Path, runs: list[dict[str, object]]) -> None:
    path.write_text(
        json.dumps({"total_count": len(runs), "workflow_runs": runs}),
        encoding="utf-8",
    )


def test_gate_admission_returns_one_exact_success(tmp_path: Path) -> None:
    response = tmp_path / "runs.json"
    _write_response(response, [_run()])

    assert admit_gate_response(response, _expectation()).run_id == 8_001
    assert admit_gate_response(response, _expectation()).run_attempt == 2


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("workflow_id", WORKFLOW_ID + 1),
        ("workflow_id", float(WORKFLOW_ID)),
        ("name", "Other Check"),
        ("path", ".github/workflows/other.yml"),
        ("event", "push"),
        ("event", "pull_request"),
        ("event", "merge_group"),
        ("status", "in_progress"),
        ("conclusion", "neutral"),
        ("head_branch", "feature/unsafe"),
        ("head_sha", "b" * 40),
    ],
)
@pytest.mark.parametrize("neighbors", ["none", "before", "after"])
def test_gate_admission_rejects_each_wrong_authority_field(
    tmp_path: Path,
    field: str,
    replacement: object,
    neighbors: str,
) -> None:
    response = tmp_path / "runs.json"
    run = _run()
    run[field] = replacement
    valid = {**_run(), "id": 8_002}
    runs = {"none": [run], "before": [valid, run], "after": [run, valid]}[neighbors]
    _write_response(response, runs)

    with pytest.raises(ReleaseAdmissionError):
        admit_gate_response(response, _expectation())


@pytest.mark.parametrize("repository_field", ["repository", "head_repository"])
@pytest.mark.parametrize("neighbors", ["none", "before", "after"])
@pytest.mark.parametrize(
    ("identity_field", "replacement"),
    [("id", REPOSITORY_ID + 1), ("full_name", "other/repository")],
)
def test_gate_admission_rejects_repository_identity_substitution(
    tmp_path: Path,
    repository_field: str,
    identity_field: str,
    replacement: object,
    neighbors: str,
) -> None:
    response = tmp_path / "runs.json"
    run = _run()
    repository = dict(cast(dict[str, object], run[repository_field]))
    repository[identity_field] = replacement
    run[repository_field] = repository
    valid = {**_run(), "id": 8_002}
    runs = {"none": [run], "before": [valid, run], "after": [run, valid]}[neighbors]
    _write_response(response, runs)

    with pytest.raises(ReleaseAdmissionError):
        admit_gate_response(response, _expectation())


@pytest.mark.parametrize("attempt", [2, 3])
def test_gate_admission_rejects_duplicate_run_ids_even_with_different_attempts(
    tmp_path: Path,
    attempt: int,
) -> None:
    response = tmp_path / "runs.json"
    _write_response(response, [_run(), {**_run(), "run_attempt": attempt}])

    with pytest.raises(ReleaseAdmissionError, match="duplicate run ids"):
        admit_gate_response(response, _expectation())


def test_gate_selection_is_independent_of_provider_order_and_attempt_order(tmp_path: Path) -> None:
    response = tmp_path / "runs.json"
    runs = [
        _run(),
        {**_run(), "id": 8_003, "run_attempt": 1},
        {**_run(), "id": 8_002, "run_attempt": 9},
    ]

    for ordered in permutations(runs):
        _write_response(response, list(ordered))
        assert admit_gate_response(response, _expectation()) == GateRun(8_003, 1)


@pytest.mark.parametrize("total", [10, 11, 1_000])
def test_gate_selection_uses_only_the_bounded_observed_snapshot(tmp_path: Path, total: int) -> None:
    response = tmp_path / "runs.json"
    runs = [{**_run(), "id": 8_001 + index} for index in range(10)]
    response.write_text(json.dumps({"total_count": total, "workflow_runs": runs}), encoding="utf-8")

    assert admit_gate_response(response, _expectation()) == GateRun(8_010, 2)


def test_gate_selection_does_not_claim_a_complete_provider_history(tmp_path: Path) -> None:
    response = tmp_path / "runs.json"
    response.write_text(
        json.dumps({"total_count": 20, "workflow_runs": [_run()]}), encoding="utf-8"
    )

    assert admit_gate_response(response, _expectation()) == GateRun(8_001, 2)


@pytest.mark.parametrize("total", [None, True, 0, -1, 1.0, "1"])
def test_gate_admission_rejects_invalid_total_counts(tmp_path: Path, total: object) -> None:
    response = tmp_path / "runs.json"
    response.write_text(
        json.dumps({"total_count": total, "workflow_runs": [_run()]}), encoding="utf-8"
    )

    with pytest.raises(ReleaseAdmissionError):
        admit_gate_response(response, _expectation())


@pytest.mark.parametrize(
    ("total", "runs"),
    [
        (0, []),
        (1, []),
        (1, None),
        (1, {}),
        (1, [None]),
        (1, [_run(), {**_run(), "id": 8_002}]),
        (11, [{**_run(), "id": 8_001 + index} for index in range(11)]),
    ],
)
def test_gate_admission_rejects_empty_malformed_overbound_or_inconsistent_snapshots(
    tmp_path: Path, total: int, runs: object
) -> None:
    response = tmp_path / "runs.json"
    response.write_text(json.dumps({"total_count": total, "workflow_runs": runs}), encoding="utf-8")

    with pytest.raises(ReleaseAdmissionError):
        admit_gate_response(response, _expectation())


@pytest.mark.parametrize("field", ["id", "run_attempt"])
@pytest.mark.parametrize("replacement", [None, True, 0, -1, 2.0, "2"])
def test_each_observed_run_requires_positive_integer_identity(
    tmp_path: Path, field: str, replacement: object
) -> None:
    response = tmp_path / "runs.json"
    malformed = {**_run(), "id": 8_002, field: replacement}
    _write_response(response, [_run(), malformed])

    with pytest.raises(ReleaseAdmissionError):
        admit_gate_response(response, _expectation())


def test_gate_admission_rejects_duplicate_json_members(tmp_path: Path) -> None:
    response = tmp_path / "runs.json"
    response.write_text(
        '{"total_count":1,"total_count":1,"workflow_runs":[]}',
        encoding="utf-8",
    )

    with pytest.raises(ReleaseAdmissionError, match="strict JSON"):
        admit_gate_response(response, _expectation())


def test_gate_admission_rejects_oversized_response(tmp_path: Path) -> None:
    response = tmp_path / "runs.json"
    response.write_bytes(b" " * (MAX_GATE_RESPONSE_BYTES + 1))

    with pytest.raises(ReleaseAdmissionError, match="size"):
        admit_gate_response(response, _expectation())


def test_gate_admission_rejects_a_symlinked_response(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    response = tmp_path / "runs.json"
    _write_response(target, [_run()])
    response.symlink_to(target)

    with pytest.raises(ReleaseAdmissionError, match="unavailable"):
        admit_gate_response(response, _expectation())


def test_gate_admission_rejects_a_response_changed_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = tmp_path / "runs.json"
    _write_response(response, [_run()])
    real_read = os.read
    changed = False

    def read_and_change(descriptor: int, count: int) -> bytes:
        nonlocal changed
        chunk = real_read(descriptor, count)
        if chunk and not changed:
            response.write_bytes(b"{}")
            changed = True
        return chunk

    monkeypatch.setattr(os, "read", read_and_change)

    with pytest.raises(ReleaseAdmissionError, match="changed"):
        admit_gate_response(response, _expectation())


def _coordinates() -> ReleaseCoordinates:
    return ReleaseCoordinates(
        repository=REPOSITORY,
        repository_id=REPOSITORY_ID,
        source_commit=SOURCE_COMMIT,
        source_ref=SOURCE_REF,
        gate_workflow_id=WORKFLOW_ID,
        gate_run_id=8_001,
        gate_run_attempt=2,
        release_event="workflow_dispatch",
        release_workflow_path=".github/workflows/release-artifact.yml",
        release_run_id=9_001,
        release_run_attempt=1,
    )


def test_release_identity_is_canonical_and_deterministic() -> None:
    identity = derive_release_identity(_coordinates())

    assert len(identity) == 64
    assert identity == derive_release_identity(_coordinates())


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(
            lambda value: replace(value, repository="other/repository"),
            id="repository",
        ),
        pytest.param(
            lambda value: replace(value, repository_id=REPOSITORY_ID + 1),
            id="repository-id",
        ),
        pytest.param(
            lambda value: replace(value, source_commit="b" * 40),
            id="source-commit",
        ),
        pytest.param(
            lambda value: replace(value, source_ref="refs/heads/other"),
            id="source-ref",
        ),
        pytest.param(
            lambda value: replace(value, gate_workflow_id=WORKFLOW_ID + 1),
            id="gate-workflow-id",
        ),
        pytest.param(
            lambda value: replace(value, gate_run_id=8_002),
            id="gate-run-id",
        ),
        pytest.param(
            lambda value: replace(value, gate_run_attempt=3),
            id="gate-run-attempt",
        ),
        pytest.param(
            lambda value: replace(value, release_event="push"),
            id="release-event",
        ),
        pytest.param(
            lambda value: replace(
                value,
                release_workflow_path=".github/workflows/other.yml",
            ),
            id="release-workflow-path",
        ),
        pytest.param(
            lambda value: replace(value, release_run_id=9_002),
            id="release-run-id",
        ),
        pytest.param(
            lambda value: replace(value, release_run_attempt=2),
            id="release-run-attempt",
        ),
    ],
)
def test_every_release_coordinate_changes_identity(
    mutate: Callable[[ReleaseCoordinates], ReleaseCoordinates],
) -> None:
    baseline = _coordinates()

    assert derive_release_identity(mutate(baseline)) != derive_release_identity(baseline)

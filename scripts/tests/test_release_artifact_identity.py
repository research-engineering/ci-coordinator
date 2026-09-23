from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from scripts.release_artifact_identity import (
    MAX_GATE_RESPONSE_BYTES,
    GateExpectation,
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


def test_gate_admission_returns_the_unique_exact_success(tmp_path: Path) -> None:
    response = tmp_path / "runs.json"
    _write_response(response, [_run()])

    assert admit_gate_response(response, _expectation()).run_id == 8_001
    assert admit_gate_response(response, _expectation()).run_attempt == 2


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("workflow_id", WORKFLOW_ID + 1),
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
def test_gate_admission_rejects_each_wrong_authority_field(
    tmp_path: Path,
    field: str,
    replacement: object,
) -> None:
    response = tmp_path / "runs.json"
    run = _run()
    run[field] = replacement
    _write_response(response, [run])

    with pytest.raises(ReleaseAdmissionError):
        admit_gate_response(response, _expectation())


@pytest.mark.parametrize("repository_field", ["repository", "head_repository"])
@pytest.mark.parametrize(
    ("identity_field", "replacement"),
    [("id", REPOSITORY_ID + 1), ("full_name", "other/repository")],
)
def test_gate_admission_rejects_repository_identity_substitution(
    tmp_path: Path,
    repository_field: str,
    identity_field: str,
    replacement: object,
) -> None:
    response = tmp_path / "runs.json"
    run = _run()
    repository = dict(cast(dict[str, object], run[repository_field]))
    repository[identity_field] = replacement
    run[repository_field] = repository
    _write_response(response, [run])

    with pytest.raises(ReleaseAdmissionError):
        admit_gate_response(response, _expectation())


@pytest.mark.parametrize("runs", [[], [_run(), _run()]])
def test_gate_admission_rejects_non_unique_successes(
    tmp_path: Path,
    runs: list[dict[str, object]],
) -> None:
    response = tmp_path / "runs.json"
    _write_response(response, runs)

    with pytest.raises(ReleaseAdmissionError, match="exactly one"):
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

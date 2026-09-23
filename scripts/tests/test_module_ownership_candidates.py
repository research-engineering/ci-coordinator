from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from scripts.module_ownership_candidates import (
    RepositoryPathSnapshot,
    classify_file_kind,
    fixture_candidate_inventory,
)
from scripts.module_ownership_profile import load_profile


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _snapshot(*paths: str) -> RepositoryPathSnapshot:
    ordered = tuple(sorted(paths))
    return RepositoryPathSnapshot(
        deleted_tracked_count=0,
        paths=ordered,
        tracked_count=len(ordered),
        untracked_count=0,
    )


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        ("frontend/tools/contract.test.ts", "test"),
        ("backend/alembic/versions/revision.py", "migration"),
        ("frontend/src/api/generated.ts", "generated"),
        ("backend/src/ci_coordinator/runtime/composition.py", "composition-root"),
        ("docs/architecture/INDEX.md", "documentation"),
        ("scripts/conformance/contract_test.py", "test"),
        ("scripts/check.py", "production-like-script"),
        ("docker/development/entrypoint.sh", "production-like-script"),
        (
            "backend/src/ci_coordinator/resources/profile.json",
            "declarative",
        ),
        ("backend/src/ci_coordinator/domain/model.py", "production-authority"),
        (".github/workflows/full-check.yml", "declarative"),
        ("NOTICE", "other"),
    ),
)
def test_file_kind_classification_is_ordered_and_total(path: str, expected: str) -> None:
    assert classify_file_kind(load_profile(), path) == expected


def test_fixture_inventory_is_deterministic_incomplete_and_cohort_separated(
    tmp_path: Path,
) -> None:
    profile = load_profile()
    paths = (
        "backend/src/ci_coordinator/large.py",
        "docs/large.md",
        "scripts/tests/test_large.py",
    )
    for relative in paths:
        _write(tmp_path, relative, "# line\n" * 401)

    first = fixture_candidate_inventory(profile, tmp_path, _snapshot(*paths))
    second = fixture_candidate_inventory(profile, tmp_path, _snapshot(*paths))

    assert first == second
    assert first["observedPathCount"] == 3
    assert first["dispositionCount"] == 3
    assert first["inventoryScope"] == "caller-supplied-fixture"
    assert first["candidateQueueComplete"] is False
    assert first["candidateTruncatedCount"] == 0
    assert first["inventoryDigest"] == second["inventoryDigest"]
    candidates = cast(list[dict[str, object]], first["candidates"])
    assert [(row["path"], row["cohort"]) for row in candidates] == [
        ("backend/src/ci_coordinator/large.py", "production"),
        ("scripts/tests/test_large.py", "test"),
    ]


def test_inventory_digests_are_independently_recomputable_and_content_sensitive(
    tmp_path: Path,
) -> None:
    profile = load_profile()
    candidate = "backend/src/ci_coordinator/large.py"
    non_candidate = "NOTICE"
    _write(tmp_path, candidate, "# line\n" * 401)
    _write(tmp_path, non_candidate, "first\n")
    snapshot = _snapshot(candidate, non_candidate)

    first = fixture_candidate_inventory(profile, tmp_path, snapshot)
    rows = cast(list[dict[str, object]], first["candidates"])
    expected_dispositions = [
        {
            "candidate": False,
            "cohort": "special",
            "contentDigest": hashlib.sha256(b"first\n").hexdigest(),
            "fileKind": "other",
            "metricValues": {},
            "path": non_candidate,
            "selectionReasons": [],
        },
        {
            "candidate": True,
            "cohort": "production",
            "contentDigest": hashlib.sha256(("# line\n" * 401).encode()).hexdigest(),
            "fileKind": "production-authority",
            "metricValues": {
                "first-party-import-contexts": 0,
                "physical-lines": 401,
                "recognized-public-declarations": 0,
            },
            "path": candidate,
            "selectionReasons": ["physical-lines:gt:400"],
        },
    ]
    assert (
        first["inventoryDigest"]
        == hashlib.sha256(_canonical_json_bytes(expected_dispositions)).hexdigest()
    )
    assert (
        first["candidateEvidenceDigest"] == hashlib.sha256(_canonical_json_bytes(rows)).hexdigest()
    )

    _write(tmp_path, non_candidate, "second\n")
    non_candidate_changed = fixture_candidate_inventory(profile, tmp_path, snapshot)
    assert non_candidate_changed["inventoryDigest"] != first["inventoryDigest"]
    assert non_candidate_changed["candidateEvidenceDigest"] == first["candidateEvidenceDigest"]

    _write(tmp_path, candidate, "# changed\n" + "# line\n" * 400)
    candidate_changed = fixture_candidate_inventory(profile, tmp_path, snapshot)
    assert candidate_changed["candidateEvidenceDigest"] != first["candidateEvidenceDigest"]


def test_aggregate_content_budget_fails_closed(tmp_path: Path) -> None:
    profile = load_profile()
    constrained = replace(
        profile,
        traversal_limits=replace(
            profile.traversal_limits,
            maximum_candidate_file_bytes=16,
            maximum_total_candidate_bytes=3,
        ),
    )
    paths = ("NOTICE", "README")
    for relative in paths:
        _write(tmp_path, relative, "ab")

    with pytest.raises(ValueError, match="aggregate byte bound"):
        fixture_candidate_inventory(constrained, tmp_path, _snapshot(*paths))


def test_candidate_depth_boundary_is_exact(tmp_path: Path) -> None:
    profile = load_profile()
    admitted = "/".join([*(f"d{index}" for index in range(15)), "file.txt"])
    rejected = "/".join([*(f"d{index}" for index in range(16)), "file.txt"])
    _write(tmp_path, admitted, "admitted\n")
    _write(tmp_path, rejected, "rejected\n")

    fixture_candidate_inventory(profile, tmp_path, _snapshot(admitted))
    with pytest.raises(ValueError, match="depth bound"):
        fixture_candidate_inventory(profile, tmp_path, _snapshot(rejected))


def test_candidate_fifo_is_rejected_without_opening_it(tmp_path: Path) -> None:
    profile = load_profile()
    relative = "backend/src/ci_coordinator/fifo.py"
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    os.mkfifo(path)

    with pytest.raises(
        ValueError,
        match=r"candidate file must be a bounded regular file: .*fifo\.py",
    ):
        fixture_candidate_inventory(profile, tmp_path, _snapshot(relative))


def test_candidate_symlink_is_rejected(tmp_path: Path) -> None:
    profile = load_profile()
    _write(tmp_path, "target.py", "value = 1\n")
    relative = "backend/src/ci_coordinator/alias.py"
    alias = tmp_path / relative
    alias.parent.mkdir(parents=True)
    alias.symlink_to(tmp_path / "target.py")

    with pytest.raises(
        ValueError,
        match=r"candidate file must be a bounded regular file: .*alias\.py",
    ):
        fixture_candidate_inventory(profile, tmp_path, _snapshot(relative))


def test_candidate_parent_symlink_is_rejected_with_owner_specific_error(
    tmp_path: Path,
) -> None:
    profile = load_profile()
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    _write(outside, "sample.py", "value = 1\n")
    parent = tmp_path / "backend/src/ci_coordinator"
    parent.parent.mkdir(parents=True)
    parent.symlink_to(outside, target_is_directory=True)
    relative = "backend/src/ci_coordinator/sample.py"

    with pytest.raises(
        ValueError,
        match="module ownership candidate file parent must contain only real",
    ):
        fixture_candidate_inventory(profile, tmp_path, _snapshot(relative))


@pytest.mark.parametrize(
    ("paths", "tracked_count"),
    (
        (("b", "a"), 2),
        (("a",), 0),
    ),
)
def test_invalid_path_snapshots_are_rejected(
    paths: tuple[str, ...],
    tracked_count: int,
) -> None:
    with pytest.raises(ValueError):
        RepositoryPathSnapshot(
            deleted_tracked_count=0,
            paths=paths,
            tracked_count=tracked_count,
            untracked_count=0,
        )

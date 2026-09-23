from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
from scripts.module_ownership_policy import module_ownership_report
from scripts.module_ownership_profile import (
    DEFAULT_PROFILE_PATH,
    REPO_ROOT,
    load_profile,
)
from scripts.proofkit_selective_plan import path_matches_any
from scripts.repository_paths import repository_path_matches

ProfileMutation = Callable[[dict[str, object]], None]


def _profile() -> dict[str, object]:
    parsed = json.loads((REPO_ROOT / DEFAULT_PROFILE_PATH).read_text(encoding="utf-8"))
    return cast(dict[str, object], parsed)


def _mapping(profile: dict[str, object], key: str) -> dict[str, object]:
    return cast(dict[str, object], profile[key])


def _review_metric(profile: dict[str, object], metric_id: str) -> dict[str, object]:
    rows = cast(list[dict[str, object]], _mapping(profile, "reviewSignals")["metrics"])
    return next(row for row in rows if row["metricId"] == metric_id)


def _replace_with_unknown_owner(profile: dict[str, object]) -> None:
    rules = cast(list[dict[str, object]], profile["forbiddenCoownership"])
    responsibility_sets = cast(list[list[str]], rules[0]["responsibilitySets"])
    responsibility_sets[0][1] = "frontend-api.devx"
    responsibility_sets.sort()


def _make_non_pair(profile: dict[str, object]) -> None:
    rules = cast(list[dict[str, object]], profile["forbiddenCoownership"])
    responsibility_sets = cast(list[list[str]], rules[0]["responsibilitySets"])
    responsibility_sets[0].append("frontend-api.generated")


def _write_profile(root: Path, profile: dict[str, object]) -> None:
    path = root / DEFAULT_PROFILE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(profile), encoding="utf-8")


def _write_owned_sources(root: Path) -> None:
    for relative in (
        "frontend/src/api/configActivation/client.ts",
        "frontend/src/api/controlPlaneIdentity/client.ts",
        "frontend/src/api/development/proxyPolicy.ts",
        "frontend/src/api/generated.ts",
        "frontend/src/api/providerInventory/schema.ts",
        "frontend/src/api/repositoryAttestation/client.ts",
        "frontend/src/api/shared/boundedFetch.ts",
        "frontend/src/api/workbench/client.ts",
        "frontend/src/api/workbench/limits.ts",
        "frontend/src/api/workflowDiscovery/events.ts",
        "frontend/src/api/workflowDiscovery/schema.ts",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("export {};\n", encoding="utf-8")


def test_repository_profile_and_current_frontend_api_are_admitted() -> None:
    profile = load_profile()
    report = module_ownership_report()

    assert report["state"] == "passed"
    summary = cast(dict[str, int], report["summary"])
    ownership = cast(list[dict[str, str]], report["ownership"])
    assert summary["exclusiveRootCount"] == len(profile.exclusive_roots)
    assert summary["forbiddenCoownershipRuleCount"] == len(profile.forbidden_coownership_rule_ids)
    assert summary["ownedFileCount"] == len(ownership)
    assert len({row["path"] for row in ownership}) == len(ownership)
    assert summary["requiredColocationRuleCount"] == len(profile.required_colocation_rule_ids)
    assert summary["responsibilityCount"] == len(profile.responsibilities)

    inventory = cast(dict[str, object], report["candidateInventory"])
    assert summary["candidateCount"] == inventory["candidateCount"]
    assert inventory["inventoryScope"] == "git-worktree"
    assert inventory["candidateQueueComplete"] is True
    assert inventory["candidateTruncatedCount"] == 0
    assert inventory["observedPathCount"] == inventory["dispositionCount"]
    coverage = cast(dict[str, object], report["ruleCoverage"])
    assert coverage["mechanicallyEvaluatedSemanticRuleIds"] == []


def test_compact_report_retains_content_addressed_candidate_receipt() -> None:
    full = module_ownership_report()
    compact = module_ownership_report(include_candidate_rows=False)
    full_inventory = cast(dict[str, object], full["candidateInventory"])
    candidate_rows = full_inventory.pop("candidates")
    full_inventory["candidateRowsIncluded"] = False

    assert compact == full
    inventory = cast(dict[str, object], compact["candidateInventory"])
    assert inventory["candidateCount"] == cast(dict[str, int], compact["summary"])["candidateCount"]
    canonical_rows = json.dumps(
        candidate_rows,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    assert inventory["candidateEvidenceDigest"] == hashlib.sha256(canonical_rows).hexdigest()


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        (
            lambda profile: _mapping(profile, "reviewSignals").update({"signalOnly": False}),
            "metrics must remain signal-only",
        ),
        (
            lambda profile: _mapping(profile, "reviewSignals").update({"verdictCeiling": "fail"}),
            "verdict ceiling must equal review-required",
        ),
        (
            lambda profile: _mapping(profile, "reviewSignals").update(
                {"unknownDisposition": "pass"}
            ),
            "unknown review signals must remain review-required",
        ),
        (
            lambda profile: _mapping(profile, "reviewSignals").update({"grammarId": "unversioned"}),
            "signal grammar id is invalid",
        ),
        (
            lambda profile: _mapping(profile, "reviewSignals").update(
                {"runtimeIds": ["cpython-0.0.0"]}
            ),
            "signal runtime ids differ from the contract",
        ),
        (
            lambda profile: _review_metric(profile, "physical-lines").update({"operator": "gte"}),
            "review metric boundaries differ from the contract",
        ),
        (
            _replace_with_unknown_owner,
            "unknown responsibilities",
        ),
        (
            lambda profile: _mapping(profile, "waiver").update({"requiresExpiry": False}),
            "waiver must remain exact",
        ),
        (
            lambda profile: _mapping(profile, "decision").update(
                {"requiresExactProfileEpoch": False}
            ),
            "decision semantics differ from the governing contract",
        ),
        (
            _make_non_pair,
            "must contain exactly two responsibilities",
        ),
        (
            lambda profile: _mapping(profile, "traversalLimits").update({"maximumEntries": 4097}),
            "maximumEntries must be a positive integer no greater than 4096",
        ),
        (
            lambda profile: _mapping(profile, "traversalLimits").update(
                {"gitPathDiscoveryTimeoutSeconds": 121}
            ),
            "gitPathDiscoveryTimeoutSeconds must be a positive integer no greater than 120",
        ),
        (
            lambda profile: _mapping(profile, "traversalLimits").update(
                {"maximumTotalCandidateBytes": 1}
            ),
            "maximumCandidateFileBytes cannot exceed maximumTotalCandidateBytes",
        ),
        (
            lambda profile: _mapping(profile, "candidateQueue").update(
                {"defaultMaximumCandidates": 10}
            ),
            "candidate queue semantics differ from the contract",
        ),
    ),
)
def test_profile_semantic_downgrades_fail_closed(
    tmp_path: Path,
    mutation: ProfileMutation,
    expected: str,
) -> None:
    profile = deepcopy(_profile())
    mutation(profile)
    _write_profile(tmp_path, profile)

    with pytest.raises(ValueError, match=expected):
        load_profile(tmp_path, DEFAULT_PROFILE_PATH)


def test_unowned_and_ambiguous_frontend_api_files_fail_closed(tmp_path: Path) -> None:
    profile = _profile()
    _write_profile(tmp_path, profile)
    _write_owned_sources(tmp_path)
    unowned = tmp_path / "frontend/src/api/client.ts"
    unowned.write_text("export {};\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"client\.ts must have exactly one path owner"):
        module_ownership_report(repo_root=tmp_path)

    unowned.unlink()
    responsibilities = profile["responsibilities"]
    assert isinstance(responsibilities, list)
    shared = next(
        item
        for item in responsibilities
        if item["responsibilityId"] == "frontend-api.shared-transport"
    )
    shared["pathPatterns"] = [
        "frontend/src/api/shared/*.ts",
        "frontend/src/api/workbench/*.ts",
    ]
    _write_profile(tmp_path, profile)
    with pytest.raises(
        ValueError,
        match=r"workbench/client\.ts must have exactly one path owner",
    ):
        module_ownership_report(repo_root=tmp_path)


def test_nested_file_does_not_match_single_segment_owner_pattern(
    tmp_path: Path,
) -> None:
    _write_profile(tmp_path, _profile())
    _write_owned_sources(tmp_path)
    rogue = tmp_path / "frontend/src/api/workbench/nested/rogue.ts"
    rogue.parent.mkdir()
    rogue.write_text("export {};\n", encoding="utf-8")

    with pytest.raises(ValueError, match=r"nested/rogue\.ts must have exactly one path owner"):
        module_ownership_report(repo_root=tmp_path)


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    (
        ("docs/**/*.md", "docs/INDEX.md", True),
        ("docs/**/*.md", "docs/architecture/INDEX.md", True),
        (
            "frontend/src/api/workbench/*.ts",
            "frontend/src/api/workbench/client.ts",
            True,
        ),
        (
            "frontend/src/api/workbench/*.ts",
            "frontend/src/api/workbench/nested/client.ts",
            False,
        ),
        (
            "frontend/src/api/workbench/*.ts",
            "frontend/src/api/WORKBENCH/client.ts",
            False,
        ),
    ),
)
def test_repository_glob_semantics_are_shared(pattern: str, path: str, expected: bool) -> None:
    assert repository_path_matches(pattern, path) is expected
    assert path_matches_any((pattern,), path) is expected


@pytest.mark.parametrize("extension", ("cts", "mts", "tsx"))
def test_typescript_files_are_inside_the_exclusive_ownership_scope(
    tmp_path: Path, extension: str
) -> None:
    _write_profile(tmp_path, _profile())
    _write_owned_sources(tmp_path)
    unowned = tmp_path / f"frontend/src/api/orphan.{extension}"
    unowned.write_text("export const Orphan = () => null;\n", encoding="utf-8")

    with pytest.raises(ValueError, match=rf"orphan\.{extension} must have exactly one path owner"):
        module_ownership_report(repo_root=tmp_path)


def test_duplicate_keys_and_nonfinite_numbers_are_rejected(tmp_path: Path) -> None:
    profile = json.dumps(_profile())
    path = tmp_path / DEFAULT_PROFILE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(profile.replace('"schemaVersion": 1', '"schemaVersion": 1, "schemaVersion": 1'))
    with pytest.raises(ValueError, match="duplicate key: schemaVersion"):
        load_profile(tmp_path, DEFAULT_PROFILE_PATH)

    path.write_text(profile.replace('"threshold": 400', '"threshold": NaN'))
    with pytest.raises(ValueError, match="non-finite constant: NaN"):
        load_profile(tmp_path, DEFAULT_PROFILE_PATH)


def test_profile_parent_symlink_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    _write_profile(outside, _profile())
    (tmp_path / "docs").symlink_to(outside / "docs", target_is_directory=True)

    with pytest.raises(ValueError, match="profile parent must contain only real directories"):
        load_profile(tmp_path, DEFAULT_PROFILE_PATH)


def test_profile_fifo_is_rejected_without_opening_it(tmp_path: Path) -> None:
    path = tmp_path / DEFAULT_PROFILE_PATH
    path.parent.mkdir(parents=True)
    os.mkfifo(path)

    with pytest.raises(
        ValueError,
        match="module ownership profile must be a bounded regular file",
    ):
        load_profile(tmp_path, DEFAULT_PROFILE_PATH)


def test_exclusive_root_parent_symlink_is_rejected(tmp_path: Path) -> None:
    _write_profile(tmp_path, _profile())
    alias = tmp_path / "alias"
    _write_owned_sources(alias)
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend/src").symlink_to(alias / "frontend/src", target_is_directory=True)

    with pytest.raises(ValueError, match="exclusive ownership root must contain only real"):
        module_ownership_report(repo_root=tmp_path)


@pytest.mark.parametrize(
    ("updates", "expected"),
    (
        ({"maximumDepth": 1}, "depth bound"),
        ({"maximumEntries": 1, "maximumOwnedFiles": 1}, "entry bound"),
        ({"maximumOwnedFiles": 1}, "owned-file bound"),
        ({"maximumRelativePathBytes": 1}, "path exceeds its byte bound"),
        ({"maximumTotalPathBytes": 1}, "path-byte bound"),
    ),
)
def test_exclusive_tree_resource_budgets_fail_closed(
    tmp_path: Path,
    updates: dict[str, int],
    expected: str,
) -> None:
    profile = _profile()
    limits = _mapping(profile, "traversalLimits")
    limits.update(updates)
    _write_profile(tmp_path, profile)
    _write_owned_sources(tmp_path)

    with pytest.raises(ValueError, match=expected):
        module_ownership_report(repo_root=tmp_path)

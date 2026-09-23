from __future__ import annotations

import copy
import hashlib
from pathlib import Path

import pytest
from scripts import proofkit_pending_review, proofkit_plan_check
from scripts.bounded_git import BoundedGitResult
from scripts.module_ownership_profile import ModuleOwnershipProfile, load_profile
from scripts.proofkit_changed_paths import ChangedPathContext
from scripts.proofkit_common import JsonObject, as_object, js_json_dumps, read_json_object
from scripts.proofkit_git_range import ProofkitGitRange
from scripts.proofkit_pending_review import (
    ReviewSnapshot,
    assert_review_snapshot_unchanged,
    capture_review_snapshot,
    pending_review,
)

SOURCE = "backend/src/ci_coordinator/example.py"
TEST = "scripts/tests/test_example.py"
DOCUMENT = "docs/features/example.md"
CONFIG = "compose.yaml"
BASE = "a" * 40
HEAD = "b" * 40


@pytest.fixture
def profile() -> ModuleOwnershipProfile:
    return load_profile(proofkit_plan_check.REPO_ROOT)


def _bindings() -> JsonObject:
    return {
        "requirements": [
            {
                "requirementId": "R1",
                "ownerId": "example",
                "specPath": DOCUMENT,
                "claimLevel": "L0",
                "proofState": "declared",
                "nonClaims": ["No semantic proof"],
            }
        ],
        "bindings": [
            {
                "requirementId": "R1",
                "scenarioId": f"scenario-{index}",
                "witnessId": f"witness-{index}",
                "witnessKind": "falsification",
                "witnessPath": path,
                "commandIds": ["python.test"],
                "environmentClasses": ["local-python"],
            }
            for index, path in enumerate((SOURCE, TEST, DOCUMENT, CONFIG))
        ],
        "witnessCommands": [
            {
                "commandId": "python.test",
                "command": "python -m pytest",
                "environmentClass": "local-python",
            }
        ],
    }


def _snapshot(profile: ModuleOwnershipProfile, paths: tuple[str, ...]) -> ReviewSnapshot:
    return ReviewSnapshot(
        {
            "baseCommit": BASE,
            "headCommit": HEAD,
            "classificationProfileSha256": profile.profile_digest,
            "policyFiles": [{"path": "proofkit/repo-profile.json", "sha256": "c" * 64}],
            "gitChanges": {
                "committed": {"paths": list(paths), "diffSha256": "d" * 64},
                "staged": {"paths": [], "diffSha256": "e" * 64},
                "unstaged": {"paths": [], "diffSha256": "e" * 64},
                "untracked": [],
            },
        },
        profile,
    )


def _review(
    profile: ModuleOwnershipProfile,
    paths: tuple[str, ...] = (SOURCE,),
    *,
    before: JsonObject | None = None,
    after: JsonObject | None = None,
    selection: tuple[str, ...] | None = None,
    snapshot: ReviewSnapshot | None = None,
) -> JsonObject:
    selected = paths if selection is None else selection
    return pending_review(
        snapshot=_snapshot(profile, paths) if snapshot is None else snapshot,
        bindings=_bindings() if after is None else after,
        base_bindings=_bindings() if before is None else before,
        actual_input={
            "changedPaths": list(selected),
            "touchedRequirementWitnesses": [
                {
                    "path": path,
                    "requirementIds": ["R1"],
                    "commands": ["python -m pytest"],
                }
                for path in selected
            ],
        },
        actual_report={"planState": "ok"},
    )


def test_source_change_without_changed_test_remains_explicitly_pending(
    profile: ModuleOwnershipProfile,
) -> None:
    report = _review(profile)
    assert report["reviewState"] == "pending"
    assert report["associationKind"] == "shared-requirement-candidates"
    assert report["requirements"] == [
        {
            "requirementId": "R1",
            "reviewState": "pending",
            "declarationChanged": False,
            "changedNonTestPaths": [SOURCE],
            "changedTestCohortPaths": [],
            "declaredTestCohortPaths": [TEST],
            "hasChangedTestCohortPath": False,
        }
    ]
    assert report["declarations"]["current"]["bindings"] == _bindings()["bindings"]
    assert "approval" not in js_json_dumps(report["requirements"]).lower()


def test_proof_path_and_falsification_witness_kind_do_not_imply_test(
    profile: ModuleOwnershipProfile,
) -> None:
    report = _review(profile, (SOURCE, DOCUMENT, CONFIG, TEST))
    assert {row["path"]: row["fileKind"] for row in report["changedPaths"]} == {
        SOURCE: "production-authority",
        DOCUMENT: "documentation",
        CONFIG: "declarative",
        TEST: "test",
    }
    assert report["requirements"][0]["changedTestCohortPaths"] == [TEST]
    assert report["requirements"][0]["reviewState"] == "pending"


def test_selection_only_test_does_not_become_actual_changed_test(
    profile: ModuleOwnershipProfile,
) -> None:
    report = _review(profile, selection=(SOURCE, TEST))
    assert report["selectionOnlyPaths"] == [TEST]
    assert report["requirements"][0]["changedTestCohortPaths"] == []


def test_unaffected_rows_are_digest_bound_without_repeating_them(
    profile: ModuleOwnershipProfile,
) -> None:
    original = _bindings()
    extended = copy.deepcopy(original)
    extended["requirements"].append({**original["requirements"][0], "requirementId": "R2"})
    extended["bindings"].append(
        {
            **original["bindings"][0],
            "requirementId": "R2",
            "scenarioId": "unaffected-scenario",
            "witnessId": "unaffected-witness",
            "witnessPath": "backend/src/ci_coordinator/other.py",
        }
    )
    report = _review(profile, before=extended, after=extended)
    assert report["declarations"]["current"] == original
    assert [row["requirementId"] for row in report["requirements"]] == ["R1"]
    assert (
        report["identity"]["currentBindingRelationSha256"]
        != _review(profile)["identity"]["currentBindingRelationSha256"]
    )


def test_edge_swap_with_same_paths_and_commands_changes_exact_relation(
    profile: ModuleOwnershipProfile,
) -> None:
    before = _bindings()
    after = copy.deepcopy(before)
    left, right = after["bindings"][:2]
    left["scenarioId"], right["scenarioId"] = right["scenarioId"], left["scenarioId"]
    original = _review(profile, before=before)
    swapped = _review(profile, before=before, after=after)
    assert {row["witnessPath"] for row in before["bindings"]} == {
        row["witnessPath"] for row in after["bindings"]
    }
    assert original["identitySha256"] != swapped["identitySha256"]
    identity = swapped["identity"]
    assert identity["baseBindingRelationSha256"] != identity["currentBindingRelationSha256"]
    assert swapped["declarations"]["base"]["bindings"] == before["bindings"]
    assert swapped["declarations"]["current"]["bindings"] == after["bindings"]
    assert swapped["requirements"][0]["declarationChanged"] is True


@pytest.mark.parametrize("change", ("deleted-binding", "command", "environment", "requirement"))
def test_declaration_delta_without_source_changes_is_included(
    profile: ModuleOwnershipProfile,
    change: str,
) -> None:
    before = _bindings()
    after = copy.deepcopy(before)
    if change == "deleted-binding":
        after["bindings"].pop(1)
    elif change == "command":
        after["witnessCommands"][0]["command"] = "python -m pytest -x"
    elif change == "environment":
        after["witnessCommands"][0]["environmentClass"] = "native-python"
        for row in after["bindings"]:
            row["environmentClasses"] = ["native-python"]
    else:
        after["requirements"][0]["nonClaims"] = ["A different semantic limit"]
    report = _review(profile, (), before=before, after=after)
    assert report["requirements"][0]["declarationChanged"] is True
    assert report["requirements"][0]["hasChangedTestCohortPath"] is False
    assert report["declarations"]["base"] == before
    assert report["declarations"]["current"] == after


@pytest.mark.parametrize("change", ("base", "head", "policy", "classification", "diff"))
def test_epoch_changes_invalidate_review_identity_and_closeout(
    profile: ModuleOwnershipProfile,
    change: str,
) -> None:
    before = _snapshot(profile, (SOURCE,))
    identity = copy.deepcopy(before.identity)
    if change in {"base", "head"}:
        identity[f"{change}Commit"] = "f" * 40
    elif change == "policy":
        identity["policyFiles"][0]["sha256"] = "f" * 64
    elif change == "classification":
        identity["classificationProfileSha256"] = "f" * 64
    else:
        identity["gitChanges"]["committed"]["diffSha256"] = "f" * 64
    after = ReviewSnapshot(identity, profile)
    assert (
        _review(profile, snapshot=before)["identitySha256"]
        != _review(
            profile,
            snapshot=after,
        )["identitySha256"]
    )
    with pytest.raises(ValueError, match="subject or policy changed"):
        assert_review_snapshot_unchanged(before, after)


def test_snapshot_retains_actual_git_origins_and_byte_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: ModuleOwnershipProfile,
) -> None:
    (tmp_path / "untracked.txt").write_bytes(b"untracked\x80")
    monkeypatch.setattr(proofkit_pending_review, "POLICY_PATHS", ())
    monkeypatch.setattr(proofkit_pending_review, "load_profile", lambda _root: profile)
    monkeypatch.setattr(
        ProofkitGitRange,
        "resolve_commit",
        lambda *_args: HEAD,
    )

    def paths(_self: object, arguments: tuple[str, ...]) -> list[str]:
        if arguments[0] == "ls-files":
            return ["untracked.txt"]
        if BASE in arguments:
            return [SOURCE]
        return [TEST] if "--cached" in arguments else [CONFIG]

    monkeypatch.setattr(ProofkitGitRange, "git_paths", paths)
    monkeypatch.setattr(
        proofkit_pending_review,
        "run_git",
        lambda *_args, **_kwargs: BoundedGitResult(0, "patch\udc80", ""),
    )
    snapshot = capture_review_snapshot(
        ChangedPathContext(BASE, HEAD, (SOURCE,)), repo_root=tmp_path
    )
    changes = snapshot.identity["gitChanges"]
    assert changes["committed"]["paths"] == [SOURCE]
    assert changes["staged"]["paths"] == [TEST]
    assert changes["unstaged"]["paths"] == [CONFIG]
    assert changes["committed"]["diffSha256"] == hashlib.sha256(b"patch\x80").hexdigest()
    assert changes["untracked"][0]["sha256"] == hashlib.sha256(b"untracked\x80").hexdigest()


def test_full_artifact_precedes_small_console_summary_and_pending_does_not_fail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report: JsonObject = {
        "schemaVersion": 1,
        "reportId": "ci-coordinator.proofkit-plan-check",
        "reportKind": "ci-coordinator.proofkit-plan-check",
        "state": "passed",
        "summary": {"pendingRequirementCount": 1},
        "nonClaims": ["No semantic approval."],
        "pendingReview": {"reviewState": "pending", "fullEvidence": "x" * 100_000},
    }
    monkeypatch.setattr(proofkit_plan_check, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(proofkit_plan_check, "plan_check_report", lambda _args: report)
    published: list[object] = []

    def sink(summary: object) -> None:
        assert read_json_object(tmp_path / proofkit_plan_check.PLAN_CHECK_ARTIFACT) == report
        assert len(js_json_dumps(summary).encode()) < 1024
        for key in ("schemaVersion", "reportId", "reportKind", "state", "summary", "nonClaims"):
            assert as_object(summary, "console")[key] == report[key]
        assert (
            as_object(summary, "console")["artifactSha256"]
            == hashlib.sha256(
                (tmp_path / proofkit_plan_check.PLAN_CHECK_ARTIFACT).read_bytes()
            ).hexdigest()
        )
        published.append(summary)

    monkeypatch.setattr(proofkit_plan_check, "write_json", sink)
    assert proofkit_plan_check.main([]) == 0
    assert len(published) == 1


def test_structural_failure_has_failed_artifact_and_exit_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proofkit_plan_check, "REPO_ROOT", tmp_path)

    def fail(_args: object) -> JsonObject:
        raise ValueError("unbound proof-like path")

    monkeypatch.setattr(proofkit_plan_check, "plan_check_report", fail)
    assert proofkit_plan_check.main([]) == 1
    artifact = read_json_object(tmp_path / proofkit_plan_check.PLAN_CHECK_ARTIFACT)
    assert artifact["state"] == "failed"
    assert artifact["failure"]["message"] == "unbound proof-like path"


def test_oversized_report_replaces_stale_success_with_failure_without_truncation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(proofkit_plan_check, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(proofkit_plan_check, "MAX_PLANNER_OUTPUT_BYTES", 1024)
    artifact = tmp_path / proofkit_plan_check.PLAN_CHECK_ARTIFACT
    artifact.parent.mkdir()
    artifact.write_text('{"state":"passed"}')
    monkeypatch.setattr(
        proofkit_plan_check,
        "plan_check_report",
        lambda _args: {
            "reportId": "ci-coordinator.proofkit-plan-check",
            "state": "passed",
            "pendingReview": {"bindings": "x" * 2048},
        },
    )
    assert proofkit_plan_check.main([]) == 1
    assert read_json_object(artifact)["state"] == "failed"
    assert "pendingReview" not in read_json_object(artifact)


def test_artifact_parent_symlink_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, outside = tmp_path / "repository", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / ".ci-evidence").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(proofkit_plan_check, "REPO_ROOT", root)
    monkeypatch.setattr(
        proofkit_plan_check,
        "plan_check_report",
        lambda _args: {
            "reportId": "ci-coordinator.proofkit-plan-check",
            "state": "passed",
        },
    )
    assert proofkit_plan_check.main([]) == 1
    assert list(outside.iterdir()) == []

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from scripts.bounded_git import DEFAULT_GIT_OUTPUT_BYTES, git_stdout_bytes, run_git
from scripts.module_ownership_candidates import classify_file_kind
from scripts.module_ownership_profile import ModuleOwnershipProfile, load_profile
from scripts.proofkit_changed_paths import ChangedPathContext
from scripts.proofkit_common import JsonObject, as_array, as_object, js_json_dumps, safe_repo_path
from scripts.proofkit_git_range import ProofkitGitRange
from scripts.proofkit_route_contract import legacy_relation_digest
from scripts.repository_paths import read_repository_regular_file

# These are the consumed planning policies and the owners of this derived view.
# Route/requirement authority is bound separately by the full normalized projection.
POLICY_PATHS = (
    "proofkit/repo-profile.json",
    "proofkit/witness-plan-input.json",
    "proofkit/proof-owner-retirements.json",
    "scripts/proofkit_plan_check.py",
    "scripts/proofkit_pending_review.py",
    "scripts/proofkit_selective_plan.py",
    "scripts/proofkit_selective_contract.py",
    "scripts/proofkit_route_contract.py",
    "scripts/proofkit_route_sources.py",
    "scripts/module_ownership_candidates.py",
    "scripts/module_ownership_profile.py",
)


@dataclass(frozen=True, slots=True)
class ReviewSnapshot:
    identity: JsonObject
    profile: ModuleOwnershipProfile


def json_digest(value: object) -> str:
    return hashlib.sha256(js_json_dumps(value).encode("utf-8")).hexdigest()


def capture_review_snapshot(context: ChangedPathContext, *, repo_root: Path) -> ReviewSnapshot:
    git = ProofkitGitRange(repo_root)
    if git.resolve_commit("HEAD", "pending review checkout") != context.head_ref:
        raise ValueError("pending review checkout differs from the admitted plan head")
    path_arguments = {
        "committed": ("diff", context.base_ref, context.head_ref),
        "staged": ("diff", "--cached", context.head_ref),
        "unstaged": ("diff",),
    }
    changes: JsonObject = {}
    for origin, arguments in path_arguments.items():
        paths = git.git_paths((*arguments, "--name-only", "--no-renames", "-z", "--"))
        patch = run_git(
            repo_root,
            (*arguments, "--binary", "--no-ext-diff", "--no-textconv", "--no-renames", "--"),
            decode_errors="surrogateescape",
        )
        changes[origin] = {
            "paths": sorted(paths),
            "diffSha256": hashlib.sha256(git_stdout_bytes(patch)).hexdigest(),
        }
    untracked: list[JsonObject] = []
    remaining = DEFAULT_GIT_OUTPUT_BYTES
    for path in sorted(git.git_paths(("ls-files", "--others", "--exclude-standard", "-z"))):
        raw = read_repository_regular_file(
            repo_root,
            Path(safe_repo_path(path)),
            "pending review untracked input",
            maximum_bytes=max(1, remaining),
        )
        remaining -= len(raw)
        if remaining < 0:
            raise ValueError("pending review untracked inputs exceed their total byte bound")
        untracked.append(
            {
                "path": path,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "mode": (repo_root / path).stat().st_mode & 0o777,
            }
        )
    changes["untracked"] = untracked
    profile = load_profile(repo_root)
    policies = [
        {
            "path": path,
            "sha256": hashlib.sha256(
                read_repository_regular_file(
                    repo_root,
                    Path(path),
                    "pending review policy",
                    maximum_bytes=DEFAULT_GIT_OUTPUT_BYTES,
                )
            ).hexdigest(),
        }
        for path in POLICY_PATHS
    ]
    return ReviewSnapshot(
        {
            "baseCommit": context.base_ref,
            "headCommit": context.head_ref,
            "gitChanges": changes,
            "policyFiles": policies,
            "classificationProfileSha256": profile.profile_digest,
        },
        profile,
    )


def assert_review_snapshot_unchanged(before: ReviewSnapshot, after: ReviewSnapshot) -> None:
    if before.identity != after.identity:
        raise ValueError("pending review Git subject or policy changed during plan admission")


def pending_review(
    *,
    snapshot: ReviewSnapshot,
    bindings: Mapping[str, object],
    base_bindings: Mapping[str, object],
    actual_input: Mapping[str, object],
    actual_report: Mapping[str, object],
) -> JsonObject:
    changes = snapshot.identity["gitChanges"]
    actual_paths = set().union(
        *(
            changes[origin]["paths"]
            for origin in (
                "committed",
                "staged",
                "unstaged",
            )
        )
    ) | {row["path"] for row in changes["untracked"]}
    selected_paths = set(as_array(actual_input["changedPaths"], "changed paths"))
    touched = _rows(actual_input["touchedRequirementWitnesses"], "touched witnesses")
    affected = {requirement for row in touched for requirement in row["requirementIds"]}
    declaration_changes = _changed_declaration_requirements(base_bindings, bindings)
    affected.update(declaration_changes)
    for document in (base_bindings, bindings):
        for row in _rows(document["bindings"], "bindings"):
            if row["witnessPath"] in actual_paths:
                affected.add(row["requirementId"])
        for row in _rows(document["requirements"], "requirements"):
            if row["specPath"] in actual_paths:
                affected.add(row["requirementId"])
    declarations = {
        "base": _scoped_declarations(base_bindings, affected),
        "current": _scoped_declarations(bindings, affected),
    }
    groups: list[JsonObject] = []
    for requirement_id in sorted(affected):
        paths: set[str] = set()
        for document in declarations.values():
            paths.update(
                row["witnessPath"]
                for row in document["bindings"]
                if row["requirementId"] == requirement_id
            )
            paths.update(
                row["specPath"]
                for row in document["requirements"]
                if row["requirementId"] == requirement_id
            )
        test_paths = {
            path for path in paths if classify_file_kind(snapshot.profile, path) == "test"
        }
        changed_tests = sorted(test_paths & actual_paths)
        groups.append(
            {
                "requirementId": requirement_id,
                "reviewState": "pending",
                "declarationChanged": requirement_id in declaration_changes,
                "changedNonTestPaths": sorted((paths - test_paths) & actual_paths),
                "changedTestCohortPaths": changed_tests,
                "declaredTestCohortPaths": sorted(test_paths),
                "hasChangedTestCohortPath": bool(changed_tests),
            }
        )
    identity: JsonObject = {
        **snapshot.identity,
        "baseBindingRelationSha256": legacy_relation_digest(base_bindings),
        "currentBindingRelationSha256": legacy_relation_digest(bindings),
        "baseBindingProjectionSha256": json_digest(base_bindings),
        "currentBindingProjectionSha256": json_digest(bindings),
        "planningInputSha256": json_digest(actual_input),
        "nativePlanSha256": json_digest(actual_report),
    }
    return {
        "schemaVersion": 1,
        "reviewState": "pending",
        "associationKind": "shared-requirement-candidates",
        "identitySha256": json_digest(identity),
        "identity": identity,
        "changedPaths": [
            {
                "path": path,
                "fileKind": classify_file_kind(snapshot.profile, path),
                "selectedForPlan": path in selected_paths,
            }
            for path in sorted(actual_paths)
        ],
        "selectionOnlyPaths": sorted(selected_paths - actual_paths),
        "requirements": groups,
        "declarations": declarations,
        "nonClaims": [
            "Shared requirements declare candidate associations, not source-to-test causal edges.",
            "Test-cohort paths include fixtures and helpers; changes prove no changed assertion.",
            "Pending review neither approves semantic adequacy nor blocks structural admission.",
            "Git observations are compared before/after admission, not an atomic snapshot.",
            "This view selects no additional execution and imposes no reviewer or approval count.",
        ],
    }


def _scoped_declarations(document: Mapping[str, object], affected: set[str]) -> JsonObject:
    rows = [
        row for row in _rows(document["bindings"], "bindings") if row["requirementId"] in affected
    ]
    command_ids = {command for row in rows for command in row["commandIds"]}
    return {
        "requirements": [
            row
            for row in _rows(document["requirements"], "requirements")
            if row["requirementId"] in affected
        ],
        "bindings": rows,
        "witnessCommands": [
            row
            for row in _rows(document["witnessCommands"], "commands")
            if row["commandId"] in command_ids
        ],
    }


def _changed_declaration_requirements(
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> set[str]:
    affected: set[str] = set()
    for field in ("requirements", "bindings"):
        old = {js_json_dumps(row): row for row in _rows(before[field], field)}
        new = {js_json_dumps(row): row for row in _rows(after[field], field)}
        affected.update((old | new)[key]["requirementId"] for key in old.keys() ^ new.keys())
    old_commands = {row["commandId"]: row for row in _rows(before["witnessCommands"], "commands")}
    new_commands = {row["commandId"]: row for row in _rows(after["witnessCommands"], "commands")}
    changed_commands = {
        key
        for key in old_commands.keys() | new_commands.keys()
        if old_commands.get(key) != new_commands.get(key)
    }
    for document in (before, after):
        affected.update(
            row["requirementId"]
            for row in _rows(document["bindings"], "bindings")
            if changed_commands.intersection(row["commandIds"])
        )
    return affected


def _rows(value: object, label: str) -> list[JsonObject]:
    return [as_object(row, label) for row in as_array(value, label)]

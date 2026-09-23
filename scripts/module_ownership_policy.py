from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

from scripts.module_ownership_candidates import candidate_inventory
from scripts.module_ownership_profile import (
    DEFAULT_PROFILE_PATH,
    REPO_ROOT,
    ModuleOwnershipProfile,
    TraversalLimits,
    load_profile,
)
from scripts.proofkit_common import write_json
from scripts.repository_paths import real_repository_directory, repository_path_matches


def module_ownership_report(
    *,
    repo_root: Path = REPO_ROOT,
    profile_path: Path = DEFAULT_PROFILE_PATH,
    include_candidate_rows: bool = True,
) -> dict[str, object]:
    profile = load_profile(repo_root, profile_path)
    ownership = validate_exclusive_ownership(profile, repo_root)
    inventory = candidate_inventory(profile, repo_root)
    candidates = inventory["candidates"]
    if not isinstance(candidates, list):
        raise TypeError("candidate inventory must expose a candidate list")
    inventory["candidateRowsIncluded"] = include_candidate_rows
    if not include_candidate_rows:
        del inventory["candidates"]
    return {
        "schemaVersion": 1,
        "reportId": "ci-coordinator.module-ownership",
        "reportKind": "ci-coordinator.module-ownership",
        "state": "passed",
        "summary": {
            "candidateCount": len(candidates),
            "exclusiveRootCount": len(profile.exclusive_roots),
            "forbiddenCoownershipRuleCount": len(profile.forbidden_coownership_rule_ids),
            "ownedFileCount": len(ownership),
            "requiredColocationRuleCount": len(profile.required_colocation_rule_ids),
            "responsibilityCount": len(profile.responsibilities),
        },
        "ownership": ownership,
        "candidateInventory": inventory,
        "ruleCoverage": {
            "declaredForbiddenCoownershipRuleIds": list(profile.forbidden_coownership_rule_ids),
            "declaredRequiredColocationRuleIds": list(profile.required_colocation_rule_ids),
            "mechanicallyEvaluatedSemanticRuleIds": [],
        },
        "nonClaims": [
            "Path ownership and profile admission do not prove semantic cohesion.",
            "Review signals do not prove a blockable architecture violation.",
            "Declared semantic rules are not mechanically evaluated by this report.",
            "Static traversal admission is not a sandbox against concurrent workspace mutation.",
        ],
    }


def validate_exclusive_ownership(
    profile: ModuleOwnershipProfile, repo_root: Path
) -> list[dict[str, str]]:
    responsibilities = {
        item.responsibility_id: item.path_patterns for item in profile.responsibilities
    }
    result: list[dict[str, str]] = []
    lexical_root = Path(os.path.abspath(repo_root))
    for ownership_root in profile.exclusive_roots:
        directory = real_repository_directory(
            lexical_root,
            ownership_root.root,
            "exclusive ownership root",
        )
        owned_paths = _bounded_owned_paths(
            directory=directory,
            lexical_root=lexical_root,
            limits=profile.traversal_limits,
            suffixes=set(ownership_root.suffixes),
        )
        for relative in owned_paths:
            matches = [
                owner_id
                for owner_id in ownership_root.responsibility_ids
                if any(
                    repository_path_matches(pattern, relative)
                    for pattern in responsibilities[owner_id]
                )
            ]
            if len(matches) != 1:
                raise ValueError(f"{relative} must have exactly one path owner; observed={matches}")
            result.append({"path": relative, "responsibilityId": matches[0]})
    result.sort(key=lambda row: row["path"])
    return result


def _bounded_owned_paths(
    *,
    directory: Path,
    lexical_root: Path,
    limits: TraversalLimits,
    suffixes: set[str],
) -> list[str]:
    entry_count = 0
    owned_file_count = 0
    total_path_bytes = 0
    result: list[str] = []

    def visit(current: Path, depth: int) -> None:
        nonlocal entry_count, owned_file_count, total_path_bytes
        with os.scandir(current) as entries:
            for entry in entries:
                entry_count += 1
                if entry_count > limits.maximum_entries:
                    raise ValueError("exclusive ownership tree exceeds its entry bound")
                entry_depth = depth + 1
                if entry_depth > limits.maximum_depth:
                    raise ValueError("exclusive ownership tree exceeds its depth bound")
                path = Path(entry.path)
                relative = path.relative_to(lexical_root).as_posix()
                path_bytes = len(relative.encode("utf-8"))
                if path_bytes > limits.maximum_relative_path_bytes:
                    raise ValueError("exclusive ownership path exceeds its byte bound")
                total_path_bytes += path_bytes
                if total_path_bytes > limits.maximum_total_path_bytes:
                    raise ValueError("exclusive ownership tree exceeds its path-byte bound")
                observed = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(observed.st_mode):
                    raise ValueError(f"exclusive ownership tree contains a symlink: {relative}")
                if stat.S_ISDIR(observed.st_mode):
                    visit(path, entry_depth)
                    continue
                if not stat.S_ISREG(observed.st_mode):
                    raise ValueError(
                        f"exclusive ownership tree contains a special file: {relative}"
                    )
                if path.suffix not in suffixes:
                    continue
                owned_file_count += 1
                if owned_file_count > limits.maximum_owned_files:
                    raise ValueError("exclusive ownership tree exceeds its owned-file bound")
                result.append(relative)

    visit(directory, 0)
    result.sort()
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate module ownership and emit its deterministic evidence."
    )
    parser.add_argument(
        "--full-candidate-ledger",
        action="store_true",
        help="include per-candidate evidence rows instead of only their digest",
    )
    arguments = parser.parse_args(argv)
    try:
        write_json(module_ownership_report(include_candidate_rows=arguments.full_candidate_ledger))
    except (OSError, TypeError, UnicodeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

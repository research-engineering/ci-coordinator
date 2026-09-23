"""Risk-owned admission for coverage.py JSON evidence."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from scripts.proofkit_changed_paths import changed_path_context_from_git_context
from scripts.proofkit_common import safe_repo_path
from scripts.proofkit_git_range import ProofkitGitRange
from scripts.repository_paths import (
    read_bounded_regular_file,
    read_repository_regular_file,
)

POLICY_RELATIVE_PATH: Final = "backend/coverage-policy.v1.json"
_MAXIMUM_COVERAGE_POLICY_BYTES: Final = 1_048_576
_MAXIMUM_COVERAGE_REPORT_BYTES: Final = 67_108_864
_POLICY_KEYS: Final = frozenset(
    {
        "changedCriticalMinimum",
        "nonClaims",
        "ownerGroups",
        "policyId",
        "schemaVersion",
        "sourceRoot",
    }
)
_FLOOR_KEYS: Final = frozenset({"branchPercent", "statementPercent"})
_OWNER_KEYS: Final = frozenset({"branchPercent", "ownerId", "paths", "statementPercent"})


@dataclass(frozen=True, slots=True)
class CoverageMetric:
    covered_statements: int
    statement_count: int
    covered_branches: int
    branch_count: int

    def plus(self, other: CoverageMetric) -> CoverageMetric:
        return CoverageMetric(
            self.covered_statements + other.covered_statements,
            self.statement_count + other.statement_count,
            self.covered_branches + other.covered_branches,
            self.branch_count + other.branch_count,
        )


@dataclass(frozen=True, slots=True)
class CoverageFloor:
    statement_percent: int
    branch_percent: int


@dataclass(frozen=True, slots=True)
class OwnerCoverage:
    owner_id: str
    paths: tuple[str, ...]
    floor: CoverageFloor


@dataclass(frozen=True, slots=True)
class CoveragePolicy:
    policy_id: str
    source_root: str
    owners: tuple[OwnerCoverage, ...]
    changed_critical_floor: CoverageFloor
    non_claims: tuple[str, ...]

    @property
    def critical_paths(self) -> tuple[str, ...]:
        return tuple(path for owner in self.owners for path in owner.paths)


def load_coverage_policy(
    repo_root: Path,
    *,
    policy_path: Path | None = None,
) -> CoveragePolicy:
    root = Path(os.path.abspath(repo_root))
    path = policy_path or repo_root / POLICY_RELATIVE_PATH
    value = _read_unique_object(
        path,
        confinement_root=root,
        maximum_bytes=_MAXIMUM_COVERAGE_POLICY_BYTES,
    )
    if (
        set(value) != _POLICY_KEYS
        or value.get("schemaVersion") != "ci-coordinator-python-coverage-policy/v1"
    ):
        raise ValueError("Python coverage policy root is invalid")
    policy_id = _text(value.get("policyId"), "coverage policy id")
    source_root = safe_repo_path(value.get("sourceRoot"))
    changed_floor = _floor(value.get("changedCriticalMinimum"), "changed critical floor")
    non_claims = _ordered_text(value.get("nonClaims"), "coverage policy non-claims")
    raw_owners = value.get("ownerGroups")
    if not isinstance(raw_owners, list) or not raw_owners:
        raise ValueError("coverage policy ownerGroups must be a non-empty array")
    owners = tuple(_owner(raw_owner) for raw_owner in cast(list[object], raw_owners))
    owner_ids = tuple(owner.owner_id for owner in owners)
    paths = tuple(path for owner in owners for path in owner.paths)
    if owner_ids != tuple(sorted(set(owner_ids))):
        raise ValueError("coverage policy owners must be unique and sorted")
    if len(paths) != len(set(paths)):
        raise ValueError("coverage policy paths must have exactly one owner")
    for relative_path in paths:
        source_path = root / source_root / relative_path
        if source_path.is_symlink() or not source_path.is_file():
            raise ValueError(f"coverage policy path is not a regular source file: {relative_path}")
    return CoveragePolicy(policy_id, source_root, owners, changed_floor, non_claims)


def changed_paths_for_coverage(
    repo_root: Path,
    environment: Mapping[str, str],
) -> tuple[str, ...] | None:
    base_ref = environment.get("PROOFKIT_BASE_REF", "").strip()
    head_ref = environment.get("PROOFKIT_HEAD_REF", "").strip()
    in_ci = environment.get("CI") == "true" or environment.get("GITHUB_ACTIONS") == "true"
    if in_ci and not base_ref:
        return None
    context_environment = dict(environment)
    if not base_ref:
        context_environment.pop("PROOFKIT_HEAD_REF", None)
        context_environment.pop("CI", None)
        context_environment.pop("GITHUB_ACTIONS", None)
    git_range = ProofkitGitRange(repo_root)
    context = changed_path_context_from_git_context(
        args=(),
        env=context_environment,
        normalize_path=safe_repo_path,
        git_paths=git_range.git_paths,
        committed_paths_since=git_range.committed_paths_since,
    )
    if base_ref and head_ref and context.head_ref != head_ref:
        raise ValueError("coverage changed-path context did not preserve the admitted head")
    return context.paths


def evaluate_coverage_report(
    coverage_path: Path,
    *,
    repo_root: Path,
    changed_paths: Sequence[str] | None,
    policy_path: Path | None = None,
) -> dict[str, object]:
    policy = load_coverage_policy(repo_root, policy_path=policy_path)
    coverage = _read_unique_object(
        coverage_path,
        maximum_bytes=_MAXIMUM_COVERAGE_REPORT_BYTES,
    )
    files = coverage.get("files")
    totals = coverage.get("totals")
    if not isinstance(files, dict) or not isinstance(totals, dict):
        raise TypeError("coverage report files and totals must be objects")
    file_metrics = {
        path: _metric(cast(Mapping[str, object], _object(record, f"coverage file {path}")))
        for path, record in files.items()
        if isinstance(path, str)
    }
    missing = sorted(set(policy.critical_paths) - file_metrics.keys())
    if missing:
        raise ValueError(f"coverage report omits policy paths: {', '.join(missing)}")

    violations: list[dict[str, object]] = []
    owner_rows: list[dict[str, object]] = []
    for owner in policy.owners:
        metric = _sum_metrics(file_metrics[path] for path in owner.paths)
        row = _metric_row(metric, owner.floor)
        row.update(ownerId=owner.owner_id, paths=list(owner.paths))
        owner_rows.append(row)
        violations.extend(_floor_violations(metric, owner.floor, owner.owner_id, "owner"))

    changed_path_set = None if changed_paths is None else set(changed_paths)
    selected = (
        set(policy.critical_paths)
        if changed_path_set is None
        else {
            path
            for path in policy.critical_paths
            if f"{policy.source_root}/{path}" in changed_path_set
        }
    )
    changed_rows: list[dict[str, object]] = []
    for path in sorted(selected):
        metric = file_metrics[path]
        row = _metric_row(metric, policy.changed_critical_floor)
        row["path"] = path
        changed_rows.append(row)
        violations.extend(
            _floor_violations(metric, policy.changed_critical_floor, path, "changed-critical")
        )

    aggregate = _metric(cast(Mapping[str, object], totals))
    return {
        "schemaVersion": 1,
        "reportKind": "ci-coordinator.python-risk-coverage",
        "policyId": policy.policy_id,
        "state": "passed" if not violations else "failed",
        "aggregateInformational": _metric_row(aggregate, None),
        "owners": owner_rows,
        "changedCritical": changed_rows,
        "violations": violations,
        "nonClaims": list(policy.non_claims),
    }


def _owner(value: object) -> OwnerCoverage:
    record = _object(value, "coverage owner")
    if set(record) != _OWNER_KEYS:
        raise ValueError("coverage owner fields are invalid")
    owner_id = _text(record.get("ownerId"), "coverage owner id")
    paths = tuple(safe_repo_path(path) for path in _ordered_text(record.get("paths"), "paths"))
    return OwnerCoverage(
        owner_id,
        paths,
        CoverageFloor(
            _percent(record.get("statementPercent"), "owner statement floor"),
            _percent(record.get("branchPercent"), "owner branch floor"),
        ),
    )


def _floor(value: object, label: str) -> CoverageFloor:
    record = _object(value, label)
    if set(record) != _FLOOR_KEYS:
        raise ValueError(f"{label} fields are invalid")
    return CoverageFloor(
        _percent(record.get("statementPercent"), f"{label} statement floor"),
        _percent(record.get("branchPercent"), f"{label} branch floor"),
    )


def _metric(record: Mapping[str, object]) -> CoverageMetric:
    summary_value = record.get("summary", record)
    summary = _object(summary_value, "coverage summary")
    metric = CoverageMetric(
        _count(summary.get("covered_lines"), "covered statements"),
        _count(summary.get("num_statements"), "statement count"),
        _count(summary.get("covered_branches"), "covered branches"),
        _count(summary.get("num_branches"), "branch count"),
    )
    if (
        metric.covered_statements > metric.statement_count
        or metric.covered_branches > metric.branch_count
    ):
        raise ValueError("coverage counters are internally inconsistent")
    return metric


def _sum_metrics(metrics: Iterable[CoverageMetric]) -> CoverageMetric:
    result = CoverageMetric(0, 0, 0, 0)
    for metric in metrics:
        result = result.plus(metric)
    return result


def _metric_row(metric: CoverageMetric, floor: CoverageFloor | None) -> dict[str, object]:
    row: dict[str, object] = {
        "coveredStatements": metric.covered_statements,
        "statementCount": metric.statement_count,
        "statementPercent": _ratio(metric.covered_statements, metric.statement_count),
        "coveredBranches": metric.covered_branches,
        "branchCount": metric.branch_count,
        "branchPercent": _ratio(metric.covered_branches, metric.branch_count),
    }
    if floor is not None:
        row["minimumStatementPercent"] = floor.statement_percent
        row["minimumBranchPercent"] = floor.branch_percent
    return row


def _floor_violations(
    metric: CoverageMetric,
    floor: CoverageFloor,
    subject: str,
    scope: str,
) -> list[dict[str, object]]:
    violations = []
    for dimension, covered, total, minimum in (
        (
            "statements",
            metric.covered_statements,
            metric.statement_count,
            floor.statement_percent,
        ),
        (
            "branches",
            metric.covered_branches,
            metric.branch_count,
            floor.branch_percent,
        ),
    ):
        if total > 0 and covered * 100 < minimum * total:
            violations.append(
                {
                    "dimension": dimension,
                    "minimumPercent": minimum,
                    "observedPercent": _ratio(covered, total),
                    "scope": scope,
                    "subject": subject,
                }
            )
    return violations


def _ratio(covered: int, total: int) -> float:
    return 100.0 if total == 0 else round(covered * 100 / total, 2)


def _read_unique_object(
    path: Path,
    *,
    confinement_root: Path | None = None,
    maximum_bytes: int,
) -> dict[str, object]:
    try:
        if confinement_root is None:
            content = read_bounded_regular_file(
                path,
                "Python coverage evidence",
                maximum_bytes=maximum_bytes,
            )
        else:
            root = Path(os.path.abspath(confinement_root))
            relative_path = Path(os.path.abspath(path)).relative_to(root)
            content = read_repository_regular_file(
                root,
                relative_path,
                "Python coverage policy",
                maximum_bytes=maximum_bytes,
            )
        value = json.loads(content, object_pairs_hook=_unique_object)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError(f"invalid JSON document: {path}") from error
    return _object(value, str(path))


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object")
    return cast(dict[str, object], value)


def _ordered_text(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a non-empty string array")
    result = tuple(cast(list[str], value))
    if result != tuple(sorted(set(result))):
        raise ValueError(f"{label} must be unique and sorted")
    return result


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be non-empty text")
    return value


def _percent(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value <= 100:
        raise ValueError(f"{label} must be an integer from 0 through 100")
    return value


def _count(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    report_path = Path(os.environ.get("COVERAGE_JSON", "backend/coverage.json"))
    result = evaluate_coverage_report(
        report_path,
        repo_root=root,
        changed_paths=changed_paths_for_coverage(root, os.environ),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["state"] == "passed" else 1)

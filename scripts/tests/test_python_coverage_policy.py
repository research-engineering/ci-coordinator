from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pytest
from scripts.python_coverage_policy import (
    evaluate_coverage_report,
    load_coverage_policy,
)


def test_owner_and_changed_critical_floors_are_independently_enforced(
    tmp_path: Path,
) -> None:
    policy_path, coverage_path = _fixture(tmp_path)

    owner_failure = evaluate_coverage_report(
        coverage_path,
        repo_root=tmp_path,
        changed_paths=(),
        policy_path=policy_path,
    )
    changed_failure = evaluate_coverage_report(
        coverage_path,
        repo_root=tmp_path,
        changed_paths=("backend/src/example/a.py",),
        policy_path=policy_path,
    )

    assert owner_failure["state"] == "failed"
    assert owner_failure["violations"] == [
        {
            "dimension": "branches",
            "minimumPercent": 80,
            "observedPercent": 75.0,
            "scope": "owner",
            "subject": "example",
        }
    ]
    assert changed_failure["state"] == "failed"
    violations = cast(list[dict[str, object]], changed_failure["violations"])
    assert {violation["scope"] for violation in violations} == {
        "changed-critical",
        "owner",
    }


def test_full_scope_checks_every_critical_file(tmp_path: Path) -> None:
    policy_path, coverage_path = _fixture(tmp_path)

    report = evaluate_coverage_report(
        coverage_path,
        repo_root=tmp_path,
        changed_paths=None,
        policy_path=policy_path,
    )

    changed_critical = cast(list[dict[str, object]], report["changedCritical"])
    assert [row["path"] for row in changed_critical] == [
        "src/example/a.py",
        "src/example/b.py",
    ]


def test_policy_rejects_duplicate_ownership(tmp_path: Path) -> None:
    policy_path, _coverage_path = _fixture(tmp_path)
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["ownerGroups"].append(
        {
            "branchPercent": 0,
            "ownerId": "second",
            "paths": ["src/example/a.py"],
            "statementPercent": 0,
        }
    )
    policy_path.write_text(json.dumps(policy), encoding="utf-8")

    with pytest.raises(ValueError, match="exactly one owner"):
        load_coverage_policy(tmp_path, policy_path=policy_path)


def test_report_rejects_missing_policy_path(tmp_path: Path) -> None:
    policy_path, coverage_path = _fixture(tmp_path)
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    del coverage["files"]["src/example/b.py"]
    coverage_path.write_text(json.dumps(coverage), encoding="utf-8")

    with pytest.raises(ValueError, match="omits policy paths"):
        evaluate_coverage_report(
            coverage_path,
            repo_root=tmp_path,
            changed_paths=(),
            policy_path=policy_path,
        )


def test_report_rejects_inconsistent_or_symlinked_evidence(tmp_path: Path) -> None:
    policy_path, coverage_path = _fixture(tmp_path)
    coverage = json.loads(coverage_path.read_text(encoding="utf-8"))
    coverage["files"]["src/example/a.py"]["summary"]["covered_lines"] = 11
    coverage_path.write_text(json.dumps(coverage), encoding="utf-8")

    with pytest.raises(ValueError, match="inconsistent"):
        evaluate_coverage_report(
            coverage_path,
            repo_root=tmp_path,
            changed_paths=(),
            policy_path=policy_path,
        )

    target = tmp_path / "coverage-target.json"
    coverage_path.replace(target)
    coverage_path.symlink_to(target)
    with pytest.raises(ValueError, match="invalid JSON document"):
        evaluate_coverage_report(
            coverage_path,
            repo_root=tmp_path,
            changed_paths=(),
            policy_path=policy_path,
        )


def test_report_admits_bounded_evidence_outside_repository(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    evidence = tmp_path / "evidence"
    policy_path, coverage_path = _fixture(repository)
    evidence.mkdir()
    external_coverage_path = evidence / "coverage.json"
    coverage_path.replace(external_coverage_path)

    report = evaluate_coverage_report(
        external_coverage_path,
        repo_root=repository,
        changed_paths=(),
        policy_path=policy_path,
    )

    assert report["policyId"] == "fixture"


def _fixture(root: Path) -> tuple[Path, Path]:
    for relative_path in ("backend/src/example/a.py", "backend/src/example/b.py"):
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("VALUE = 1\n", encoding="utf-8")
    policy = {
        "changedCriticalMinimum": {"branchPercent": 90, "statementPercent": 90},
        "nonClaims": ["bounded fixture"],
        "ownerGroups": [
            {
                "branchPercent": 80,
                "ownerId": "example",
                "paths": ["src/example/a.py", "src/example/b.py"],
                "statementPercent": 80,
            }
        ],
        "policyId": "fixture",
        "schemaVersion": "ci-coordinator-python-coverage-policy/v1",
        "sourceRoot": "backend",
    }
    coverage = {
        "files": {
            "src/example/a.py": {"summary": _summary(9, 10, 8, 10)},
            "src/example/b.py": {"summary": _summary(9, 10, 7, 10)},
        },
        "totals": _summary(18, 20, 15, 20),
    }
    policy_path = root / "policy.json"
    coverage_path = root / "coverage.json"
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    coverage_path.write_text(json.dumps(coverage), encoding="utf-8")
    return policy_path, coverage_path


def _summary(
    covered_statements: int,
    statement_count: int,
    covered_branches: int,
    branch_count: int,
) -> dict[str, int]:
    return {
        "covered_lines": covered_statements,
        "num_statements": statement_count,
        "covered_branches": covered_branches,
        "num_branches": branch_count,
    }

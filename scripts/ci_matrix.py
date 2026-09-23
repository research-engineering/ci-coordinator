from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from time import monotonic

from scripts.bounded_git import capture_git_text
from scripts.bounded_process import spawn
from scripts.ci_matrix_contract import admit_execution_results, load_matrix
from scripts.ci_matrix_inventory import snapshot
from scripts.quality_plan import MAX_COMMAND_OUTPUT_BYTES, project_command_environment
from scripts.repository_paths import real_repository_directory

REPO_ROOT = Path(__file__).resolve().parent.parent


def check(root: Path) -> dict[str, object]:
    from scripts.ci_matrix_risks import check as check_risk_coverage

    profile, quality = load_matrix(root)
    inventory = snapshot(root, profile)
    return {
        "schemaVersion": "ci-coordinator.ci-matrix-inventory/v1",
        "status": "passed",
        "commandCount": len(quality.commands),
        "utilityGroupCount": len(profile.utilityGroups),
        "externalCheckCount": len(profile.externalChecks),
        "riskCoverage": check_risk_coverage(root),
        **inventory,
    }


def run_group(root: Path, group_id: str) -> bool:
    profile, quality = load_matrix(root)
    groups = [group for group in profile.utilityGroups if group.id == group_id]
    if len(groups) != 1:
        raise ValueError(f"unknown CI utility group: {group_id}")
    before = snapshot(root, profile)
    commit = capture_git_text(root, ("rev-parse", "HEAD"), strip=True)
    report_dir = root / ".ci-evidence"
    report_dir.mkdir(exist_ok=True)
    real_repository_directory(root, Path(".ci-evidence"), "CI evidence directory")
    results: list[dict[str, object]] = []
    for command_id in groups[0].commandIds:
        command = quality.commands[command_id]
        started = monotonic()
        result = spawn(
            command.argv[0],
            command.argv[1:],
            cwd=command.cwd,
            env=project_command_environment(command, os.environ),
            max_buffer=MAX_COMMAND_OUTPUT_BYTES,
            timeout_seconds=command.timeout_ms / 1000,
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        row: dict[str, object] = {
            "commandId": command_id,
            "argvSha256": hashlib.sha256(json.dumps(command.argv).encode()).hexdigest(),
            "elapsedMilliseconds": round((monotonic() - started) * 1000),
            "exitCode": result.status,
            "status": "passed" if result.error is None and result.status == 0 else "failed",
            "processError": result.error,
        }
        results.append(row)
    after = snapshot(root, profile)
    unchanged = before == after
    admitted = admit_execution_results(groups[0].commandIds, results)
    passed = unchanged and all(row.status == "passed" for row in admitted)
    report = {
        "schemaVersion": "ci-coordinator.ci-utility-execution/v1",
        "groupId": group_id,
        "subjectCommit": commit,
        "runId": os.environ.get("GITHUB_RUN_ID"),
        "runAttempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "inventory": before,
        "inputsUnchanged": unchanged,
        "results": results,
        "status": "passed" if passed else "failed",
    }
    destination = report_dir / f"{group_id}.json"
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps(report, sort_keys=True))
    return passed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Admit the CI check matrix and execute utility groups"
    )
    parser.add_argument("operation", choices=("check", "run"))
    parser.add_argument("--group")
    args = parser.parse_args(argv)
    try:
        if args.operation == "check":
            if args.group is not None:
                raise ValueError("inventory admission takes no execution group")
            print(json.dumps(check(REPO_ROOT), sort_keys=True))
            return 0
        if args.group is None:
            raise ValueError("utility execution requires an explicit group")
        return 0 if run_group(REPO_ROOT, args.group) else 1
    except (OSError, RuntimeError, ValueError) as error:
        print(f"CI matrix admission failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

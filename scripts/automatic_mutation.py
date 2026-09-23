from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from scripts.bounded_process import spawn
from scripts.repository_paths import read_repository_regular_file

ROOT = Path(__file__).resolve().parent.parent
MAX_REPORT_BYTES = 8 * 1024 * 1024
Count = Annotated[int, Field(strict=True, ge=0)]
COUNTS = TypeAdapter(dict[str, Count])
MUTMUT_FIELDS = frozenset(
    {
        "killed",
        "survived",
        "total",
        "no_tests",
        "skipped",
        "suspicious",
        "timeout",
        "check_was_interrupted_by_user",
        "segfault",
    }
)
StrykerStatus = Literal[
    "Killed",
    "Survived",
    "NoCoverage",
    "CompileError",
    "RuntimeError",
    "Timeout",
    "Ignored",
    "Pending",
]


class _Mutant(BaseModel):
    model_config = ConfigDict(strict=True)
    id: str = Field(min_length=1)
    status: StrykerStatus


class _FileReport(BaseModel):
    mutants: list[_Mutant] = Field(min_length=1)


class _StrykerReport(BaseModel):
    schemaVersion: Literal["1.0"]
    files: dict[str, _FileReport]


def admit_counts(tool: str, payload: bytes) -> dict[str, int]:
    if tool == "python":
        counts = COUNTS.validate_json(payload)
        if set(counts) != MUTMUT_FIELDS or counts["total"] < 1:
            raise ValueError("mutmut report is empty or has an unknown shape")
        if sum(value for key, value in counts.items() if key != "total") != counts["total"]:
            raise ValueError("mutmut report has incomplete result accounting")
        if any(counts[key] for key in ("suspicious", "segfault", "check_was_interrupted_by_user")):
            raise ValueError("mutmut reported an execution failure")
        if counts["killed"] + counts["survived"] == 0:
            raise ValueError("mutmut did not report any normally resolved mutants")
        return counts
    if tool != "frontend":
        raise ValueError("unknown automatic mutation profile")
    report = _StrykerReport.model_validate_json(payload)
    if set(report.files) != {"src/api/shared/operationId.ts"}:
        raise ValueError("Stryker report does not cover the configured source")
    mutants = report.files["src/api/shared/operationId.ts"].mutants
    if len({mutant.id for mutant in mutants}) != len(mutants):
        raise ValueError("Stryker report repeats a mutant")
    counts = dict(Counter(mutant.status for mutant in mutants))
    if counts.get("Pending", 0) or counts.get("RuntimeError", 0):
        raise ValueError("Stryker reported an incomplete or failed execution")
    if counts.get("Killed", 0) + counts.get("Survived", 0) == 0:
        raise ValueError("Stryker did not report any normally resolved mutants")
    return {"total": len(mutants), **counts}


def run(tool: str, *, root: Path = ROOT) -> dict[str, object]:
    python = str(root / "backend/.venv/bin/python")
    commands: tuple[tuple[str, ...], ...]
    if tool == "python":
        cwd = root / "backend"
        scratch = cwd / "mutants"
        report_path = "backend/mutants/mutmut-cicd-stats.json"
        commands = (
            (python, "-m", "mutmut", "run", "--max-children", "2"),
            (python, "-m", "mutmut", "export-cicd-stats"),
        )
    elif tool == "frontend":
        cwd = root / "frontend"
        scratch = cwd / "reports/mutation"
        report_path = "frontend/reports/mutation/mutation.json"
        commands = (("pnpm", "exec", "stryker", "run"),)
    else:
        raise ValueError("unknown automatic mutation profile")
    if scratch.exists() or scratch.is_symlink():
        raise ValueError("automatic mutation requires a fresh disposable checkout")
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "CI": "true",
        "NO_COLOR": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    for command in commands:
        result = spawn(
            command[0],
            command[1:],
            cwd=cwd,
            env=environment,
            max_buffer=MAX_REPORT_BYTES,
            timeout_seconds=240,
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.status != 0 or result.error is not None or result.failure_kind is not None:
            raise RuntimeError(
                f"{tool} mutation execution failed: exit={result.status}, "
                f"reason={result.failure_kind or 'nonzero-exit'}"
            )
    payload = read_repository_regular_file(
        root,
        Path(report_path),
        "automatic mutation report",
        maximum_bytes=MAX_REPORT_BYTES,
    )
    return {
        "state": "observed",
        "profile": tool,
        "upstreamCounts": admit_counts(tool, payload),
        "reportPath": report_path,
        "nonClaims": [
            "Upstream result categories are diagnostic, not certified test kills.",
            "Survivors require requirement-based review; no mutation-score gate is claimed.",
            "This bounded campaign does not replace the curated mutation witnesses.",
        ],
    }


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m scripts.automatic_mutation <python|frontend>")
    print(json.dumps(run(sys.argv[1]), indent=2))


if __name__ == "__main__":
    main()

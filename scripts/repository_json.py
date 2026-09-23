from __future__ import annotations

import json
from pathlib import Path
from typing import Final

from scripts.bounded_git import git_stdout_bytes, run_git
from scripts.review_decisions import validate_decision_register

MAX_JSON_BYTES: Final = 16 * 1024 * 1024


class JsonAdmissionError(ValueError):
    pass


def tracked_json_paths(repo_root: Path) -> tuple[Path, ...]:
    result = run_git(
        repo_root,
        (
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
            "--",
            "*.json",
        ),
        decode_errors="surrogateescape",
    )
    return tuple(
        path
        for raw in git_stdout_bytes(result).split(b"\0")
        if raw and (path := repo_root / raw.decode("utf-8", errors="strict")).is_file()
    )


def admit_json(path: Path) -> None:
    size = path.stat().st_size
    if size > MAX_JSON_BYTES:
        raise JsonAdmissionError(f"{path}: JSON source exceeds {MAX_JSON_BYTES} bytes")
    try:
        source = path.read_text(encoding="utf-8", errors="strict")
        json.loads(
            source,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, JsonAdmissionError) as error:
        raise JsonAdmissionError(f"{path}: {error}") from error


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    paths = tracked_json_paths(repo_root)
    failures: list[str] = []
    for path in paths:
        try:
            admit_json(path)
        except JsonAdmissionError as error:
            failures.append(str(error))
    try:
        decisions = validate_decision_register(repo_root)
    except (OSError, ValueError) as error:
        failures.append(f"decision register: {error}")
        decisions = ()
    if failures:
        print("\n".join(sorted(failures)))
        return 1
    print(f"admitted {len(paths)} JSON files")
    for decision in decisions:
        print(f"{decision.decision_id}: {decision.state}; semantic review remains required")
    return 0


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise JsonAdmissionError(f"duplicate object key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise JsonAdmissionError(f"non-finite numeric constant: {value}")


if __name__ == "__main__":
    raise SystemExit(main())

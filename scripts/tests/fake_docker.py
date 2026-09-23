#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _record(args: list[str]) -> None:
    state_dir = Path(os.environ["FAKE_DOCKER_STATE_DIR"])
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / "calls.jsonl").open("a", encoding="utf-8") as output:
        output.write(json.dumps({"args": args, "cwd": os.getcwd()}))
        output.write("\n")


def _inspect_status(scenario: str) -> str:
    if scenario == "unhealthy_cleanup_failure":
        return "unhealthy"
    return "healthy"


def _write_logs() -> None:
    sys.stdout.write("container log stdout\n")
    sys.stderr.write("container log stderr\n")


def main() -> int:
    args = sys.argv[1:]
    _record(args)
    scenario = os.environ.get("FAKE_DOCKER_SCENARIO", "success")
    command = args[0] if args else ""

    if command == "build":
        if scenario == "build_failure":
            sys.stdout.write("  build failed on stdout  \n")
            return 17
        return 0

    if command == "run":
        if scenario == "startup_failure":
            sys.stderr.write("  startup failed  \n")
            return 18
        return 0

    if command == "inspect":
        sys.stdout.write(f"{_inspect_status(scenario)}\n")
        return 0

    if command == "logs":
        _write_logs()
        return 0

    if args[:3] == ["image", "rm", "--force"]:
        if scenario == "unhealthy_cleanup_failure":
            sys.stdout.write("  image cleanup exploded  \n")
            return 22
        return 0

    if args[:2] == ["rm", "--force"]:
        if scenario == "unhealthy_cleanup_failure":
            sys.stderr.write("  container cleanup exploded  \n")
            return 21
        return 0

    if command == "exec" and args[2:] == ["python", "--version"]:
        version = "Python 3.13.14" if scenario == "wrong_version" else "Python 3.13.15"
        sys.stdout.write(f"{version}\n")
        return 0

    if command == "exec" and args[2:] == [
        "python",
        "-c",
        "import os; print(os.getuid(), os.getgid())",
    ]:
        identity = {
            "root_user": "0 0",
            "wrong_user": "1000 10001",
            "wrong_group": "10001 1000",
        }.get(scenario, "10001 10001")
        sys.stdout.write(f"{identity}\n")
        return 0

    if command == "exec" and args[2:] == ["alembic", "--help"]:
        if scenario == "migration_cli_failure":
            sys.stderr.write("  migration CLI failed  \n")
            return 23
        return 0

    if command == "exec" and args[2:] == ["alembic", "heads"]:
        head = (
            "20991231_9999 (head)"
            if scenario == "migration_head_failure"
            else ("20260915_0015 (head)")
        )
        sys.stdout.write(f"{head}\n")
        return 0

    if command == "exec" and args[2:4] == ["python", "-c"]:
        source = args[4]
        if scenario == "health_failure" and "'/healthz'" in source:
            sys.stderr.write("  health request failed  \n")
            return 19
        if scenario == "operator_ui_failure" and "mount_operator_ui" in source:
            sys.stderr.write("  operator UI request failed  \n")
            return 25
        if scenario == "migration_artifact_failure" and "alembic.ini" in source:
            sys.stderr.write("  migration artifact missing  \n")
            return 24
        return 0

    sys.stderr.write(f"unexpected fake Docker argv: {args!r}\n")
    return 99


if __name__ == "__main__":
    raise SystemExit(main())

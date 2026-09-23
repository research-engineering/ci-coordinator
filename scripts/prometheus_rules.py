from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Final, Literal

from scripts.bounded_process import spawn

PROMETHEUS_IMAGE: Final = (
    "prom/prometheus:v3.14.0"
    "@sha256:5ce7540c3c00ef4ab0c9d2c995c6a5b9c421f44b4a115d97a2c7af3b1c21cbb0"
)
RULES_PATH: Final = Path("deploy/observability/ci-coordinator.rules.yml")
TESTS_PATH: Final = RULES_PATH.with_name("ci-coordinator.rules.test.yml")
_MAX_OUTPUT_BYTES: Final = 1_048_576


def docker_arguments(repo_root: Path, operation: Literal["check", "test"]) -> tuple[str, ...]:
    rules_directory = (repo_root / RULES_PATH.parent).resolve()
    input_path = RULES_PATH if operation == "check" else TESTS_PATH
    return (
        "run",
        "--rm",
        "--network=none",
        "--read-only",
        "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=65532:65532",
        "--entrypoint=/bin/promtool",
        "--volume",
        f"{rules_directory}:/rules:ro",
        PROMETHEUS_IMAGE,
        operation,
        "rules",
        f"/rules/{input_path.name}",
    )


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    docker = os.environ.get("CI_COORDINATOR_DOCKER_BIN", "docker")
    operations: tuple[Literal["check", "test"], ...] = ("check", "test")
    for operation in operations:
        result = spawn(
            docker,
            docker_arguments(repo_root, operation),
            cwd=repo_root,
            max_buffer=_MAX_OUTPUT_BYTES,
            timeout_seconds=300,
        )
        if result.error is not None:
            sys.stderr.write(result.error + "\n")
            return 1
        if result.status != 0:
            sys.stderr.write((result.stderr or result.stdout).strip() + "\n")
            return result.status or 1
    sys.stdout.write(
        json.dumps(
            {
                "image": PROMETHEUS_IMAGE,
                "nonClaims": ["Rule witnesses do not prove a production scrape or alert delivery."],
                "reportKind": "ci-coordinator.prometheus-rules",
                "rulesPath": RULES_PATH.as_posix(),
                "testsPath": TESTS_PATH.as_posix(),
                "schemaVersion": 1,
                "state": "passed",
            },
            indent=2,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import resource
import signal
import sys
import tempfile
import time
from collections.abc import Sequence
from contextlib import ExitStack
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from types import FrameType

from scripts.bounded_process import StopPredicate, spawn

_BUDGETS = {"fast": 180, "deep": 300}
_DEFAULT_SEED = 20260919


def _seed(value: str) -> int:
    if not value.isascii() or not value.isdecimal() or not 0 <= int(value) <= 2**32 - 1:
        raise argparse.ArgumentTypeError("seed must be an integer between 0 and 4294967295")
    return int(value)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run isolated, schema-driven API checks")
    parser.add_argument("profile", choices=tuple(_BUDGETS), nargs="?", default="fast")
    parser.add_argument("--seed", type=_seed, default=_DEFAULT_SEED)
    arguments = parser.parse_args(argv)
    cancelled = False

    def request_cancellation(_number: int, _frame: FrameType | None) -> None:
        nonlocal cancelled
        cancelled = True

    with ExitStack() as handlers:
        for number in (signal.SIGTERM, signal.SIGINT):
            previous = signal.signal(number, request_cancellation)
            handlers.callback(signal.signal, number, previous)
        return _run_campaign(arguments, lambda: cancelled)


def _run_campaign(arguments: argparse.Namespace, stop_requested: StopPredicate) -> int:
    root = Path(__file__).resolve().parents[1]
    artifacts = root / ".api-contract"
    try:
        artifacts.mkdir(exist_ok=True)
        output = Path(tempfile.mkdtemp(prefix=f"{arguments.profile}-", dir=artifacts))
        lock_digest = hashlib.sha256((root / "backend/uv.lock").read_bytes()).hexdigest()
        schemathesis_version = version("schemathesis")
        hypothesis_version = version("hypothesis")
    except (OSError, PackageNotFoundError):
        print("API contract prerequisites unavailable; campaign not started", file=sys.stderr)
        return 2
    environment = dict(os.environ)
    environment.update(
        CI_COORDINATOR_API_CAMPAIGN=arguments.profile,
        CI_COORDINATOR_API_SEED=str(arguments.seed),
        CI_COORDINATOR_API_ARTIFACTS=str(output / "failures"),
        PYTEST_ADDOPTS="",
    )
    started = time.monotonic()
    cpu_before = resource.getrusage(resource.RUSAGE_CHILDREN)
    result = spawn(
        sys.executable,
        ("-m", "pytest", "-ra", "tests/unit/api_contract"),
        cwd=root / "backend",
        env=environment,
        timeout_seconds=_BUDGETS[arguments.profile],
        max_buffer=1024 * 1024,
        stop_requested=stop_requested,
    )
    elapsed = time.monotonic() - started
    cpu_after = resource.getrusage(resource.RUSAGE_CHILDREN)
    summary = {
        "schemaVersion": 1,
        "profile": arguments.profile,
        "seed": arguments.seed,
        "python": platform.python_version(),
        "schemathesis": schemathesis_version,
        "hypothesis": hypothesis_version,
        "lockSha256": lock_digest,
        "deadlineSeconds": _BUDGETS[arguments.profile],
        "elapsedSeconds": elapsed,
        "reapedChildCpuSeconds": (
            (cpu_after.ru_utime - cpu_before.ru_utime) + (cpu_after.ru_stime - cpu_before.ru_stime)
        ),
        "exitCode": result.status,
        "failureKind": result.failure_kind,
        "error": result.error,
        "cancelled": stop_requested() or result.failure_kind == "cancelled",
    }
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    artifacts_complete = True
    try:
        (output / "stdout.txt").write_text(result.stdout, encoding="utf-8")
        (output / "stderr.txt").write_text(result.stderr, encoding="utf-8")
        (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    except OSError:
        artifacts_complete = False
        print("API contract artifacts unavailable; evidence incomplete", file=sys.stderr)
    else:
        print(f"API contract artifacts: {output}")
    if result.error is not None:
        print(result.error, file=sys.stderr)
    if result.status is None or result.failure_kind is not None or result.error is not None:
        return 2
    return result.status if result.status or (artifacts_complete and not stop_requested()) else 2


if __name__ == "__main__":
    raise SystemExit(main())

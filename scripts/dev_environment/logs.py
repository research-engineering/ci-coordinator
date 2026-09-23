from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from scripts.bounded_process import InteractiveResult, run_interactive
from scripts.dev_environment.compose import ComposeError, ProviderInvocation
from scripts.dev_environment.diagnostics import Reason
from scripts.dev_environment.environment import EnvironmentError, dependency_lease
from scripts.dev_environment.identity import InstanceIdentity
from scripts.dev_environment.log_transport import read_logs


def stream_logs(
    invocations: Sequence[ProviderInvocation],
    *,
    identity: InstanceIdentity,
    stop_requested: Callable[[], bool] | None = None,
    stdout_fd: int | None = None,
) -> None:
    if not 1 <= len(invocations) <= 6:
        raise ComposeError("invalid log stream count", reason=Reason.INVALID_ARGUMENT)
    if any(invocation.cwd.resolve() != identity.repo_root for invocation in invocations):
        raise ComposeError("log environment ownership mismatch", reason=Reason.FOREIGN_STATE)
    stop = threading.Event()
    futures: dict[Future[InteractiveResult], bool] = {}
    with (
        _log_lease(identity) as descriptor,
        ThreadPoolExecutor(
            max_workers=len(invocations), thread_name_prefix="development-logs"
        ) as executor,
    ):
        try:
            for invocation in invocations:
                following = "--follow" in invocation.argv
                command = log_reader_invocation(invocation)
                future = executor.submit(
                    run_interactive,
                    command.argv[0],
                    command.argv[1:],
                    cwd=command.cwd,
                    env=command.environment,
                    stop_requested=stop.is_set,
                    timeout_seconds=None if following else 30,
                    stderr=subprocess.DEVNULL,
                    stdout_fd=stdout_fd,
                    inherited_fds=(descriptor,),
                )
                futures[future] = following
            while futures:
                if stop_requested is not None and stop_requested():
                    stop.set()
                completed, _ = wait(futures, timeout=0.2, return_when=FIRST_COMPLETED)
                for future in completed:
                    following = futures.pop(future)
                    result = future.result()
                    if (
                        stop.is_set()
                        and result.failure_kind == "cancelled"
                        and result.cancellation_signal_sent
                        and result.process_group_quiescent
                        and not result.escalated
                    ):
                        continue
                    if (
                        not result.process_group_quiescent
                        or result.returncode != 0
                        or result.failure_kind is not None
                        or result.escalated
                    ):
                        raise ComposeError(
                            "log provider stopped", reason=Reason.PROVIDER_UNAVAILABLE
                        )
                    if following:
                        raise ComposeError(
                            "selected log stream ended; reconnect explicitly",
                            reason=Reason.STALE_OBSERVATION,
                        )
        except KeyboardInterrupt:
            return
        finally:
            stop.set()
            for pending in futures:
                if not pending.result().process_group_quiescent:
                    raise ComposeError(
                        "log cleanup is unproven", reason=Reason.PROVIDER_UNAVAILABLE
                    )


@contextmanager
def _log_lease(identity: InstanceIdentity) -> Iterator[int]:
    try:
        with dependency_lease(identity) as descriptor:
            yield descriptor
    except EnvironmentError as error:
        raise ComposeError("log environment is unavailable", reason=error.reason) from error


def log_reader_invocation(invocation: ProviderInvocation) -> ProviderInvocation:
    argv = invocation.argv
    if (
        len(argv) not in (5, 6)
        or argv[:3] != ("docker", "logs", "--tail")
        or (len(argv) == 6 and argv[4] != "--follow")
        or re.fullmatch(r"[0-9a-f]{12,64}", argv[-1]) is None
        or not argv[3].isascii()
        or not argv[3].isdecimal()
        or not 0 <= int(argv[3]) <= 10000
    ):
        raise ComposeError("invalid log selection", reason=Reason.INVALID_ARGUMENT)
    return ProviderInvocation(
        (sys.executable, "-m", "scripts.dev_environment.logs", *argv[2:]),
        invocation.cwd,
        invocation.environment,
    )


def _read_logs(container: str, *, tail: int, follow: bool, output: BinaryIO) -> None:
    read_logs(
        container, tail=tail, follow=follow, output=output, environment=os.environ, cwd=Path.cwd()
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tail", type=int, required=True)
    parser.add_argument("--follow", action="store_true")
    parser.add_argument("container")
    options = parser.parse_args(argv)
    if (
        not 0 <= options.tail <= 10000
        or re.fullmatch(r"[0-9a-f]{12,64}", options.container) is None
    ):
        return 2
    try:
        _read_logs(
            options.container, tail=options.tail, follow=options.follow, output=sys.stdout.buffer
        )
    except Exception:
        # Only decoded application bytes may reach stdout; the parent owns diagnostics.
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

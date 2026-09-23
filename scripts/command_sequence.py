from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from scripts.bounded_process import spawn

DEFAULT_MAX_OUTPUT_BYTES = 16 * 1024 * 1024
DEFAULT_TIMEOUT_SECONDS = 600.0

type Environment = Mapping[str, str]


class CommandRunner(Protocol):
    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Environment | None = None,
    ) -> int: ...


@dataclass(frozen=True, slots=True)
class Command:
    argv: tuple[str, ...]
    cwd: Path


def run_commands(
    commands: Iterable[Command],
    *,
    env: Environment | None = None,
    runner: CommandRunner | None = None,
) -> int:
    execute = _run if runner is None else runner
    for command in commands:
        status = execute(command.argv, cwd=command.cwd, env=env)
        if status != 0:
            return 128 + abs(status) if status < 0 else status
    return 0


def _run(argv: Sequence[str], *, cwd: Path, env: Environment | None = None) -> int:
    if not argv:
        raise ValueError("command argv must not be empty")
    result = spawn(
        argv[0],
        argv[1:],
        cwd=cwd,
        env=env,
        max_buffer=DEFAULT_MAX_OUTPUT_BYTES,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.error is not None:
        print(result.error, file=sys.stderr)
        return 1
    return 1 if result.status is None else result.status

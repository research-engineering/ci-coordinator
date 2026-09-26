from __future__ import annotations

import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from scripts.bounded_process import spawn

_DEFAULT_MAX_OUTPUT_BYTES = 8 * 1024 * 1024
_DEFAULT_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class ProofkitProcessResult:
    returncode: int
    stdout: str
    stderr: str


def resolve_proofkit_executable(
    injected: str | Path | None = None,
) -> str:
    if injected is not None:
        return os.fspath(injected)
    executable_name = "agentic-proofkit.exe" if os.name == "nt" else "agentic-proofkit"
    environment_root = Path(sys.prefix).resolve(strict=True)
    executable = (
        environment_root / ("Scripts" if os.name == "nt" else "bin") / executable_name
    ).resolve(strict=True)
    if (
        not executable.is_relative_to(environment_root)
        or not executable.is_file()
        or not os.access(executable, os.X_OK)
    ):
        raise FileNotFoundError(
            "agentic-proofkit requires an executable inside the active Python environment"
        )
    return str(executable)


def invoke_proofkit(
    executable: str,
    command: str,
    args: Sequence[str],
    *,
    cwd: Path,
    input_text: str | None = None,
    max_output_bytes: int | None = None,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> ProofkitProcessResult:
    output_limit = _DEFAULT_MAX_OUTPUT_BYTES if max_output_bytes is None else max_output_bytes
    result = spawn(
        executable,
        (command, *args),
        cwd=cwd,
        input_text=input_text,
        max_buffer=output_limit,
        timeout_seconds=timeout_seconds,
    )
    if result.error is not None:
        raise RuntimeError(result.error)
    if result.status is None:
        raise RuntimeError("Proofkit process terminated without an exit status")
    return ProofkitProcessResult(
        returncode=result.status,
        stdout=result.stdout,
        stderr=result.stderr,
    )

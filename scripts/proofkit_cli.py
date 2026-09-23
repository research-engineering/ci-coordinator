from __future__ import annotations

import os
import shutil
import sys
from collections.abc import Mapping, Sequence
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
    *,
    env: Mapping[str, str] | None = None,
) -> str:
    if injected is not None:
        return os.fspath(injected)
    executable_name = "agentic-proofkit.exe" if os.name == "nt" else "agentic-proofkit"
    environment_candidate = Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")
    environment_candidate /= executable_name
    if environment_candidate.is_file() and os.access(environment_candidate, os.X_OK):
        return str(environment_candidate)
    path = None if env is None else env.get("PATH")
    resolved = shutil.which("agentic-proofkit", path=path)
    if resolved is None:
        raise FileNotFoundError(
            "agentic-proofkit executable was not found in the active Python environment or PATH"
        )
    return resolved


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

from __future__ import annotations

import base64
import gzip
import os
import subprocess
from pathlib import Path

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_CONTROL_PATH = _REPOSITORY_ROOT / "fixtures/target-repository/.ci-coordinator/ci-coordinator.cjs"
_CHUNK_COUNT = 7
_CHUNK_CHARACTERS = 65_536


def plan_transport_environment(
    content: bytes,
    *,
    reason: str = "signed_plan_available",
) -> dict[str, str]:
    encoded = base64.urlsafe_b64encode(gzip.compress(content, mtime=0)).rstrip(b"=").decode()
    chunks = tuple(
        encoded[index : index + _CHUNK_CHARACTERS]
        for index in range(0, len(encoded), _CHUNK_CHARACTERS)
    )
    if len(chunks) > _CHUNK_COUNT:
        raise ValueError("test plan exceeds the admitted transport")
    return {
        "CI_COORDINATOR_PLAN_REASON": reason,
        **{
            f"CI_COORDINATOR_PLAN_CHUNK_{index}": chunks[index] if index < len(chunks) else ""
            for index in range(_CHUNK_COUNT)
        },
    }


def run_plan_consumer(
    tmp_path: Path,
    environment: dict[str, str],
) -> tuple[subprocess.CompletedProcess[str], Path]:
    plan_path = tmp_path / "plan.json"
    completed = subprocess.run(
        ["node", str(_CONTROL_PATH), "consume-plan"],
        cwd=_REPOSITORY_ROOT,
        env={**os.environ, "PLAN_PATH": str(plan_path), **environment},
        check=False,
        text=True,
        capture_output=True,
        timeout=5,
    )
    return completed, plan_path

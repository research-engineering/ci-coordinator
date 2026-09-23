from __future__ import annotations

import os
import sys
import tomllib
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from scripts.bounded_process import spawn
from scripts.python_import_boundary_engine import inventory_python_sources
from scripts.repository_paths import real_repository_directory
from scripts.repository_source_admission import read_bounded_repository_bytes


def admit_backend_source_roots(repo_root: Path, backend_root: Path, roots: tuple[str, ...]) -> None:
    if backend_root != repo_root / "backend":
        raise ValueError("dependency hygiene requires the repository backend root")
    raw = read_bounded_repository_bytes(
        repo_root, PurePosixPath("backend/pyproject.toml"), maximum_bytes=262_144
    )
    project = tomllib.loads(raw.decode("utf-8")).get("project")
    if not isinstance(project, dict) or project.get("name") != "ci-coordinator-backend":
        raise ValueError("dependency hygiene project identity is invalid")
    for root in roots:
        real_repository_directory(repo_root, Path("backend") / root, "dependency hygiene source")
        inventory = inventory_python_sources(backend_root / root)
        if not inventory.files or inventory.symlinks:
            raise ValueError(f"dependency hygiene source root is empty or symlinked: {root}")


def run_import_linter(backend_root: Path, environment: Mapping[str, str]) -> None:
    executable = backend_root / ".venv" / "bin" / "lint-imports"
    if not executable.is_file():
        raise ValueError("locked Import Linter executable is missing")
    result = spawn(
        str(executable),
        ("--config", "pyproject.toml", "--no-cache", "--no-logo"),
        cwd=backend_root,
        env={
            "PATH": environment.get("PATH", os.defpath),
            "PYTHONPATH": str(backend_root / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        max_buffer=16 * 1024 * 1024,
        timeout_seconds=90.0,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    if result.error is not None or result.failure_kind is not None or result.status != 0:
        raise ValueError("standard Python import-boundary check failed")

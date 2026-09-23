from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Never, Protocol, cast

from scripts.bounded_process import spawn


class FailReporter(Protocol):
    def __call__(self, message: str, diagnostics: dict[str, object]) -> Never: ...


class SuccessReporter(Protocol):
    def __call__(
        self,
        payload: dict[str, object],
        additional_non_claims: Sequence[str] = (),
    ) -> None: ...


class RepoPath(Protocol):
    def __call__(self, path: Path) -> str: ...


@dataclass(frozen=True, slots=True)
class PythonEnvironmentWitnessContext:
    backend_root: Path
    environment: Mapping[str, str]
    fail: FailReporter
    mode: str
    python_executable: str
    repo_path: RepoPath
    repo_root: Path
    report: SuccessReporter
    requirements_path: Path
    venv_python: Path
    venv_root: Path


class PythonEnvironmentWitnesses:
    def __init__(self, context: PythonEnvironmentWitnessContext) -> None:
        self._context = context

    def run_install_check(self) -> None:
        context = self._context
        if not context.requirements_path.exists():
            context.fail(
                "backend requirements lock is missing",
                {
                    "mode": context.mode,
                    "path": context.repo_path(context.requirements_path),
                },
            )
        if not context.venv_python.exists():
            self._run_inherited(
                "uv",
                (
                    "venv",
                    "--no-project",
                    "--python",
                    context.python_executable,
                    str(context.venv_root),
                ),
                "failed to create python virtual environment",
            )
        self._run_inherited(
            "uv",
            (
                "pip",
                "sync",
                "--python",
                str(context.venv_python),
                "--require-hashes",
                "--strict",
                "--compile-bytecode",
                str(context.requirements_path),
            ),
            "failed to synchronize locked python dependencies",
        )
        self._run_inherited(
            "uv",
            (
                "pip",
                "install",
                "--python",
                str(context.venv_python),
                "--no-deps",
                "--no-build-isolation",
                "--offline",
                "--no-cache",
                "--editable",
                str(context.backend_root),
            ),
            "failed to install the local Python project",
        )

    def run_lock_check(self) -> None:
        context = self._context
        self._run_inherited(
            "uv",
            (
                "lock",
                "--project",
                str(context.backend_root),
                "--check",
            ),
            "failed to start uv lock check",
            cwd=context.repo_root,
        )
        locked_requirements = context.requirements_path.read_text(encoding="utf-8")
        if not locked_requirements.startswith(REQUIREMENTS_HEADER):
            context.fail(
                "backend requirements export header is not canonical",
                {
                    "mode": context.mode,
                    "path": context.repo_path(context.requirements_path),
                },
            )
        exported = spawn(
            "uv",
            (
                "export",
                "--project",
                str(context.backend_root),
                "--all-groups",
                "--format",
                "requirements.txt",
                "--no-emit-project",
                "--frozen",
                "--no-header",
            ),
            cwd=context.repo_root,
            env=context.environment,
            max_buffer=64 * 1024 * 1024,
        )
        if exported.error is not None:
            context.fail(
                f"failed to export locked Python dependencies: {exported.error}",
                {"mode": context.mode},
            )
        if exported.status is None:
            context.fail("locked dependency export returned no status", {"mode": context.mode})
        if exported.status != 0:
            _exit_with_returncode(exported.status)
        locked_projection = locked_requirements[len(REQUIREMENTS_HEADER) :]
        if exported.stdout != locked_projection:
            context.fail(
                "backend requirements export is stale",
                {
                    "mode": context.mode,
                    "path": context.repo_path(context.requirements_path),
                },
            )
        context.report(
            {
                "mode": context.mode,
                "reportKind": "ci-coordinator.python-lock-check",
                "state": "passed",
            }
        )

    def run_package_check(self) -> None:
        context = self._context
        if not context.venv_python.exists():
            context.fail(
                "python virtual environment is missing; run "
                "python -m scripts.python_witness install-check",
                {
                    "mode": context.mode,
                    "path": context.repo_path(context.venv_python),
                },
            )
        result = spawn(
            str(context.venv_python),
            (
                "-m",
                "scripts.python_package_witness",
                str(context.repo_root),
                str(context.backend_root),
                str(context.venv_python),
            ),
            cwd=context.repo_root,
            env=context.environment,
            max_buffer=16 * 1024 * 1024,
        )
        if result.error is not None:
            context.fail(
                f"failed to start Python package witness: {result.error}",
                {"mode": context.mode},
            )
        if result.status is None:
            context.fail("Python package witness returned no status", {"mode": context.mode})
        if result.status != 0:
            context.fail(
                "Python package check failed",
                {
                    "mode": context.mode,
                    "stderr": result.stderr.strip(),
                    "stdout": result.stdout.strip(),
                },
            )
        payload_value: object = json.loads(result.stdout)
        if not isinstance(payload_value, dict):
            raise TypeError("Python package witness report must be a JSON object")
        payload = cast(dict[str, object], payload_value)
        context.report(
            {
                "artifact": payload["artifact"],
                "capacityQualificationResourceCount": payload["capacityQualificationResourceCount"],
                "ciEconomicsResourceCount": payload["ciEconomicsResourceCount"],
                "consumerContractLabResourceCount": payload["consumerContractLabResourceCount"],
                "dependencyMode": "provisioned-locked-environment",
                "githubIngestionResourceCount": payload["githubIngestionResourceCount"],
                "operatorUiNestedAssetCount": payload["operatorUiNestedAssetCount"],
                "mode": context.mode,
                "persistenceResourceCount": payload["persistenceResourceCount"],
                "productionAdmissionResourceCount": payload["productionAdmissionResourceCount"],
                "publicSymbolCount": payload["publicSymbolCount"],
                "reportKind": "ci-coordinator.python-package-check",
                "resourceCount": payload["resourceCount"],
                "runtimeSettingsResourceCount": payload["runtimeSettingsResourceCount"],
                "targetArtifactsResourceCount": payload["targetArtifactsResourceCount"],
                "state": "passed",
            },
            (
                (
                    "Package public-API inspection reuses the locked development environment for "
                    "runtime dependencies; it does not prove isolated dependency resolution, "
                    "publication, or registry availability."
                ),
            ),
        )

    def _run_inherited(
        self,
        command: str,
        arguments: tuple[str, ...],
        error_prefix: str,
        *,
        cwd: Path | None = None,
    ) -> None:
        context = self._context
        result = spawn(
            command,
            arguments,
            cwd=cwd or context.backend_root,
            env=context.environment,
            max_buffer=64 * 1024 * 1024,
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.error is not None:
            context.fail(f"{error_prefix}: {result.error}", {"mode": context.mode})
        if result.status is None:
            context.fail(f"{error_prefix}: no process status", {"mode": context.mode})
        if result.status != 0:
            _exit_with_returncode(result.status)


def create_python_environment_witnesses(
    context: PythonEnvironmentWitnessContext,
) -> PythonEnvironmentWitnesses:
    return PythonEnvironmentWitnesses(context)


def _exit_with_returncode(returncode: int) -> Never:
    raise SystemExit(returncode if returncode > 0 else 1)


REQUIREMENTS_HEADER = (
    "# This file was autogenerated by uv via the following command:\n"
    "#    uv export --project backend --all-groups --format requirements.txt "
    "--output-file backend/requirements-dev.lock --no-emit-project --frozen\n"
)

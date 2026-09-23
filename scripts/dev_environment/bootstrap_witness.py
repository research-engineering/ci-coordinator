from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import sys
import tempfile
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path

from scripts.bounded_process import spawn

_MAX_SOURCE_FILES = 4096
_MAX_SOURCE_BYTES = 67_108_864
_FRONTEND_GUARD = ".ci-coordinator-bootstrap-guard"
_BACKEND_ADMISSION = """
from pathlib import Path
from scripts.dev_environment.environment import admit_dependencies, dependency_lease
from scripts.dev_environment.identity import derive_instance_identity
identity = derive_instance_identity(Path.cwd())
with dependency_lease(identity):
    admit_dependencies(identity, 'backend')
print('backend-admitted')
"""


class BootstrapWitnessError(RuntimeError):
    def __init__(self, reason: str, **diagnostic: object) -> None:
        super().__init__(reason)
        self.diagnostic = diagnostic


def run(repo_root: Path, mise: Path) -> dict[str, object]:
    phase = "bootstrap_setup"
    temporary: tempfile.TemporaryDirectory[str] | None = None
    primary_error: BaseException | None = None
    try:
        if sys.platform != "linux" or os.geteuid() == 0:
            raise BootstrapWitnessError("requires_unprivileged_linux_runner")
        mise = mise.resolve(strict=True)
        if not mise.is_file() or not os.access(mise, os.X_OK):
            raise BootstrapWitnessError("mise_unavailable")
        configuration = tomllib.loads((repo_root / "mise.toml").read_text())
        expected_mise = configuration["min_version"]
        temporary = tempfile.TemporaryDirectory(prefix="ci-coordinator-bootstrap-")
        workspace = Path(temporary.name)
        project = workspace / "project"
        project.mkdir()
        phase = "source_copy"
        sources = _copy_inputs(repo_root, project)
        phase = "environment_isolation"
        environment = _isolated_environment(workspace, project, mise)
        guarded = _guard_frontend_tools(workspace)
        phase = "mise_version"
        version = _checked(mise, ("--version",), project, environment, phase, 20)
        if not version.split() or version.split()[0] != expected_mise:
            raise BootstrapWitnessError("mise_version_mismatch")
        phase = "backend_task"
        _checked(mise, ("run", "install:backend"), project, environment, phase, 900)
        phase = "backend_task_frontend_guard"
        _assert_frontend_absent(workspace, project, guarded)
        phase = "mise_exec"
        output = _checked(
            mise,
            ("exec", "--", "python", "-S", "-c", _BACKEND_ADMISSION),
            project,
            environment,
            phase,
            60,
        )
        if output.strip() != "backend-admitted":
            raise BootstrapWitnessError("backend_admission_failed")
        phase = "mise_exec_frontend_guard"
        _assert_frontend_absent(workspace, project, guarded)
        phase = "source_integrity"
        _assert_inputs_unchanged(project, sources)
        phase = "backend_tool_admission"
        installed: dict[str, str] = {}
        for name in ("python", "uv"):
            requested = configuration["tools"][name]
            location = workspace / "data/installs" / name / requested
            if not location.is_dir() or not location.resolve().is_relative_to(workspace / "data"):
                raise BootstrapWitnessError("backend_tool_install_unproven")
            installed[name] = requested
        return {
            "schemaVersion": 1,
            "witness": "developer-backend-bootstrap",
            "state": "passed",
            "platform": sys.platform,
            "architecture": platform.machine(),
            "miseVersion": expected_mise,
            "commands": [
                "mise run install:backend",
                "mise exec -- python -S backend-admission",
            ],
            "installedTools": installed,
            "frontendInstallGuards": ["node", "pnpm"],
            "frontendToolInvocations": [],
            "frontendArtifacts": [],
            "sourceDigest": hashlib.sha256(
                json.dumps(sources, sort_keys=True).encode()
            ).hexdigest(),
        }
    except BootstrapWitnessError as error:
        error.diagnostic.setdefault("phase", phase)
        primary_error = error
        raise
    except (OSError, ValueError, KeyError, TypeError) as error:
        primary_error = _unavailable(phase, error)
        raise primary_error from error
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if temporary is not None:
            try:
                # TemporaryDirectory handles read-only children without following symlinks.
                temporary.cleanup()
            except Exception as error:
                if primary_error is None:
                    raise _unavailable("workspace_cleanup", error) from error
                if isinstance(primary_error, BootstrapWitnessError):
                    primary_error.diagnostic["cleanupFailed"] = True


def _unavailable(phase: str, error: Exception) -> BootstrapWitnessError:
    return BootstrapWitnessError(
        "bootstrap_unavailable",
        phase=phase,
        errorKind="filesystem" if isinstance(error, OSError) else "invalid_input",
        errno=error.errno if isinstance(error, OSError) else None,
    )


def _copy_inputs(source: Path, destination: Path) -> dict[str, str]:
    paths = [
        source / name
        for name in (
            "mise.toml",
            "mise.lock",
            "backend/pyproject.toml",
            "backend/uv.lock",
            "scripts/__init__.py",
            "scripts/bounded_process.py",
        )
    ]
    for relative in ("backend/src", "scripts/dev_environment"):
        for path in sorted((source / relative).rglob("*")):
            if any(
                part in {"__pycache__", ".mypy_cache", ".ruff_cache"} or part.endswith(".egg-info")
                for part in path.parts
            ):
                continue
            if path.is_symlink():
                raise BootstrapWitnessError("source_symlink")
            if path.is_file():
                paths.append(path)
    if len(paths) > _MAX_SOURCE_FILES:
        raise BootstrapWitnessError("source_file_bound")
    total_bytes = 0
    manifest: dict[str, str] = {}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise BootstrapWitnessError("source_unavailable")
        total_bytes += path.stat().st_size
        if total_bytes > _MAX_SOURCE_BYTES:
            raise BootstrapWitnessError("source_byte_bound")
        relative_path = path.relative_to(source)
        target = destination / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        manifest[str(relative_path)] = hashlib.sha256(target.read_bytes()).hexdigest()
    return manifest


def _isolated_environment(workspace: Path, project: Path, mise: Path) -> dict[str, str]:
    environment = {
        name: value
        for name, value in os.environ.items()
        if name
        in {
            "HOME",
            "USER",
            "LOGNAME",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "NO_PROXY",
            "http_proxy",
            "https_proxy",
            "no_proxy",
        }
    }
    binaries = workspace / "bin"
    binaries.mkdir()
    (binaries / "mise").symlink_to(mise)
    for tool in ("node", "pnpm"):
        sentinel = binaries / tool
        sentinel.write_text(
            "#!/bin/sh\nprintf '%s\\n' "
            + shlex.quote(tool)
            + " >> "
            + shlex.quote(str(workspace / "frontend-invocations"))
            + "\nexit 97\n"
        )
        sentinel.chmod(0o700)
    for name in (
        "data",
        "cache",
        "config",
        "system-config",
        "system-data",
        "shims",
        "uv-cache",
        "state",
        "mise-state",
        "mise-tmp",
    ):
        (workspace / name).mkdir(mode=0o700)
    (workspace / "config/config.toml").write_text("")
    environment.update(
        {
            "PATH": f"{binaries}:/usr/bin:/bin",
            "MISE_DATA_DIR": str(workspace / "data"),
            "MISE_CACHE_DIR": str(workspace / "cache"),
            "MISE_CONFIG_DIR": str(workspace / "config"),
            "MISE_SYSTEM_CONFIG_DIR": str(workspace / "system-config"),
            "MISE_SYSTEM_DATA_DIR": str(workspace / "system-data"),
            "MISE_GLOBAL_CONFIG_FILE": str(workspace / "config/config.toml"),
            "MISE_GLOBAL_CONFIG_ROOT": str(workspace / "config"),
            "MISE_STATE_DIR": str(workspace / "mise-state"),
            "MISE_TMP_DIR": str(workspace / "mise-tmp"),
            "MISE_SHIMS_DIR": str(workspace / "shims"),
            "MISE_TRUSTED_CONFIG_PATHS": str(project),
            "MISE_YES": "1",
            "UV_CACHE_DIR": str(workspace / "uv-cache"),
            "CI_COORDINATOR_DEV_STATE_HOME": str(workspace / "state"),
            "CI": "true",
            "NO_COLOR": "1",
        }
    )
    return environment


def _guard_frontend_tools(workspace: Path) -> tuple[Path, ...]:
    directories = tuple(workspace / "data/installs" / name for name in ("node", "pnpm"))
    for directory in directories:
        directory.mkdir(parents=True)
        # mise prunes empty install directories while rebuilding runtime symlinks.
        (directory / _FRONTEND_GUARD).touch(mode=0o400)
        directory.chmod(0o500)
    return directories


def _assert_frontend_absent(workspace: Path, project: Path, guarded: tuple[Path, ...]) -> None:
    if (workspace / "frontend-invocations").exists():
        raise BootstrapWitnessError("frontend_tool_used_or_installed")
    for directory in guarded:
        if directory.is_symlink() or not directory.is_dir():
            raise BootstrapWitnessError("frontend_install_guard_changed")
        marker = directory / _FRONTEND_GUARD
        if marker.is_symlink() or not marker.is_file():
            raise BootstrapWitnessError("frontend_install_guard_changed")
        if any(path != marker for path in directory.iterdir()):
            raise BootstrapWitnessError("frontend_tool_used_or_installed")
    if any(
        (project / relative).exists()
        for relative in ("node_modules", "frontend/node_modules", ".pnpm-store")
    ):
        raise BootstrapWitnessError("frontend_artifact_created")


def _assert_inputs_unchanged(project: Path, sources: dict[str, str]) -> None:
    if any(
        not (project / path).is_file()
        or hashlib.sha256((project / path).read_bytes()).hexdigest() != digest
        for path, digest in sources.items()
    ):
        raise BootstrapWitnessError("source_changed")


def _checked(
    mise: Path,
    arguments: Sequence[str],
    project: Path,
    environment: Mapping[str, str],
    phase: str,
    timeout: int,
) -> str:
    result = spawn(
        str(mise),
        arguments,
        cwd=project,
        env=environment,
        max_buffer=4_194_304,
        timeout_seconds=timeout,
    )
    if result.status != 0 or result.error is not None or result.failure_kind is not None:
        raise BootstrapWitnessError(
            phase + "_failed",
            phase=phase,
            exitCode=result.status,
            failureKind=result.failure_kind,
            providerError=result.error is not None,
        )
    return result.stdout


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mise", type=Path, required=True)
    options = parser.parse_args(argv)
    try:
        receipt = run(Path(__file__).resolve().parents[2], options.mise)
    except BootstrapWitnessError as error:
        sys.stdout.write(
            json.dumps(
                {"schemaVersion": 1, "state": "failed", "reason": str(error), **error.diagnostic}
            )
            + "\n"
        )
        return 2
    except (OSError, ValueError, KeyError, TypeError):
        sys.stdout.write(
            '{"schemaVersion":1,"state":"failed","reason":"bootstrap_unavailable",'
            '"phase":"bootstrap_entry"}\n'
        )
        return 2
    sys.stdout.write(json.dumps(receipt, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

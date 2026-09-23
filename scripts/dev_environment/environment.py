from __future__ import annotations

import fcntl
import hashlib
import json
import os
import platform
import stat
import sys
import tomllib
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

from scripts.bounded_process import spawn
from scripts.dev_environment.diagnostics import Reason
from scripts.dev_environment.identity import InstanceIdentity
from scripts.dev_environment.private_files import (
    atomic_write_private_text,
    ensure_private_directory,
    read_private_text,
    require_private_directory,
)

DependencyScope = Literal["backend", "frontend"]
_MARKER_DIRECTORY = ".ci-coordinator-dependencies"
_IMMUTABLE_IDENTITY = ("schemaVersion", "rootDigest", "platform", "architecture", "scope")
_MAX_INPUT_BYTES = 8_388_608


class EnvironmentError(RuntimeError):
    def __init__(self, reason: Reason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@contextmanager
def dependency_lease(identity: InstanceIdentity, *, exclusive: bool = False) -> Iterator[int]:
    directory = identity.state_home / "dependency-locks"
    ensure_private_directory(identity.state_home)
    ensure_private_directory(directory)
    path = directory / f"{identity.project_name}.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        _admit_lock(path, descriptor)
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise EnvironmentError(Reason.PREPARATION_IN_PROGRESS) from error
        _admit_lock(path, descriptor)
        yield descriptor
    finally:
        # Unlocking the shared open-file description would release an inherited child's lease.
        os.close(descriptor)


def admit_dependencies(identity: InstanceIdentity, scope: DependencyScope) -> None:
    destination = _destination(identity, scope)
    _admit_destination(identity, destination, required=True)
    expected = dependency_identity(identity, scope)
    observed = _read_marker(identity, destination)
    if observed is None:
        raise EnvironmentError(Reason.DEPENDENCIES_MISSING)
    _admit_owner(observed, expected)
    pending = _pending_path(identity, scope)
    if observed != expected or pending.exists() or pending.is_symlink():
        raise EnvironmentError(Reason.ENVIRONMENT_STALE)
    if scope == "backend":
        _admit_python(identity)
    else:
        _admit_destination(identity, identity.repo_root / "frontend/node_modules", required=True)


def prepare_dependencies(
    identity: InstanceIdentity,
    scopes: Sequence[DependencyScope],
    *,
    environment: Mapping[str, str] | None = None,
) -> None:
    if (
        not scopes
        or len(set(scopes)) != len(scopes)
        or any(scope not in {"backend", "frontend"} for scope in scopes)
    ):
        raise EnvironmentError(Reason.INVALID_ARGUMENT)
    with dependency_lease(identity, exclusive=True) as descriptor:
        requests = [_preparation_request(identity, scope) for scope in scopes]
        for scope, expected, force_frontend in requests:
            destination = _destination(identity, scope)
            provider_environment = _provider_environment(identity, scope, environment)
            _admit_tools(identity, scope, provider_environment, descriptor)
            pending = _pending_path(identity, scope)
            ensure_private_directory(pending.parent)
            atomic_write_private_text(pending, _json(expected))
            command: tuple[str, ...]
            if scope == "backend":
                command = (
                    "uv",
                    "sync",
                    "--project",
                    "backend",
                    "--frozen",
                    "--all-groups",
                    "--python",
                    str(_base_interpreter(identity)),
                    "--no-python-downloads",
                )
            else:
                command = (
                    "pnpm",
                    "install",
                    "--frozen-lockfile",
                    *(["--force"] if force_frontend else []),
                )
            result = spawn(
                command[0],
                command[1:],
                cwd=identity.repo_root,
                env=provider_environment,
                max_buffer=1_048_576,
                timeout_seconds=600,
                inherited_fds=(descriptor,),
            )
            if result.error is not None or result.status != 0:
                raise EnvironmentError(Reason.DEPENDENCIES_MISSING)
            _admit_destination(identity, destination, required=True)
            if expected != dependency_identity(identity, scope):
                raise EnvironmentError(Reason.ENVIRONMENT_STALE)
            if scope == "backend":
                _admit_python(identity)
            else:
                _admit_destination(
                    identity, identity.repo_root / "frontend/node_modules", required=True
                )
            _write_marker(identity, destination, expected)
            pending.unlink()


def dependency_identity(identity: InstanceIdentity, scope: DependencyScope) -> dict[str, object]:
    root = identity.repo_root
    if scope == "backend":
        files = ["backend/pyproject.toml", "backend/uv.lock"]
        names = ("python", "uv")
    elif scope == "frontend":
        files = [
            "package.json",
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
            ".npmrc",
            "frontend/package.json",
        ]
        _admit_path(identity, root / "patches", required=False)
        patches = sorted((root / "patches").glob("*.patch"))
        if len(patches) > 128:
            raise EnvironmentError(Reason.INVALID_STATE)
        files.extend(str(path.relative_to(root)) for path in patches)
        names = ("node", "pnpm")
    else:
        raise EnvironmentError(Reason.INVALID_ARGUMENT)
    digests = {
        relative: hashlib.sha256(_read_input(identity, root / relative)).hexdigest()
        for relative in files
    }
    tools = _tool_versions(identity)
    return {
        "schemaVersion": 1,
        "rootDigest": identity.root_digest,
        "platform": sys.platform,
        "architecture": platform.machine(),
        "scope": scope,
        "tools": {name: tools[name] for name in names},
        "inputs": digests,
    }


def _preparation_request(
    identity: InstanceIdentity,
    scope: DependencyScope,
) -> tuple[DependencyScope, dict[str, object], bool]:
    destination = _destination(identity, scope)
    _admit_destination(identity, destination, required=False)
    expected = dependency_identity(identity, scope)
    observed = _read_marker(identity, destination)
    pending = _pending_path(identity, scope)
    if pending.exists() or pending.is_symlink():
        _admit_owner(_read_record(pending), expected)
    if observed is not None:
        _admit_owner(observed, expected)
    elif scope == "backend" and destination.exists() and any(destination.iterdir()):
        _admit_python(identity)
        _admit_legacy_editable(identity)
    if scope == "frontend":
        for relative in ("frontend/node_modules", ".pnpm-store"):
            _admit_destination(identity, identity.repo_root / relative, required=False)
    return scope, expected, scope == "frontend" and observed is None and destination.exists()


def _read_marker(identity: InstanceIdentity, destination: Path) -> dict[str, object] | None:
    directory = destination / _MARKER_DIRECTORY
    _admit_destination(identity, directory, required=False)
    path = directory / "identity.json"
    if not path.exists() and not path.is_symlink():
        return None
    return _read_record(path)


def _read_record(path: Path) -> dict[str, object]:
    try:
        require_private_directory(path.parent)
        observed = json.loads(read_private_text(path))
    except (OSError, ValueError) as error:
        raise EnvironmentError(Reason.INVALID_STATE) from error
    if not isinstance(observed, dict):
        raise EnvironmentError(Reason.INVALID_STATE)
    return observed


def _write_marker(identity: InstanceIdentity, destination: Path, value: dict[str, object]) -> None:
    directory = destination / _MARKER_DIRECTORY
    _admit_destination(identity, directory, required=False)
    ensure_private_directory(directory)
    atomic_write_private_text(directory / "identity.json", _json(value))


def _json(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def _pending_path(identity: InstanceIdentity, scope: DependencyScope) -> Path:
    return identity.state_home / "dependency-preparation" / f"{identity.project_name}-{scope}.json"


def _admit_owner(observed: dict[str, object], expected: dict[str, object]) -> None:
    if type(observed.get("schemaVersion")) is not int:
        raise EnvironmentError(Reason.INVALID_STATE)
    if any(observed.get(key) != expected[key] for key in _IMMUTABLE_IDENTITY):
        raise EnvironmentError(Reason.FOREIGN_STATE)


def _destination(identity: InstanceIdentity, scope: DependencyScope) -> Path:
    return identity.repo_root / ("backend/.venv" if scope == "backend" else "node_modules")


def _admit_path(identity: InstanceIdentity, path: Path, *, required: bool = True) -> None:
    try:
        relative = path.relative_to(identity.repo_root)
    except ValueError as error:
        raise EnvironmentError(Reason.INVALID_STATE) from error
    current = identity.repo_root
    for index, part in enumerate(relative.parts):
        current /= part
        try:
            status = current.lstat()
        except FileNotFoundError as error:
            if not required:
                return
            raise EnvironmentError(Reason.DEPENDENCIES_MISSING) from error
        if stat.S_ISLNK(status.st_mode) or (
            index < len(relative.parts) - 1 and not stat.S_ISDIR(status.st_mode)
        ):
            raise EnvironmentError(Reason.INVALID_STATE)


def _admit_destination(identity: InstanceIdentity, path: Path, *, required: bool) -> None:
    _admit_path(identity, path, required=required)
    if not path.exists():
        return
    status = path.lstat()
    if not stat.S_ISDIR(status.st_mode) or status.st_uid != os.getuid():
        raise EnvironmentError(Reason.INVALID_STATE)


def _read_input(
    identity: InstanceIdentity, path: Path, *, maximum: int = _MAX_INPUT_BYTES
) -> bytes:
    _admit_path(identity, path)
    status = path.stat()
    if not stat.S_ISREG(status.st_mode) or status.st_size > maximum:
        raise EnvironmentError(Reason.INVALID_STATE)
    return path.read_bytes()


def _tool_versions(identity: InstanceIdentity) -> dict[str, str]:
    value = tomllib.loads(_read_input(identity, identity.repo_root / "mise.toml").decode("utf-8"))
    tools = value.get("tools")
    if not isinstance(tools, dict) or any(
        not isinstance(tools.get(name), str) for name in ("python", "uv", "node", "pnpm")
    ):
        raise EnvironmentError(Reason.INVALID_STATE)
    return {name: str(tools[name]) for name in ("python", "uv", "node", "pnpm")}


def _base_interpreter(identity: InstanceIdentity) -> Path:
    expected = _tool_versions(identity)["python"]
    if (
        sys.platform not in {"darwin", "linux"}
        or sys.implementation.name != "cpython"
        or ".".join(map(str, sys.version_info[:3])) != expected
    ):
        raise EnvironmentError(Reason.UNSUPPORTED_TOOL)
    return Path(getattr(sys, "_base_executable", sys.executable)).resolve(strict=True)


def _admit_python(identity: InstanceIdentity) -> None:
    destination = _destination(identity, "backend")
    config = dict(
        line.split(" = ", 1)
        for line in _read_input(identity, destination / "pyvenv.cfg", maximum=8192)
        .decode()
        .splitlines()
        if " = " in line
    )
    expected = _tool_versions(identity)["python"]
    if config.get("implementation") != "CPython" or config.get("version_info") != expected:
        raise EnvironmentError(Reason.ENVIRONMENT_STALE)
    _admit_destination(identity, destination / "bin", required=True)
    executable = destination / "bin/python"
    try:
        actual = executable.resolve(strict=True)
    except OSError as error:
        raise EnvironmentError(Reason.DEPENDENCIES_MISSING) from error
    if actual != _base_interpreter(identity) or not os.access(actual, os.X_OK):
        raise EnvironmentError(Reason.FOREIGN_STATE)


def _admit_legacy_editable(identity: InstanceIdentity) -> None:
    library = _destination(identity, "backend") / "lib"
    _admit_destination(identity, library, required=False)
    records = list(
        library.glob("python*/site-packages/ci_coordinator_backend-*.dist-info/direct_url.json")
    )
    if len(records) > 1:
        raise EnvironmentError(Reason.FOREIGN_STATE)
    for path in records:
        try:
            record = json.loads(_read_input(identity, path, maximum=8192))
            source = urlsplit(record["url"])
        except (ValueError, KeyError, TypeError) as error:
            raise EnvironmentError(Reason.INVALID_STATE) from error
        if (
            source.scheme != "file"
            or source.netloc
            or Path(unquote(source.path)).resolve() != identity.repo_root / "backend"
        ):
            raise EnvironmentError(Reason.FOREIGN_STATE)


def _admit_tools(
    identity: InstanceIdentity,
    scope: DependencyScope,
    environment: Mapping[str, str],
    descriptor: int,
) -> None:
    _base_interpreter(identity)
    versions = _tool_versions(identity)
    names = ("uv",) if scope == "backend" else ("node", "pnpm")
    for name in names:
        result = spawn(
            name,
            ("--version",),
            cwd=identity.repo_root,
            env=environment,
            max_buffer=8192,
            timeout_seconds=10,
            inherited_fds=(descriptor,),
        )
        output = result.stdout.strip()
        expected = ("uv " if name == "uv" else "v" if name == "node" else "") + versions[name]
        if (
            result.error is not None
            or result.status != 0
            or (output != expected and not (name == "uv" and output.startswith(expected + " (")))
        ):
            raise EnvironmentError(Reason.UNSUPPORTED_TOOL)


def _provider_environment(
    identity: InstanceIdentity, scope: DependencyScope, environment: Mapping[str, str] | None
) -> dict[str, str]:
    result = dict(os.environ if environment is None else environment)
    for name in tuple(result):
        if (
            name.startswith("UV_") and name not in {"UV_CACHE_DIR", "UV_NO_CACHE", "UV_OFFLINE"}
        ) or name in {
            "VIRTUAL_ENV",
            "CONDA_PREFIX",
            "PYTHONHOME",
            "PYTHONPATH",
        }:
            result.pop(name)
    if scope == "backend":
        result["UV_PROJECT_ENVIRONMENT"] = str(_destination(identity, "backend"))
    result["MISE_AUTO_INSTALL"] = "false"
    return result


def _admit_lock(path: Path, descriptor: int) -> None:
    opened = os.fstat(descriptor)
    current = path.lstat()
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != os.getuid()
        or stat.S_IMODE(opened.st_mode) != 0o600
        or opened.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise EnvironmentError(Reason.INVALID_STATE)

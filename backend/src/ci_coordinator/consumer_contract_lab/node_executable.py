from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Final

from ci_coordinator.consumer_contract_lab.process import run_bounded

_NODE_VERSION: Final = "v24.21.0"
_ADMISSION_SECONDS: Final = 10
_DIRECTORY_SETTINGS: Final = (
    "HOME",
    "MISE_DATA_DIR",
    "MISE_CONFIG_DIR",
    "MISE_CACHE_DIR",
    "XDG_DATA_HOME",
    "XDG_CONFIG_HOME",
    "XDG_CACHE_HOME",
)
_LOCALE: Final = {"LANG": "C", "LC_ALL": "C"}


class NodeExecutableError(RuntimeError):
    pass


def admit_node_executable(coordinator_root: Path, target_root: Path) -> str:
    deadline = time.monotonic() + _ADMISSION_SECONDS
    environment = dict(os.environ)
    try:
        target = target_root.resolve(strict=True)
        _outside_target(coordinator_root, target)
        node = _executable(shutil.which("node", path=environment.get("PATH", "")), target)
        mise_candidate = shutil.which("mise", path=environment.get("PATH", ""))
        if mise_candidate is not None and node.samefile(mise_candidate):
            selection_environment = dict(_LOCALE)
            if not environment.get("HOME"):
                environment["HOME"] = str(Path.home())
            for name in _DIRECTORY_SETTINGS:
                if value := environment.get(name):
                    _outside_target(Path(value), target)
                    selection_environment[name] = value
            raw = _query(
                node,
                ("which", "node", "--tool", f"core:node@{_NODE_VERSION[1:]}"),
                coordinator_root,
                selection_environment,
                deadline,
            )
            if not raw.endswith(b"\n") or raw.count(b"\n") != 1:
                raise NodeExecutableError("mise returned an invalid Node path")
            node = _executable(raw[:-1].decode("utf-8"), target)
        raw_identity = _query(
            node,
            ("--eval", "process.stdout.write(JSON.stringify([process.version,process.execPath]))"),
            coordinator_root,
            dict(_LOCALE),
            deadline,
        )
        if json.loads(raw_identity) != [_NODE_VERSION, str(node)]:
            raise NodeExecutableError("Node.js runtime identity is not admitted")
        return str(node)
    except NodeExecutableError:
        raise
    except (OSError, UnicodeError, RuntimeError, TypeError, ValueError) as error:
        raise NodeExecutableError("Node.js runtime admission failed") from error


def _executable(candidate: str | None, target: Path) -> Path:
    if not candidate:
        raise NodeExecutableError("Node.js executable is unavailable")
    path = Path(candidate)
    _outside_target(path, target)
    resolved = path.resolve(strict=True)
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise NodeExecutableError("Node.js executable is not an executable regular file")
    return resolved


def _outside_target(path: Path, target: Path) -> None:
    if (
        not path.is_absolute()
        or path.is_relative_to(target)
        or path.resolve().is_relative_to(target)
    ):
        raise NodeExecutableError("tool selection path is not outside target authority")


def _query(
    executable: Path,
    arguments: tuple[str, ...],
    cwd: Path,
    environment: dict[str, str],
    deadline: float,
) -> bytes:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise NodeExecutableError("Node.js runtime admission deadline expired")
    result = run_bounded(
        str(executable),
        arguments,
        cwd=cwd,
        env=environment,
        max_output_bytes=8_192,
        timeout_seconds=remaining,
    )
    if (
        result.status != 0
        or result.error is not None
        or result.stderr
        or time.monotonic() >= deadline
    ):
        raise NodeExecutableError("Node.js runtime admission failed")
    return result.stdout

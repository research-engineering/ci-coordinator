"""The sole runtime boundary allowed to read process environment values."""

from __future__ import annotations

import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ci_coordinator.runtime_settings import (
    RuntimeSettings,
    RuntimeSettingsRejection,
    admit_runtime_settings,
)

_MAX_PATH_UTF8_BYTES = 4_096


@dataclass(frozen=True, slots=True)
class _FileSecret:
    direct_name: str
    file_name: str
    maximum_bytes: int
    allowed_modes: frozenset[str]


_CONNECTED_MODES = frozenset({"enforcing", "non_enforcing"})
_ENFORCING_MODES = frozenset({"enforcing"})


_FILE_SECRETS = tuple(
    sorted(
        (
            _FileSecret(
                "CI_COORDINATOR_BREAK_GLASS_BEARER_TOKEN",
                "CI_COORDINATOR_BREAK_GLASS_BEARER_TOKEN_FILE",
                4_096,
                _CONNECTED_MODES,
            ),
            _FileSecret(
                "CI_COORDINATOR_CONTROL_PLANE_SESSION_KEY",
                "CI_COORDINATOR_CONTROL_PLANE_SESSION_KEY_FILE",
                64,
                _CONNECTED_MODES,
            ),
            _FileSecret(
                "CI_COORDINATOR_DATABASE_DSN",
                "CI_COORDINATOR_DATABASE_DSN_FILE",
                65_536,
                _CONNECTED_MODES,
            ),
            _FileSecret(
                "CI_COORDINATOR_GITHUB_APP_CLIENT_SECRET",
                "CI_COORDINATOR_GITHUB_APP_CLIENT_SECRET_FILE",
                4_096,
                _CONNECTED_MODES,
            ),
            _FileSecret(
                "CI_COORDINATOR_GITHUB_PRIVATE_KEY",
                "CI_COORDINATOR_GITHUB_PRIVATE_KEY_FILE",
                65_536,
                _CONNECTED_MODES,
            ),
            _FileSecret(
                "CI_COORDINATOR_METRICS_BEARER_TOKEN",
                "CI_COORDINATOR_METRICS_BEARER_TOKEN_FILE",
                4_096,
                _CONNECTED_MODES,
            ),
            _FileSecret(
                "CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_SECRET",
                "CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_SECRET_FILE",
                4_096,
                _CONNECTED_MODES,
            ),
            _FileSecret(
                "CI_COORDINATOR_PLAN_SIGNING_PRIVATE_KEY",
                "CI_COORDINATOR_PLAN_SIGNING_PRIVATE_KEY_FILE",
                65_536,
                _CONNECTED_MODES,
            ),
            _FileSecret(
                "CI_COORDINATOR_PRODUCTION_ADMISSION_PUBLIC_KEY_PEM",
                "CI_COORDINATOR_PRODUCTION_ADMISSION_PUBLIC_KEY_PEM_FILE",
                16_384,
                _ENFORCING_MODES,
            ),
            _FileSecret(
                "CI_COORDINATOR_WEBHOOK_SECRET",
                "CI_COORDINATOR_WEBHOOK_SECRET_FILE",
                65_536,
                _CONNECTED_MODES,
            ),
        ),
        key=lambda item: item.file_name,
    )
)


def snapshot_process_environment() -> dict[str, str]:
    """Detach process values at one explicit composition boundary."""
    return dict(os.environ)


def load_runtime_settings_from_environment(
    environment: Mapping[str, str] | None = None,
) -> RuntimeSettings | RuntimeSettingsRejection:
    """Snapshot process wiring before pure settings admission."""
    source = snapshot_process_environment() if environment is None else environment
    resolved = _resolve_file_secrets(dict(source))
    if isinstance(resolved, RuntimeSettingsRejection):
        return resolved
    return admit_runtime_settings(resolved)


def _resolve_file_secrets(
    source: dict[str, str],
) -> dict[str, str] | RuntimeSettingsRejection:
    mode = source.get("CI_COORDINATOR_RUNTIME_MODE")
    if mode not in {"disabled", "enforcing", "non_enforcing"}:
        return source
    for contract in _FILE_SECRETS:
        if contract.file_name not in source:
            continue
        if mode not in contract.allowed_modes:
            return RuntimeSettingsRejection("invalid_setting_value", contract.file_name)
        if contract.direct_name in source:
            return RuntimeSettingsRejection("invalid_setting_value", contract.file_name)
    for contract in _FILE_SECRETS:
        if contract.file_name not in source:
            continue
        value = _read_secret_file(source[contract.file_name], contract.maximum_bytes)
        if value is None:
            return RuntimeSettingsRejection("invalid_setting_value", contract.file_name)
        del source[contract.file_name]
        source[contract.direct_name] = value
    return source


def _read_secret_file(path_text: object, maximum_bytes: int) -> str | None:
    if (
        type(path_text) is not str
        or not path_text
        or path_text != os.path.normpath(path_text)
        or "\x00" in path_text
    ):
        return None
    try:
        path_size = len(path_text.encode("utf-8", errors="strict"))
    except UnicodeEncodeError:
        return None
    if path_size > _MAX_PATH_UTF8_BYTES:
        return None
    path = Path(path_text)
    if not path.is_absolute():
        return None
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NONBLOCK
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return None
    close_failed = False
    try:
        before = os.fstat(descriptor)
        maximum_file_bytes = maximum_bytes + 1
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or not 0 <= before.st_size <= maximum_file_bytes
        ):
            return None
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(65_536, maximum_file_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum_file_bytes:
                return None
        after = os.fstat(descriptor)
    except OSError:
        return None
    finally:
        try:
            os.close(descriptor)
        except OSError:
            close_failed = True
    if close_failed:
        return None
    if (before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
    ) or total != before.st_size:
        return None
    raw_value = b"".join(chunks)
    if raw_value.endswith(b"\n"):
        raw_value = raw_value[:-1]
    if not raw_value or len(raw_value) > maximum_bytes or b"\x00" in raw_value:
        return None
    try:
        return raw_value.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None

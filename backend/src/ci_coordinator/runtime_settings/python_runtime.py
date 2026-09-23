"""Machine-owned interpreter profile and exact runtime admission."""

from __future__ import annotations

import json
import platform
import re
import sys
import sysconfig
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.resources import files
from typing import Final, cast

type PythonVersion = tuple[int, int, int]

PYTHON_RUNTIME_PROFILE_RESOURCE: Final = "python-runtime-profile.v1.json"
PYTHON_RUNTIME_PROFILE_SCHEMA: Final = "ci-coordinator-python-runtime-profile/v1"
PYTHON_RUNTIME_PROFILE_ID: Final = "ci-coordinator/python-runtime/v1"
_ROOT_KEYS: Final = frozenset(
    {
        "schemaVersion",
        "profileId",
        "implementation",
        "buildVariant",
        "supportedVersions",
        "requiresPython",
        "staticTarget",
        "containerVersion",
    }
)
_VERSION_PATTERN: Final = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True, slots=True)
class PythonRuntimeProfile:
    implementation: str
    build_variant: str
    supported_versions: tuple[PythonVersion, ...]
    requires_python: str
    static_target: str
    container_version: PythonVersion


@dataclass(frozen=True, slots=True)
class UnsupportedPythonRuntime:
    actual_implementation: str
    actual_version: str
    supported_implementation: str
    supported_versions: tuple[str, ...]
    actual_build_variant: str = "gil-enabled"
    supported_build_variant: str = "gil-enabled"


def parse_python_runtime_profile(raw_document: bytes) -> PythonRuntimeProfile:
    if type(raw_document) is not bytes:
        raise ValueError("Python runtime profile must be exact bytes")
    try:
        document = json.loads(raw_document, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("Python runtime profile must be duplicate-free UTF-8 JSON") from error
    if type(document) is not dict:
        raise ValueError("Python runtime profile must be an object")
    mapping = cast(Mapping[str, object], document)
    if (
        set(mapping) != _ROOT_KEYS
        or mapping.get("schemaVersion") != PYTHON_RUNTIME_PROFILE_SCHEMA
        or mapping.get("profileId") != PYTHON_RUNTIME_PROFILE_ID
        or mapping.get("implementation") != "CPython"
        or mapping.get("buildVariant") != "gil-enabled"
    ):
        raise ValueError("Python runtime profile identity is unsupported")
    raw_versions = mapping["supportedVersions"]
    if type(raw_versions) is not list:
        raise ValueError("supported Python versions must be an array")
    versions = tuple(_parse_version(item) for item in cast(Sequence[object], raw_versions))
    if not versions or versions != tuple(sorted(set(versions))):
        raise ValueError("supported Python versions must be non-empty, unique, and ordered")
    version_lines = tuple((major, minor) for major, minor, _patch in versions)
    if (
        len(set(version_lines)) != len(version_lines)
        or len({major for major, _ in version_lines}) != 1
    ):
        raise ValueError("supported Python versions must identify one patch per minor line")
    expected_static_target = ".".join(str(part) for part in version_lines[0])
    expected_requires_python = _requires_python_envelope(versions)
    if (
        mapping.get("requiresPython") != expected_requires_python
        or mapping.get("staticTarget") != expected_static_target
    ):
        raise ValueError("Python runtime profile projections are inconsistent")
    container_version = _parse_version(mapping["containerVersion"])
    if container_version != versions[-1]:
        raise ValueError("container Python version must be the newest supported runtime")
    return PythonRuntimeProfile(
        implementation="CPython",
        build_variant="gil-enabled",
        supported_versions=versions,
        requires_python=expected_requires_python,
        static_target=expected_static_target,
        container_version=container_version,
    )


def load_bundled_python_runtime_profile() -> PythonRuntimeProfile:
    raw_document = (
        files("ci_coordinator.runtime_settings.resources")
        .joinpath(PYTHON_RUNTIME_PROFILE_RESOURCE)
        .read_bytes()
    )
    return parse_python_runtime_profile(raw_document)


def admit_python_runtime(
    version_info: Sequence[int] | None = None,
    implementation: str | None = None,
    *,
    free_threaded: bool | None = None,
) -> UnsupportedPythonRuntime | None:
    """Return a typed rejection unless the exact CPython build is supported."""
    version = (
        (sys.version_info.major, sys.version_info.minor, sys.version_info.micro)
        if version_info is None
        else _observed_version(version_info)
    )
    observed_implementation = (
        platform.python_implementation() if implementation is None else implementation
    )
    observed_build_variant = _observed_build_variant(free_threaded)
    if (
        version is not None
        and observed_implementation == PYTHON_RUNTIME_PROFILE.implementation
        and version in SUPPORTED_PYTHON_VERSIONS
        and observed_build_variant == PYTHON_RUNTIME_PROFILE.build_variant
    ):
        return None
    return UnsupportedPythonRuntime(
        actual_implementation=observed_implementation,
        actual_version="<invalid>" if version is None else _render_version(version),
        supported_implementation=PYTHON_RUNTIME_PROFILE.implementation,
        supported_versions=tuple(_render_version(item) for item in SUPPORTED_PYTHON_VERSIONS),
        actual_build_variant=observed_build_variant,
        supported_build_variant=PYTHON_RUNTIME_PROFILE.build_variant,
    )


def _observed_build_variant(free_threaded: bool | None) -> str:
    if free_threaded is None:
        raw_value = sysconfig.get_config_var("Py_GIL_DISABLED")
        if raw_value is None or (type(raw_value) is int and raw_value == 0):
            return "gil-enabled"
        if type(raw_value) is int and raw_value == 1:
            return "free-threaded"
        return "<invalid>"
    if type(free_threaded) is bool:
        return "free-threaded" if free_threaded else "gil-enabled"
    return "<invalid>"


def _parse_version(value: object) -> PythonVersion:
    if type(value) is not str or (match := _VERSION_PATTERN.fullmatch(value)) is None:
        raise ValueError("Python runtime version must be a canonical patch version")
    return (int(match[1]), int(match[2]), int(match[3]))


def _observed_version(value: Sequence[int]) -> PythonVersion | None:
    if len(value) < 3:
        return None
    version = value[0], value[1], value[2]
    if any(type(part) is not int or part < 0 for part in version):
        return None
    return version


def _render_version(version: PythonVersion) -> str:
    return ".".join(str(part) for part in version)


def _requires_python_envelope(versions: tuple[PythonVersion, ...]) -> str:
    minimum = _render_version(versions[0])
    if len(versions) == 1:
        return f"=={minimum}"
    major, newest_minor, _patch = versions[-1]
    return f">={minimum},<{major}.{newest_minor + 1}"


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Python runtime profile contains a duplicate key")
        result[key] = value
    return result


PYTHON_RUNTIME_PROFILE: Final = load_bundled_python_runtime_profile()
SUPPORTED_PYTHON_VERSIONS: Final = PYTHON_RUNTIME_PROFILE.supported_versions

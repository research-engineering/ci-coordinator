from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import tomllib
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any, Final, cast
from urllib.parse import urlparse

from scripts.bounded_process import spawn

PACKAGE_NAME: Final = "agentic-proofkit"
PACKAGE_VERSION: Final = "0.14.18"
PACKAGE_SPEC: Final = f"{PACKAGE_NAME}=={PACKAGE_VERSION}"
PYPI_REGISTRY: Final = "https://pypi.org/simple"
WHEEL_SUFFIXES: Final = frozenset(
    {
        "py3-none-macosx_13_0_arm64.whl",
        "py3-none-macosx_13_0_x86_64.whl",
        "py3-none-manylinux_2_17_aarch64.whl",
        "py3-none-manylinux_2_17_x86_64.whl",
    }
)
SHA256_PATTERN: Final = re.compile(r"sha256:[0-9a-f]{64}\Z")


@dataclass(frozen=True, slots=True)
class LockedWheel:
    filename: str
    digest: str


@dataclass(frozen=True, slots=True)
class LockAdmission:
    registry: str
    version: str
    wheels: tuple[LockedWheel, ...]


def admit_locked_distribution(pyproject_path: Path, lock_path: Path) -> LockAdmission:
    project = _load_toml(pyproject_path)
    groups = _mapping(project.get("dependency-groups"), "dependency-groups")
    dev = _sequence(groups.get("dev"), "dependency-groups.dev")
    package_specs = sorted(
        item for item in dev if isinstance(item, str) and item.startswith(PACKAGE_NAME)
    )
    if package_specs != [PACKAGE_SPEC]:
        raise ValueError(f"development dependencies must contain exactly {PACKAGE_SPEC}")

    lock = _load_toml(lock_path)
    packages = _sequence(lock.get("package"), "package")
    matches = [
        _mapping(item, "package entry")
        for item in packages
        if isinstance(item, dict) and item.get("name") == PACKAGE_NAME
    ]
    if len(matches) != 1:
        raise ValueError(f"lock must contain exactly one {PACKAGE_NAME} package")
    package = matches[0]
    version = _string(package.get("version"), "package.version")
    if version != PACKAGE_VERSION:
        raise ValueError(f"locked {PACKAGE_NAME} version must be {PACKAGE_VERSION}")
    source = _mapping(package.get("source"), "package.source")
    registry = _string(source.get("registry"), "package.source.registry")
    if registry != PYPI_REGISTRY:
        raise ValueError(f"locked {PACKAGE_NAME} registry must be {PYPI_REGISTRY}")

    wheels = tuple(
        sorted(
            (_locked_wheel(item) for item in _sequence(package.get("wheels"), "package.wheels")),
            key=lambda wheel: wheel.filename,
        )
    )
    suffixes = {_wheel_suffix(wheel.filename) for wheel in wheels}
    if suffixes != WHEEL_SUFFIXES or len(wheels) != len(WHEEL_SUFFIXES):
        raise ValueError("locked agentic-proofkit wheel platform set is incomplete or ambiguous")
    return LockAdmission(registry=registry, version=version, wheels=wheels)


def current_platform_suffix(*, system: str | None = None, machine: str | None = None) -> str:
    selected_system = platform.system() if system is None else system
    selected_machine = platform.machine() if machine is None else machine
    normalized_machine = {
        "aarch64": "arm64",
        "amd64": "x86_64",
        "arm64": "arm64",
        "x86_64": "x86_64",
    }.get(selected_machine.lower())
    if normalized_machine is None:
        raise ValueError(f"unsupported proof tooling architecture: {selected_machine}")
    if selected_system == "Darwin":
        return f"py3-none-macosx_13_0_{normalized_machine}.whl"
    if selected_system == "Linux":
        linux_machine = "aarch64" if normalized_machine == "arm64" else normalized_machine
        return f"py3-none-manylinux_2_17_{linux_machine}.whl"
    raise ValueError(f"unsupported proof tooling operating system: {selected_system}")


def build_report(repo_root: Path) -> tuple[dict[str, object], int]:
    failures: list[str] = []
    lock_admission: LockAdmission | None = None
    binary_digest: str | None = None
    installed_version: str | None = None
    selected_wheel: LockedWheel | None = None
    try:
        lock_admission = admit_locked_distribution(
            repo_root / "backend" / "pyproject.toml",
            repo_root / "backend" / "uv.lock",
        )
        suffix = current_platform_suffix()
        platform_wheels = [
            wheel for wheel in lock_admission.wheels if wheel.filename.endswith(suffix)
        ]
        if len(platform_wheels) != 1:
            raise ValueError("lock does not contain exactly one wheel for the current platform")
        selected_wheel = platform_wheels[0]
    except (OSError, TypeError, ValueError, tomllib.TOMLDecodeError) as error:
        failures.append(str(error))

    try:
        installed = distribution(PACKAGE_NAME)
        installed_version = installed.version
        if installed_version != PACKAGE_VERSION:
            raise ValueError(f"installed {PACKAGE_NAME} version must be {PACKAGE_VERSION}")
        package_root = Path(str(installed.locate_file("agentic_proofkit"))).resolve()
        environment_root = Path(sys.prefix).resolve()
        binary = (package_root / "bin" / "agentic-proofkit").resolve()
        console_script = (environment_root / "bin" / "agentic-proofkit").resolve()
        if not package_root.is_relative_to(environment_root):
            raise ValueError("agentic-proofkit package is outside the active Python environment")
        if not binary.is_file() or not binary.is_relative_to(package_root):
            raise ValueError("agentic-proofkit native binary is outside the installed distribution")
        if not os.access(binary, os.X_OK):
            raise ValueError("agentic-proofkit native binary is not executable")
        if not console_script.is_file() or not console_script.is_relative_to(environment_root):
            raise ValueError("agentic-proofkit console script is outside the active environment")
        result = spawn(
            str(console_script),
            ("--help",),
            cwd=repo_root,
            max_buffer=1024 * 1024,
            timeout_seconds=30,
        )
        if (
            result.error is not None
            or result.status != 0
            or result.stderr
            or not result.stdout.startswith("Usage:\n")
        ):
            raise ValueError("agentic-proofkit CLI smoke failed")
        binary_digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    except (
        OSError,
        PackageNotFoundError,
        TypeError,
        ValueError,
    ) as error:
        failures.append(str(error))

    state = "passed" if not failures else "failed"
    report: dict[str, object] = {
        "schemaVersion": 1,
        "reportKind": "ci-coordinator.python-distribution-dependency",
        "reportId": "ci-coordinator.agentic-proofkit-dependency",
        "state": state,
        "summary": {
            "accepted": not failures,
            "failureCount": len(failures),
            "installedVersion": installed_version,
            "lockedRegistry": lock_admission.registry if lock_admission else None,
            "lockedVersion": lock_admission.version if lock_admission else None,
            "selectedWheel": selected_wheel.filename if selected_wheel else None,
        },
        "artifact": {
            "binarySha256": binary_digest,
            "wheelSha256": selected_wheel.digest.removeprefix("sha256:")
            if selected_wheel
            else None,
        },
        "diagnostics": sorted(failures),
        "nonClaims": [
            (
                "This witness admits caller-owned manifest, uv lock, installed metadata, "
                "location, and CLI smoke facts."
            ),
            (
                "It does not authenticate PyPI availability, publisher identity, provider "
                "execution, proof freshness, or production readiness."
            ),
        ],
    }
    return report, 0 if state == "passed" else 1


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    report, status = build_report(repo_root)
    print(json.dumps(report, indent=2, sort_keys=True))
    return status


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as source:
        return tomllib.load(source)


def _mapping(value: object, context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{context} must be a string-keyed table")
    return cast(dict[str, Any], value)


def _sequence(value: object, context: str) -> list[Any]:
    if not isinstance(value, list):
        raise TypeError(f"{context} must be an array")
    return value


def _string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be non-empty text")
    return value


def _locked_wheel(value: object) -> LockedWheel:
    wheel = _mapping(value, "package wheel")
    url = _string(wheel.get("url"), "package wheel url")
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "files.pythonhosted.org":
        raise ValueError("locked agentic-proofkit wheel must use files.pythonhosted.org")
    filename = Path(parsed.path).name
    digest = _string(wheel.get("hash"), "package wheel hash")
    if not SHA256_PATTERN.fullmatch(digest):
        raise ValueError("locked agentic-proofkit wheel must use a lowercase SHA-256 hash")
    return LockedWheel(filename=filename, digest=digest)


def _wheel_suffix(filename: str) -> str:
    prefix = f"agentic_proofkit-{PACKAGE_VERSION}-"
    if not filename.startswith(prefix):
        raise ValueError("locked agentic-proofkit wheel filename has an unexpected identity")
    return filename.removeprefix(prefix)


if __name__ == "__main__":
    raise SystemExit(main())

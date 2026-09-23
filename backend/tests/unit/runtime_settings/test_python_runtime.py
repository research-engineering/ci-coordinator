from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from ci_coordinator.runtime_settings import (
    PYTHON_RUNTIME_PROFILE,
    PythonRuntimeProfile,
    UnsupportedPythonRuntime,
    admit_python_runtime,
    parse_python_runtime_profile,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]


def test_exact_supported_python_version_is_admitted() -> None:
    assert admit_python_runtime((3, 13, 15)) is None


@pytest.mark.parametrize(
    "version",
    ((3, 12, 13), (3, 13, 14), (3, 13, 16), (3, 14, 7), (3, 15, 0)),
)
def test_every_other_python_patch_is_rejected(version: tuple[int, int, int]) -> None:
    assert admit_python_runtime(version) == UnsupportedPythonRuntime(
        actual_implementation="CPython",
        actual_version=".".join(str(part) for part in version),
        supported_implementation="CPython",
        supported_versions=("3.13.15",),
    )


def test_non_cpython_implementation_is_rejected() -> None:
    assert admit_python_runtime((3, 13, 15), "PyPy") == UnsupportedPythonRuntime(
        actual_implementation="PyPy",
        actual_version="3.13.15",
        supported_implementation="CPython",
        supported_versions=("3.13.15",),
    )


def test_free_threaded_cpython_build_is_rejected() -> None:
    assert admit_python_runtime((3, 13, 15), free_threaded=True) == UnsupportedPythonRuntime(
        actual_implementation="CPython",
        actual_version="3.13.15",
        supported_implementation="CPython",
        supported_versions=("3.13.15",),
        actual_build_variant="free-threaded",
        supported_build_variant="gil-enabled",
    )


@pytest.mark.parametrize("version", ((), (3,), (3, 13), (3, 13, True), (3, 13, -1)))
def test_malformed_runtime_versions_are_typed_rejections(version: tuple[int, ...]) -> None:
    assert admit_python_runtime(version) == UnsupportedPythonRuntime(
        actual_implementation="CPython",
        actual_version="<invalid>",
        supported_implementation="CPython",
        supported_versions=("3.13.15",),
    )


def test_bundled_profile_is_the_exact_admitted_contract() -> None:
    assert (
        PythonRuntimeProfile(
            implementation="CPython",
            build_variant="gil-enabled",
            supported_versions=((3, 13, 15),),
            requires_python="==3.13.15",
            static_target="3.13",
            container_version=(3, 13, 15),
        )
        == PYTHON_RUNTIME_PROFILE
    )


def test_profile_parser_rejects_duplicate_keys() -> None:
    with pytest.raises(ValueError, match="duplicate-free"):
        parse_python_runtime_profile(b'{"schemaVersion":"one","schemaVersion":"two"}')


@pytest.mark.parametrize(
    "source",
    (
        b'{"schemaVersion":"ci-coordinator-python-runtime-profile/v1","profileId":"ci-coordinator/python-runtime/v1","implementation":"CPython","buildVariant":"gil-enabled","supportedVersions":[],"requiresPython":"==3.13.15","staticTarget":"3.13","containerVersion":"3.13.15"}',
        b'{"schemaVersion":"ci-coordinator-python-runtime-profile/v1","profileId":"ci-coordinator/python-runtime/v1","implementation":"CPython","buildVariant":"gil-enabled","supportedVersions":["3.13.15","3.13.15"],"requiresPython":"==3.13.15","staticTarget":"3.13","containerVersion":"3.13.15"}',
        b'{"schemaVersion":"ci-coordinator-python-runtime-profile/v1","profileId":"ci-coordinator/python-runtime/v1","implementation":"CPython","buildVariant":"gil-enabled","supportedVersions":["3.13.15"],"requiresPython":">=3.13.15,<3.15","staticTarget":"3.13","containerVersion":"3.13.15"}',
        b'{"schemaVersion":"ci-coordinator-python-runtime-profile/v1","profileId":"ci-coordinator/python-runtime/v1","implementation":"CPython","buildVariant":"gil-enabled","supportedVersions":["3.13.15"],"requiresPython":"==3.13.15","staticTarget":"3.13","containerVersion":"3.13.14"}',
    ),
)
def test_profile_parser_rejects_internally_inconsistent_runtime_sets(source: bytes) -> None:
    with pytest.raises(ValueError):
        parse_python_runtime_profile(source)


def test_canonical_and_packaged_profiles_are_byte_identical() -> None:
    canonical = REPOSITORY_ROOT / "docs/specs/ci-coordinator-runtime/python-runtime-profile.v1.json"
    packaged = (
        REPOSITORY_ROOT
        / "backend/src/ci_coordinator/runtime_settings/resources/python-runtime-profile.v1.json"
    )

    assert canonical.read_bytes() == packaged.read_bytes()
    assert parse_python_runtime_profile(canonical.read_bytes()) == PYTHON_RUNTIME_PROFILE


def test_packaging_and_container_surfaces_project_the_runtime_profile() -> None:
    project = tomllib.loads((REPOSITORY_ROOT / "backend/pyproject.toml").read_text())
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text()

    assert project["project"]["requires-python"] == PYTHON_RUNTIME_PROFILE.requires_python
    assert project["tool"]["mypy"]["python_version"] == PYTHON_RUNTIME_PROFILE.static_target
    container_version = ".".join(map(str, PYTHON_RUNTIME_PROFILE.container_version))
    assert any(
        line.startswith(f"FROM python:{container_version}-") for line in dockerfile.splitlines()
    )

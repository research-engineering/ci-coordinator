from __future__ import annotations

import tomllib
from pathlib import Path
from typing import cast

import pytest
from scripts.proofkit_dependency import (
    PACKAGE_SPEC,
    admit_locked_distribution,
    build_report,
    current_platform_suffix,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_repository_proofkit_distribution_is_admitted() -> None:
    admission = admit_locked_distribution(
        REPO_ROOT / "backend" / "pyproject.toml",
        REPO_ROOT / "backend" / "uv.lock",
    )

    assert admission.version == "0.14.18"
    assert len(admission.wheels) == 4
    assert all(wheel.digest.startswith("sha256:") for wheel in admission.wheels)


def test_active_environment_executes_admitted_distribution() -> None:
    report, status = build_report(REPO_ROOT)

    assert status == 0
    assert report["state"] == "passed"


def test_malformed_dependency_shape_produces_a_failure_report(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "pyproject.toml").write_text(
        '[dependency-groups]\ndev = "agentic-proofkit==0.14.18"\n',
        encoding="utf-8",
    )

    report, status = build_report(tmp_path)

    assert status == 1
    assert report["state"] == "failed"
    assert cast(dict[str, object], report["summary"])["failureCount"] == 1
    assert report["diagnostics"] == ["dependency-groups.dev must be an array"]


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Darwin", "arm64", "py3-none-macosx_13_0_arm64.whl"),
        ("Darwin", "x86_64", "py3-none-macosx_13_0_x86_64.whl"),
        ("Linux", "aarch64", "py3-none-manylinux_2_17_aarch64.whl"),
        ("Linux", "amd64", "py3-none-manylinux_2_17_x86_64.whl"),
    ],
)
def test_supported_platform_projection(system: str, machine: str, expected: str) -> None:
    assert current_platform_suffix(system=system, machine=machine) == expected


@pytest.mark.parametrize(
    ("system", "machine"),
    [("Windows", "x86_64"), ("Darwin", "riscv64"), ("Linux", "ppc64le")],
)
def test_unpublished_platform_is_rejected(system: str, machine: str) -> None:
    with pytest.raises(ValueError, match="unsupported proof tooling"):
        current_platform_suffix(system=system, machine=machine)


def test_manifest_spec_is_exact() -> None:
    with (REPO_ROOT / "backend" / "pyproject.toml").open("rb") as source:
        project = tomllib.load(source)

    assert PACKAGE_SPEC in project["dependency-groups"]["dev"]

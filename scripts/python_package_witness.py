from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from scripts.bounded_process import CommandResult, spawn


class PackageWitnessError(RuntimeError):
    pass


_NESTED_PACKAGE_DATA_WITNESS = Path(
    "src/ci_coordinator/api/http/static/assets/package-witness/nested.js"
)
_NESTED_PACKAGE_DATA_CONTENT = b"export const packageDataWitness = true;\n"


@dataclass(frozen=True, slots=True)
class PackageWitnessResult:
    artifact: str
    public_symbol_count: int
    capacity_qualification_resource_count: int
    ci_economics_resource_count: int
    consumer_contract_lab_resource_count: int
    resource_count: int
    persistence_resource_count: int
    production_admission_resource_count: int
    github_ingestion_resource_count: int
    operator_ui_nested_asset_count: int
    runtime_settings_resource_count: int
    target_artifacts_resource_count: int


def inspect_package(repo_root: Path, backend_root: Path, venv_python: Path) -> PackageWitnessResult:
    with tempfile.TemporaryDirectory(prefix="ci-coordinator-python-package-") as temporary:
        dist_root = Path(temporary)
        build_project = dist_root / "project"
        shutil.copytree(
            backend_root,
            build_project,
            ignore=shutil.ignore_patterns(
                ".mypy_cache",
                ".pytest_cache",
                ".ruff_cache",
                ".venv",
                "*.egg-info",
                "__pycache__",
                "build",
            ),
        )
        build_environment = {
            **os.environ,
            "PATH": f"{venv_python.parent}{os.pathsep}{os.environ.get('PATH', '')}",
            "VIRTUAL_ENV": str(venv_python.parent.parent),
        }
        nested_asset = build_project / _NESTED_PACKAGE_DATA_WITNESS
        nested_asset.parent.mkdir(parents=True, exist_ok=True)
        nested_asset.write_bytes(_NESTED_PACKAGE_DATA_CONTENT)
        _run(
            (
                "uv",
                "build",
                "--project",
                str(build_project),
                "--no-build-isolation",
                "--out-dir",
                str(dist_root),
            ),
            cwd=repo_root,
            environment=build_environment,
        )
        wheels = tuple(sorted(dist_root.glob("*.whl")))
        source_distributions = tuple(sorted(dist_root.glob("*.tar.gz")))
        if len(wheels) != 1 or len(source_distributions) != 1:
            raise PackageWitnessError(
                "package build must produce exactly one source distribution and one wheel"
            )

        inspection_venv = dist_root / "inspection-venv"
        inspection_python = _venv_python(inspection_venv)
        _run((str(venv_python), "-m", "venv", str(inspection_venv)), cwd=dist_root)
        _run(
            (
                str(inspection_python),
                "-m",
                "pip",
                "install",
                "--quiet",
                "--disable-pip-version-check",
                "--no-deps",
                "--no-index",
                str(wheels[0]),
            ),
            cwd=dist_root,
        )
        inspection = _run(
            (
                str(inspection_python),
                "-I",
                str(repo_root / "scripts" / "package_resource_inspection.py"),
                str(repo_root / "docs" / "specs" / "ci-coordinator-core"),
                str(repo_root / "docs" / "specs" / "ci-coordinator-runtime"),
                str(backend_root / "src" / "ci_coordinator" / "target_artifacts" / "resources"),
                str(
                    backend_root / "src" / "ci_coordinator" / "consumer_contract_lab" / "resources"
                ),
                str(
                    backend_root / "src" / "ci_coordinator" / "capacity_qualification" / "resources"
                ),
                str(backend_root / "src" / "ci_coordinator" / "ci_economics" / "resources"),
                str(inspection_venv),
                str(_locked_site_packages(venv_python)),
            ),
            cwd=dist_root,
        )
        payload = _inspection_payload(inspection.stdout)
        public_api = _run(
            (
                str(venv_python),
                "-I",
                str(repo_root / "scripts" / "package_public_api_inspection.py"),
                payload["sitePackages"],
            ),
            cwd=dist_root,
        )
        public_payload: object = json.loads(public_api.stdout)
        if not isinstance(public_payload, dict):
            raise PackageWitnessError("wheel public API inspection returned an invalid result")
        public_symbol_count = public_payload.get("publicSymbolCount")
        if not isinstance(public_symbol_count, int) or public_symbol_count < 1:
            raise PackageWitnessError("wheel public API inspection returned an invalid result")
        return PackageWitnessResult(
            artifact=wheels[0].name,
            public_symbol_count=public_symbol_count,
            capacity_qualification_resource_count=1,
            ci_economics_resource_count=1,
            consumer_contract_lab_resource_count=2,
            resource_count=8,
            persistence_resource_count=3,
            production_admission_resource_count=1,
            github_ingestion_resource_count=1,
            operator_ui_nested_asset_count=1,
            runtime_settings_resource_count=4,
            target_artifacts_resource_count=6,
        )


def _inspection_payload(source: str) -> dict[str, str]:
    value: object = json.loads(source)
    expected_counts = {
        "capacityQualificationResourceCount": 1,
        "ciEconomicsResourceCount": 1,
        "consumerContractLabResourceCount": 2,
        "resourceCount": 8,
        "persistenceResourceCount": 3,
        "productionAdmissionResourceCount": 1,
        "githubIngestionResourceCount": 1,
        "operatorUiNestedAssetCount": 1,
        "runtimeSettingsResourceCount": 4,
        "targetArtifactsResourceCount": 7,
    }
    if (
        not isinstance(value, dict)
        or set(value) != {*expected_counts, "sitePackages"}
        or any(value.get(key) != count for key, count in expected_counts.items())
    ):
        raise PackageWitnessError("wheel inspection returned an invalid result")
    site_packages = value.get("sitePackages")
    if not isinstance(site_packages, str):
        raise PackageWitnessError("wheel inspection returned an invalid result")
    return {"sitePackages": site_packages}


def _run(
    arguments: tuple[str, ...],
    *,
    cwd: Path,
    environment: dict[str, str] | None = None,
) -> CommandResult:
    # Arguments originate from the closed witness catalog, never product input.
    completed = spawn(
        arguments[0],
        arguments[1:],
        cwd=cwd,
        env=environment,
        max_buffer=16 * 1024 * 1024,
    )
    if completed.error is not None:
        raise PackageWitnessError(completed.error)
    if completed.status != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise PackageWitnessError(detail or f"command exited {completed.status}")
    return completed


def _venv_python(venv_root: Path) -> Path:
    if sys.platform == "win32":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def _locked_site_packages(venv_python: Path) -> Path:
    completed = _run(
        (str(venv_python), "-c", "import site; print(site.getsitepackages()[0])"),
        cwd=venv_python.parent,
    )
    site_packages = Path(completed.stdout.strip())
    if not site_packages.is_dir():
        raise PackageWitnessError("locked virtual environment site-packages is unavailable")
    return site_packages


def main() -> int:
    if len(sys.argv) != 4:
        print(
            "usage: python -m scripts.python_package_witness REPO_ROOT BACKEND_ROOT VENV_PYTHON",
            file=sys.stderr,
        )
        return 2
    try:
        result = inspect_package(
            Path(sys.argv[1]).resolve(),
            Path(sys.argv[2]).resolve(),
            Path(sys.argv[3]).absolute(),
        )
    except (OSError, PackageWitnessError, ValueError, json.JSONDecodeError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "artifact": result.artifact,
                "capacityQualificationResourceCount": (
                    result.capacity_qualification_resource_count
                ),
                "ciEconomicsResourceCount": result.ci_economics_resource_count,
                "consumerContractLabResourceCount": (result.consumer_contract_lab_resource_count),
                "publicSymbolCount": result.public_symbol_count,
                "resourceCount": result.resource_count,
                "persistenceResourceCount": result.persistence_resource_count,
                "productionAdmissionResourceCount": result.production_admission_resource_count,
                "githubIngestionResourceCount": result.github_ingestion_resource_count,
                "operatorUiNestedAssetCount": result.operator_ui_nested_asset_count,
                "runtimeSettingsResourceCount": result.runtime_settings_resource_count,
                "targetArtifactsResourceCount": result.target_artifacts_resource_count,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

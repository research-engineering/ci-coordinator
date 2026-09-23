from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from scripts.bounded_process import spawn
from scripts.dependency_hygiene import admit_backend_source_roots
from scripts.python_import_boundary_engine import ImportRule
from scripts.python_import_boundary_policy import imported_modules
from scripts.python_witness import PythonWitness

REPO_ROOT = Path(__file__).resolve().parents[2]


def _backend(root: Path) -> Path:
    backend = root / "backend"
    for relative in ("src/ci_coordinator/__init__.py", "alembic/env.py"):
        path = backend / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    (backend / "pyproject.toml").write_text(
        '[project]\nname = "ci-coordinator-backend"\n', encoding="utf-8"
    )
    return backend


@pytest.mark.parametrize(
    "mutation", ["wrong-root", "absent", "empty", "foreign-project", "symlink", "symlink-parent"]
)
def test_dependency_hygiene_rejects_unadmitted_roots(tmp_path: Path, mutation: str) -> None:
    backend = _backend(tmp_path)
    if mutation == "wrong-root":
        backend = tmp_path
    elif mutation == "absent":
        (backend / "pyproject.toml").unlink()
    elif mutation == "empty":
        (backend / "alembic/env.py").unlink()
    elif mutation == "foreign-project":
        (backend / "pyproject.toml").write_text('[project]\nname = "foreign"\n')
    elif mutation == "symlink-parent":
        (backend / "src").rename(backend / "actual-src")
        (backend / "src").symlink_to(backend / "actual-src", target_is_directory=True)
    else:
        path = backend / "alembic/env.py"
        path.unlink()
        path.symlink_to(backend / "src/ci_coordinator/__init__.py")
    with pytest.raises((ValueError, OSError)):
        admit_backend_source_roots(tmp_path, backend, ("src/ci_coordinator", "alembic"))


def test_dependency_usage_cannot_report_absent_backend_as_skipped(tmp_path: Path) -> None:
    witness = PythonWitness(
        backend_root=tmp_path / "backend",
        environment={},
        mode="dependency-usage",
        python_executable=sys.executable,
        repo_root=tmp_path,
    )
    with pytest.raises(SystemExit):
        witness.run()


@pytest.mark.parametrize(
    "source,dependencies,development,code",
    [
        ("import undeclared_dependency\n", [], [], "DEP001"),
        ("import click\n", [], [], "DEP003"),
        ("import pytest\n", [], ["pytest"], "DEP004"),
        ("value = 1\n", ["click"], [], "DEP002"),
    ],
)
def test_deptry_detects_dependency_contract_defects(
    tmp_path: Path, source: str, dependencies: list[str], development: list[str], code: str
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/module.py").write_text(source)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "dependency-probe"\nversion = "0.0.0"\n'
        f"dependencies = {dependencies!r}\n[dependency-groups]\ndev = {development!r}\n"
    )
    result = spawn(
        sys.executable,
        ("-m", "deptry", "src", "--no-ansi"),
        cwd=tmp_path,
        env={"PATH": os.defpath},
        max_buffer=1_048_576,
        timeout_seconds=30.0,
    )
    assert result.error is None
    assert result.status == 1
    assert code in result.stdout + result.stderr


@pytest.mark.parametrize(
    "relative",
    [
        "integrations/__init__.py",
        "integrations/github/transport.py",
        "app/service.py",
        "runtime/composition.py",
        "api/http/router.py",
        "kernel/value.py",
    ],
)
@pytest.mark.parametrize(
    "source",
    [
        "import httpx2\n",
        "import httpx2 as transport\n",
        "from httpx2 import Client\n",
        "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import httpx2\n",
    ],
)
def test_standard_http_contract_matches_incumbent_direct_import_policy(
    tmp_path: Path, relative: str, source: str
) -> None:
    backend = _backend(tmp_path)
    source_root = backend / "src/ci_coordinator"
    target = source_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    parent = target.parent
    while parent != source_root:
        (parent / "__init__.py").touch()
        parent = parent.parent
    (source_root / "integrations").mkdir(exist_ok=True)
    (source_root / "integrations/__init__.py").touch()
    target.write_text(source)
    (backend / "pyproject.toml").write_bytes((REPO_ROOT / "backend/pyproject.toml").read_bytes())
    incumbent = ImportRule(
        applies=lambda path: (
            "/ci_coordinator/" in path and "/ci_coordinator/integrations/" not in path
        ),
        forbidden=("httpx2",),
        rule_id="python.import-boundary.http-client",
    )
    normalized = f"/repo/backend/src/ci_coordinator/{relative}"
    rejected = incumbent.applies(normalized) and any(
        incumbent.violates(imported) for imported in imported_modules(target, source_root)
    )
    result = spawn(
        str(Path(sys.executable).with_name("lint-imports")),
        ("--config", "pyproject.toml", "--no-cache", "--no-logo"),
        cwd=backend,
        env={"PATH": os.defpath, "PYTHONPATH": str(backend / "src")},
        max_buffer=1_048_576,
        timeout_seconds=30.0,
    )
    assert result.error is None
    assert result.status == (1 if rejected else 0), result.stdout + result.stderr


def test_dependency_hygiene_uses_only_production_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = _backend(tmp_path)
    observed: list[tuple[str, tuple[str, ...]]] = []
    witness = PythonWitness(
        backend_root=backend,
        environment={},
        mode="dependency-usage",
        python_executable=sys.executable,
        repo_root=tmp_path,
    )
    monkeypatch.setattr(
        witness, "run_python_module", lambda module, argv: observed.append((module, argv))
    )
    witness.run()
    assert observed == [("deptry", ("src", "alembic", "--config", "pyproject.toml", "--no-ansi"))]

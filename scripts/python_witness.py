from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, Never

from scripts.bounded_process import spawn
from scripts.dependency_hygiene import admit_backend_source_roots, run_import_linter
from scripts.python_coverage_policy import (
    changed_paths_for_coverage,
    evaluate_coverage_report,
)
from scripts.python_environment_witness import (
    PythonEnvironmentWitnessContext,
    create_python_environment_witnesses,
)
from scripts.python_import_boundary_engine import inventory_python_sources
from scripts.python_import_boundary_policy import (
    IMPORT_RULES,
    governance_rule_coverage_violations,
    import_rule_coverage_violations,
    imported_modules,
)

BackendState = Literal["absent", "invalid", "present"]
ModeRunner = Callable[[], None]
_COVERAGE_TIMEOUT_SECONDS: Final = 2_400.0
_PYTHON_MODULE_TIMEOUT_SECONDS: Final = 600.0
PYTHON_TEST_PROCESS_TIMEOUT_SECONDS: Final = 1_080.0


@dataclass(frozen=True, slots=True)
class BackendDiscovery:
    files: tuple[Path, ...]
    state: BackendState


class PythonWitness:
    def __init__(
        self,
        *,
        backend_root: Path,
        environment: Mapping[str, str],
        mode: str | None,
        python_executable: str,
        repo_root: Path,
    ) -> None:
        self._backend_root = backend_root
        self._environment = dict(environment)
        self._mode = mode
        self._python_executable = python_executable
        self._repo_root = repo_root
        self._requirements_path = backend_root / "requirements-dev.lock"
        self._venv_root = backend_root / ".venv"
        self._venv_python = _venv_python(self._venv_root)

    def run(self) -> None:
        if self._mode is None or self._mode not in MODE_NAMES:
            displayed_mode = "<missing>" if self._mode is None else self._mode
            self.fail(f"unknown python witness mode: {displayed_mode}", {})
        mode = self._mode
        environment_witnesses = create_python_environment_witnesses(
            PythonEnvironmentWitnessContext(
                backend_root=self._backend_root,
                environment=self._environment,
                fail=self.fail,
                mode=mode,
                python_executable=self._python_executable,
                repo_path=self.repo_path,
                repo_root=self._repo_root,
                report=self.report,
                requirements_path=self._requirements_path,
                venv_python=self._venv_python,
                venv_root=self._venv_root,
            )
        )
        modes: dict[str, ModeRunner] = {
            "install-check": environment_witnesses.run_install_check,
            "import-boundary": self.run_import_boundary,
            "lock-check": environment_witnesses.run_lock_check,
            "package-check": environment_witnesses.run_package_check,
            "coverage": self.run_coverage,
            "dependency-usage": self.run_dependency_usage,
            "format": self.run_format,
            "lint": self.run_lint,
            "persistence-test": self.run_persistence_test,
            "test": self.run_test,
            "typecheck": self.run_typecheck,
        }

        if mode in {"dependency-usage", "import-boundary"}:
            roots = (
                ("src/ci_coordinator", "alembic")
                if mode == "dependency-usage"
                else ("src/ci_coordinator",)
            )
            try:
                admit_backend_source_roots(self._repo_root, self._backend_root, roots)
            except (OSError, ValueError) as error:
                self.fail(str(error), {"mode": mode})
            modes[mode]()
            return
        backend = self.discover_backend()
        if backend.state == "absent":
            self.report(
                {
                    "mode": mode,
                    "reason": "no_backend_surface",
                    "reportKind": "ci-coordinator.python-witness",
                    "state": "skipped",
                }
            )
            return
        if backend.state == "invalid":
            self.fail(
                "backend files exist but backend/pyproject.toml is missing",
                {
                    "backendFileCount": len(backend.files),
                    "mode": mode,
                },
            )
        modes[mode]()

    def discover_backend(self) -> BackendDiscovery:
        if not self._backend_root.exists():
            return BackendDiscovery(files=(), state="absent")
        files = _list_files(self._backend_root)
        if not files:
            return BackendDiscovery(files=files, state="absent")
        if not (self._backend_root / "pyproject.toml").exists():
            return BackendDiscovery(files=files, state="invalid")
        return BackendDiscovery(files=files, state="present")

    def run_format(self) -> None:
        self.run_python_module("ruff", ("check", "--fix", *PYTHON_QUALITY_TARGETS))
        self.run_python_module(
            "ruff", ("format", "--target-version", "py313", *PYTHON_QUALITY_TARGETS)
        )

    def run_lint(self) -> None:
        self.run_python_module("ruff", ("check", *PYTHON_QUALITY_TARGETS))
        self.run_python_module(
            "ruff", ("format", "--target-version", "py313", "--check", *PYTHON_QUALITY_TARGETS)
        )

    def run_dependency_usage(self) -> None:
        self.run_python_module(
            "deptry", ("src", "alembic", "--config", "pyproject.toml", "--no-ansi")
        )

    def run_persistence_test(self) -> None:
        self.run_python_module(
            "pytest",
            ("-m", "persistence", "tests/integration/persistence"),
        )

    def run_test(self) -> None:
        self.run_python_module(
            "pytest",
            (
                "--durations=25",
                "-m",
                "not persistence",
                "tests",
                "../scripts/tests",
                "../scripts/conformance/audit_persistence_byte_contract_test.py",
            ),
            environment={
                "PYTHONPATH": _joined_path(
                    str(self._repo_root),
                    self._environment.get("PYTHONPATH"),
                )
            },
            timeout_seconds=PYTHON_TEST_PROCESS_TIMEOUT_SECONDS,
        )

    def run_coverage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="ci-python-coverage-") as directory:
            coverage_path = Path(directory) / "coverage.json"
            self.run_python_module(
                "pytest",
                (
                    "-p",
                    "scripts.python_coverage_diagnostics",
                    "--cov=ci_coordinator",
                    "--cov-branch",
                    f"--cov-report=json:{coverage_path}",
                    "--cov-report=term-missing",
                    "--tb=short",
                    "--maxfail=1",
                    "--durations=0",
                    "--durations-min=0",
                    "tests",
                    "../scripts/tests",
                    "../scripts/conformance/audit_persistence_byte_contract_test.py",
                ),
                environment={
                    "PYTHONPATH": _joined_path(
                        str(self._repo_root),
                        self._environment.get("PYTHONPATH"),
                    )
                },
                timeout_seconds=_COVERAGE_TIMEOUT_SECONDS,
            )
            evaluation = evaluate_coverage_report(
                coverage_path,
                repo_root=self._repo_root,
                changed_paths=changed_paths_for_coverage(self._repo_root, self._environment),
            )
        if evaluation["state"] != "passed":
            self.fail("risk-owned Python coverage policy failed", evaluation)
        self.report(evaluation)

    def run_typecheck(self) -> None:
        self.run_python_module(
            "mypy",
            (
                "--explicit-package-bases",
                "src",
                "tests",
                "alembic",
                "../scripts",
                "../docker/runtime",
            ),
            environment={
                "MYPYPATH": _joined_path(
                    str(self._repo_root),
                    str(self._backend_root / "tests" / "unit"),
                    self._environment.get("MYPYPATH"),
                )
            },
        )
        self.run_python_module(
            "mypy",
            (
                "../.github/actions/secret-scan/entrypoint.py",
                "../.github/actions/secret-scan/runtime.py",
                "../.github/actions/secret-scan/scanner.py",
            ),
        )

    def run_python_module(
        self,
        module_name: str,
        arguments: tuple[str, ...],
        *,
        environment: Mapping[str, str] | None = None,
        timeout_seconds: float = _PYTHON_MODULE_TIMEOUT_SECONDS,
    ) -> None:
        mode = self._required_mode()
        if not self._venv_python.exists():
            self.fail(
                "python virtual environment is missing; run "
                "python -m scripts.python_witness install-check",
                {
                    "mode": mode,
                    "path": self.repo_path(self._venv_python),
                },
            )
        process_environment = {**self._environment, **(environment or {})}
        result = spawn(
            str(self._venv_python),
            ("-m", module_name, *arguments),
            cwd=self._backend_root,
            env=process_environment,
            max_buffer=64 * 1024 * 1024,
            timeout_seconds=timeout_seconds,
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.error is not None:
            self.fail(
                f"failed to run python module {module_name}: {result.error}",
                {"mode": mode},
            )
        if result.status is None:
            self.fail(
                f"python module {module_name} returned no status",
                {"mode": mode},
            )
        if result.status != 0:
            _exit_with_returncode(result.status)

    def run_import_boundary(self) -> None:
        mode = self._required_mode()
        source_root = self._backend_root / "src" / "ci_coordinator"
        source_inventory = inventory_python_sources(source_root)
        if source_inventory.symlinks:
            self.fail(
                "python import-boundary source inventory contains symlinks",
                {
                    "mode": mode,
                    "paths": [self.repo_path(path) for path in source_inventory.symlinks],
                },
            )
        coverage_violations = governance_rule_coverage_violations(
            source_root,
            inventory=source_inventory,
        )
        if coverage_violations:
            self.fail(
                "python import-boundary rule coverage is incomplete",
                {
                    "mode": mode,
                    "violations": [
                        {
                            "contextName": violation.context_name,
                            "kind": violation.kind,
                            "path": self.repo_path(source_root / violation.context_name),
                            "ruleId": violation.rule_id,
                        }
                        for violation in coverage_violations
                    ],
                },
            )
        general_coverage_violations = import_rule_coverage_violations(
            source_root,
            inventory=source_inventory,
        )
        if general_coverage_violations:
            self.fail(
                "python import-boundary closed-world coverage is incomplete",
                {
                    "mode": mode,
                    "violations": [
                        {
                            "contextName": violation.context_name,
                            "kind": violation.kind,
                            "path": self.repo_path(violation.source_path),
                            "ruleId": violation.rule_id,
                        }
                        for violation in general_coverage_violations
                    ],
                },
            )
        files = source_inventory.files
        violations: list[dict[str, str]] = []
        for path in files:
            normalized_path = self.repo_path(path)
            try:
                imports = imported_modules(path, source_root)
            except Exception:
                self.fail(
                    "failed to parse python imports",
                    {
                        "message": traceback.format_exc().strip(),
                        "mode": mode,
                        "path": self.repo_path(path),
                    },
                )
            for rule in IMPORT_RULES:
                if not rule.applies(normalized_path):
                    continue
                violations.extend(
                    {
                        "forbidden": imported,
                        "path": self.repo_path(path),
                        "ruleId": rule.rule_id,
                    }
                    for imported in imports
                    if rule.violates(imported)
                )
        if violations:
            self.fail(
                "python import-boundary violations detected",
                {"mode": mode, "violations": violations},
            )
        try:
            run_import_linter(self._backend_root, self._environment)
        except (OSError, ValueError) as error:
            self.fail(str(error), {"mode": mode})
        self.report(
            {
                "checkedFileCount": len(files),
                "mode": mode,
                "reportKind": "ci-coordinator.python-import-boundary",
                "state": "passed",
            }
        )

    def repo_path(self, path: Path) -> str:
        relative = os.path.relpath(path, self._repo_root)
        return "" if relative == "." else relative.replace("\\", "/")

    def report(
        self,
        payload: dict[str, object],
        additional_non_claims: Sequence[str] = (),
    ) -> None:
        report_payload: dict[str, object] = {
            "nonClaims": [*BASE_NON_CLAIMS, *additional_non_claims],
            "schemaVersion": 1,
            **payload,
        }
        sys.stdout.write(f"{json.dumps(report_payload, ensure_ascii=False, indent=2)}\n")

    def fail(self, message: str, diagnostics: dict[str, object]) -> Never:
        self.report(
            {
                "diagnostics": diagnostics,
                "message": message,
                "reportKind": "ci-coordinator.python-witness",
                "state": "failed",
            }
        )
        raise SystemExit(1)

    def _required_mode(self) -> str:
        if self._mode is None:
            self.fail("unknown python witness mode: <missing>", {})
        return self._mode


def _list_files(root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            files.extend(_list_files(entry))
        elif entry.is_file():
            files.append(entry)
    return tuple(sorted(files))


def _venv_python(venv_root: Path) -> Path:
    if sys.platform == "win32":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def _joined_path(*parts: str | None) -> str:
    return os.pathsep.join(part for part in parts if part)


def _exit_with_returncode(returncode: int) -> Never:
    raise SystemExit(returncode if returncode > 0 else 1)


def main(
    arguments: Sequence[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    resolved_arguments = tuple(sys.argv[1:] if arguments is None else arguments)
    resolved_environment = dict(os.environ if environment is None else environment)
    repo_root_value = resolved_environment.get("CI_COORDINATOR_REPO_ROOT")
    backend_root_value = resolved_environment.get("CI_COORDINATOR_BACKEND_ROOT")
    python_executable = resolved_environment.get("CI_COORDINATOR_PYTHON", "python3")
    repo_root = Path(
        Path(__file__).resolve().parent.parent if repo_root_value is None else repo_root_value
    ).resolve()
    backend_root = Path(
        repo_root / "backend" if backend_root_value is None else backend_root_value
    ).resolve()
    witness = PythonWitness(
        backend_root=backend_root,
        environment=resolved_environment,
        mode=resolved_arguments[0] if resolved_arguments else None,
        python_executable=python_executable,
        repo_root=repo_root,
    )
    witness.run()
    return 0


MODE_NAMES = frozenset(
    {
        "install-check",
        "import-boundary",
        "lock-check",
        "package-check",
        "coverage",
        "dependency-usage",
        "format",
        "lint",
        "persistence-test",
        "test",
        "typecheck",
    }
)

PYTHON_QUALITY_TARGETS = (
    ".",
    "../scripts",
    "../docker/runtime",
    "../.github/actions/secret-scan",
)

BASE_NON_CLAIMS = (
    "Python witness routing does not prove provider or deployment readiness.",
    "Python import-boundary analysis is a static source-policy witness, not a runtime sandbox.",
    "Skipped Python witnesses are valid only when no backend surface exists.",
)


if __name__ == "__main__":
    raise SystemExit(main())

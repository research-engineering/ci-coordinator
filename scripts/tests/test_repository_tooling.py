from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from scripts.bounded_git import capture_git_text
from scripts.bounded_process import CommandResult
from scripts.command_sequence import Command, run_commands
from scripts.dependency_audit import commands as dependency_audit_commands
from scripts.python_witness import PythonWitness
from scripts.quality_plan import load_quality_plan, project_command_environment
from scripts.repository_json import JsonAdmissionError, admit_json, tracked_json_paths
from scripts.workflow_lint import commands as workflow_lint_commands
from scripts.workflow_lint import shell_source_paths


def test_command_sequence_stops_at_first_failure(tmp_path: Path) -> None:
    observed: list[tuple[str, ...]] = []

    def run(argv: Sequence[str], *, cwd: Path, env: Mapping[str, str] | None = None) -> int:
        del cwd, env
        observed.append(tuple(argv))
        return 7 if len(observed) == 2 else 0

    status = run_commands(
        [
            Command(("first",), tmp_path),
            Command(("second",), tmp_path),
            Command(("third",), tmp_path),
        ],
        runner=run,
    )

    assert status == 7
    assert observed == [("first",), ("second",)]


def test_default_command_sequence_enforces_process_bounds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def bounded_spawn(
        command: str,
        args: Sequence[str],
        **kwargs: object,
    ) -> CommandResult:
        observed.update(command=command, args=tuple(args), **kwargs)
        return CommandResult(status=0, stdout="", stderr="")

    monkeypatch.setattr("scripts.command_sequence.spawn", bounded_spawn)

    assert run_commands((Command(("probe", "argument"), tmp_path),)) == 0
    assert observed["command"] == "probe"
    assert observed["args"] == ("argument",)
    assert observed["max_buffer"] == 16 * 1024 * 1024
    assert observed["timeout_seconds"] == 600.0


def test_install_check_compiles_only_locked_dependency_bytecode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend_root = tmp_path / "backend"
    venv_python = backend_root / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    (backend_root / "pyproject.toml").touch()
    requirements = backend_root / "requirements-dev.lock"
    requirements.touch()
    observed: list[tuple[str, ...]] = []
    environment = {"PATH": "/bin", "PYTHONDONTWRITEBYTECODE": "1"}

    def bounded_spawn(command: str, args: Sequence[str], **kwargs: object) -> CommandResult:
        assert kwargs["cwd"] == backend_root
        assert kwargs["env"] == environment
        observed.append((command, *args))
        return CommandResult(status=0, stdout="", stderr="")

    monkeypatch.setattr("scripts.python_environment_witness.spawn", bounded_spawn)

    PythonWitness(
        backend_root=backend_root,
        environment=environment,
        mode="install-check",
        python_executable="python3",
        repo_root=tmp_path,
    ).run()

    assert observed == [
        (
            "uv",
            "pip",
            "sync",
            "--python",
            str(venv_python),
            "--require-hashes",
            "--strict",
            "--compile-bytecode",
            str(requirements),
        ),
        (
            "uv",
            "pip",
            "install",
            "--python",
            str(venv_python),
            "--no-deps",
            "--no-build-isolation",
            "--offline",
            "--no-cache",
            "--editable",
            str(backend_root),
        ),
    ]


def test_typecheck_witness_includes_runtime_scripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend_root = tmp_path / "backend"
    venv_python = backend_root / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    (backend_root / "pyproject.toml").touch()
    observed: list[tuple[tuple[str, ...], object]] = []
    environment = {"PATH": "/bin", "MYPYPATH": "existing-mypy-path"}

    def bounded_spawn(command: str, args: Sequence[str], **kwargs: object) -> CommandResult:
        assert command == str(venv_python)
        assert kwargs["cwd"] == backend_root
        assert kwargs["timeout_seconds"] == 600.0
        observed.append((tuple(args), kwargs["env"]))
        return CommandResult(status=0, stdout="", stderr="")

    monkeypatch.setattr("scripts.python_witness.spawn", bounded_spawn)

    PythonWitness(
        backend_root=backend_root,
        environment=environment,
        mode="typecheck",
        python_executable="python3",
        repo_root=tmp_path,
    ).run()

    assert observed == [
        (
            (
                "-m",
                "mypy",
                "--explicit-package-bases",
                "src",
                "tests",
                "alembic",
                "../scripts",
                "../docker/runtime",
            ),
            {
                **environment,
                "MYPYPATH": os.pathsep.join(
                    (str(tmp_path), str(backend_root / "tests" / "unit"), "existing-mypy-path")
                ),
            },
        ),
        (
            (
                "-m",
                "mypy",
                "../.github/actions/secret-scan/entrypoint.py",
                "../.github/actions/secret-scan/runtime.py",
                "../.github/actions/secret-scan/scanner.py",
            ),
            environment,
        ),
    ]


def test_coverage_witness_preserves_complete_selection_and_process_bound(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = tmp_path / "backend"
    venv_python = backend_root / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    (backend_root / "pyproject.toml").touch()
    observed: dict[str, object] = {}
    coverage = load_quality_plan().commands["python.coverage"]
    assert coverage.argv[:3] == (
        "backend/.venv/bin/python",
        "-m",
        "scripts.python_witness",
    )
    assert len(coverage.argv) == 4

    def bounded_spawn(
        command: str,
        args: Sequence[str],
        **kwargs: object,
    ) -> CommandResult:
        observed.update(command=command, args=tuple(args), **kwargs)
        return CommandResult(status=0, stdout="", stderr="")

    monkeypatch.setattr("scripts.python_witness.spawn", bounded_spawn)
    monkeypatch.setattr(
        "scripts.python_witness.changed_paths_for_coverage",
        lambda *_args: (),
    )
    monkeypatch.setattr(
        "scripts.python_witness.evaluate_coverage_report",
        lambda *_args, **_kwargs: {
            "reportKind": "ci-coordinator.python-risk-coverage",
            "state": "passed",
        },
    )
    witness = PythonWitness(
        backend_root=backend_root,
        environment=project_command_environment(coverage, {"PATH": "/bin", "CI": "true"}),
        mode=coverage.argv[3],
        python_executable="python3",
        repo_root=tmp_path,
    )

    witness.run()

    assert observed["command"] == str(venv_python)
    args = observed["args"]
    assert isinstance(args, tuple)
    assert args[:4] == ("-m", "pytest", "-p", "scripts.python_coverage_diagnostics")
    assert args[4:6] == ("--cov=ci_coordinator", "--cov-branch")
    assert args[6].startswith("--cov-report=json:")
    assert args[7:] == (
        "--cov-report=term-missing",
        "--tb=short",
        "--maxfail=1",
        "--durations=0",
        "--durations-min=0",
        "tests",
        "../scripts/tests",
        "../scripts/conformance/audit_persistence_byte_contract_test.py",
    )
    assert observed["timeout_seconds"] == 2_400.0
    assert coverage.timeout_ms == 2_460_000

    witness.run_persistence_test()

    assert observed["args"] == (
        "-m",
        "pytest",
        "-m",
        "persistence",
        "tests/integration/persistence",
    )
    assert observed["timeout_seconds"] == 600.0

    observed = {}
    witness.run_test()

    assert observed["args"] == (
        "-m",
        "pytest",
        "--durations=25",
        "-m",
        "not persistence",
        "tests",
        "../scripts/tests",
        "../scripts/conformance/audit_persistence_byte_contract_test.py",
    )
    assert observed["timeout_seconds"] == 900.0
    assert load_quality_plan().commands["python.test"].timeout_ms == 960_000


def test_dependency_audit_covers_supported_python_and_frontend_locks(
    tmp_path: Path,
) -> None:
    commands = dependency_audit_commands(tmp_path)

    assert [command.argv for command in commands[:-1]] == [
        (
            "uv",
            "audit",
            "--project",
            project,
            "--frozen",
            "--no-config",
            "--preview-features",
            "audit-command,json-output",
            "--output-format",
            "json",
            "--python-platform",
            "linux",
            "--service-format",
            "osv",
            "--service-url",
            "https://api.osv.dev",
            "--python-version",
            "3.13.15",
        )
        for project in ("backend", "tooling/quality")
    ]
    root = Path(__file__).resolve().parents[2]
    tracked_locks = capture_git_text(root, ("ls-files", "-z", "--", "uv.lock", "**/uv.lock"))
    assert {command.argv[3] for command in commands[:-1]} == {
        Path(path).parent.as_posix() for path in tracked_locks.split("\0") if path
    }
    assert all(command.cwd == tmp_path for command in commands)
    assert commands[-1].argv == ("pnpm", "audit", "--audit-level", "high", "--json")


_LINT_WORKFLOWS = (
    ".github/workflows/a.yaml",
    ".github/workflows/z.yml",
    "fixtures/native-target-repository/.github/workflows/b.yaml",
    "fixtures/native-target-repository/.github/workflows/y.yml",
    "fixtures/target-repository/.github/workflows/c.yml",
    "fixtures/target-repository/.github/workflows/x.yaml",
)
_LINT_SHELLS = (
    ".github/actions/secret-scan/install.sh",
    ".github/actions/secret-scan/run.sh",
    ".devcontainer/post-create.sh",
    ".githooks/pre-push",
    "docker/development/secret-entrypoint.sh",
    "docker/runtime/assemble.sh",
    "docker/runtime/install.sh",
    "docker/runtime/security/build.sh",
)


@pytest.fixture
def lint_sources(tmp_path: Path) -> Path:
    for source in reversed((*_LINT_WORKFLOWS, *_LINT_SHELLS)):
        path = tmp_path / source
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture\n", encoding="utf-8")
    return tmp_path


def test_workflow_lint_uses_digest_built_actionlint_and_locked_zizmor(lint_sources: Path) -> None:
    commands = workflow_lint_commands(lint_sources)
    assert commands[0].argv[:3] == ("docker", "build", "--pull")
    assert commands[1].argv[:3] == ("docker", "run", "--rm")
    ignore_index = commands[1].argv.index("-ignore")
    assert "trusted-plan-request" in commands[1].argv[ignore_index + 1]
    assert commands[1].argv[ignore_index + 2 :] == ("--", *_LINT_WORKFLOWS)
    assert [command.argv[-3:] for command in commands[2:-1]] == [
        ("--shell=bash", "--", _LINT_SHELLS[0]),
        ("--shell=bash", "--", _LINT_SHELLS[1]),
        ("--shell=bash", "--", _LINT_SHELLS[2]),
        ("--shell=sh", "--", _LINT_SHELLS[3]),
        ("--shell=sh", "--", _LINT_SHELLS[4]),
        ("--shell=bash", "--", _LINT_SHELLS[5]),
        ("--shell=sh", "--", _LINT_SHELLS[6]),
        ("--shell=bash", "--", _LINT_SHELLS[7]),
    ]
    assert all("--norc" in command.argv for command in commands[2:-1])
    for command in commands[1:-1]:
        assert command.argv[command.argv.index("--network") + 1] == "none"
        assert "--read-only" in command.argv
        assert command.argv[command.argv.index("--cap-drop") + 1] == "ALL"
        assert command.argv[command.argv.index("--security-opt") + 1] == "no-new-privileges"
    assert commands[-1].argv[-6:] == (
        "--offline",
        "--persona",
        "pedantic",
        ".github",
        "fixtures/native-target-repository/.github",
        "fixtures/target-repository/.github",
    )


@pytest.mark.parametrize("invalid_kind", ["missing", "empty", "symlink", "directory", "ancestor"])
@pytest.mark.parametrize("source", _LINT_WORKFLOWS[::2])
def test_workflow_lint_rejects_incomplete_or_nonregular_inputs(
    lint_sources: Path, invalid_kind: str, source: str
) -> None:
    path = lint_sources / source
    directory = path.parent
    if invalid_kind in {"missing", "empty"}:
        for child in directory.iterdir():
            child.unlink()
        if invalid_kind == "missing":
            directory.rmdir()
        reason = "unavailable" if invalid_kind == "missing" else "empty"
        expected = f"workflow lint directory is {reason}: {directory.relative_to(lint_sources)}"
    elif invalid_kind == "ancestor":
        ancestor = directory.parent
        destination = ancestor.with_name("github-original")
        ancestor.rename(destination)
        ancestor.symlink_to(destination, target_is_directory=True)
        expected = f"lint input contains a symlink: {ancestor.relative_to(lint_sources)}"
    else:
        destination = path.with_suffix(".source")
        path.rename(destination)
        if invalid_kind == "symlink":
            path.symlink_to(destination)
            expected = f"lint input contains a symlink: {source}"
        else:
            path.mkdir()
            expected = f"workflow lint source is not a regular file: {path.name}"
    with pytest.raises(ValueError, match=f"^{re.escape(expected)}$"):
        workflow_lint_commands(lint_sources)


@pytest.mark.parametrize("source", _LINT_SHELLS)
@pytest.mark.parametrize("invalid_kind", ["missing", "directory", "symlink", "ancestor"])
def test_shell_admission_mutates_only_one_otherwise_valid_input(
    lint_sources: Path, source: str, invalid_kind: str
) -> None:
    path = lint_sources / source
    if invalid_kind == "ancestor":
        ancestor = path.parent
        destination = ancestor.with_name(ancestor.name + "-original")
        ancestor.rename(destination)
        ancestor.symlink_to(destination, target_is_directory=True)
        expected = f"lint input contains a symlink: {ancestor.relative_to(lint_sources)}"
    else:
        destination = path.with_suffix(".source")
        path.rename(destination)
        if invalid_kind == "symlink":
            path.symlink_to(destination)
            expected = f"lint input contains a symlink: {source}"
        else:
            if invalid_kind == "directory":
                path.mkdir()
            expected = f"shell lint source is not a regular file: {source}"
    with pytest.raises(ValueError, match=f"^{re.escape(expected)}$"):
        workflow_lint_commands(lint_sources)


@pytest.mark.parametrize(
    "name,content",
    [
        ("scripts/entrypoint", "#!/bin/sh\necho ok\n"),
        ("scripts/entrypoint.bash", "echo ok\n"),
        ("scripts/entrypoint", "#!/usr/bin/env -S bash -eu\n"),
        ("scripts/entrypoint", "#! /opt/bin/zsh\n"),
    ],
)
def test_shell_inventory_recognizes_new_sources_without_a_sh_suffix(
    tmp_path: Path, name: str, content: str
) -> None:
    source = tmp_path / name
    source.parent.mkdir()
    source.write_text(content, encoding="utf-8")
    assert shell_source_paths(tmp_path, (name,)) == (name,)


def test_shell_inventory_does_not_misclassify_python_or_binary_files(tmp_path: Path) -> None:
    (tmp_path / "python").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
    (tmp_path / "binary").write_bytes(b"\xff\x00\n")
    assert shell_source_paths(tmp_path, ("python", "binary")) == ()


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ('{"a": 1, "a": 2}\n', "duplicate object key"),
        ('{"value": NaN}\n', "non-finite numeric constant"),
    ],
)
def test_json_admission_rejects_ambiguous_values(tmp_path: Path, source: str, message: str) -> None:
    path = tmp_path / "input.json"
    path.write_text(source, encoding="utf-8")

    with pytest.raises(JsonAdmissionError, match=message):
        admit_json(path)


def test_json_inventory_excludes_deleted_cached_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    present = tmp_path / "present.json"
    present.write_text("{}\n", encoding="utf-8")

    class _Result:
        stdout = "present.json\0deleted.json\0"

    monkeypatch.setattr("scripts.repository_json.run_git", lambda *args, **kwargs: _Result())

    assert tracked_json_paths(tmp_path) == (present,)

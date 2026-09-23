"""GitHub-only native Go source-selection counterexamples; no dependency install."""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.bounded_git import run_git
from scripts.bounded_process import spawn
from scripts.ci_utility_checks import UtilityCommand, admit_output, commands, project_environment

_CASES = (
    ("plain", 'package witness\n\n// import "C" is text, not a dependency.\nvar Label = "C"\n'),
    ("cgo-direct", 'package witness\nimport "C"\n'),
    ("cgo-group", 'package witness\nimport (\n "C"\n)\n'),
    ("cgo-raw", "package witness\nimport `C`\n"),
    ("cgo-escaped", 'package witness\nimport "\\x43"\n'),
)


def _output(command: UtilityCommand, environment: dict[str, str]) -> str:
    result = spawn(
        command.argv[0],
        command.argv[1:],
        cwd=command.cwd,
        env=environment,
        max_buffer=1024 * 1024,
        timeout_seconds=30,
    )
    if result.error is not None or result.status != 0:
        raise RuntimeError(
            f"native Go input witness process failed: {result.error or result.stderr}"
        )
    return result.stdout


def main() -> int:
    environment = project_environment(os.environ)
    environment.update(GOPROXY="off", GOSUMDB="off")
    with TemporaryDirectory(prefix="ci-go-input-witness-") as directory:
        root = Path(directory)
        version = UtilityCommand(
            ("go", "version"), root, "version", r"go version go1\.27\.1 linux/amd64"
        )
        admit_output(version, _output(version, environment))
        for name, source in _CASES:
            fixture = root / name
            fixture.mkdir()
            run_git(fixture, ("init", "--quiet"))
            (fixture / "go.mod").write_text("module example.invalid/input-witness\n\ngo 1.27.1\n")
            (fixture / "plain.go").write_text("package witness\n")
            (fixture / "subject.go").write_text(source)
            planned = commands(fixture, "gofmt")
            native = [command for command in planned if command.output_contract == "go-inputs"]
            if len(native) != 1:
                raise RuntimeError("native Go witness did not select exactly one package inventory")
            stdout = _output(native[0], environment)
            try:
                admit_output(native[0], stdout)
            except ValueError as error:
                if name == "plain" or "unsupported IgnoredGoFiles" not in str(error):
                    # Only this fixed, generated fixture is printed, never project input.
                    print(f"native Go input witness fixture metadata ({name}): {stdout}")
                    raise
            else:
                if name != "plain":
                    raise RuntimeError(f"native Go admission accepted unsupported source: {name}")
            print(f"native Go input witness: {name} passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

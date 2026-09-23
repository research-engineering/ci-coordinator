from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from scripts.bounded_git import BoundedGitError
from scripts.bounded_process import spawn
from scripts.ci_utility_inventory import (
    QUALITY_DIRECTORY,
    admit_devcontainer_schema,
    admit_go_effective_inputs,
    admit_spelling_vendor_exclusions,
    admitted_sources,
    go_module_sources,
    go_modules,
    is_dockerfile,
    repository_paths,
    schema_groups,
    source_text,
    spelling_exclusion,
    spelling_line_file,
)

CHECK_IDS = (
    "yaml",
    "schemas",
    "hadolint",
    "build-checks",
    "gofmt",
    "go-vet",
    "staticcheck",
    "govulncheck",
    "spelling",
)
BUILDER_NAME = "ci-utility-checks"
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 600.0
_PYTHON_VERSIONS = {"yamllint": "1.38.0", "check-jsonschema": "0.38.0", "codespell": "2.4.3"}


@dataclass(frozen=True, slots=True)
class UtilityCommand:
    argv: tuple[str, ...]
    cwd: Path
    output_contract: Literal[
        "status", "empty", "version", "hadolint", "buildkit", "go-inputs", "govulncheck"
    ] = "status"
    expected_version: str = ""
    expected_go_files: tuple[str, ...] = ()


def _python_tool(root: Path, name: str) -> str:
    return str(root / QUALITY_DIRECTORY / ".venv" / "bin" / name)


def _version(
    root: Path, executable: str, arguments: tuple[str, ...], pattern: str
) -> UtilityCommand:
    return UtilityCommand((executable, *arguments), root, "version", pattern)


def commands(root: Path, check_id: str) -> tuple[UtilityCommand, ...]:
    """Plan fixed argv without executing a utility or installing dependencies."""
    if check_id not in CHECK_IDS:
        raise ValueError(f"unsupported utility check: {check_id}")
    argv: tuple[str, ...]
    prefix: tuple[UtilityCommand, ...]
    selection: tuple[str, ...]
    names = repository_paths(root)
    if check_id in {"yaml", "schemas", "spelling"}:
        tool = {"yaml": "yamllint", "schemas": "check-jsonschema", "spelling": "codespell"}[
            check_id
        ]
        binary = _python_tool(root, tool)
        prefix = (
            _version(
                root,
                binary,
                ("--version",),
                rf"(?:{re.escape(tool)}(?:, version)?[ ,]+)?{re.escape(_PYTHON_VERSIONS[tool])}",
            ),
        )
        if check_id == "yaml":
            inputs = admitted_sources(
                root, tuple(name for name in names if Path(name).suffix in {".yaml", ".yml"})
            )
            config = f"{QUALITY_DIRECTORY}/yamllint.yaml"
            admitted_sources(root, (config,))
            return (
                *prefix,
                UtilityCommand(
                    (
                        binary,
                        "--strict",
                        "--format",
                        "parsable",
                        "--config-file",
                        config,
                        "--",
                        *inputs,
                    ),
                    root,
                ),
            )
        if check_id == "schemas":
            checks = []
            for schema, inputs in schema_groups(root, names):
                if schema == "metaschema":
                    selection = ("--check-metaschema",)
                elif schema.startswith("vendor."):
                    selection = ("--builtin-schema", schema)
                else:
                    admit_devcontainer_schema(root)
                    selection = ("--schemafile", schema)
                checks.append(
                    UtilityCommand((binary, *selection, "--color", "never", "--", *inputs), root)
                )
            return (*prefix, *checks)
        config = f"{QUALITY_DIRECTORY}/codespell.ini"
        words = f"{QUALITY_DIRECTORY}/spelling-words.dic"
        admitted_sources(root, (config, words))
        admit_spelling_vendor_exclusions(root, names)
        inputs = admitted_sources(
            root, tuple(name for name in names if spelling_exclusion(name) is None)
        )
        # Codespell also discovers configuration in its cwd, even with --config.
        for extra in ("setup.cfg", ".codespellrc"):
            if (root / QUALITY_DIRECTORY / extra).exists():
                raise ValueError(f"unadmitted spelling configuration: {extra}")
        project = tomllib.loads(source_text(root, f"{QUALITY_DIRECTORY}/pyproject.toml"))
        if "codespell" in project.get("tool", {}):
            raise ValueError("unadmitted spelling configuration in quality pyproject.toml")
        argv = (
            binary,
            "--config",
            str(root / config),
            "--builtin",
            "clear,rare",
            "--ignore-words",
            str(root / words),
            "--check-hidden",
            "--quiet-level",
            "0",
        )
        ordinary = []
        exceptional = []
        for name in inputs:
            excluded = spelling_line_file(root, name)
            if excluded is None:
                ordinary.append(name)
            else:
                exceptional.append(
                    UtilityCommand(
                        (*argv, "--exclude-file", str(root / excluded), "--", str(root / name)),
                        root / QUALITY_DIRECTORY,
                    )
                )
        return (
            *prefix,
            *(
                UtilityCommand(
                    (*argv, "--", *(str(root / name) for name in ordinary[index : index + 128])),
                    root / QUALITY_DIRECTORY,
                )
                for index in range(0, len(ordinary), 128)
            ),
            *exceptional,
        )
    if check_id in {"hadolint", "build-checks"}:
        inputs = admitted_sources(root, tuple(name for name in names if is_dockerfile(name)))
        if check_id == "hadolint":
            config = f"{QUALITY_DIRECTORY}/hadolint.yaml"
            admitted_sources(root, (config, f"{QUALITY_DIRECTORY}/hadolint-exceptions.json"))
            return (
                _version(root, "hadolint", ("--version",), r"Haskell Dockerfile Linter 2\.15\.1"),
                UtilityCommand(
                    (
                        "hadolint",
                        "--config",
                        config,
                        "--no-fail",
                        "--disable-ignore-pragma",
                        "--",
                        *inputs,
                    ),
                    root,
                    "hadolint",
                ),
            )
        return (
            _version(
                root,
                "docker",
                ("buildx", "version"),
                r"github\.com/docker/buildx v0\.37\.1(?: [^\n]+)?",
            ),
            UtilityCommand(
                ("docker", "buildx", "ls", "--format", "json"),
                root,
                "buildkit",
            ),
            *(
                UtilityCommand(
                    (
                        "docker",
                        "buildx",
                        "build",
                        "--builder",
                        BUILDER_NAME,
                        "--check",
                        "--progress=plain",
                        "--build-arg",
                        "BUILDKIT_DOCKERFILE_CHECK=error=true",
                        "--file",
                        name,
                        ".",
                    ),
                    root,
                )
                for name in inputs
            ),
        )
    modules = go_modules(root, names)
    prefix = (
        _version(root, "go", ("version",), r"go version go1\.27\.1 linux/amd64"),
        *(
            UtilityCommand(
                (
                    "go",
                    "list",
                    "-e",
                    "-find",
                    "-mod=readonly",
                    "-json=Dir,GoFiles,CgoFiles,IgnoredGoFiles,InvalidGoFiles,TestGoFiles,XTestGoFiles,Error,Incomplete",
                    "./...",
                ),
                root / module,
                "go-inputs",
                expected_go_files=go_module_sources(names, module),
            )
            for module in modules
        ),
    )
    if check_id == "gofmt":
        inputs = admitted_sources(root, tuple(name for name in names if name.endswith(".go")))
        return (*prefix, UtilityCommand(("gofmt", "-l", "--", *inputs), root, "empty"))
    if check_id == "go-vet":
        argv = ("go", "vet", "-mod=readonly", "./...")
    elif check_id == "staticcheck":
        prefix += (
            _version(root, "staticcheck", ("-version",), r"staticcheck 2026\.2\.1 \(0\.8\.1\)"),
        )
        argv = ("staticcheck", "-checks=all", "-tests=true", "-f=text", "./...")
    else:
        prefix += (
            _version(root, "govulncheck", ("-version",), r"(?s).*\bgovulncheck@v1\.8\.0\b.*"),
        )
        # JSON/SARIF deliberately return success even when vulnerabilities exist.
        argv = ("govulncheck", "-format=text", "-test", "-db=https://vuln.go.dev", "./...")
    return (
        *prefix,
        *(
            UtilityCommand(
                argv, root / module, "govulncheck" if check_id == "govulncheck" else "status"
            )
            for module in modules
        ),
    )


def project_environment(source: Mapping[str, str]) -> dict[str, str]:
    environment = {key: source[key] for key in ("PATH", "HOME", "TMPDIR") if key in source}
    environment.update(
        {
            "PATH": source.get("PATH", os.defpath),
            "LC_ALL": "C.UTF-8",
            "NO_COLOR": "1",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "GOENV": "off",
            "GOWORK": "off",
            "GOTOOLCHAIN": "local",
            "GOFLAGS": "-mod=readonly",
            "GOPROXY": "https://proxy.golang.org",
            "GOSUMDB": "sum.golang.org",
            "GOOS": "linux",
            "GOARCH": "amd64",
            "CGO_ENABLED": "0",
        }
    )
    if any(not Path(part).is_absolute() for part in environment["PATH"].split(os.pathsep)):
        raise ValueError("utility PATH must contain only absolute, nonempty directories")
    for key in ("HOME", "TMPDIR"):
        if key in environment and not Path(environment[key]).is_absolute():
            raise ValueError(f"utility {key} must be absolute")
    return environment


def _admit_hadolint(root: Path, stdout: str) -> None:
    allowances = json.loads(source_text(root, f"{QUALITY_DIRECTORY}/hadolint-exceptions.json"))
    diagnostics = json.loads(stdout)
    if not isinstance(allowances, list) or not isinstance(diagnostics, list):
        raise ValueError("Hadolint output and exceptions must be arrays")
    for allowance in allowances:
        if not isinstance(allowance, dict) or set(allowance) != {
            "file",
            "line",
            "level",
            "code",
            "source",
            "reason",
            "sha256",
        }:
            raise ValueError("unrecognized Hadolint exception")
        digest = hashlib.sha256(source_text(root, allowance["file"]).encode()).hexdigest()
        if digest != allowance["sha256"]:
            raise ValueError(f"stale Hadolint exception source: {allowance['file']}")
    remaining = list(allowances)
    for diagnostic in diagnostics:
        if not isinstance(diagnostic, dict) or set(diagnostic) != {
            "file",
            "line",
            "column",
            "level",
            "code",
            "message",
        }:
            raise ValueError("unrecognized Hadolint diagnostic")
        found = None
        for allowance in remaining:
            if all(
                diagnostic.get(key) == allowance.get(key)
                for key in ("file", "line", "level", "code")
            ):
                lines = source_text(root, diagnostic["file"]).splitlines()
                start = diagnostic["line"] - 1
                expected = allowance["source"].splitlines()
                if lines[start : start + len(expected)] == expected and allowance["reason"]:
                    found = allowance
                    break
        if found is None:
            raise ValueError(
                f"unadmitted Hadolint diagnostic: {json.dumps(diagnostic, sort_keys=True)}"
            )
        remaining.remove(found)


def _admit_govulncheck(stdout: str) -> None:
    text = " ".join(stdout.split())
    if text == "No vulnerabilities found.":
        return
    # v1.8.0 also succeeds for uncalled findings; retain its symbol-level threshold.
    informational = re.fullmatch(
        r"=== Symbol Results === No vulnerabilities found\. "
        r"Your code is affected by 0 vulnerabilities\. "
        r"This scan also found (0|[1-9][0-9]*) (vulnerability|vulnerabilities) "
        r"in packages you import and (0|[1-9][0-9]*) (vulnerability|vulnerabilities) "
        r"in modules you require, but your code doesn't appear to call these vulnerabilities\. "
        r"Use '-show verbose' for more details\.",
        text,
    )
    if informational is not None:
        imported, imported_word, required, required_word = informational.groups()
        counts = (int(imported), int(required))
        if any(counts) and all(
            word == ("vulnerability" if count == 1 else "vulnerabilities")
            for count, word in zip(counts, (imported_word, required_word), strict=True)
        ):
            return
    raise ValueError("govulncheck did not emit a complete clean symbol-scan result")


def admit_output(command: UtilityCommand, stdout: str) -> None:
    if command.output_contract == "govulncheck":
        _admit_govulncheck(stdout)
    if command.output_contract == "go-inputs":
        admit_go_effective_inputs(command.cwd, command.expected_go_files, stdout)
    if command.output_contract == "empty" and stdout.strip():
        raise ValueError("gofmt reported files requiring formatting")
    if (
        command.output_contract == "version"
        and re.fullmatch(command.expected_version, stdout.strip()) is None
    ):
        raise ValueError(f"utility version differs from the pinned contract: {stdout.strip()!r}")
    if command.output_contract == "hadolint":
        _admit_hadolint(command.cwd, stdout)
    if command.output_contract == "buildkit":
        builders = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        if not all(isinstance(builder, dict) for builder in builders):
            raise ValueError("Buildx builder inventory must contain objects")
        selected = [builder for builder in builders if builder.get("Name") == BUILDER_NAME]
        if (
            len(selected) != 1
            or selected[0].get("Driver") != "docker-container"
            or selected[0].get("Err")
        ):
            raise ValueError("the exact utility BuildKit builder is unavailable")
        nodes = selected[0].get("Nodes")
        if (
            not isinstance(nodes, list)
            or not nodes
            or any(
                not isinstance(node, dict)
                or node.get("Status") != "running"
                or node.get("Version") != "v0.33.0"
                or node.get("Err")
                for node in nodes
            )
        ):
            raise ValueError("BuildKit builder must have running, exactly pinned v0.33.0 nodes")


def run(root: Path, check_id: str, source_environment: Mapping[str, str]) -> int:
    environment = project_environment(source_environment)
    planned = commands(root, check_id)
    for command in planned:
        result = spawn(
            command.argv[0],
            command.argv[1:],
            cwd=command.cwd,
            env=environment,
            max_buffer=MAX_OUTPUT_BYTES,
            timeout_seconds=COMMAND_TIMEOUT_SECONDS,
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.error is not None or result.status != 0:
            if result.error is not None:
                print(result.error, file=sys.stderr)
            return result.status if result.status is not None and result.status > 0 else 1
        admit_output(command, result.stdout)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one bounded repository utility check.")
    parser.add_argument("check_id", choices=CHECK_IDS)
    options = parser.parse_args(argv)
    try:
        return run(Path(__file__).resolve().parents[1], options.check_id, os.environ)
    except (OSError, ValueError, BoundedGitError) as error:
        print(f"utility check rejected: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

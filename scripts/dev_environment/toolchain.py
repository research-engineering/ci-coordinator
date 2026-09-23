"""Read-only consistency admission for repository-owned toolchain declarations.

This checks declarations, not installed tools, lock regeneration or OCI content.
Keep the finite set of relationships here; versions remain in native owners.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shlex
import sys
import tomllib
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TextIO, cast

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

_TOOLS = ("python", "node", "uv", "pnpm", "gitleaks")
_GITLEAKS_PLATFORMS = {
    "linux-arm64": "linux_arm64",
    "linux-x64": "linux_x64",
    "macos-arm64": "darwin_arm64",
}
_SCANNER_SOURCE = ".github/actions/secret-scan/scanner.py"
_EXACT_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_PROFILES = (
    "docs/specs/ci-coordinator-runtime/python-runtime-profile.v1.json",
    "backend/src/ci_coordinator/runtime_settings/resources/python-runtime-profile.v1.json",
)
_DOCKER_TOOLS = {
    "Dockerfile": {"python", "node", "uv", "pnpm"},
    "docker/development/backend.Dockerfile": {"python", "uv"},
    "frontend/Dockerfile.dev": {"node", "pnpm"},
    ".devcontainer/Dockerfile": {"mise"},
}
_SETUP_ACTIONS = {
    "actions/setup-python": ("python", "python-version"),
    "actions/setup-node": ("node", "node-version"),
    "astral-sh/setup-uv": ("uv", "version"),
    "pnpm/action-setup": ("pnpm", "version"),
}


@dataclass(frozen=True)
class ToolchainIssue:
    path: str
    selector: str
    reason: str
    detail: str


@dataclass(frozen=True)
class ImageReference:
    path: str
    line: int
    reference: str
    tool: str | None
    declared_version: str | None
    digest: str
    digest_identity: str = "not_verified"


@dataclass
class ToolchainReport:
    tools: dict[str, str] = field(default_factory=dict)
    inputs: dict[str, str] = field(default_factory=dict)
    issues: list[ToolchainIssue] = field(default_factory=list)
    images: list[ImageReference] = field(default_factory=list)

    @property
    def state(self) -> str:
        return "failed" if self.issues else "passed"

    def projection(self) -> dict[str, object]:
        return {
            "schemaVersion": "ci-coordinator-toolchain-check/v1",
            "state": self.state,
            **asdict(self),
            "evidence": "static_declarations_only",
            "digestIdentity": "not_verified",
        }

    def reject(self, path: str, selector: str, reason: str, detail: str) -> None:
        self.issues.append(ToolchainIssue(path, selector, reason, detail))

    def expect(self, path: str, selector: str, actual: object, expected: object) -> None:
        if actual != expected:
            self.reject(path, selector, "mismatch", f"expected {expected!r}; found {actual!r}")


class _Sources:
    def __init__(self, root: Path, report: ToolchainReport) -> None:
        self.root = root
        self.report = report

    def text(self, path: str) -> str:
        raw = (self.root / path).read_bytes()
        self.report.inputs[path] = hashlib.sha256(raw).hexdigest()
        return raw.decode("utf-8")

    def document(self, path: str) -> object:
        source = self.text(path)
        if path.endswith(".json"):
            return json.loads(source, object_pairs_hook=_unique_object, parse_constant=_nonfinite)
        if path.endswith((".yml", ".yaml")):
            return YAML(typ="safe").load(source)
        return tomllib.loads(source)

    def unchanged(self) -> None:
        for path, digest in sorted(self.report.inputs.items()):
            try:
                current = hashlib.sha256((self.root / path).read_bytes()).hexdigest()
            except OSError:
                current = None
            if current != digest:
                self.report.reject(path, "file", "input_changed", "rerun after edits settle")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _nonfinite(value: str) -> object:
    raise ValueError(f"non-finite JSON constant: {value}")


def _at(document: object, *keys: str) -> object:
    for key in keys:
        if not isinstance(document, dict):
            return None
        document = document.get(key)
    return document


def _version(report: ToolchainReport, path: str, selector: str, value: object) -> str | None:
    if not isinstance(value, str) or _EXACT_VERSION.fullmatch(value) is None:
        report.reject(path, selector, "non_exact_version", "expected a literal major.minor.patch")
        return None
    return value


def _manifest(report: ToolchainReport, path: str, document: object) -> None:
    tools = report.tools
    if path == "mise.lock":
        for tool in _TOOLS:
            rows = _at(document, "tools", tool)
            if not isinstance(rows, list) or len(rows) != 1:
                report.reject(path, f"tools.{tool}", "invalid_lock", "expected one locked version")
                continue
            report.expect(path, f"tools.{tool}.version", _at(rows[0], "version"), tools[tool])
            report.expect(
                path, f"tools.{tool}.specifiers", _at(rows[0], "specifiers"), [tools[tool]]
            )
            if tool == "gitleaks":
                _gitleaks_lock(report, rows[0])
    elif path in ("package.json", "frontend/package.json"):
        for tool in ("node", "pnpm"):
            report.expect(path, f"engines.{tool}", _at(document, "engines", tool), tools[tool])
        if path == "package.json" or _at(document, "packageManager") is not None:
            report.expect(
                path, "packageManager", _at(document, "packageManager"), f"pnpm@{tools['pnpm']}"
            )
    elif path in _PROFILES:
        # The current runtime owner admits exactly one CPython patch line.
        # A future multi-runtime profile needs an explicit compatibility decision.
        for key, expected in {
            "supportedVersions": [tools["python"]],
            "requiresPython": f"=={tools['python']}",
            "containerVersion": tools["python"],
            "staticTarget": tools["python"].rsplit(".", 1)[0],
        }.items():
            report.expect(path, key, _at(document, key), expected)
    elif path == "backend/pyproject.toml":
        minor = tools["python"].rsplit(".", 1)[0]
        report.expect(
            path,
            "project.requires-python",
            _at(document, "project", "requires-python"),
            f"=={tools['python']}",
        )
        report.expect(
            path, "tool.mypy.python_version", _at(document, "tool", "mypy", "python_version"), minor
        )
        report.expect(
            path,
            "tool.ruff.target-version",
            _at(document, "tool", "ruff", "target-version"),
            "py" + minor.replace(".", ""),
        )
    elif path == "backend/uv.lock":
        report.expect(
            path, "requires-python", _at(document, "requires-python"), f"=={tools['python']}"
        )
    elif path == ".devcontainer/devcontainer.json":
        report.expect(path, "build.dockerfile", _at(document, "build", "dockerfile"), "Dockerfile")
        report.expect(path, "build.context", _at(document, "build", "context"), "..")
        arguments = _at(document, "build", "args")
        if arguments is not None and not isinstance(arguments, dict):
            raise ValueError("expected build.args mapping")
        override = _at(document, "build", "args", "MISE_VERSION")
        if override is not None:
            report.expect(path, "build.args.MISE_VERSION", override, tools["mise"])
        # Features can install another toolchain outside the checked Dockerfile.
        features = _at(document, "features")
        if features is not None:
            report.expect(path, "features", features, {})


def _gitleaks_lock(report: ToolchainReport, row: object) -> None:
    if not isinstance(row, dict):
        return
    report.expect(
        "mise.lock", "tools.gitleaks.backend", row.get("backend"), "aqua:gitleaks/gitleaks"
    )
    platforms = {key.removeprefix("platforms.") for key in row if key.startswith("platforms.")}
    report.expect("mise.lock", "tools.gitleaks.platforms", platforms, set(_GITLEAKS_PLATFORMS))
    version = report.tools["gitleaks"]
    for platform, asset in _GITLEAKS_PLATFORMS.items():
        selector = f"tools.gitleaks.platforms.{platform}"
        locked = _at(row, f"platforms.{platform}")
        checksum = _at(locked, "checksum")
        if not isinstance(checksum, str) or _SHA256.fullmatch(checksum) is None:
            report.reject("mise.lock", selector + ".checksum", "invalid_lock", "SHA-256 required")
        report.expect(
            "mise.lock",
            selector + ".url",
            _at(locked, "url"),
            "https://github.com/gitleaks/gitleaks/releases/download/"
            f"v{version}/gitleaks_{version}_{asset}.tar.gz",
        )


def _install_tasks(report: ToolchainReport, config: object) -> None:
    report.expect(
        "mise.toml", "settings.auto_install", _at(config, "settings", "auto_install"), False
    )
    for task, tools in (
        ("install", "python uv node pnpm gitleaks"),
        ("install:backend", "python uv"),
        ("install:frontend", "python node pnpm"),
    ):
        commands = [f"mise install --locked {tools}"]
        if task == "install":
            commands.append(
                "mise exec -- sh -c 'scanner_version=$(gitleaks version) && "
                f'test "$scanner_version" = "{report.tools["gitleaks"]}"\''
            )
        commands.append(f"mise exec -- python -S -m scripts.dev_environment.task {task}")
        report.expect("mise.toml", f"tasks.{task}.run", _at(config, "tasks", task, "run"), commands)


def _scanner_version(report: ToolchainReport, source: str) -> None:
    try:
        module = ast.parse(source)
    except SyntaxError as error:
        raise ValueError("scanner module syntax is invalid") from error
    versions = [
        node.value.value if isinstance(node.value, ast.Constant) else None
        for node in module.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets)
    ]
    if len(versions) != 1:
        report.reject(_SCANNER_SOURCE, "VERSION", "invalid_pin", "one literal scanner pin required")
        return
    version = _version(report, _SCANNER_SOURCE, "VERSION", versions[0])
    report.expect(_SCANNER_SOURCE, "VERSION", version, report.tools["gitleaks"])


def _instructions(source: str) -> Iterator[tuple[int, str, str]]:
    pending = ""
    start = 0
    for line, raw in enumerate(source.splitlines(), 1):
        text = raw.strip()
        if text.lower().startswith("# escape=") and text != "# escape=\\":
            raise ValueError("only the default Dockerfile escape is supported")
        if not text or text.startswith("#"):
            continue
        if not pending:
            start = line
        pending += text.removesuffix("\\") + " "
        if text.endswith("\\"):
            continue
        parts = pending.strip().split(maxsplit=1)
        if len(parts) != 2 or "<<" in pending:
            raise ValueError(f"unsupported Dockerfile instruction at line {start}")
        yield start, parts[0].upper(), parts[1]
        pending = ""
    if pending:
        raise ValueError(f"unfinished Dockerfile continuation at line {start}")


def _image(report: ToolchainReport, path: str, line: int, reference: str) -> str | None:
    selector = f"line {line} image"
    tagged, separator, digest = reference.partition("@")
    repository, colon, tag = tagged.rpartition(":")
    if not separator or _SHA256.fullmatch(digest) is None or not colon or not tag:
        report.reject(path, selector, "unpinned_image", "expected a literal tag@sha256:<64 hex>")
        return None
    if "$" in reference or "/" in tag:
        report.reject(
            path, selector, "unsupported_image", "dynamic image references are not admitted"
        )
        return None
    canonical = repository.removeprefix("docker.io/").removeprefix("library/")
    tool = {"python": "python", "node": "node", "ghcr.io/astral-sh/uv": "uv"}.get(canonical)
    version = None
    if tool is not None:
        version = _version(report, path, selector, tag.split("-", 1)[0])
        report.expect(path, selector, version, report.tools[tool])
    report.images.append(ImageReference(path, line, reference, tool, version, digest))
    return tool


def _dockerfile(report: ToolchainReport, path: str, source: str) -> None:
    seen: set[str] = set()
    stages: set[str] = set()
    stage_count = 0
    for line, instruction, argument in _instructions(source):
        tokens = shlex.split(argument, comments=True)
        reference = None
        if instruction == "FROM":
            if tokens and tokens[0].startswith("--platform="):
                tokens = tokens[1:]
            if not (len(tokens) == 1 or (len(tokens) == 3 and tokens[1].upper() == "AS")):
                raise ValueError(f"unsupported FROM at line {line}")
            reference = tokens[0]
            if reference.lower() in stages or reference == "scratch":
                reference = None
            if len(tokens) == 3:
                stages.add(tokens[2].lower())
            stage_count += 1
        elif instruction == "COPY":
            for token in tokens:
                if token.startswith("--from="):
                    candidate = token.removeprefix("--from=")
                    if candidate.lower() not in stages and not (
                        candidate.isdecimal() and int(candidate) < stage_count
                    ):
                        reference = candidate
        elif instruction == "ARG" and argument.startswith("MISE_VERSION"):
            key, _, value = argument.partition("=")
            report.expect(path, f"line {line} ARG name", key, "MISE_VERSION")
            report.expect(path, f"line {line} MISE_VERSION", value, report.tools["mise"])
            seen.add("mise")
        elif instruction == "RUN":
            for index, word in enumerate(tokens):
                if word != "corepack" or tokens[index + 1 : index + 2] != ["prepare"]:
                    continue
                pin = tokens[index + 2 : index + 3]
                report.expect(
                    path, f"line {line} corepack.prepare", pin, [f"pnpm@{report.tools['pnpm']}"]
                )
                seen.add("pnpm")
        if reference is not None:
            tool = _image(report, path, line, reference)
            if tool is not None:
                seen.add(tool)
    for tool in sorted(_DOCKER_TOOLS[path] - seen):
        report.reject(
            path, tool, "missing_declaration", "no admitted declaration for required tool"
        )


def _workflow(report: ToolchainReport, path: str, document: object) -> None:
    jobs = _at(document, "jobs")
    if not isinstance(jobs, dict) or not jobs:
        raise ValueError("expected a nonempty workflow jobs mapping")
    for job, body in jobs.items():
        steps = _at(body, "steps")
        if steps is None and isinstance(_at(body, "uses"), str):
            continue
        if not isinstance(steps, list):
            raise ValueError(f"expected jobs.{job}.steps array")
        for index, step in enumerate(steps):
            uses = _at(step, "uses")
            if not isinstance(uses, str):
                continue
            action = _SETUP_ACTIONS.get(uses.split("@", 1)[0])
            if action is not None:
                tool, key = action
                report.expect(
                    path,
                    f"jobs.{job}.steps[{index}].with.{key}",
                    _at(step, "with", key),
                    report.tools[tool],
                )


def check_toolchain(repo_root: Path) -> ToolchainReport:
    """Return all observed drift; never install, execute tools or contact a provider."""
    report = ToolchainReport()
    sources = _Sources(repo_root, report)
    try:
        config = sources.document("mise.toml")
    except (OSError, ValueError) as error:
        report.reject("mise.toml", "file", "invalid_source", str(error))
        return report
    for tool in (*_TOOLS, "mise"):
        keys = ("min_version",) if tool == "mise" else ("tools", tool)
        version = _version(report, "mise.toml", ".".join(keys), _at(config, *keys))
        if version is not None:
            report.tools[tool] = version
    if report.issues:
        return report
    _install_tasks(report, config)

    manifests = (
        "mise.lock",
        "package.json",
        "frontend/package.json",
        "backend/pyproject.toml",
        "backend/uv.lock",
        *_PROFILES,
        ".devcontainer/devcontainer.json",
    )
    workflows = sorted(
        {*repo_root.glob(".github/workflows/*.yml"), *repo_root.glob(".github/workflows/*.yaml")}
    )
    if not workflows:
        report.reject(".github/workflows", "files", "missing_declaration", "no workflows found")
    workflow_paths = tuple(path.relative_to(repo_root).as_posix() for path in workflows)
    for path in (*manifests, _SCANNER_SOURCE, *_DOCKER_TOOLS, *workflow_paths):
        try:
            if path == _SCANNER_SOURCE:
                _scanner_version(report, sources.text(path))
            elif path in _DOCKER_TOOLS:
                _dockerfile(report, path, sources.text(path))
            elif path in workflow_paths:
                _workflow(report, path, sources.document(path))
            else:
                _manifest(report, path, sources.document(path))
        except (OSError, ValueError, YAMLError) as error:
            report.reject(path, "file", "invalid_source", str(error))
    sources.unchanged()
    return report


def run(argv: Sequence[str], *, repo_root: Path, stdout: TextIO) -> int:
    """Root task adapter: no environment admission or lifecycle state required."""
    parser = argparse.ArgumentParser(prog="toolchain-check", description=__doc__)
    parser.add_argument("--format", choices=("human", "json"), default="json")
    options = parser.parse_args(argv)
    report = check_toolchain(repo_root)
    if options.format == "json":
        stdout.write(json.dumps(report.projection(), sort_keys=True) + "\n")
    else:
        stdout.write(f"Toolchain declarations: {report.state}\n")
        stdout.writelines(
            f"  {issue.path} [{issue.selector}]: {issue.reason}: {issue.detail}\n"
            for issue in report.issues
        )
        stdout.write(
            "Static declarations only; OCI digest identity and lock artifacts unverified.\n"
        )
    return 0 if report.state == "passed" else 1


def main(argv: Sequence[str] | None = None) -> int:
    return run(
        sys.argv[1:] if argv is None else argv,
        repo_root=Path(__file__).resolve().parents[2],
        stdout=cast(TextIO, sys.stdout),
    )


if __name__ == "__main__":
    raise SystemExit(main())

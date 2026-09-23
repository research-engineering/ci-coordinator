from __future__ import annotations

import ast
import hashlib
import json
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from ruamel.yaml import YAML

from scripts.bounded_git import run_git
from scripts.ci_matrix_contract import MatrixProfile
from scripts.ci_utility_checks import commands as utility_commands
from scripts.ci_utility_inventory import go_modules, spelling_exclusion
from scripts.documentation_graph_policy import load_policy
from scripts.python_witness import PYTHON_QUALITY_TARGETS
from scripts.repository_json import tracked_json_paths
from scripts.repository_paths import (
    read_repository_regular_file,
    repository_path_matches,
)
from scripts.target_control_paths import _SOURCE_FILENAMES, BUNDLE_PATH, SOURCE_ROOT
from scripts.workflow_lint import SHELL_SOURCES

Role = Literal["native-input", "owner-witness", "generated", "fixture", "non-code"]


@dataclass(frozen=True)
class InputDisposition:
    command_id: str
    role: Role
    predicate: str
    owner_path: str


_BIOME_INCLUDES = (
    "**",
    "!dist",
    "!coverage",
    "!node_modules",
    "!openapi",
    "!playwright-report",
    "!test-results",
    "!src/api/generated.ts",
    "!*.tsbuildinfo",
)
_RUFF_EXCLUDED_COMPONENTS = frozenset(
    {
        "__pypackages__",
        "_build",
        "buck-out",
        "dist",
        "node_modules",
        "site-packages",
        "venv",
    }
)
_BIOME_MAX_SOURCE_BYTES = 1_048_576
_FONT_OWNER_SHA256 = "ba44321839c16450627a8ba8b2f6e5aa5f2dd4e9277cf7e5a2cf094ad482af8a"
_FONT_PATHS = frozenset(
    f"frontend/src/assets/fonts/{name}"
    for name in (
        "lato-latin-400-normal.woff2",
        "lato-latin-700-normal.woff2",
        "lato-latin-ext-400-normal.woff2",
        "lato-latin-ext-700-normal.woff2",
    )
)
_TEXT_METADATA = frozenset(
    {
        ".gitignore",
        "tooling/quality/.gitignore",
        "AGENTS.md",
        "tooling/quality/README.md",
    }
)
_OWNER_INPUTS: dict[str, tuple[str, str, str]] = {
    **{
        f"docker/runtime/{path}": (
            "container.smoke",
            "Dockerfile",
            "Pinned acquisition or security repair consumed by the actual container build",
        )
        for path in (
            "ubuntu-snapshot.conf",
            "security/zlib-control",
            "security/check_zlib.c",
            "security/zlib.patch",
            "security/python-poplib.patch",
            "security/python-stringprep.patch",
            "security/python-tarfile.patch",
            "security/python-urllib-request.patch",
            "security/python-zipfile-__init__.patch",
        )
    },
    ".github/actions/secret-scan/checksums.txt": (
        "secret.scan",
        ".github/actions/secret-scan/install.sh",
        "Pinned scanner archive identities used by native provisioning",
    ),
    ".github/actions/secret-scan/empty.ignore": (
        "secret.scan",
        ".github/actions/secret-scan/scanner.py",
        "Explicit empty ignore input in the isolated scanner context",
    ),
    ".github/actions/secret-scan/policy.toml": (
        "secret.scan",
        ".github/actions/secret-scan/scanner.py",
        "Packaged scanner rules and exact benign-source exceptions",
    ),
    "docker/ci/connected-proxy.conf": (
        "frontend.connected",
        "scripts/ci_business_witness/runner.py",
        "TLS routing consumed by the isolated connected business fixture",
    ),
    "backend/pyproject.toml": (
        "python.lock-check",
        "scripts/python_environment_witness.py",
        "Python project and frozen dependency lock agreement",
    ),
    "backend/uv.lock": (
        "python.lock-check",
        "scripts/python_environment_witness.py",
        "Python frozen lock admission",
    ),
    "backend/requirements-dev.lock": (
        "python.lock-check",
        "scripts/python_environment_witness.py",
        "Hashed requirements export parity",
    ),
    "tooling/quality/pyproject.toml": (
        "dependency.audit",
        "scripts/dependency_audit.py",
        "Frozen quality-project dependency advisory audit",
    ),
    "tooling/quality/uv.lock": (
        "dependency.audit",
        "scripts/dependency_audit.py",
        "Frozen quality-project dependency advisory audit",
    ),
    "mise.toml": (
        "devcontainer.verify",
        "scripts/devcontainer_witness.py",
        "Developer toolchain preparation and portable proof",
    ),
    "mise.lock": (
        "devcontainer.verify",
        "scripts/devcontainer_witness.py",
        "Locked developer toolchain preparation",
    ),
    ".npmrc": (
        "frontend.install",
        "frontend/package.json",
        "Frontend package-manager configuration",
    ),
    ".env.example": (
        "development.stack",
        "scripts/dev_environment/environment.py",
        "Owned developer configuration template",
    ),
    ".dockerignore": (
        "container.smoke",
        "Dockerfile",
        "Container build-context filtering",
    ),
    "backend/alembic.ini": (
        "container.smoke",
        "scripts/container_runtime_smoke.py",
        "Packaged Alembic configuration presence",
    ),
    "backend/alembic/script.py.mako": (
        "python.persistence-test",
        "backend/alembic/env.py",
        "Migration-template owner; generation behavior remains a native witness obligation",
    ),
    "scripts/dev_environment/bootstrap_database.sql": (
        "development.stack",
        "compose.yaml",
        "Developer database bootstrap SQL mounted by Compose",
    ),
    "backend/src/ci_coordinator/py.typed": (
        "python.package-check",
        "backend/pyproject.toml",
        "Packaged Python typing marker",
    ),
    "frontend/index.html": (
        "frontend.build",
        "frontend/vite.config.ts",
        "Vite HTML build entrypoint",
    ),
    "frontend/src/assets/fonts/OFL.txt": (
        "utility.spelling",
        "scripts/ci_utility_inventory.py",
        "License text spelling only; license semantics remain owner-reviewed",
    ),
    "LICENSE": (
        "text.policy",
        "scripts/python_witness.py",
        "Project license text; legal meaning remains owner-reviewed",
    ),
    "NOTICE": (
        "text.policy",
        "scripts/python_witness.py",
        "Third-party attribution text; license compatibility is not inferred",
    ),
    "docs/specs/ci-coordinator-release/SPDX-LICENSE.txt": (
        "text.policy",
        "scripts/python_witness.py",
        "Retained upstream license text; legal meaning remains owner-reviewed",
    ),
    "tooling/quality/codespell.ini": (
        "utility.spelling",
        "scripts/ci_utility_checks.py",
        "Explicit spelling configuration",
    ),
    "tooling/quality/spelling-words.dic": (
        "utility.spelling",
        "scripts/ci_utility_checks.py",
        "Explicit spelling vocabulary",
    ),
    "tooling/quality/schemas/devcontainers/LICENSE-CODE": (
        "utility.spelling",
        "scripts/ci_utility_inventory.py",
        "Declared immutable upstream spelling exclusion",
    ),
    "docs/images/repository-catalog.png": (
        "documentation.graph",
        "scripts/documentation_graph_markdown.py",
        "Linked documentation image identity; image pixels are not analyzed",
    ),
}


def _text(root: Path, path: str) -> str:
    return read_repository_regular_file(
        root, Path(path), "CI input owner", maximum_bytes=1_048_576
    ).decode("utf-8")


def _explicit_utility_paths(root: Path, check: str) -> set[str]:
    return {
        argument
        for command in utility_commands(root, check)
        if "--" in command.argv
        for argument in command.argv[command.argv.index("--") + 1 :]
    }


def _admit_python_discovery(root: Path) -> None:
    configuration = tomllib.loads(_text(root, "backend/pyproject.toml"))["tool"]["ruff"]
    lint = configuration.get("lint", {})
    ignores = lint.get("per-file-ignores", {})
    if (
        set(configuration) != {"line-length", "src", "target-version", "lint", "format"}
        or set(lint) != {"select", "per-file-ignores"}
        or "F" not in lint["select"]
        or any(
            not isinstance(rules, list)
            or any(rule not in {"S101", "S105", "S106", "S603", "S607", "S608"} for rule in rules)
            for rules in ignores.values()
        )
        or configuration["format"] != {"exclude": ["alembic/versions/*.py"]}
    ):
        raise ValueError("Python discovery configuration needs input-contract admission")
    source = ast.parse(_text(root, "scripts/python_witness.py"))
    owner = next(
        node
        for node in source.body
        if isinstance(node, ast.ClassDef) and node.name == "PythonWitness"
    )
    method = next(
        node for node in owner.body if isinstance(node, ast.FunctionDef) and node.name == "run_lint"
    )
    expected = ast.parse('self.run_python_module("ruff", ("check", *PYTHON_QUALITY_TARGETS))').body[
        0
    ]
    if not method.body or ast.dump(method.body[0]) != ast.dump(expected):
        raise ValueError("Python discovery command needs input-contract admission")


def python_inputs(root: Path, paths: tuple[str, ...]) -> set[str]:
    roots = tuple((root / "backend" / path).resolve() for path in PYTHON_QUALITY_TARGETS)
    if any(not path.is_relative_to(root.resolve()) for path in roots):
        raise ValueError("Python quality root escapes repository")
    _admit_python_discovery(root)
    candidates = tuple(
        path
        for path in paths
        if Path(path).suffix in {".py", ".pyi"}
        and any(
            (root / path).resolve().is_relative_to(directory)
            and not any(
                part.startswith(".") or part in _RUFF_EXCLUDED_COMPONENTS
                for part in (root / path).resolve().relative_to(directory).parts
            )
            for directory in roots
        )
    )
    for path in paths:
        if Path(path).name in {
            "ruff.toml",
            ".ruff.toml",
            "pyproject.toml",
        } and path not in {
            "backend/pyproject.toml",
            "tooling/quality/pyproject.toml",
        }:
            raise ValueError(f"nested Python/tool configuration needs admission: {path}")
    result = run_git(root, ("ls-files", "--cached", "--ignored", "--exclude-standard", "-z"))
    ignored = {path for path in result.stdout.split("\0") if path}
    return set(candidates) - ignored


def frontend_inputs(root: Path, paths: tuple[str, ...]) -> set[str]:
    biome = json.loads(_text(root, "frontend/biome.json"))
    package = json.loads(_text(root, "frontend/package.json"))
    if (
        set(biome) != {"$schema", "files", "formatter", "linter", "assist"}
        or tuple(biome["files"]["includes"]) != _BIOME_INCLUDES
        or set(biome["files"]) != {"includes"}
        or set(biome["formatter"]) != {"enabled", "indentStyle", "lineWidth"}
        or biome["formatter"]["enabled"] is not True
        or set(biome["linter"]) != {"enabled", "rules"}
        or biome["linter"]["enabled"] is not True
        or biome["linter"]["rules"].get("preset") != "recommended"
        or biome["assist"] != {"actions": {"source": {"organizeImports": "on"}}}
        or package["scripts"]["lint"].split(" && ")[0] != "biome check ."
        or "pnpm run lint" not in package["scripts"]["check"].split(" && ")
    ):
        raise ValueError("frontend discovery command or include policy needs admission")
    admitted: set[str] = set()
    for path in paths:
        relative = PurePosixPath(path)
        if not relative.is_relative_to("frontend"):
            continue
        relative = relative.relative_to("frontend")
        if relative.name in {"biome.json", "biome.jsonc"} and path != "frontend/biome.json":
            raise ValueError(f"nested frontend discovery configuration needs admission: {path}")
        if (
            relative.suffix not in {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".css"}
            or relative.as_posix() == "src/api/generated.ts"
            or relative.parts[0] in {value[1:] for value in _BIOME_INCLUDES[1:7]}
            or any(part.startswith(".") for part in relative.parts)
        ):
            continue
        read_repository_regular_file(
            root,
            Path(path),
            "Biome source input",
            maximum_bytes=_BIOME_MAX_SOURCE_BYTES,
        )
        admitted.add(path)
    return admitted


def _generated_inputs(root: Path) -> dict[str, InputDisposition]:
    result = {
        "frontend/openapi/workbench.openapi.json": InputDisposition(
            "frontend.contract-python",
            "generated",
            "Exact served OpenAPI contract regeneration",
            "scripts/frontend_contract.py",
        ),
        "frontend/src/api/generated.ts": InputDisposition(
            "frontend.contract-types",
            "generated",
            "OpenAPI-to-TypeScript exact regeneration",
            "frontend/package.json",
        ),
        BUNDLE_PATH.relative_to(BUNDLE_PATH.parents[5]).as_posix(): InputDisposition(
            "target-control-bundle.check",
            "generated",
            "Exact CommonJS bundle regeneration",
            "scripts/target_control_bundle.py",
        ),
        ".github/workflows/coordinated-checks.yml": InputDisposition(
            "self-ci.check",
            "generated",
            "Exact native workflow and control projection",
            "scripts/self_ci_generate.py",
        ),
        (
            "backend/src/ci_coordinator/target_artifacts/resources/trusted-plan-request.yml"
        ): InputDisposition(
            "python.package-check",
            "generated",
            "Packaged requester resource identity",
            "scripts/package_resource_inspection.py",
        ),
    }
    filenames = (
        "ci-coordinator.cjs",
        "dependency-graph.v1.json",
        "execution-registry.v1.json",
        "target-artifacts-source.v1.json",
        "test-manifest.v1.json",
    )
    for directory, command in (
        (".ci-coordinator", "self-ci.check"),
        ("fixtures/target-repository/.ci-coordinator", "target-artifacts.check"),
        (
            "fixtures/native-target-repository/.ci-coordinator",
            "native-target-artifacts.check",
        ),
    ):
        for name in filenames:
            path = f"{directory}/{name}"
            if (root / path).is_file():
                result[path] = InputDisposition(
                    command,
                    "generated" if directory == ".ci-coordinator" else "fixture",
                    "Exact target-artifact projection; not application-source analysis",
                    "scripts/self_ci_generate.py"
                    if directory == ".ci-coordinator"
                    else f"{directory}/target-artifacts-source.v1.json",
                )
    for name in ("validation-catalog.v1.json", "self-ci-inventory.v1.json"):
        result[f".ci-coordinator/{name}"] = InputDisposition(
            "self-ci.check",
            "generated",
            "Exact self-consumer projection",
            "scripts/self_ci_generate.py",
        )
    return result


def input_dispositions(
    root: Path, profile: MatrixProfile, paths: tuple[str, ...]
) -> dict[str, InputDisposition]:
    python = python_inputs(root, paths)
    frontend = frontend_inputs(root, paths)
    json_paths = {path.relative_to(root).as_posix() for path in tracked_json_paths(root)}
    yaml_paths = _explicit_utility_paths(root, "yaml")
    docker_paths = _explicit_utility_paths(root, "hadolint")
    go_paths = _explicit_utility_paths(root, "gofmt")
    modules = go_modules(root, paths)
    generated = _generated_inputs(root)
    documentation = load_policy(root).markdown_globs
    commands = {row.commandId for row in profile.commands}
    source_root = SOURCE_ROOT.relative_to(SOURCE_ROOT.parents[4]).as_posix()
    control_sources = {f"{source_root}/{name}" for name in _SOURCE_FILENAMES}
    result: dict[str, InputDisposition] = {}
    for path in paths:
        row = generated.get(path)
        if row is None and path in _OWNER_INPUTS:
            command, owner, predicate = _OWNER_INPUTS[path]
            row = InputDisposition(command, "owner-witness", predicate, owner)
        if row is None and path in python:
            row = InputDisposition(
                "python.lint",
                "native-input",
                "Ruff source discovery and lint",
                "scripts/python_witness.py",
            )
        if row is None and path in frontend:
            row = InputDisposition(
                "frontend.quality",
                "native-input",
                "Biome source discovery and static diagnostics; not per-file type/test coverage",
                "frontend/biome.json",
            )
        if row is None and path in control_sources:
            row = InputDisposition(
                "target-control-bundle.check",
                "owner-witness",
                "Closed CommonJS source graph and bundle parity",
                "scripts/target_control_bundle.py",
            )
        if row is None and path in {
            "backend/src/ci_coordinator/consumer_contract_lab/resources/node-runtime-guard.cjs",
            "backend/src/ci_coordinator/consumer_contract_lab/resources/runtime-harness.cjs",
        }:
            row = InputDisposition(
                "python.package-check",
                "owner-witness",
                "Exact installed consumer-lab resource identity; execution is separate",
                "scripts/package_resource_inspection.py",
            )
        if row is None and path in json_paths:
            row = InputDisposition(
                "repository.json",
                "fixture" if path.startswith("fixtures/") else "native-input",
                "Strict JSON syntax only; schema and domain semantics remain owner-specific",
                "scripts/repository_json.py",
            )
        if row is None and path in yaml_paths:
            row = InputDisposition(
                "utility.yaml",
                "fixture" if path.startswith("fixtures/") else "native-input",
                "YAML syntax and duplicate-key diagnostics only",
                "scripts/ci_utility_checks.py",
            )
        if row is None and path in docker_paths:
            row = InputDisposition(
                "utility.hadolint",
                "native-input",
                "Dockerfile lint with exact owner-admitted exceptions",
                "scripts/ci_utility_checks.py",
            )
        if row is None and path in go_paths:
            row = InputDisposition(
                "utility.gofmt",
                "native-input",
                "Go formatting input; native Go discovery admission remains a separate predicate",
                "scripts/ci_utility_checks.py",
            )
        if row is None and any(
            path == f"{module}/{name}" for module in modules for name in ("go.mod", "go.sum")
        ):
            row = InputDisposition(
                "utility.go-vet",
                "owner-witness",
                "Read-only Go module resolution input",
                "scripts/ci_utility_checks.py",
            )
        if row is None and path in {source for source, _ in SHELL_SOURCES}:
            row = InputDisposition(
                "workflow.lint",
                "native-input",
                "Explicit ShellCheck dialect and argv",
                "scripts/workflow_lint.py",
            )
        if row is None and any(repository_path_matches(pattern, path) for pattern in documentation):
            row = InputDisposition(
                "documentation.graph",
                "native-input",
                "Configured Markdown graph syntax and local links",
                "scripts/documentation_graph_policy.py",
            )
        if row is None and (path in _TEXT_METADATA or Path(path).suffix == ".md"):
            row = InputDisposition(
                "utility.spelling",
                "non-code",
                "Spelling-only metadata; no code or configuration semantic coverage claimed",
                "scripts/ci_utility_inventory.py",
            )
        if (
            row is None
            and path.startswith("tooling/quality/spelling/")
            and Path(path).suffix == ".txt"
        ):
            if spelling_exclusion(path) is None:
                raise ValueError(f"unowned spelling exclusion input: {path}")
            row = InputDisposition(
                "utility.spelling",
                "owner-witness",
                "Exact fixture-line exclusion checked against source",
                "scripts/ci_utility_inventory.py",
            )
        if row is None and path.startswith("patches/") and Path(path).suffix == ".patch":
            workspace = YAML(typ="safe", pure=True).load(_text(root, "pnpm-workspace.yaml"))
            patches = workspace.get("patchedDependencies", {})
            if not isinstance(patches, dict) or path not in patches.values():
                raise ValueError(f"unowned package patch: {path}")
            row = InputDisposition(
                "frontend.install",
                "owner-witness",
                "Declared package patch applied by frozen install",
                "pnpm-workspace.yaml",
            )
        if (
            row is None
            and path.startswith("frontend/src/assets/fonts/")
            and Path(path).suffix == ".woff2"
        ):
            owner_bytes = read_repository_regular_file(
                root,
                Path("frontend/src/styles/tokens.css"),
                "font input owner",
                maximum_bytes=1_048_576,
            )
            if (
                path not in _FONT_PATHS
                or hashlib.sha256(owner_bytes).hexdigest() != _FONT_OWNER_SHA256
            ):
                raise ValueError(f"font has no admitted CSS consumer: {path}")
            row = InputDisposition(
                "frontend.build",
                "owner-witness",
                "Reviewed exact CSS bytes bind four font inputs; source changes need readmission",
                "frontend/src/styles/tokens.css",
            )
        if row is None:
            raise ValueError(f"CI matrix has no effective input owner for {path}")
        if row.command_id not in commands or not (root / row.owner_path).is_file():
            raise ValueError(f"CI input consumer or owner is unavailable: {path}")
        result[path] = row
    return result

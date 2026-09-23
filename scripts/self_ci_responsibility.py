from __future__ import annotations

import hashlib
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from ci_coordinator.kernel import canonical_json
from ci_coordinator.repo_context.freshness import matches_path_pattern
from scripts.ci_matrix_contract import MatrixProfile, load_matrix
from scripts.ci_matrix_inventory import MAX_FILE_BYTES, MAX_PATHS, MAX_TOTAL_BYTES, classify_paths
from scripts.ci_utility_inventory import go_modules, repository_paths
from scripts.proofkit_common import JsonObject
from scripts.repository_paths import read_repository_regular_file, repository_path_matches
from scripts.self_ci_catalog import FamilySettings

OWNER_PATHS = (
    "proofkit/ci-matrix.v1.json",
    "proofkit/ci-risk-coverage.v1.json",
    "proofkit/quality-plan.v1.json",
    "proofkit/witness-plan-input.json",
    "scripts/ci_matrix_contract.py",
    "scripts/ci_matrix_inputs.py",
    "scripts/ci_matrix_risks.py",
    "scripts/ci_matrix_risk_execution.py",
    "scripts/ci_utility_checks.py",
    "scripts/ci_utility_inventory.py",
)
GLOBAL_RISK_PATHS = tuple(
    sorted(
        {
            ".ci-coordinator/**",
            ".devcontainer/**",
            ".github/**",
            ".npmrc",
            "Dockerfile",
            "Dockerfile.*",
            "backend/pyproject.toml",
            "backend/requirements-dev.lock",
            "backend/uv.lock",
            "backend/src/ci_coordinator/config_control/**",
            "backend/src/ci_coordinator/integrations/github/repository_context.py",
            "backend/src/ci_coordinator/integrations/github/self_ci_inventory.py",
            "backend/src/ci_coordinator/integrations/github/recursive_git_tree.py",
            "backend/src/ci_coordinator/integrations/github/git_snapshot.py",
            "backend/src/ci_coordinator/integrations/github/workflow_discovery_client.py",
            "backend/src/ci_coordinator/integrations/github/installation_request_admission.py",
            "backend/src/ci_coordinator/kernel/**",
            "backend/src/ci_coordinator/planning_core/**",
            "backend/src/ci_coordinator/repo_context/**",
            "backend/src/ci_coordinator/runtime_settings/**",
            "backend/src/ci_coordinator/target_artifacts/**",
            "backend/src/ci_coordinator/validation_contract/**",
            "backend/src/ci_coordinator/verification_core/**",
            "docker/**",
            "mise.lock",
            "mise.toml",
            "package.json",
            "patches/**",
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
            "proofkit/**",
            "scripts/**",
            "tooling/quality/**",
        }
    )
)
_SELECTIVE_COMMANDS = {
    "utility-config": {"utility.yaml", "utility.schemas"},
    "utility-go-static": {"utility.gofmt", "utility.go-vet", "utility.staticcheck"},
}


@dataclass(frozen=True)
class ResponsibilityProjection:
    settings: dict[str, FamilySettings]
    graph: JsonObject
    owner_inputs: dict[str, bytes]
    path_inventory: tuple[tuple[str, int], ...]
    profile: MatrixProfile
    inventory: JsonObject

    def assert_current(self, root: Path) -> None:
        if path_inventory(root, self.profile) != self.path_inventory:
            raise ValueError("self CI finite path universe changed during generation")


def path_inventory(root: Path, profile: MatrixProfile) -> tuple[tuple[str, int], ...]:
    paths = repository_paths(root)
    if len(paths) > MAX_PATHS:
        raise ValueError("self CI graph exceeds the matrix path bound")
    classify_paths(profile, paths)
    total = 0
    records: list[tuple[str, int]] = []
    for path in paths:
        payload = read_repository_regular_file(
            root, Path(path), "self CI graph member", maximum_bytes=MAX_FILE_BYTES
        )
        total += len(payload)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("self CI graph input bytes exceed the matrix aggregate bound")
        observed = (root / path).lstat()
        if not stat.S_ISREG(observed.st_mode):
            raise ValueError("self CI graph member changed file kind")
        records.append((path, 0o100755 if observed.st_mode & 0o111 else 0o100644))
    return tuple(records)


def responsibility_projection(root: Path) -> ResponsibilityProjection:
    inputs = {
        path: read_repository_regular_file(
            root, Path(path), "self CI responsibility owner", maximum_bytes=MAX_FILE_BYTES
        )
        for path in OWNER_PATHS
    }
    profile, _quality = load_matrix(root)
    records = path_inventory(root, profile)
    paths = tuple(path for path, _mode in records)
    modules = go_modules(root, paths)
    for module in modules:
        path = (Path(module) / "go.mod").as_posix()
        content = read_repository_regular_file(
            root, Path(path), "self CI Go module owner", maximum_bytes=MAX_FILE_BYTES
        )
        if re.search(r"(?m)^\s*replace\b", content.decode("utf-8")):
            raise ValueError(
                "Go replacement dependencies require explicit responsibility admission"
            )
        inputs[path] = content
    settings = family_responsibilities(profile, paths, modules)
    inventory: JsonObject = {
        "scope": "git-observed-regular-paths-and-modes",
        "pathCount": len(records),
        "pathInventorySha256": hashlib.sha256(
            canonical_json([list(row) for row in records])
        ).hexdigest(),
        "goModuleDirectories": list(modules),
        "selectiveFamilies": [
            {
                "jobId": job_id,
                "responsibilityPaths": list(row.responsibility_paths),
                "inputPathCount": len(
                    selected := [
                        path
                        for path in paths
                        if any(
                            matches_path_pattern(pattern, path)
                            for pattern in row.responsibility_paths
                        )
                    ]
                ),
                "inputPathsSha256": hashlib.sha256(canonical_json(selected)).hexdigest(),
            }
            for job_id, row in sorted(settings.items())
        ],
        "nonClaims": [
            "Exact nodes cover finite Git paths, not future files or arbitrary dependencies.",
            "Only config and Go static may be omitted; native families remain required.",
            "Path membership is content-independent; authenticated diff and head bind bytes.",
        ],
    }
    graph: JsonObject = {
        "source": "configured",
        "invalidatesWhenChanged": list(GLOBAL_RISK_PATHS),
        "globalRiskPaths": list(GLOBAL_RISK_PATHS),
        "nodes": [{"path": path, "dependents": [], "riskClasses": ["source"]} for path in paths],
    }
    return ResponsibilityProjection(settings, graph, inputs, records, profile, inventory)


def family_responsibilities(
    profile: MatrixProfile, paths: tuple[str, ...], modules: tuple[str, ...]
) -> dict[str, FamilySettings]:
    groups = {group.jobId: group for group in profile.utilityGroups}
    settings: dict[str, FamilySettings] = {}
    for job_id, commands in _SELECTIVE_COMMANDS.items():
        if job_id not in groups or set(groups[job_id].commandIds) != commands:
            raise ValueError("selective utility command contract changed")
        surfaces = [
            surface for surface in profile.surfaces if commands.intersection(surface.commandIds)
        ]
        patterns = {pattern for surface in surfaces for pattern in surface.paths}
        if not patterns:
            raise ValueError("selective utility family has no declared input surface")
        if job_id == "utility-config":
            patterns.update({"**/*.ini"})
        else:
            if not modules:
                raise ValueError("selective Go family requires a closed module inventory")
            patterns.update({"**/go.work", "**/go.work.sum", "**/staticcheck.conf"})
            patterns.update("**" if module == "." else module + "/**" for module in modules)
        if any(
            repository_path_matches(pattern, path) != matches_path_pattern(pattern, path)
            for pattern in patterns
            for path in paths
        ):
            raise ValueError("utility and runtime responsibility pattern semantics disagree")
        required_inputs = (
            tuple(
                path
                for path in paths
                if Path(path).suffix in {".yaml", ".yml", ".json", ".toml", ".ini"}
            )
            if job_id == "utility-config"
            else tuple(
                path
                for path in paths
                if (
                    path.endswith(".go")
                    or Path(path).name
                    in {"go.mod", "go.sum", "go.work", "go.work.sum", "staticcheck.conf"}
                    or any(module == "." or path.startswith(module + "/") for module in modules)
                )
            )
        )
        if any(
            not any(matches_path_pattern(pattern, path) for pattern in patterns)
            for path in required_inputs
        ):
            raise ValueError("matrix responsibility omits a direct utility input")
        settings[job_id] = FamilySettings(
            responsibility_paths=tuple(sorted(patterns)),
            omit_allowed=True,
            responsibility_risk_classes=(),
        )
    return settings

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from scripts.bounded_git import capture_git_text
from scripts.diagram_contract import is_diagram_input
from scripts.proofkit_common import (
    JsonObject,
    as_array,
    as_object,
    parse_json_object,
    read_json_object,
)
from scripts.proofkit_retirements import (
    admitted_proof_owner_retirements,
    proof_like_path_patterns_for_range,
)
from scripts.proofkit_route_sources import load_route_authority
from scripts.repository_paths import repository_path_matches

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENT_ADMISSION_PATH_PATTERNS = (
    "docs/specs/**/requirements.v1.json",
    "proofkit/requirement-bindings.json",
    "proofkit/routes/*.v2.json",
)
DOCUMENTATION_GRAPH_PATH_PATTERNS = (
    "README.md",
    "ROADMAP.md",
    "docs/**/*.md",
)


@dataclass(frozen=True, slots=True)
class _BindingIndex:
    requirement_ids: Mapping[str, list[str]]
    rows: Mapping[str, Sequence[JsonObject]]


def _index_bindings(bindings: Mapping[str, object]) -> _BindingIndex:
    requirements: dict[str, set[str]] = {}
    rows: dict[str, list[JsonObject]] = {}
    for requirement in _rows(bindings, "requirements"):
        path = requirement.get("specPath")
        requirement_id = requirement.get("requirementId")
        if isinstance(path, str) and isinstance(requirement_id, str):
            requirements.setdefault(path, set()).add(requirement_id)
    for binding in _rows(bindings, "bindings"):
        path = binding.get("witnessPath")
        if not isinstance(path, str):
            continue
        rows.setdefault(path, []).append(binding)
        requirement_id = binding.get("requirementId")
        if isinstance(requirement_id, str):
            requirements.setdefault(path, set()).add(requirement_id)
    return _BindingIndex(
        {path: sorted(identifiers) for path, identifiers in requirements.items()}, rows
    )


def selective_gate_plan_input(
    *,
    base_ref: str,
    paths: Sequence[str],
    repo_root: Path = REPO_ROOT,
    requirement_bindings: Mapping[str, object] | None = None,
    base_requirement_bindings: Mapping[str, object] | None = None,
) -> JsonObject:
    changed_paths = sorted(set(paths))
    commands = _witness_command_catalog(repo_root)
    if requirement_bindings is None:
        requirement_bindings = load_route_authority(repo_root=repo_root).binding_projection
    if base_requirement_bindings is None:
        base_requirement_bindings = load_route_authority(
            repo_root=repo_root,
            ref=base_ref,
        ).binding_projection
    binding_index = _index_bindings(requirement_bindings)
    range_proof_like_patterns = proof_like_path_patterns_for_range(
        _read_json_at_commit(repo_root, base_ref, "proofkit/repo-profile.json"),
        read_json_object(repo_root / "proofkit/repo-profile.json"),
    )
    python_dependency_patterns = [
        "backend/pyproject.toml",
        "backend/requirements-dev.lock",
        "backend/uv.lock",
    ]
    dependency_paths = [path for path in changed_paths if path in python_dependency_patterns]
    proof_like_paths = [
        path for path in changed_paths if path_matches_any(range_proof_like_patterns, path)
    ]
    deleted_proof_like_paths = [
        path for path in proof_like_paths if not (repo_root / path).exists()
    ]
    removed_proof_like_paths = sorted(
        admitted_proof_owner_retirements(
            base_bindings=base_requirement_bindings,
            deleted_proof_like_paths=deleted_proof_like_paths,
            head_bindings=requirement_bindings,
            head_path_exists=lambda path: (repo_root / path).exists(),
            manifest=read_json_object(repo_root / "proofkit/proof-owner-retirements.json"),
        )
    )
    requirement_witness_paths = [
        path for path in changed_paths if binding_index.requirement_ids.get(path)
    ]
    requirement_impact_paths = sorted(set(proof_like_paths + requirement_witness_paths))
    unbound_proof_like_paths = [
        path
        for path in proof_like_paths
        if path not in removed_proof_like_paths and not binding_index.requirement_ids.get(path)
    ]
    bound_requirement_impact_paths = [
        path for path in requirement_impact_paths if binding_index.requirement_ids.get(path)
    ]
    python_public_api_touched = any(
        path in {"Dockerfile", "backend/pyproject.toml"}
        or (path.startswith("backend/src/ci_coordinator/") and path.endswith(".py"))
        for path in changed_paths
    )
    base_commands = [
        _plan_command(
            "proofkit.verify", commands.get("proofkit.verify"), "non_skippable_spec_control"
        )
    ]
    if any(is_diagram_input(path) for path in changed_paths):
        base_commands.extend(
            _plan_command(command_id, commands.get(command_id), "documentation_diagram_input")
            for command_id in (
                "documentation.diagrams-inventory",
                "documentation.diagrams",
                "documentation.diagrams-falsifiers",
                "documentation.diagrams-process",
            )
        )
    return {
        "schemaVersion": 1,
        "archiveOrBinaryPathPatterns": [],
        "artifactIntegrityPolicies": [],
        "baseCommands": base_commands,
        "changedPaths": changed_paths,
        "dependencyFreshness": {
            "command": commands.get("python.install-check"),
            "paths": dependency_paths,
        },
        "fallbackCoverage": [],
        "generatedArtifactRules": [],
        "ignoredProofLikePaths": removed_proof_like_paths,
        "nonClaims": [
            "CI Coordinator supplies changed paths from git state or explicit CLI arguments.",
            (
                "Removed proof-like paths are ignored only when a range-bound retirement manifest "
                "commits the complete base binding set, records an explicit obsolete disposition, "
                "removes every head path and binding, and every affected requirement or "
                "its explicit "
                "replacement retains a live head witness."
            ),
            "This plan does not execute commands or prove receipt freshness.",
        ],
        "packageCommands": [],
        "pathTriggeredCommands": [
            _path_command(
                "documentation.graph",
                commands.get("documentation.graph"),
                "documentation_graph_surface",
                list(DOCUMENTATION_GRAPH_PATH_PATTERNS),
            ),
            _path_command(
                "dependency.check",
                commands.get("dependency.check"),
                "dependency_surface",
                python_dependency_patterns,
            ),
            _path_command(
                "requirements.admission",
                commands.get("requirements.admission"),
                "requirement_source_or_binding_surface",
                list(REQUIREMENT_ADMISSION_PATH_PATTERNS),
            ),
            _path_command(
                "repository.json",
                commands.get("repository.json"),
                "static_surface",
                [
                    "Dockerfile",
                    "Dockerfile.workflow-lint",
                    "README.md",
                    "ROADMAP.md",
                    "backend/**",
                    "docs/**",
                    "fixtures/**",
                    "proofkit/*.json",
                    "proofkit/**/*.json",
                    "scripts/**/*.py",
                ],
            ),
            _path_command(
                "target-control-bundle.check",
                commands.get("target-control-bundle.check"),
                "target_control_bundle",
                [
                    "backend/src/ci_coordinator/target_artifacts/control_source/**",
                    "backend/src/ci_coordinator/target_artifacts/resources/ci-coordinator.cjs",
                    "package.json",
                    "pnpm-lock.yaml",
                    "pnpm-workspace.yaml",
                    "scripts/target_control_bundle.py",
                ],
            ),
            _path_command(
                "target-artifacts.check",
                commands.get("target-artifacts.check"),
                "target_artifact_contract",
                [
                    "backend/src/ci_coordinator/execution_orchestration/target_registry*.py",
                    "backend/src/ci_coordinator/repo_context/dependency_graph*.py",
                    "backend/src/ci_coordinator/runner_capacity/manifest_codec.py",
                    "backend/src/ci_coordinator/target_artifacts/**",
                    "docs/specs/ci-coordinator-runtime/dependency-graph.schema.v1.json",
                    "docs/specs/ci-coordinator-runtime/target-artifacts-source.schema.v1.json",
                    "docs/specs/ci-coordinator-runtime/target-execution-registry.schema.v1.json",
                    "docs/specs/ci-coordinator-runtime/test-manifest.schema.v1.json",
                    "fixtures/target-repository/.ci-coordinator/**",
                ],
            ),
            _path_command(
                "native-target-artifacts.check",
                commands.get("native-target-artifacts.check"),
                "native_target_artifact_contract",
                [
                    "backend/src/ci_coordinator/execution_orchestration/target_registry*.py",
                    "backend/src/ci_coordinator/repo_context/dependency_graph*.py",
                    "backend/src/ci_coordinator/runner_capacity/manifest_codec.py",
                    "backend/src/ci_coordinator/target_artifacts/**",
                    "docs/specs/ci-coordinator-runtime/dependency-graph.schema.v1.json",
                    "docs/specs/ci-coordinator-runtime/target-artifacts-source.schema.v1.json",
                    "docs/specs/ci-coordinator-runtime/target-execution-registry.schema.v1.json",
                    "docs/specs/ci-coordinator-runtime/test-manifest.schema.v1.json",
                    "fixtures/native-target-repository/.ci-coordinator/**",
                ],
            ),
            _path_command(
                "python.install-check",
                commands.get("python.install-check"),
                "python_dependency_surface",
                [*python_dependency_patterns, "scripts/python_environment_witness.py"],
            ),
            _path_command(
                "python.import-boundary",
                commands.get("python.import-boundary"),
                "python_import_boundary",
                [
                    "backend/**/*.py",
                    "backend/pyproject.toml",
                    "backend/requirements-dev.lock",
                    "scripts/python_environment_witness.py",
                    "scripts/python_import_boundary_policy.py",
                    "scripts/python_witness.py",
                ],
            ),
            _path_command(
                "python.lint",
                commands.get("python.lint"),
                "python_static_surface",
                [
                    "backend/**/*.py",
                    "backend/pyproject.toml",
                    "backend/requirements-dev.lock",
                    "scripts/**/*.py",
                ],
            ),
            _path_command(
                "python.test",
                commands.get("python.test"),
                "python_test_surface",
                [
                    "backend/**/*.py",
                    "backend/pyproject.toml",
                    "backend/requirements-dev.lock",
                    "backend/tests/**",
                    "scripts/**/*.py",
                ],
            ),
            _path_command(
                "python.typecheck",
                commands.get("python.typecheck"),
                "python_type_surface",
                [
                    "backend/**/*.py",
                    "backend/pyproject.toml",
                    "backend/requirements-dev.lock",
                    "scripts/**/*.py",
                ],
            ),
        ],
        "preexistingFailures": [],
        "privatePathPrefixes": [".local/"],
        "proofLikePathPatterns": range_proof_like_patterns,
        "publicApi": {
            "command": commands.get("python.package-check"),
            "touched": python_public_api_touched,
        },
        "requirementImpact": {
            "command": commands.get("proofkit.verify"),
            "touched": bool(requirement_impact_paths),
        },
        "scanObligation": {
            "command": commands.get("text.policy"),
            "commandId": "text-policy",
            "commandOwnership": "proofkit_text_policy",
            "mode": "diff-scoped",
            "reason": "text_policy",
            "required": True,
        },
        "touchedRequirementWitnesses": _touched_requirement_witnesses(
            bound_requirement_impact_paths, commands, binding_index
        ),
        "unknownEdges": [
            {
                "edgeClass": "requirement_binding",
                "edgeId": _unknown_edge_id(path),
                "path": path,
                "reason": "unbound_proof_like_path",
            }
            for path in unbound_proof_like_paths
        ],
    }


def path_matches_any(patterns: Sequence[str], path: str) -> bool:
    return any(repository_path_matches(pattern, path) for pattern in patterns)


def _read_json_at_commit(repo_root: Path, ref: str, path: str) -> JsonObject:
    commit = _git_text(
        repo_root, ("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}")
    ).strip()
    return _parse_json_object(_git_text(repo_root, ("show", f"{commit}:{path}")), path)


def _path_command(
    command_id: str, command: str | None, reason: str, path_patterns: list[str]
) -> JsonObject:
    return {
        "command": _plan_command(command_id, command, reason),
        "pathPatterns": path_patterns,
    }


def _plan_command(command_id: str, command: str | None, reason: str) -> JsonObject:
    if not command:
        raise ValueError(f"missing witness command: {command_id}")
    return {"id": command_id, "command": command, "reason": reason}


def _witness_command_catalog(repo_root: Path) -> dict[str, str]:
    witness_plan = read_json_object(repo_root / "proofkit/witness-plan-input.json")
    catalog: dict[str, str] = {}
    for raw_command in as_array(witness_plan.get("commands"), "witness plan commands"):
        command = as_object(raw_command, "witness plan command")
        command_id = command.get("id")
        argv = command.get("argv")
        if (
            not isinstance(command_id, str)
            or not isinstance(argv, list)
            or any(not isinstance(arg, str) for arg in argv)
        ):
            raise ValueError("witness plan command is invalid")
        catalog[command_id] = " ".join(argv)
    return catalog


def _touched_requirement_witnesses(
    paths: Sequence[str], commands: Mapping[str, str], bindings: _BindingIndex
) -> list[JsonObject]:
    witnesses: list[JsonObject] = []
    for path in paths:
        command_values: list[str] = []
        for command_id in _command_ids_for_path(bindings, path):
            command = commands.get(command_id)
            if command is None:
                raise ValueError(
                    "missing witness command for requirement binding commandId: " + command_id
                )
            command_values.append(command)
        witnesses.append(
            {
                "commands": command_values,
                "path": path,
                "requirementIds": bindings.requirement_ids.get(path, []),
            }
        )
    return witnesses


def _command_ids_for_path(bindings: _BindingIndex, path: str) -> list[str]:
    command_ids = {"proofkit.verify"}
    for binding in bindings.rows.get(path, ()):
        raw_ids = binding.get("commandIds", [])
        if not isinstance(raw_ids, list):
            raise TypeError("binding command ids must be an array")
        for command_id in raw_ids:
            if not isinstance(command_id, str):
                raise TypeError("binding command id must be a string")
            command_ids.add(command_id)
    if path_matches_any(REQUIREMENT_ADMISSION_PATH_PATTERNS, path):
        command_ids.add("requirements.admission")
    if path.endswith(".md"):
        command_ids.add("text.policy")
    return sorted(command_ids)


def _rows(document: Mapping[str, object], field: str) -> list[JsonObject]:
    return [as_object(row, field) for row in as_array(document.get(field, []), field)]


def _unknown_edge_id(path: str) -> str:
    digest = hashlib.sha256(path.encode()).hexdigest()[:24]
    encoded = "".join("abcdefghijklmnop"[int(nibble, 16)] for nibble in digest)
    return f"ci-coordinator.unbound-proof-like.{encoded}"


def _git_text(repo_root: Path, args: Sequence[str]) -> str:
    return capture_git_text(repo_root, args)


def _parse_json_object(source: str, context: str) -> JsonObject:
    return parse_json_object(source, context)

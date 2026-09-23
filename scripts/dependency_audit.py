from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ci_coordinator.runtime_settings import PYTHON_RUNTIME_PROFILE
from scripts.bounded_process import CommandResult, spawn
from scripts.command_sequence import Command
from scripts.dependency_audit_reports import PackageIdentity, admit_npm_report, admit_uv_report
from scripts.dependency_audit_sources import (
    admit_audit_configuration,
    audit_source,
    npm_inventory,
    python_inventory,
)


@dataclass(frozen=True)
class AuditInventory:
    packages: frozenset[PackageIdentity]
    source_hashes: tuple[tuple[str, str], ...]


def audit_environment(source: Mapping[str, str]) -> dict[str, str]:
    return {
        "PATH": source.get("PATH", os.defpath),
        "UV_NO_CACHE": "true",
        "NPM_CONFIG_USERCONFIG": os.devnull,
        "NPM_CONFIG_GLOBALCONFIG": os.devnull,
        "npm_config_registry": "https://registry.npmjs.org",
    }


def commands(repo_root: Path) -> tuple[Command, ...]:
    common = (
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
    )
    python_commands = tuple(
        Command(
            (
                "uv",
                "audit",
                "--project",
                project,
                *common,
                "--python-version",
                ".".join(map(str, version)),
            ),
            repo_root,
        )
        for project in ("backend", "tooling/quality")
        for version in PYTHON_RUNTIME_PROFILE.supported_versions
    )
    return (
        *python_commands,
        Command(("pnpm", "audit", "--audit-level", "high", "--json"), repo_root),
    )


def audit_inventory(repo_root: Path, command: Command) -> AuditInventory:
    paths: tuple[str, ...]
    if command not in commands(repo_root):
        raise ValueError("audit command differs from the complete native audit scope")
    if command.argv[0] == "uv":
        project = command.argv[3]
        paths = (f"{project}/pyproject.toml", f"{project}/uv.lock")
        packages = python_inventory(repo_root, project)
    else:
        admit_audit_configuration(repo_root)
        paths = (
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
            ".npmrc",
            "package.json",
            "frontend/package.json",
        )
        packages = npm_inventory(repo_root)
    return AuditInventory(
        packages,
        tuple(
            (path, hashlib.sha256(audit_source(repo_root, path).encode()).hexdigest())
            for path in paths
        ),
    )


def admit_audit_result(
    repo_root: Path,
    command: Command,
    result: CommandResult,
    *,
    inventory: AuditInventory,
) -> dict[str, object]:
    if result.error is not None or result.failure_kind is not None or result.status not in {0, 1}:
        raise ValueError("native dependency audit did not complete successfully")
    if audit_inventory(repo_root, command) != inventory:
        raise ValueError("dependency audit sources changed during execution")
    if command.argv[0] == "uv":
        report = admit_uv_report(result.stdout, inventory.packages)
        adverse_statuses = report.summary.adverse_statuses
    else:
        admit_npm_report(result.stdout, inventory.packages)
        adverse_statuses = 0
    if result.status != 0:
        raise ValueError("native dependency audit failed despite an apparently clean report")
    return {
        "state": "passed",
        "packageCount": len(inventory.packages),
        "sourceHashes": dict(inventory.source_hashes),
        "adverseStatusCount": adverse_statuses,
        "nonClaims": [
            "Package-count reconciliation does not identify every upstream vulnerability query.",
            "uv 0.12.17 audits all reachable groups without platform marker filtering.",
            "Registry findings do not prove absence of unknown vulnerabilities.",
        ],
    }


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    environment = audit_environment(os.environ)
    try:
        for command in commands(repo_root):
            inventory = audit_inventory(repo_root, command)
            result = spawn(
                command.argv[0],
                command.argv[1:],
                cwd=command.cwd,
                env=environment,
                max_buffer=16 * 1024 * 1024,
                timeout_seconds=600.0,
            )
            admitted = admit_audit_result(repo_root, command, result, inventory=inventory)
            print(json.dumps({"command": list(command.argv), **admitted}, sort_keys=True))
    except (OSError, ValueError, TypeError) as error:
        print(f"dependency audit admission failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

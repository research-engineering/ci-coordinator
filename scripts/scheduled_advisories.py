from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tomllib
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from scripts.bounded_git import run_git
from scripts.bounded_process import CommandResult, spawn
from scripts.ci_utility_checks import UtilityCommand, admit_output, project_environment
from scripts.ci_utility_checks import commands as utility_commands
from scripts.ci_utility_inventory import repository_paths, source_text
from scripts.command_sequence import Command
from scripts.dependency_audit import admit_audit_result, audit_environment, audit_inventory
from scripts.dependency_audit import commands as dependency_commands
from scripts.dependency_audit_sources import admit_npm_lock as admit_npm_lock
from scripts.release_predicate_admission import _load_strict_json, _timestamp
from scripts.release_publisher_identity import REPOSITORY as REPOSITORY
from scripts.release_publisher_identity import repository_id
from scripts.release_repair_admission import SOURCE_PATHS

SCOPES = ("python", "npm", "go", "released-image")
MAX_RECEIPT_AGE_SECONDS = 36 * 60 * 60
MAX_RECEIPT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
COMMAND_TIMEOUT_SECONDS = 600.0
SCHEMA = "ci-coordinator.scheduled-advisories/v1"


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def digest(content: str | bytes) -> str:
    return hashlib.sha256(content.encode() if isinstance(content, str) else content).hexdigest()


def execute(argv: tuple[str, ...], root: Path, environment: Mapping[str, str]) -> CommandResult:
    return spawn(
        argv[0],
        argv[1:],
        cwd=root,
        env=environment,
        max_buffer=MAX_OUTPUT_BYTES,
        timeout_seconds=COMMAND_TIMEOUT_SECONDS,
    )


def require_success(result: CommandResult) -> str:
    if result.error is not None or result.failure_kind is not None or result.status != 0:
        raise ValueError("native-command-failed")
    return result.stdout


def command_fact(argv: tuple[str, ...], result: CommandResult, *, cwd: str) -> dict[str, object]:
    identifiers = sorted(
        set(
            re.findall(
                r"\b(?:CVE-\d{4}-\d{4,}|GHSA-[a-z0-9]{4}-[a-z0-9]{4}-[a-z0-9]{4}|GO-\d{4}-\d{4,})\b",
                result.stdout + result.stderr,
            )
        )
    )
    return {
        "argv": list(argv),
        "cwd": cwd,
        "exitCode": result.status,
        "failureKind": result.failure_kind,
        "processError": result.error is not None,
        "stdoutSha256": digest(result.stdout),
        "stderrSha256": digest(result.stderr),
        "advisoryIds": identifiers[:32],
        "advisoryIdsTruncated": len(identifiers) > 32,
    }


def package_commands(root: Path, scope: str) -> tuple[UtilityCommand, ...]:
    if scope == "go":
        planned = utility_commands(root, "govulncheck")
    elif scope in {"python", "npm"}:
        planned = tuple(
            UtilityCommand(command.argv, command.cwd)
            for command in dependency_commands(root)
            if (command.argv[0] == "uv") == (scope == "python")
        )
    else:
        raise ValueError("unsupported-package-scope")
    if not planned:
        raise ValueError("empty-native-audit-plan")
    return planned


def input_digests(root: Path, scope: str) -> dict[str, str]:
    names = repository_paths(root)
    if scope == "python":
        selected = tuple(
            f"{project}/{name}"
            for project in ("backend", "tooling/quality")
            for name in ("pyproject.toml", "uv.lock")
        )
        for project in ("backend", "tooling/quality"):
            packages = tomllib.loads(source_text(root, f"{project}/uv.lock")).get("package")
            if not isinstance(packages, list) or not packages:
                raise ValueError("empty-python-lock")
    elif scope == "npm":
        admit_npm_lock(source_text(root, "pnpm-lock.yaml"))
        selected = (
            *(name for name in names if Path(name).name == "package.json"),
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
            ".npmrc",
        )
    elif scope == "go":
        selected = tuple(
            name
            for name in names
            if name.endswith(".go") or Path(name).name in {"go.mod", "go.sum"}
        )
    else:
        selected = (
            "docs/specs/ci-coordinator-release/vulnerability-gate-policy.v1.json",
            "docker/runtime/security/repaired-matches.v1.json",
            *sorted(SOURCE_PATHS),
        )
    if not selected:
        raise ValueError("empty-advisory-inputs")
    return {name: digest(source_text(root, name)) for name in sorted(selected)}


def new_receipt(root: Path, scope: str, source_commit: str) -> dict[str, object]:
    if scope not in SCOPES or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None:
        raise ValueError("invalid-advisory-subject")
    if run_git(root, ("rev-parse", "HEAD")).stdout.strip() != source_commit:
        raise ValueError("advisory-checkout-mismatch")
    return {
        "schemaVersion": SCHEMA,
        "repository": REPOSITORY,
        "scope": scope,
        "sourceCommit": source_commit,
        "startedAt": now(),
        "observedAt": None,
        "state": "failed",
        "inputs": {},
        "commands": [],
        "maxAgeSeconds": MAX_RECEIPT_AGE_SECONDS,
    }


def write_receipt(path: Path, receipt: dict[str, object]) -> None:
    raw = (json.dumps(receipt, sort_keys=True, indent=2) + "\n").encode()
    if len(raw) > MAX_RECEIPT_BYTES:
        raise ValueError("advisory-receipt-limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)


def admit_fresh_receipt(
    value: object,
    *,
    root: Path,
    scope: str,
    source_commit: str,
    observed_at: str,
    evidence_directory: Path | None = None,
) -> None:
    if not isinstance(value, dict) or any(
        value.get(key) != expected
        for key, expected in (
            ("schemaVersion", SCHEMA),
            ("repository", REPOSITORY),
            ("scope", scope),
            ("sourceCommit", source_commit),
            ("state", "passed"),
            ("maxAgeSeconds", MAX_RECEIPT_AGE_SECONDS),
        )
    ):
        raise ValueError("advisory-receipt-subject-or-result")
    if not isinstance(value.get("inputs"), dict) or not value["inputs"]:
        raise ValueError("advisory-receipt-empty-inputs")
    if any(
        not isinstance(item, str) or re.fullmatch(r"[0-9a-f]{64}", item) is None
        for item in value["inputs"].values()
    ):
        raise ValueError("advisory-receipt-input-hash")
    if not isinstance(value.get("commands"), list) or not value["commands"]:
        raise ValueError("advisory-receipt-empty-commands")
    for command in value["commands"]:
        if (
            not isinstance(command, dict)
            or type(command.get("exitCode")) is not int
            or command.get("exitCode") not in ({0, 2} if scope == "released-image" else {0})
            or (command.get("processError") is not False or command.get("failureKind") is not None)
            or command.get("admissionError") is not None
        ):
            raise ValueError("advisory-receipt-command-failed")
        if not isinstance(command.get("argv"), list) or not command["argv"]:
            raise ValueError("advisory-receipt-command-empty")
    started = _timestamp(value.get("startedAt"), "scan start")
    completed = _timestamp(value.get("observedAt"), "scan observation")
    if not timedelta(0) <= completed - started <= timedelta(minutes=30):
        raise ValueError("advisory-receipt-observation-window")
    age = _timestamp(observed_at, "current observation") - _timestamp(
        value.get("observedAt"), "scan observation"
    )
    if not timedelta(0) <= age <= timedelta(seconds=MAX_RECEIPT_AGE_SECONDS):
        raise ValueError("advisory-receipt-stale-or-future")
    if value["inputs"] != input_digests(root, scope):
        raise ValueError("advisory-receipt-inputs-mismatch")
    if scope == "released-image":
        from scripts.scheduled_release_scan import admit_scan_receipt

        if evidence_directory is None:
            raise ValueError("release-evidence-directory-required")
        admit_scan_receipt(root, value, evidence_directory)
    else:
        expected = [
            {"argv": list(command.argv), "cwd": command.cwd.relative_to(root).as_posix()}
            for command in package_commands(root, scope)
        ]
        actual = [{"argv": item["argv"], "cwd": item.get("cwd")} for item in value["commands"]]
        if actual != expected:
            raise ValueError("advisory-receipt-command-plan-mismatch")


def scan_packages(root: Path, scope: str, receipt: dict[str, object]) -> None:
    environment = project_environment(os.environ)
    environment.update(
        {
            "UV_NO_CACHE": "true",
            "npm_config_registry": "https://registry.npmjs.org",
        }
    )
    facts: list[dict[str, object]] = []
    receipt["commands"] = facts
    passed = True
    if scope == "go":
        planned = package_commands(root, scope)
        database_observations: list[str] = []
        for command in planned:
            result = execute(command.argv, command.cwd, environment)
            facts.append(
                command_fact(command.argv, result, cwd=command.cwd.relative_to(root).as_posix())
            )
            require_success(result)
            admit_output(command, result.stdout)
            if command.argv == ("govulncheck", "-version"):
                database_observations.extend(
                    re.findall(r"(?m)^DB updated: ([0-9: .+UTCZ-]{1,80})$", result.stdout)
                )
        receipt["feed"] = {
            "url": "https://vuln.go.dev",
            "versionCommandDatabaseTimestamps": database_observations,
            "scanSnapshotTimestamp": None,
        }
    else:
        environment = audit_environment(os.environ)
        for dependency_command in package_commands(root, scope):
            argv = dependency_command.argv
            native_command = Command(argv, dependency_command.cwd)
            inventory = audit_inventory(root, native_command)
            result = execute(argv, dependency_command.cwd, environment)
            fact = command_fact(
                argv, result, cwd=dependency_command.cwd.relative_to(root).as_posix()
            )
            facts.append(fact)
            try:
                admit_audit_result(root, native_command, result, inventory=inventory)
            except (ValueError, TypeError) as error:
                fact["admissionError"] = type(error).__name__
                passed = False
        receipt["feed"] = {
            "url": "https://api.osv.dev" if scope == "python" else "https://registry.npmjs.org",
            "globalTimestamp": None,
        }
    receipt["state"] = "passed" if passed else "failed"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Observe bounded native advisory checks.")
    parser.add_argument("scope", choices=(*SCOPES, "summarize"))
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipts", type=Path)
    options = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    if options.scope == "summarize":
        try:
            if options.receipts is None:
                raise ValueError("receipt-directory-required")
            for scope in SCOPES:
                _, value = _load_strict_json(
                    options.receipts / f"{scope}.json", label=scope, maximum=MAX_RECEIPT_BYTES
                )
                admit_fresh_receipt(
                    value,
                    root=root,
                    scope=scope,
                    source_commit=options.source_commit,
                    observed_at=now(),
                    evidence_directory=options.receipts,
                )
            write_receipt(
                options.output,
                {
                    "state": "passed",
                    "scopes": list(SCOPES),
                    "observedAt": now(),
                    "sourceCommit": options.source_commit,
                },
            )
            return 0
        except (OSError, ValueError):
            print("advisory coverage is failed, missing, invalid or stale", file=sys.stderr)
            return 1
    receipt = new_receipt(root, options.scope, options.source_commit)
    try:
        receipt["inputs"] = input_digests(root, options.scope)
        if options.scope == "released-image":
            from scripts.scheduled_release_scan import scan_release

            repository_id()
            scan_release(root, receipt, options.output.parent)
        else:
            scan_packages(root, options.scope, receipt)
        if input_digests(root, options.scope) != receipt["inputs"]:
            receipt["state"] = "failed"
            receipt["reason"] = "advisory-inputs-changed"
    except (OSError, ValueError):
        receipt["state"] = "failed"
        receipt["reason"] = "native-or-subject-admission-failed"
    receipt["observedAt"] = now()
    write_receipt(options.output, receipt)
    print(f"advisory scope={options.scope} state={receipt['state']}")
    return 0 if receipt["state"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

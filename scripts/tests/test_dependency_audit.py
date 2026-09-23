from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from scripts.bounded_process import CommandResult
from scripts.dependency_audit import (
    admit_audit_result,
    audit_environment,
    audit_inventory,
    commands,
)
from scripts.dependency_audit_reports import AuditFindingsError, admit_npm_report, admit_uv_report
from scripts.dependency_audit_sources import (
    admit_audit_configuration,
    npm_inventory,
    python_inventory,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGES = frozenset({("example", "1.0.0")})


def uv_report() -> dict[str, Any]:
    return {
        "schema": {"version": "preview"},
        "summary": {"audited_packages": 1, "vulnerabilities": 0, "adverse_statuses": 0},
        "vulnerabilities": [],
        "adverse_statuses": [],
    }


def npm_report() -> dict[str, Any]:
    return {
        "advisories": {},
        "metadata": {
            "vulnerabilities": {"info": 0, "low": 0, "moderate": 0, "high": 0, "critical": 0},
            "dependencies": 1,
            "devDependencies": 0,
            "optionalDependencies": 0,
            "totalDependencies": 1,
        },
    }


def _python_source(root: Path, *, source: str = 'registry = "https://pypi.org/simple"') -> None:
    backend = root / "backend"
    backend.mkdir()
    (backend / "pyproject.toml").write_text('[project]\nname = "ci-coordinator-backend"\n')
    (backend / "uv.lock").write_text(
        'version = 1\nrevision = 3\n[[package]]\nname = "ci-coordinator-backend"\n'
        'version = "0.1.0"\nsource = {editable = "."}\n'
        f'[[package]]\nname = "example"\nversion = "1.0.0"\nsource = {{{source}}}\n'
    )


def _npm_source(root: Path) -> None:
    (root / "pnpm-lock.yaml").write_text(
        "lockfileVersion: '9.0'\nimporters:\n  .:\n    dependencies:\n"
        "      example: {specifier: '1.0.0', version: '1.0.0'}\n"
        "packages:\n  example@1.0.0:\n    resolution: {integrity: sha512-owned}\n"
        "snapshots:\n  example@1.0.0: {}\n"
    )


@pytest.mark.parametrize(
    "source",
    [
        'git = "https://example.invalid/repo"',
        'editable = "../other"',
        'directory = "../other"',
        'url = "https://example.invalid/pkg.whl"',
        'registry = "https://other.invalid/simple"',
    ],
)
def test_python_audit_rejects_unexpected_source(tmp_path: Path, source: str) -> None:
    _python_source(tmp_path, source=source)
    with pytest.raises(ValueError, match="unadmitted source"):
        python_inventory(tmp_path, "backend")


def test_python_audit_rejects_unversioned_package(tmp_path: Path) -> None:
    _python_source(tmp_path)
    lock = tmp_path / "backend/uv.lock"
    lock.write_text(lock.read_text().replace('version = "1.0.0"\n', ""))
    with pytest.raises(ValueError, match="identity is missing"):
        python_inventory(tmp_path, "backend")


def test_native_source_inventories_include_every_registry_lock_entry() -> None:
    assert python_inventory(REPO_ROOT, "backend")
    assert python_inventory(REPO_ROOT, "tooling/quality")
    assert ("pnpm", "12.5.1") in npm_inventory(REPO_ROOT)
    admit_audit_configuration(REPO_ROOT)


@pytest.mark.parametrize("mutation", ["tarball", "local-importer", "unversioned", "empty"])
def test_npm_audit_rejects_unexpected_sources(tmp_path: Path, mutation: str) -> None:
    _npm_source(tmp_path)
    lock = tmp_path / "pnpm-lock.yaml"
    content = lock.read_text()
    if mutation == "tarball":
        content = content.replace(
            "integrity: sha512-owned", "tarball: https://other.invalid/pkg.tgz"
        )
    elif mutation == "local-importer":
        content = content.replace("version: '1.0.0'", "version: 'link:../other'")
    elif mutation == "unversioned":
        content = content.replace("example@1.0.0", "example@unversioned")
    else:
        content = ""
    lock.write_text(content)
    with pytest.raises(ValueError):
        npm_inventory(tmp_path)


@pytest.mark.parametrize("content", ["", "clean", "{}", '{"x":1,"x":2}', '{"x":NaN}'])
def test_empty_or_malformed_audit_reports_are_not_success(content: str) -> None:
    for admit in (admit_uv_report, admit_npm_report):
        with pytest.raises(ValueError):
            admit(content, PACKAGES)


@pytest.mark.parametrize("mutation", ["zero", "missing", "boolean", "schema", "skip", "counter"])
def test_uv_report_requires_actual_schema_and_nonempty_scope(mutation: str) -> None:
    report = uv_report()
    if mutation == "zero":
        report["summary"]["audited_packages"] = 0
    elif mutation == "missing":
        del report["vulnerabilities"]
    elif mutation == "boolean":
        report["summary"]["audited_packages"] = True
    elif mutation == "schema":
        report["schema"]["version"] = "future"
    elif mutation == "skip":
        report["skipped"] = ["example"]
    else:
        report["summary"]["vulnerabilities"] = 1
    with pytest.raises(ValueError):
        admit_uv_report(json.dumps(report), PACKAGES)


def test_uv_rejects_vulnerability_even_if_process_reports_success() -> None:
    report = uv_report()
    report["summary"]["vulnerabilities"] = 1
    report["vulnerabilities"] = [
        {
            "dependency": {"name": "example", "version": "1.0.0"},
            "id": "GHSA-abcd-1234-efgh",
            "display_id": "GHSA-abcd-1234-efgh",
            "aliases": [],
            "summary": None,
            "description": None,
            "link": None,
            "fix_versions": [],
            "published": None,
            "modified": None,
        }
    ]
    with pytest.raises(AuditFindingsError, match="unaccepted vulnerabilities"):
        admit_uv_report(json.dumps(report), PACKAGES)


def test_uv_preserves_native_nonblocking_adverse_status_policy() -> None:
    report = uv_report()
    report["adverse_statuses"] = [{"name": "example", "status": "deprecated", "reason": None}]
    report["summary"]["adverse_statuses"] = 1
    assert admit_uv_report(json.dumps(report), PACKAGES).summary.adverse_statuses == 1


def test_npm_classifies_a_valid_critical_finding_separately_from_scanner_error() -> None:
    report = npm_report()
    report["advisories"] = {
        "123": {
            "findings": [
                {
                    "version": "1.0.0",
                    "paths": [".>example"],
                    "dev": False,
                    "optional": False,
                    "bundled": False,
                }
            ],
            "id": 123,
            "title": "Synthetic finding",
            "module_name": "example",
            "vulnerable_versions": "<2.0.0",
            "patched_versions": ">=2.0.0",
            "severity": "critical",
            "cwe": "",
            "github_advisory_id": "GHSA-abcd-1234-efgh",
            "url": "https://github.com/advisories/GHSA-abcd-1234-efgh",
        }
    }
    report["metadata"]["vulnerabilities"]["critical"] = 1
    with pytest.raises(AuditFindingsError):
        admit_npm_report(json.dumps(report), PACKAGES)


def test_npm_preserves_high_threshold_and_rejects_hidden_high_findings() -> None:
    report = npm_report()
    report["metadata"]["vulnerabilities"]["low"] = 3
    admit_npm_report(json.dumps(report), PACKAGES)
    report["metadata"]["vulnerabilities"]["high"] = 1
    with pytest.raises(ValueError, match="omitted"):
        admit_npm_report(json.dumps(report), PACKAGES)


@pytest.mark.parametrize("mutation", ["empty", "foreign-field", "counter", "boolean"])
def test_npm_report_rejects_incomplete_scope_and_unknown_skip_shape(mutation: str) -> None:
    report = npm_report()
    if mutation == "empty":
        report["metadata"]["totalDependencies"] = 0
    elif mutation == "foreign-field":
        report["ignored"] = ["GHSA-abcd-1234-efgh"]
    elif mutation == "counter":
        report["metadata"]["devDependencies"] = 2
    else:
        report["metadata"]["vulnerabilities"]["high"] = False
    with pytest.raises(ValueError):
        admit_npm_report(json.dumps(report), PACKAGES)


@pytest.mark.parametrize(
    "mutation", ["status", "absent-status", "timeout", "sources", "invalid-report"]
)
def test_audit_result_rejects_process_or_source_failure(tmp_path: Path, mutation: str) -> None:
    _python_source(tmp_path)
    command = commands(tmp_path)[0]
    inventory = audit_inventory(tmp_path, command)
    result = CommandResult(0, json.dumps(uv_report()), "")
    if mutation == "status":
        result = CommandResult(1, result.stdout, "service unavailable")
    elif mutation == "absent-status":
        result = CommandResult(None, result.stdout, "")
    elif mutation == "timeout":
        result = CommandResult(0, result.stdout, "", failure_kind="timeout")
    elif mutation == "sources":
        path = tmp_path / "backend/uv.lock"
        path.write_text(path.read_text() + "\n")
    else:
        result = CommandResult(0, "{}", "")
    with pytest.raises(ValueError):
        admit_audit_result(tmp_path, command, result, inventory=inventory)


def test_audit_environment_cannot_inherit_skip_flags_or_credentials() -> None:
    env = audit_environment(
        {
            "PATH": "/bin",
            "UV_NO_DEV": "true",
            "NPM_CONFIG_PRODUCTION": "true",
            "NPM_TOKEN": "synthetic",
            "GITHUB_TOKEN": "synthetic",
            "UV_SERVICE_URL": "https://other.invalid",
        }
    )
    assert set(env) == {
        "PATH",
        "UV_NO_CACHE",
        "NPM_CONFIG_USERCONFIG",
        "NPM_CONFIG_GLOBALCONFIG",
        "npm_config_registry",
    }


@pytest.mark.parametrize("change", ["workspace-ignore", "rc-production", "package-ignore"])
def test_audit_suppression_requires_owned_admission(tmp_path: Path, change: str) -> None:
    for relative in ("pnpm-workspace.yaml", ".npmrc", "package.json", "frontend/package.json"):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((REPO_ROOT / relative).read_bytes())
    if change == "workspace-ignore":
        path = tmp_path / "pnpm-workspace.yaml"
        path.write_text(path.read_text() + "\naudit:\n  ignore: [GHSA-abcd-1234-efgh]\n")
    elif change == "rc-production":
        (tmp_path / ".npmrc").write_text("engine-strict=true\nproduction=true\n")
    else:
        path = tmp_path / "package.json"
        data = deepcopy(json.loads(path.read_text()))
        data["pnpm"] = {"auditConfig": {"ignoreGhsas": ["GHSA-abcd-1234-efgh"]}}
        path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        admit_audit_configuration(tmp_path)

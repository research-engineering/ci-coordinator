from __future__ import annotations

import hashlib
import json
import tomllib
from pathlib import Path

import pytest

from ci_coordinator.runtime_settings import (
    EntrypointDispositionRejection,
    admit_entrypoint_disposition,
    load_bundled_caller_inventory,
    load_bundled_entrypoint_disposition,
    parse_entrypoint_disposition,
)


def test_entrypoint_disposition_binds_every_runtime_authority_to_a_caller() -> None:
    callers = load_bundled_caller_inventory()
    disposition = load_bundled_entrypoint_disposition(callers)
    caller_by_id = {caller.caller_id: caller for caller in callers.records}
    runtime_records = [
        record for record in disposition.records if record.disposition == "runtime_authority"
    ]

    assert {record.caller_id for record in runtime_records} == set(caller_by_id)
    for record in runtime_records:
        assert record.caller_id is not None
        caller = caller_by_id[record.caller_id]
        assert (record.path, record.kind, record.current_target) == (
            caller.path,
            caller.kind,
            caller.current_target,
        )


def test_entrypoint_disposition_exhaustively_covers_the_declared_source_profile() -> None:
    disposition = load_bundled_entrypoint_disposition(load_bundled_caller_inventory())
    records = {record.source_id: record for record in disposition.records}
    profile = disposition.discovery

    assert (
        tuple(sorted(path.name for path in _repo_path("").glob("Dockerfile*") if path.is_file()))
        == profile.container_files
    )
    for path, command_source, health_source, expected_command, expected_target in (
        (
            "Dockerfile",
            "container.command",
            "container.healthcheck",
            'CMD ["python", "-m", "ci_coordinator.runtime"]',
            "python -m ci_coordinator.runtime",
        ),
    ):
        docker_lines = _repo_path(path).read_text(encoding="utf-8").splitlines()
        commands = [line for line in docker_lines if line.startswith(("CMD ", "ENTRYPOINT "))]
        health_checks = [line for line in docker_lines if line.startswith("HEALTHCHECK ")]
        assert commands == [expected_command]
        assert len(health_checks) == 1 and " CMD " in health_checks[0]
        assert records[command_source].current_target == expected_target
        assert records[health_source].current_target == health_checks[0].split(" CMD ", 1)[1]
    lint_record = records["container.workflow-lint"]
    assert lint_record.member_ids == ("Dockerfile.workflow-lint",)
    assert lint_record.current_target == (
        "sha256:" + hashlib.sha256(_repo_path("Dockerfile.workflow-lint").read_bytes()).hexdigest()
    )

    assert profile.python_project_manifests == ("backend/pyproject.toml",)
    pyproject = tomllib.loads(
        _repo_path(profile.python_project_manifests[0]).read_text(encoding="utf-8")
    )
    expected_scripts = {
        "ci-coordinator-audit-replay": "ci_coordinator.replay_cli:main",
        "ci-coordinator-consumer-lab": "ci_coordinator.consumer_contract_lab.bootstrap:main",
        "ci-coordinator-database-access": "ci_coordinator.database_access_cli:main",
        "ci-coordinator-target-artifacts": "ci_coordinator.target_artifacts.cli:main",
        "ci-coordinator-target-authority-evidence": (
            "ci_coordinator.target_authority_evidence.cli:main"
        ),
    }
    assert pyproject["project"]["scripts"] == expected_scripts
    console_records = {
        record.member_ids[0]: record
        for record in disposition.records
        if record.kind == "python_console_script"
    }
    assert console_records.keys() == expected_scripts.keys()
    for name, target in expected_scripts.items():
        record = console_records[name]
        assert record.path == "backend/pyproject.toml"
        assert record.current_target == f"{name} = {target}"
    assert profile.python_module_entrypoints == ("backend/src/ci_coordinator/runtime/__main__.py",)
    assert _repo_path(profile.python_module_entrypoints[0]).is_file()

    workflow_paths = tuple(
        sorted(
            path.relative_to(_repo_path("")).as_posix()
            for directory in profile.workflow_directories
            for path in _repo_path(directory).rglob("*")
            if path.is_file() and path.suffix in {".yaml", ".yml"}
        )
    )
    workflow_record = records["workflow.documents"]
    assert workflow_record.member_ids == workflow_paths
    assert workflow_record.current_target == _sha256_json(
        {path: hashlib.sha256(_repo_path(path).read_bytes()).hexdigest() for path in workflow_paths}
    )
    deployment_paths = tuple(
        sorted(
            path.relative_to(_repo_path("")).as_posix()
            for directory in profile.deployment_directories
            for path in _repo_path(directory).rglob("*")
            if path.is_file()
        )
    )
    deployment_record = records["deployment.documents"]
    assert deployment_record.member_ids == deployment_paths
    assert deployment_record.current_target == _sha256_json(
        {
            path: hashlib.sha256(_repo_path(path).read_bytes()).hexdigest()
            for path in deployment_paths
        }
    )
    root_deployment_paths = tuple(
        path for path in profile.root_deployment_files if _repo_path(path).is_file()
    )
    root_deployment_record = records["deployment.root-files"]
    assert root_deployment_record.member_ids == root_deployment_paths
    assert root_deployment_record.current_target == _sha256_json(
        {
            path: hashlib.sha256(_repo_path(path).read_bytes()).hexdigest()
            for path in root_deployment_paths
        }
    )
    generated_paths = tuple(
        sorted(
            path.relative_to(_repo_path("")).as_posix()
            for directory in profile.generated_entrypoint_directories
            for path in _repo_path(directory).rglob("*")
            if path.is_file()
        )
    )
    generated_record = records["generated.documents"]
    assert profile.generated_entrypoint_directories == (
        ".ci-coordinator",
        "generated",
        "scripts/generated",
    )
    assert ".ci-coordinator/ci-coordinator.cjs" in generated_paths
    assert generated_record.kind == "generated_entrypoint"
    assert generated_record.disposition == "non_runtime"
    assert generated_record.caller_id is None
    assert generated_record.member_ids == generated_paths
    assert generated_record.current_target == _sha256_json(
        {
            path: hashlib.sha256(_repo_path(path).read_bytes()).hexdigest()
            for path in generated_paths
        }
    )


def test_entrypoint_disposition_rejects_unknown_and_unmatched_runtime_sources() -> None:
    callers = load_bundled_caller_inventory()
    document = json.loads(
        _repo_path(
            "backend/src/ci_coordinator/runtime_settings/resources/"
            "runtime-entrypoint-disposition.v1.json"
        ).read_text(encoding="utf-8")
    )
    document["sources"][0]["disposition"] = "unknown"
    document["sources"][0]["callerId"] = None

    assert admit_entrypoint_disposition(document, callers) == EntrypointDispositionRejection(
        "unknown_entrypoint_source", "container.command"
    )

    document["sources"][0]["disposition"] = "runtime_authority"
    document["sources"][0]["callerId"] = "missing"
    assert admit_entrypoint_disposition(document, callers) == EntrypointDispositionRejection(
        "unmatched_runtime_authority", "callers"
    )


def test_entrypoint_disposition_rejects_duplicate_json_keys_before_admission() -> None:
    raw_disposition = (
        b'{"schemaVersion":"ci-coordinator-runtime-entrypoint-disposition/v1",'
        b'"dispositionId":"ci-coordinator/runtime-entrypoint-disposition/v1",'
        b'"discovery":{},"sources":[],"sources":[]}'
    )

    with pytest.raises(ValueError, match="duplicate-free"):
        parse_entrypoint_disposition(raw_disposition, load_bundled_caller_inventory())


def _repo_path(name: str) -> Path:
    return Path(__file__).parents[4] / name


def _sha256_json(value: object) -> str:
    serialized = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return f"sha256:{hashlib.sha256(serialized.encode()).hexdigest()}"

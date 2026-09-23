from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PYTHON_ENTRYPOINT = ("-m", "scripts.container_runtime_smoke")
FAKE_DOCKER = REPO_ROOT / "scripts" / "tests" / "fake_docker.py"
_RESOURCE_PATTERN = re.compile(
    r"(ci-coordinator(?::python-runtime-smoke|-python-runtime-smoke))-\d+-[0-9a-f]{16}"
)

EXPECTED_SUCCESS_STDOUT = (
    "{\n"
    '  "migrationHead": "20260915_0015 (head)",\n'
    '  "migrationRevisions": [\n'
    '    "20260716_0001_initial_schema.py",\n'
    '    "20260901_0002_config_epoch_registration_operations.py",\n'
    '    "20260904_0003_ci_economics_evidence.py",\n'
    '    "20260906_0004_retire_pre_cutover_capabilities.py",\n'
    '    "20260906_0005_production_generation_cutover.py",\n'
    '    "20260907_0006_retire_economics_evidence_v1.py",\n'
    '    "20260908_0007_source_aware_economics.py",\n'
    '    "20260909_0008_retire_economics_evidence_v2.py",\n'
    '    "20260909_0009_persistent_economics_budgets.py",\n'
    '    "20260910_0010_repository_observation.py",\n'
    '    "20260912_0011_actions_history.py",\n'
    '    "20260913_0012_administrator_activity.py",\n'
    '    "20260913_0013_analytics_purpose.py",\n'
    '    "20260915_0014_retire_economics_evidence_v3.py",\n'
    '    "20260915_0015_total_collection_state.py"\n'
    "  ],\n"
    '  "operatorUiBundle": "admitted",\n'
    '  "pythonVersion": "Python 3.13.15",\n'
    '  "runtimeUserId": "10001",\n'
    '  "nonClaims": [\n'
    '    "This smoke proves one local disabled-mode Python image build, startup, '
    "liveness response, admitted operator UI bundle, and self-contained "
    'migration artifact.",\n'
    '    "It does not prove registry publication, signatures, multi-architecture '
    'equivalence, rollout, or deployment readiness."\n'
    "  ],\n"
    '  "reportId": "ci-coordinator.python-container-runtime-smoke",\n'
    '  "reportKind": "ci-coordinator.python-container-runtime-smoke",\n'
    '  "schemaVersion": 1,\n'
    '  "state": "passed"\n'
    "}\n"
)

EXPECTED_STDERR = {
    "success": "",
    "build_failure": "Python runtime container image build failed: build failed on stdout\n",
    "startup_failure": "Python runtime container startup failed: startup failed\n",
    "unhealthy_cleanup_failure": (
        "container log stdout\n"
        "container log stderr\n"
        "container became unhealthy; container cleanup failed: container cleanup exploded; "
        "image cleanup failed: image cleanup exploded\n"
    ),
    "wrong_version": (
        "container log stdout\ncontainer log stderr\nunexpected Python version: Python 3.13.14\n"
    ),
    "health_failure": (
        "container log stdout\n"
        "container log stderr\n"
        "health endpoint failed: health request failed\n"
    ),
    "operator_ui_failure": (
        "container log stdout\n"
        "container log stderr\n"
        "operator UI bundle failed: operator UI request failed\n"
    ),
    "migration_cli_failure": (
        "container log stdout\ncontainer log stderr\nmigration CLI failed: migration CLI failed\n"
    ),
    "migration_head_failure": (
        "container log stdout\n"
        "container log stderr\n"
        "unexpected migration head: 20991231_9999 (head)\n"
    ),
    "migration_artifact_failure": (
        "container log stdout\n"
        "container log stderr\n"
        "migration artifact failed: migration artifact missing\n"
    ),
    "root_user": (
        "container log stdout\ncontainer log stderr\nruntime container must use UID/GID 10001\n"
    ),
    "wrong_user": (
        "container log stdout\ncontainer log stderr\nruntime container must use UID/GID 10001\n"
    ),
    "wrong_group": (
        "container log stdout\ncontainer log stderr\nruntime container must use UID/GID 10001\n"
    ),
}


@dataclass(frozen=True, slots=True)
class Invocation:
    args: tuple[str, ...]
    cwd: str


@dataclass(frozen=True, slots=True)
class SmokeRun:
    process: subprocess.CompletedProcess[str]
    invocations: tuple[Invocation, ...]


def _read_invocations(path: Path) -> tuple[Invocation, ...]:
    records: list[Invocation] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value: object = json.loads(line)
        if not isinstance(value, dict):
            raise TypeError("fake Docker record must be an object")
        record = cast(dict[str, object], value)
        raw_args = record.get("args")
        cwd = record.get("cwd")
        if (
            not isinstance(raw_args, list)
            or not all(isinstance(item, str) for item in raw_args)
            or not isinstance(cwd, str)
        ):
            raise AssertionError("fake Docker record has an invalid shape")
        records.append(Invocation(args=tuple(cast(list[str], raw_args)), cwd=cwd))
    return tuple(records)


def _run_smoke(
    command: list[str],
    *,
    scenario: str,
    state_dir: Path,
) -> SmokeRun:
    state_dir.mkdir()
    environment = os.environ.copy()
    environment.update(
        {
            "CI_COORDINATOR_DOCKER_BIN": str(FAKE_DOCKER),
            "FAKE_DOCKER_SCENARIO": scenario,
            "FAKE_DOCKER_STATE_DIR": str(state_dir),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    process = subprocess.run(
        command,
        cwd=REPO_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=15,
        check=False,
    )
    return SmokeRun(
        process=process,
        invocations=_read_invocations(state_dir / "calls.jsonl"),
    )


def _normalize(invocations: tuple[Invocation, ...]) -> tuple[Invocation, ...]:
    return tuple(
        Invocation(
            args=tuple(_RESOURCE_PATTERN.sub(r"\1-<suffix>", arg) for arg in call.args),
            cwd=call.cwd,
        )
        for call in invocations
    )


@pytest.mark.parametrize(
    "scenario",
    [
        "success",
        "build_failure",
        "startup_failure",
        "unhealthy_cleanup_failure",
        "wrong_version",
        "health_failure",
        "operator_ui_failure",
        "migration_cli_failure",
        "migration_head_failure",
        "migration_artifact_failure",
        "root_user",
        "wrong_user",
        "wrong_group",
    ],
)
def test_python_entrypoint_preserves_admitted_outcomes(tmp_path: Path, scenario: str) -> None:
    run = _run_smoke(
        [sys.executable, *PYTHON_ENTRYPOINT],
        scenario=scenario,
        state_dir=tmp_path / "python",
    )

    assert run.process.stderr == EXPECTED_STDERR[scenario]
    if scenario == "success":
        assert run.process.stdout == EXPECTED_SUCCESS_STDOUT
        assert run.process.returncode == 0
    else:
        assert run.process.stdout == ""
        assert run.process.returncode == 1


def test_python_entrypoint_preserves_docker_and_http_argv(tmp_path: Path) -> None:
    result = _run_smoke(
        [sys.executable, *PYTHON_ENTRYPOINT],
        scenario="success",
        state_dir=tmp_path / "python",
    )

    calls = _normalize(result.invocations)
    image = "ci-coordinator:python-runtime-smoke-<suffix>"
    container = "ci-coordinator-python-runtime-smoke-<suffix>"
    health_check = (
        "import http.client,json; "
        "connection=http.client.HTTPConnection('127.0.0.1',3080,timeout=5); "
        "connection.request('GET','/healthz'); response=connection.getresponse(); "
        "payload=json.loads(response.read()); connection.close(); "
        "assert response.status == 200 and payload == {'ok': True, 'status': 'alive'}"
    )
    operator_ui_check = (
        "from fastapi import FastAPI; "
        "from ci_coordinator.api.http.operator_ui import "
        "bundled_operator_ui_directory,mount_operator_ui; "
        "routes=mount_operator_ui(FastAPI(),bundled_operator_ui_directory()); "
        "paths={getattr(route,'path',None) for route in routes}; "
        "assert paths == {'/','/assets','/workbench','/workbench/'}"
    )
    migration_artifact_check = (
        "from pathlib import Path; "
        "root=Path('/app/backend'); "
        "assert (root/'alembic.ini').is_file(); "
        "versions=sorted(path.name for path in (root/'alembic/versions').glob('*.py') "
        "if path.name != '__init__.py'); "
        "assert versions == ['20260716_0001_initial_schema.py', "
        "'20260901_0002_config_epoch_registration_operations.py', "
        "'20260904_0003_ci_economics_evidence.py', "
        "'20260906_0004_retire_pre_cutover_capabilities.py', "
        "'20260906_0005_production_generation_cutover.py', "
        "'20260907_0006_retire_economics_evidence_v1.py', "
        "'20260908_0007_source_aware_economics.py', "
        "'20260909_0008_retire_economics_evidence_v2.py', "
        "'20260909_0009_persistent_economics_budgets.py', "
        "'20260910_0010_repository_observation.py', "
        "'20260912_0011_actions_history.py', "
        "'20260913_0012_administrator_activity.py', "
        "'20260913_0013_analytics_purpose.py', "
        "'20260915_0014_retire_economics_evidence_v3.py', "
        "'20260915_0015_total_collection_state.py']"
    )
    expected_args = (
        ("build", "--pull", "--file", "Dockerfile", "--tag", image, "."),
        (
            "run",
            "--detach",
            "--name",
            container,
            "--env",
            "CI_COORDINATOR_RUNTIME_MODE=disabled",
            "--env",
            "CI_COORDINATOR_BIND_HOST=0.0.0.0",
            "--env",
            "CI_COORDINATOR_BIND_PORT=3080",
            "--env",
            "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS=30",
            image,
        ),
        ("inspect", "--format", "{{.State.Health.Status}}", container),
        ("exec", container, "python", "--version"),
        (
            "exec",
            container,
            "python",
            "-c",
            "import os; print(os.getuid(), os.getgid())",
        ),
        ("exec", container, "python", "-c", health_check),
        ("exec", container, "python", "-c", operator_ui_check),
        ("exec", container, "alembic", "--help"),
        ("exec", container, "alembic", "heads"),
        ("exec", container, "python", "-c", migration_artifact_check),
        ("rm", "--force", container),
        ("image", "rm", "--force", image),
    )

    assert tuple(call.args for call in calls) == expected_args
    assert all(call.cwd == str(REPO_ROOT) for call in calls)
    assert result.process.stdout == EXPECTED_SUCCESS_STDOUT
    assert result.process.stderr == ""
    assert result.process.returncode == 0

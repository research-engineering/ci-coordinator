from __future__ import annotations

from scripts.container_smoke_runner import (
    ContainerRunContext,
    ContainerSmokeProfile,
    InspectionContext,
    JsonValue,
    run_container_smoke,
)

_HEALTH_CHECK = (
    "import http.client,json; "
    "connection=http.client.HTTPConnection('127.0.0.1',3080,timeout=5); "
    "connection.request('GET','/healthz'); response=connection.getresponse(); "
    "payload=json.loads(response.read()); connection.close(); "
    "assert response.status == 200 and payload == {'ok': True, 'status': 'alive'}"
)
_OPERATOR_UI_BUNDLE_CHECK = (
    "from fastapi import FastAPI; "
    "from ci_coordinator.api.http.operator_ui import "
    "bundled_operator_ui_directory,mount_operator_ui; "
    "routes=mount_operator_ui(FastAPI(),bundled_operator_ui_directory()); "
    "paths={getattr(route,'path',None) for route in routes}; "
    "assert paths == {'/','/assets','/workbench','/workbench/'}"
)
_MIGRATION_REVISIONS = (
    "20260716_0001_initial_schema.py",
    "20260901_0002_config_epoch_registration_operations.py",
    "20260904_0003_ci_economics_evidence.py",
    "20260906_0004_retire_pre_cutover_capabilities.py",
    "20260906_0005_production_generation_cutover.py",
    "20260907_0006_retire_economics_evidence_v1.py",
    "20260908_0007_source_aware_economics.py",
    "20260909_0008_retire_economics_evidence_v2.py",
    "20260909_0009_persistent_economics_budgets.py",
    "20260910_0010_repository_observation.py",
    "20260912_0011_actions_history.py",
    "20260913_0012_administrator_activity.py",
    "20260913_0013_analytics_purpose.py",
    "20260915_0014_retire_economics_evidence_v3.py",
    "20260915_0015_total_collection_state.py",
)
_MIGRATION_HEAD = "20260915_0015 (head)"
_MIGRATION_ARTIFACT_CHECK = (
    "from pathlib import Path; "
    "root=Path('/app/backend'); "
    "assert (root/'alembic.ini').is_file(); "
    "versions=sorted(path.name for path in (root/'alembic/versions').glob('*.py') "
    "if path.name != '__init__.py'); "
    f"assert versions == {list(_MIGRATION_REVISIONS)!r}"
)


def _build_args(image: str) -> tuple[str, ...]:
    return ("build", "--pull", "--file", "Dockerfile", "--tag", image, ".")


def _run_args(context: ContainerRunContext) -> tuple[str, ...]:
    return (
        "run",
        "--detach",
        "--name",
        context.container,
        "--env",
        "CI_COORDINATOR_RUNTIME_MODE=disabled",
        "--env",
        "CI_COORDINATOR_BIND_HOST=0.0.0.0",
        "--env",
        "CI_COORDINATOR_BIND_PORT=3080",
        "--env",
        "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS=30",
        context.image,
    )


def _inspect(context: InspectionContext) -> dict[str, JsonValue]:
    python_version = context.output(
        ["exec", context.container, "python", "--version"],
        "Python identity",
    )
    user_identity = context.output(
        [
            "exec",
            context.container,
            "python",
            "-c",
            "import os; print(os.getuid(), os.getgid())",
        ],
        "runtime user identity",
    )
    if python_version != "Python 3.13.15":
        raise RuntimeError(f"unexpected Python version: {python_version}")
    if user_identity != "10001 10001":
        raise RuntimeError("runtime container must use UID/GID 10001")
    context.run(
        ["exec", context.container, "python", "-c", _HEALTH_CHECK],
        "health endpoint",
    )
    context.run(
        ["exec", context.container, "python", "-c", _OPERATOR_UI_BUNDLE_CHECK],
        "operator UI bundle",
    )
    context.run(
        ["exec", context.container, "alembic", "--help"],
        "migration CLI",
    )
    migration_head = context.output(
        ["exec", context.container, "alembic", "heads"],
        "migration head",
    )
    if migration_head != _MIGRATION_HEAD:
        raise RuntimeError(f"unexpected migration head: {migration_head}")
    context.run(
        ["exec", context.container, "python", "-c", _MIGRATION_ARTIFACT_CHECK],
        "migration artifact",
    )
    return {
        "migrationHead": migration_head,
        "migrationRevisions": list(_MIGRATION_REVISIONS),
        "operatorUiBundle": "admitted",
        "pythonVersion": python_version,
        "runtimeUserId": "10001",
    }


_PROFILE = ContainerSmokeProfile(
    build_args=_build_args,
    container_prefix="ci-coordinator-python-runtime-smoke",
    image_prefix="ci-coordinator:python-runtime-smoke",
    inspect=_inspect,
    label="Python runtime container",
    non_claims=(
        (
            "This smoke proves one local disabled-mode Python image build, startup, "
            "liveness response, admitted operator UI bundle, and self-contained "
            "migration artifact."
        ),
        (
            "It does not prove registry publication, signatures, multi-architecture "
            "equivalence, rollout, or deployment readiness."
        ),
    ),
    report_id="ci-coordinator.python-container-runtime-smoke",
    run_args=_run_args,
)


def main() -> int:
    return 0 if run_container_smoke(_PROFILE) else 1


if __name__ == "__main__":
    raise SystemExit(main())

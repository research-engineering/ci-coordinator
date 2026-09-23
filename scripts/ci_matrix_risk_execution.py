from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from scripts.ci_matrix_contract import load_matrix
from scripts.ci_test_execution import UNIVERSE
from scripts.proofkit_common import JsonObject, as_array, as_object, read_json_object
from scripts.self_ci_source import NATIVE_PATH, admit_native_source

EXECUTION_INPUTS = frozenset(
    {
        NATIVE_PATH,
        "frontend/package.json",
        "frontend/vitest.config.ts",
        "frontend/playwright.config.ts",
        "scripts/ci_test_execution.py",
        "scripts/ci_test_plan.py",
        "scripts/ci_test_report.py",
        "scripts/python_coverage_diagnostics.py",
        "scripts/browser_test_admission.py",
        "scripts/browser_test_execution.py",
        "frontend/connected.playwright.config.ts",
        "docker/ci/connected-administrator.compose.yaml",
        "docker/ci/connected-browser.Dockerfile",
        "scripts/ci_business_witness/__init__.py",
        "scripts/ci_business_witness/__main__.py",
        "scripts/ci_business_witness/attached_logs.py",
        "scripts/ci_business_witness/consumers.py",
        "scripts/ci_business_witness/diagnostics.py",
        "scripts/ci_business_witness/fixture.py",
        "scripts/ci_business_witness/lifecycle.py",
        "scripts/ci_business_witness/oracle.py",
        "scripts/ci_business_witness/runner.py",
        "scripts/ci_business_witness/storage.py",
        "scripts/python_witness.py",
    }
)
_PYTHON_COMMANDS = frozenset({"python.test", "python.persistence-test", "runtime026.test"})
_NATIVE_STEPS = {
    "native-test-plan": (
        "backend/.venv/bin/python -m scripts.ci_test_execution plan --plan .ci-native/plan.json "
        "--diagnostics .ci-native/diagnostics/plan",
    ),
    "native-test-shards": (
        "timeout --signal=INT --kill-after=5s 960s "
        "backend/.venv/bin/python -m scripts.ci_test_execution run "
        '--plan .ci-native/plan.json --shard "$NATIVE_SHARD" '
        '--output ".ci-native/shard-$NATIVE_SHARD" '
        '--diagnostics ".ci-native/diagnostics/$NATIVE_SHARD"',
    ),
    "postgres-witness": (
        "backend/.venv/bin/python -m scripts.ci_test_execution combine "
        "--plan .ci-native/plan.json --artifacts .ci-native/shards --output .ci-native/aggregate",
    ),
    "operator-workbench": (
        "pnpm --filter @ci-coordinator/operator-ui check\n"
        "pnpm --filter @ci-coordinator/operator-ui build",
        "backend/.venv/bin/python -m scripts.frontend_bundle\n"
        "backend/.venv/bin/python -m scripts.browser_test_execution --output .ci-native/browser",
    ),
    "connected-administrator": ("backend/.venv/bin/python -m scripts.ci_business_witness",),
}


def native_execution_owner(
    root: Path, workflow_source: bytes
) -> tuple[JsonObject, Mapping[str, tuple[str, ...]]]:
    profile, quality = load_matrix(root)
    if profile.nativeWorkflow != NATIVE_PATH:
        raise ValueError("risk execution profile has no admitted native owner")
    native = admit_native_source(workflow_source)
    package = read_json_object(root / "frontend/package.json")
    scripts = as_object(package["scripts"], "frontend scripts")
    expected_scripts = {
        "check": "pnpm run lint && tsc -b && pnpm run test:coverage",
        "lint": (
            "biome check . && pnpm run lint:promises && pnpm run lint:promises:contract"
            " && pnpm run lint:transport:contract"
        ),
        "lint:promises": "eslint src tests dev --max-warnings=0",
        "lint:promises:contract": "node tools/promise-lint-contract.mjs",
        "lint:transport:contract": "node tools/transport-lint-contract.mjs",
        "test:coverage": "vitest run --coverage.enabled=true",
    }
    if any(scripts.get(key) != value for key, value in expected_scripts.items()):
        raise ValueError("risk frontend execution chain needs owner admission")
    for job_id, expected in _NATIVE_STEPS.items():
        job = native.jobs[job_id]
        steps = [
            as_object(value, "native proof step") for value in as_array(job.get("steps"), "steps")
        ]
        for script in expected:
            matches = [
                step
                for step in steps
                if isinstance(step.get("run"), str) and str(step["run"]).strip() == script
            ]
            if (
                len(matches) != 1
                or "if" in matches[0]
                or matches[0].get("continue-on-error", False)
            ):
                raise ValueError(f"risk execution command is absent or suppressed: {job_id}")
    return native.workflow, {name: command.argv for name, command in quality.commands.items()}


def admit_execution_binding(
    workflow_path: str,
    command_ids: list[str],
    witness_path: str,
    commands: Mapping[str, tuple[str, ...]],
) -> None:
    if workflow_path != NATIVE_PATH:
        raise ValueError("risk binding substitutes an unadmitted execution workflow")
    for command in command_ids:
        if command not in commands:
            raise ValueError("risk binding execution command is unknown")
        if command in _PYTHON_COMMANDS:
            if not any(
                witness_path == path or witness_path.startswith(path + "/") for path in UNIVERSE
            ):
                raise ValueError("risk assertion is outside the native Python collection")
            if command == "runtime026.test" and witness_path not in commands[command]:
                raise ValueError("risk assertion is outside the bounded runtime command")
            if command == "python.persistence-test" and not witness_path.startswith(
                "backend/tests/integration/persistence/"
            ):
                raise ValueError("risk assertion is outside the persistence command")
        elif command == "frontend.browser":
            if not witness_path.startswith("frontend/tests/browser/") or not witness_path.endswith(
                ".spec.ts"
            ):
                raise ValueError("risk assertion is outside the browser collection")
        elif command == "frontend.quality":
            if witness_path == "frontend/tools/promise-lint-contract.mjs":
                continue
            if not witness_path.startswith("frontend/tests/") or not witness_path.endswith(
                (".test.ts", ".test.tsx")
            ):
                raise ValueError("risk assertion is outside the frontend unit collection")
        elif command == "frontend.connected":
            if witness_path != "frontend/tests/connected/administratorBudget.spec.ts":
                raise ValueError("risk assertion is outside the connected administrator collection")
        else:
            raise ValueError("risk execution command needs explicit native-owner admission")

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
import warnings
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML
from scripts.mutation.mutation_suite_specs import SUITES, ExecutionGroup
from scripts.quality_plan import load_quality_plan

from ci_coordinator.runtime_settings import PYTHON_RUNTIME_PROFILE

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_PROVIDER_WORKFLOW_PATH = _REPOSITORY_ROOT / ".github/workflows/python-persistence.yml"
_PORTAL_ALIAS_WARNING = (
    "The anyio.abc.BlockingPortal alias is deprecated, "
    "use anyio.from_thread.BlockingPortal instead."
)
_PORTAL_ALIAS_WARNING_FILTER = (
    r"ignore:^The anyio\.abc\.BlockingPortal alias is deprecated, "
    r"use anyio\.from_thread\.BlockingPortal instead\.\Z"
    r":DeprecationWarning:^starlette\.testclient\Z:53"
)


def test_provider_workflow_supports_only_the_admitted_python_patches() -> None:
    workflow = _read_workflow(_PROVIDER_WORKFLOW_PATH)
    triggers = _mapping(workflow["on"])
    jobs = _mapping(workflow["jobs"])

    assert set(triggers) == {"merge_group", "pull_request", "workflow_dispatch"}
    assert _mapping(triggers["merge_group"])["types"] == ["checks_requested"]
    for trigger in triggers.values():
        if isinstance(trigger, dict):
            assert not set(trigger).intersection(
                {"branches", "branches-ignore", "paths", "paths-ignore", "tags", "tags-ignore"}
            )
    assert set(jobs) == {
        "api-contract-exploration",
        "connected-administrator",
        "connected-development-stack",
        "container-runtime-smoke",
        "developer-bootstrap",
        "developer-container",
        "developer-host-lifecycle",
        "native-test-plan",
        "native-test-shards",
        "operator-workbench",
        "persistence-mutation",
        "postgres-witness",
        "provider-dependency-review",
        "pull-request-gate",
        "repository-quality",
        "secret-scan",
        "serial-qualification",
        "trusted-plan-request-entrypoint",
        "utility-config",
        "utility-docker",
        "utility-go-static",
        "utility-go-vulnerabilities",
        "utility-spelling",
    }
    supported_versions = [
        ".".join(map(str, version)) for version in PYTHON_RUNTIME_PROFILE.supported_versions
    ]
    assert supported_versions == ["3.13.15"]
    postgres_steps = _sequence(_mapping(jobs["postgres-witness"])["steps"])
    postgres_setup = next(step for step in postgres_steps if step.get("name") == "Set up Python")
    assert _mapping(postgres_setup["with"])["python-version"] == ".".join(
        map(str, PYTHON_RUNTIME_PROFILE.container_version)
    )
    mutation_steps = _sequence(_mapping(jobs["persistence-mutation"])["steps"])
    setup_python = next(step for step in mutation_steps if step.get("name") == "Set up Python")
    assert _mapping(setup_python["with"])["python-version"] == ".".join(
        map(str, PYTHON_RUNTIME_PROFILE.container_version)
    )


def test_provider_workflow_preserves_the_exact_proofkit_range() -> None:
    workflow = _read_workflow(_PROVIDER_WORKFLOW_PATH)
    workflow_environment = _mapping(workflow["env"])
    base_expression = str(workflow_environment["PROOFKIT_BASE_REF"])

    assert "github.event.pull_request.base.sha" in base_expression
    assert "github.event.merge_group.base_sha" in base_expression
    assert "inputs.proof_scope == 'range'" in base_expression
    assert "inputs.proofkit_base_sha" in base_expression
    assert workflow_environment["PROOFKIT_HEAD_REF"] == "${{ github.sha }}"

    jobs = _mapping(workflow["jobs"])
    for job in jobs.values():
        job_mapping = _mapping(job)
        _assert_range_is_not_shadowed(job_mapping.get("env"))
        steps = job_mapping.get("steps")
        if steps is None:
            assert "uses" in job_mapping
            continue
        for step in _sequence(steps):
            _assert_range_is_not_shadowed(step.get("env"))

    quality_steps = _sequence(_mapping(jobs["repository-quality"])["steps"])
    selective_step = next(
        step for step in quality_steps if step.get("name") == "Admit selective Proofkit routing"
    )
    assert selective_step["run"] == "backend/.venv/bin/python -m scripts.proofkit_plan_check"
    assert selective_step["if"] == (
        "github.event_name == 'pull_request' || github.event_name == 'merge_group' || "
        "(github.event_name == 'workflow_dispatch' && inputs.proof_scope == 'range')"
    )
    fixed_step = next(
        step
        for step in quality_steps
        if step.get("name") == "Run repository and Python quality witnesses"
    )
    assert "scripts.proofkit_plan_check" not in str(fixed_step["run"])


def test_provider_workflow_does_not_repeat_static_proof_in_runtime_jobs() -> None:
    workflow = _read_workflow(_PROVIDER_WORKFLOW_PATH)
    jobs = _mapping(workflow["jobs"])
    quality_steps = _sequence(_mapping(jobs["repository-quality"])["steps"])
    postgres_steps = _sequence(_mapping(jobs["postgres-witness"])["steps"])
    container_steps = _sequence(_mapping(jobs["container-runtime-smoke"])["steps"])

    quality_commands = "\n".join(str(step.get("run", "")) for step in quality_steps)
    postgres_commands = "\n".join(str(step.get("run", "")) for step in postgres_steps)
    container_commands = "\n".join(str(step.get("run", "")) for step in container_steps)

    assert "scripts.dependency_audit" in quality_commands
    assert "scripts.python_witness test" not in quality_commands
    assert "scripts.python_witness test" not in postgres_commands
    assert "scripts.ci_test_execution combine" in postgres_commands
    assert "scripts.python_witness persistence-test" not in postgres_commands
    assert "scripts.proofkit_" not in postgres_commands
    assert "scripts.python_witness lint" not in postgres_commands
    assert "scripts.python_witness typecheck" not in postgres_commands
    assert "scripts.container_runtime_smoke" in container_commands


def test_browser_failure_diagnostics_do_not_weaken_required_screenshot_retention() -> None:
    workflow = _read_workflow(_PROVIDER_WORKFLOW_PATH)
    steps = _sequence(_mapping(_mapping(workflow["jobs"])["operator-workbench"])["steps"])
    screenshots = next(
        step for step in steps if step.get("name") == "Retain operator view screenshots"
    )
    diagnostics = next(
        step for step in steps if step.get("name") == "Retain browser failure diagnostics"
    )
    screenshot_options = _mapping(screenshots["with"])
    diagnostic_options = _mapping(diagnostics["with"])
    assert screenshot_options["path"] == "frontend/test-results/**/*.png"
    assert screenshot_options["if-no-files-found"] == "error"
    assert (
        diagnostics["if"] == "${{ !cancelled() && steps.browser_witnesses.outcome == 'failure' }}"
    )
    assert set(str(diagnostic_options["path"]).splitlines()) == {
        "frontend/test-results/**/*.zip",
        "frontend/test-results/**/error-context.md",
    }
    assert diagnostic_options["if-no-files-found"] == "warn"
    assert screenshot_options["retention-days"] == diagnostic_options["retention-days"] == 7


def test_mutation_registries_cover_every_configured_suite() -> None:
    workflow = _read_workflow(_PROVIDER_WORKFLOW_PATH)
    jobs = _mapping(workflow["jobs"])
    mutation_steps = _sequence(_mapping(jobs["persistence-mutation"])["steps"])
    mutation_job = _mapping(jobs["persistence-mutation"])
    assert mutation_job["needs"] == ["repository-quality"]
    assert "if" not in mutation_job
    assert mutation_job.get("continue-on-error", False) is False
    strategy = _mapping(mutation_job["strategy"])
    assert strategy["fail-fast"] is False
    workflow_suites = _mapping(strategy["matrix"])["suite"]
    expected_suites = {
        name
        for name, spec in SUITES.items()
        if spec.execution_group is ExecutionGroup.PERSISTENCE_MUTATION
    }
    assert len(workflow_suites) == len(set(workflow_suites)) == len(expected_suites)
    assert set(workflow_suites) == expected_suites
    quality_steps = _sequence(_mapping(jobs["repository-quality"])["steps"])
    preflight_steps = [
        step
        for step in quality_steps
        if step.get("run")
        == "backend/.venv/bin/python -m scripts.mutation.mutation_suite_specs preflight"
    ]
    assert len(preflight_steps) == 1
    assert "if" not in preflight_steps[0]
    assert preflight_steps[0].get("continue-on-error", False) is False
    mutation_step = next(
        step for step in mutation_steps if step.get("name") == "Run governed mutation witnesses"
    )
    assert mutation_step["env"] == {"SUITE_NAME": "${{ matrix.suite }}"}
    assert mutation_step["run"] == (
        'backend/.venv/bin/python -m scripts.mutation.mutation_suite_specs "$SUITE_NAME"'
    )
    assert "if" not in mutation_step
    assert mutation_step.get("continue-on-error", False) is False

    prefix = "backend/.venv/bin/python -m scripts.mutation.mutation_suite_specs "
    commands_by_group = {
        ExecutionGroup.OPERATOR_WORKBENCH: "\n".join(
            str(step.get("run", ""))
            for step in _sequence(_mapping(jobs["operator-workbench"])["steps"])
        ),
    }
    for group, commands in commands_by_group.items():
        workflow_suites = re.findall(rf"{re.escape(prefix)}([a-z0-9-]+)", commands)
        expected_suites = {name for name, spec in SUITES.items() if spec.execution_group is group}
        assert len(workflow_suites) == len(set(workflow_suites)) == len(expected_suites)
        assert set(workflow_suites) == expected_suites

    quality_plan = _mapping(
        json.loads((_REPOSITORY_ROOT / "proofkit/quality-plan.v1.json").read_text(encoding="utf-8"))
    )
    branch_head_commands = quality_plan["branchHeadAdditionalCommandIds"]
    assert isinstance(branch_head_commands, list)
    mutation_commands = {
        command
        for command in branch_head_commands
        if isinstance(command, str) and command.startswith("mutation.")
    }
    assert mutation_commands == {spec.command_id for spec in SUITES.values()}


def test_provider_workflow_provisions_tools_before_their_first_use() -> None:
    workflow = _read_workflow(_PROVIDER_WORKFLOW_PATH)
    jobs = _mapping(workflow["jobs"])

    quality_steps = _sequence(_mapping(jobs["repository-quality"])["steps"])
    quality_names = [step.get("name") for step in quality_steps]
    assert quality_names.index("Set up pnpm") < quality_names.index("Audit locked dependencies")
    assert quality_names.index("Set up Node.js") < quality_names.index("Audit locked dependencies")
    dependency_install_index = quality_names.index("Install locked target-control build dependency")
    bundle_check_index = quality_names.index("Check generated target control bundle")
    assert dependency_install_index < bundle_check_index
    assert quality_names.index("Install locked Python dependencies") < quality_names.index(
        "Lint GitHub workflow contracts"
    )
    quality_setup_node = next(
        step for step in quality_steps if step.get("name") == "Set up Node.js"
    )
    assert _mapping(quality_setup_node["with"])["node-version"] == "24.21.0"
    bundle_step = next(
        step
        for step in quality_steps
        if step.get("name") == "Check generated target control bundle"
    )
    assert bundle_step["run"] == ("backend/.venv/bin/python -m scripts.target_control_bundle check")
    source_falsifier_step = next(
        step
        for step in quality_steps
        if step.get("name") == "Falsify esbuild dynamic-loader omission"
    )
    assert quality_names.index("Check generated target control bundle") < quality_names.index(
        "Falsify esbuild dynamic-loader omission"
    )
    assert source_falsifier_step["run"] == (
        "backend/.venv/bin/python -m pytest -q "
        "scripts/tests/test_target_control_source_admission.py -k real_esbuild"
    )

    for job_name in (
        "native-test-plan",
        "native-test-shards",
        "postgres-witness",
        "serial-qualification",
    ):
        native_steps = _sequence(_mapping(jobs[job_name])["steps"])
        node_index = next(
            index for index, step in enumerate(native_steps) if step.get("name") == "Set up Node.js"
        )
        execution_index = next(
            index
            for index, step in enumerate(native_steps)
            if "scripts.ci_test_execution" in str(step.get("run", ""))
        )
        assert node_index < execution_index
        assert _mapping(native_steps[node_index]["with"])["node-version"] == "24.21.0"

    operator_steps = _sequence(_mapping(jobs["operator-workbench"])["steps"])
    operator_names = [step.get("name") for step in operator_steps]
    assert operator_names.index("Set up pnpm") < operator_names.index("Set up Node.js")
    assert operator_names.index("Set up Node.js") < operator_names.index(
        "Install locked frontend dependencies"
    )
    setup_node = next(step for step in operator_steps if step.get("name") == "Set up Node.js")
    setup_pnpm = next(step for step in operator_steps if step.get("name") == "Set up pnpm")
    assert _mapping(setup_node["with"])["node-version"] == "24.21.0"
    assert _mapping(setup_pnpm["with"])["version"] == "12.5.1"
    developer_container_steps = _sequence(_mapping(jobs["developer-container"])["steps"])
    developer_container_names = [step.get("name") for step in developer_container_steps]
    assert developer_container_names.index("Set up pnpm") < developer_container_names.index(
        "Set up Node.js"
    )
    assert developer_container_names.index("Set up Node.js") < developer_container_names.index(
        "Install locked frontend dependencies"
    )
    developer_setup_node = next(
        step for step in developer_container_steps if step.get("name") == "Set up Node.js"
    )
    developer_setup_pnpm = next(
        step for step in developer_container_steps if step.get("name") == "Set up pnpm"
    )
    assert _mapping(developer_setup_node["with"])["node-version"] == "24.21.0"
    assert _mapping(developer_setup_pnpm["with"])["version"] == "12.5.1"
    devcontainer = next(
        step
        for step in developer_container_steps
        if step.get("name") == "Verify the least-privilege Dev Container"
    )
    assert devcontainer["run"] == ("backend/.venv/bin/python -m scripts.devcontainer_witness")
    connected_steps = _sequence(_mapping(jobs["connected-development-stack"])["steps"])
    connected_names = [step.get("name") for step in connected_steps]
    connected_prepare = connected_names.index("Prepare browser effect dependencies explicitly")
    for setup_name in (
        "Set up pnpm for browser effect witnesses",
        "Set up Node.js for browser effect witnesses",
    ):
        assert connected_names.index(setup_name) < connected_prepare
    assert connected_prepare < connected_names.index(
        "Run connected stack and source-watch witnesses"
    )
    for job_name, job in jobs.items():
        if job_name in {
            "connected-development-stack",
            "developer-container",
            "operator-workbench",
            "native-test-plan",
            "native-test-shards",
            "postgres-witness",
            "repository-quality",
            "serial-qualification",
        }:
            continue
        steps = _mapping(job).get("steps")
        if steps is None:
            reusable_workflows = {
                "api-contract-exploration": "$/.github/workflows/api-contract.yml",
                "secret-scan": "$/.github/workflows/source-assurance.yml",
                "trusted-plan-request-entrypoint": "$/.github/workflows/trusted-plan-request.yml",
            }
            assert job_name in reusable_workflows
            assert _mapping(job)["uses"] == reusable_workflows[job_name]
            continue
        assert all("setup-node" not in str(step.get("uses", "")) for step in _sequence(steps))

    for job_name in (
        "repository-quality",
        "native-test-plan",
        "native-test-shards",
        "postgres-witness",
        "serial-qualification",
        "persistence-mutation",
        "operator-workbench",
        "connected-development-stack",
        "developer-container",
    ):
        steps = _sequence(_mapping(jobs[job_name])["steps"])
        setup_uv = next(step for step in steps if step.get("name") == "Set up uv")
        assert _mapping(setup_uv["with"])["prune-cache"] is True
        install = next(
            step
            for step in steps
            if step.get("name") in {"Install dependencies", "Install locked Python dependencies"}
        )
        first_environment_use = next(
            index
            for index, step in enumerate(steps)
            if "backend/.venv/" in str(step.get("run", ""))
        )
        assert steps.index(install) < first_environment_use


def test_native_jobs_require_unconditional_execution_and_coverage_join() -> None:
    workflow = _read_workflow(_PROVIDER_WORKFLOW_PATH)
    jobs = _mapping(workflow["jobs"])
    postgres_job = _mapping(jobs["postgres-witness"])
    postgres_steps = _sequence(postgres_job["steps"])
    coverage_command = " ".join(load_quality_plan().commands["python.coverage"].argv)
    assert coverage_command == "backend/.venv/bin/python -m scripts.python_witness coverage"
    combine_command = (
        "backend/.venv/bin/python -m scripts.ci_test_execution combine "
        "--plan .ci-native/plan.json --artifacts .ci-native/shards --output .ci-native/aggregate"
    )
    coverage_steps = [step for step in postgres_steps if step.get("run") == combine_command]

    assert len(coverage_steps) == 1
    coverage_step = coverage_steps[0]
    assert "if" not in coverage_step
    assert "env" not in coverage_step
    assert coverage_step.get("continue-on-error", False) is False
    assert "if" not in postgres_job
    assert postgres_job.get("continue-on-error", False) is False
    assert postgres_job["timeout-minutes"] == 10
    assert set(postgres_job) == {"name", "needs", "runs-on", "timeout-minutes", "steps"}
    assert postgres_job["needs"] == ["native-test-plan", "native-test-shards"]
    assert _mapping(jobs["native-test-shards"])["needs"] == ["native-test-plan"]
    for job_name, operation in (("native-test-plan", "plan"), ("native-test-shards", "run")):
        job = _mapping(jobs[job_name])
        assert "if" not in job
        assert job.get("continue-on-error", False) is False
        commands = [
            step
            for step in _sequence(job["steps"])
            if f"scripts.ci_test_execution {operation} " in str(step.get("run", ""))
        ]
        assert len(commands) == 1
        assert "if" not in commands[0]
        assert commands[0].get("continue-on-error", False) is False
    assert "defaults" not in workflow
    assert set(_mapping(workflow["env"])) == {
        "PROOFKIT_BASE_REF",
        "PROOFKIT_HEAD_REF",
        "RYUK_CONTAINER_IMAGE",
    }
    for step in postgres_steps:
        assert "env" not in step
        assert "shell" not in step
    assert [str(step["run"]).strip() for step in postgres_steps if "run" in step] == [
        "python3 -m scripts.python_witness lock-check\n"
        "python3 -m scripts.python_witness install-check",
        "backend/.venv/bin/python -c 'import platform; actual = platform.python_version(); "
        'raise SystemExit(0 if actual == "3.13.15" else '
        'f"expected Python 3.13.15, got {actual}")\'',
        combine_command,
    ]
    with (_REPOSITORY_ROOT / "backend/pyproject.toml").open("rb") as source:
        pytest_options = tomllib.load(source)["tool"]["pytest"]["ini_options"]
    assert pytest_options == {
        "addopts": ["--strict-config", "--strict-markers", "-p", "no:schemathesis"],
        "anyio_mode": "auto",
        "filterwarnings": ["error", _PORTAL_ALIAS_WARNING_FILTER],
        "markers": ["persistence: requires a real PostgreSQL provider"],
        "pythonpath": ["src", "tests/unit", ".."],
        "testpaths": ["tests", "../scripts/tests"],
    }


def test_portal_alias_warning_exception_is_bound_to_exact_upstream_versions() -> None:
    assert (package_version("starlette"), package_version("anyio")) == ("1.6.0", "4.15.1"), (
        "Requalify and retire the exact upstream BlockingPortal warning exception"
    )
    with warnings.catch_warnings(record=True) as captured:
        warnings.warn_explicit(
            _PORTAL_ALIAS_WARNING,
            DeprecationWarning,
            filename="starlette/testclient.py",
            lineno=53,
            module="starlette.testclient",
        )
    assert not captured


@pytest.mark.parametrize(
    ("message", "category", "module", "line"),
    [
        (_PORTAL_ALIAS_WARNING, DeprecationWarning, "ci_coordinator.api.http", 53),
        (_PORTAL_ALIAS_WARNING, DeprecationWarning, "starlette.testclient.extra", 53),
        ("Unrelated deprecation", DeprecationWarning, "starlette.testclient", 53),
        (_PORTAL_ALIAS_WARNING + " extra", DeprecationWarning, "starlette.testclient", 53),
        (_PORTAL_ALIAS_WARNING, FutureWarning, "starlette.testclient", 53),
        (_PORTAL_ALIAS_WARNING, DeprecationWarning, "starlette.testclient", 54),
        (
            _PORTAL_ALIAS_WARNING.replace("anyio.abc", "anyioXabc"),
            DeprecationWarning,
            "starlette.testclient",
            53,
        ),
    ],
)
def test_warning_exception_does_not_hide_unrelated_warnings(
    message: str, category: type[Warning], module: str, line: int
) -> None:
    with pytest.raises(category), warnings.catch_warnings():
        warnings.warn_explicit(
            message, category, filename="warning-control.py", lineno=line, module=module
        )


@pytest.mark.parametrize("serial_requested", [False, True])
@pytest.mark.parametrize("api_requested", [False, True])
def test_pull_request_gate_rejects_each_unsuccessful_witness_group(
    serial_requested: bool, api_requested: bool
) -> None:
    workflow = _read_workflow(_PROVIDER_WORKFLOW_PATH)
    jobs = _mapping(workflow["jobs"])
    gate_steps = _sequence(_mapping(jobs["pull-request-gate"])["steps"])
    gate = next(
        step for step in gate_steps if step.get("name") == "Require every native witness group"
    )
    environment = {name: "success" for name in _mapping(gate["env"])}
    environment["SERIAL_REQUESTED"] = str(serial_requested).lower()
    environment["SERIAL_RESULT"] = "success" if serial_requested else "skipped"
    environment["API_CONTRACT_REQUESTED"] = str(api_requested).lower()
    environment["API_CONTRACT_RESULT"] = "success" if api_requested else "skipped"
    environment["TRUSTED_PLAN_REQUEST_REASON"] = "plan_url_invalid"
    script = str(gate["run"])

    assert _run_gate(script, environment) == 0
    for failed_name in environment.keys() - {"SERIAL_REQUESTED", "API_CONTRACT_REQUESTED"}:
        for outcome in {"success", "failure", "cancelled", "skipped"} - {environment[failed_name]}:
            assert _run_gate(script, environment | {failed_name: outcome}) != 0, (
                failed_name,
                outcome,
            )


def test_api_campaign_is_isolated_and_full_qualification_admits_the_optional_result() -> None:
    campaign = _read_workflow(_REPOSITORY_ROOT / ".github/workflows/api-contract.yml")
    assert set(_mapping(campaign["on"])) == {"workflow_call", "workflow_dispatch"}
    assert campaign["permissions"] == {"contents": "read"}
    jobs = _mapping(campaign["jobs"])
    assert set(jobs) == {"api-contract"}
    job = _mapping(jobs["api-contract"])
    assert job["timeout-minutes"] == 7
    steps = _sequence(job["steps"])
    names = [step.get("name") for step in steps]
    install_index = names.index("Install locked Python dependencies")
    for name, input_name, version in (
        ("Set up Python", "python-version", "3.13.15"),
        ("Set up uv", "version", "0.12.17"),
    ):
        setup_index = names.index(name)
        assert setup_index < install_index
        assert _mapping(steps[setup_index]["with"])[input_name] == version
    assert install_index < names.index("Run the isolated campaign")
    assert steps[install_index]["run"] == (
        "python3 -m scripts.python_witness lock-check\n"
        "python3 -m scripts.python_witness install-check\n"
    )
    run = next(step for step in steps if step.get("name") == "Run the isolated campaign")
    assert run["run"] == (
        "backend/.venv/bin/python -m scripts.api_contract_campaign "
        '--seed "$API_SEED" -- "$API_PROFILE"'
    )
    assert run.get("continue-on-error", False) is False
    workflow = _read_workflow(_PROVIDER_WORKFLOW_PATH)
    caller_jobs = _mapping(workflow["jobs"])
    exploration = _mapping(caller_jobs["api-contract-exploration"])
    assert (
        exploration["if"] == "github.event_name == 'workflow_dispatch' && inputs.api_contract_deep"
    )
    assert exploration["uses"] == "$/.github/workflows/api-contract.yml"
    assert exploration["with"] == {"profile": "deep"}
    assert "api-contract-exploration" in _mapping(caller_jobs["pull-request-gate"])["needs"]


def _assert_range_is_not_shadowed(value: object) -> None:
    if isinstance(value, dict):
        assert not {"PROOFKIT_BASE_REF", "PROOFKIT_HEAD_REF"}.intersection(value)


def _run_gate(script: str, environment: dict[str, str]) -> int:
    result = subprocess.run(
        ("bash", "-c", script),
        cwd=_REPOSITORY_ROOT,
        env=os.environ | environment,
        capture_output=True,
        check=False,
        text=True,
    )
    return result.returncode


def _read_workflow(path: Path) -> dict[str, Any]:
    parser = YAML(typ="safe")
    return _mapping(parser.load(path.read_text(encoding="utf-8")))


def _mapping(value: object) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _sequence(value: object) -> list[dict[str, Any]]:
    assert isinstance(value, list)
    assert all(isinstance(item, dict) for item in value)
    return value

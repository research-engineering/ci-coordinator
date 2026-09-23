from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from scripts.ci_matrix_contract import load_matrix
from scripts.quality_plan import (
    WitnessCommand,
    execute_commands,
    load_quality_plan,
    project_command_environment,
)


def _write_documents(root: Path, *, quality: dict[str, object]) -> None:
    proofkit = root / "proofkit"
    proofkit.mkdir()
    (proofkit / "quality-plan.v1.json").write_text(json.dumps(quality), encoding="utf-8")
    witness = {
        "vocabulary": {
            "environmentClassPolicies": [
                {
                    "cachePolicies": ["read-only", "write-local"],
                    "credentialClasses": ["none"],
                    "environmentClass": "local-python",
                    "networkPolicies": ["none"],
                }
            ]
        },
        "commands": [
            {
                "argv": ["true"],
                "cachePolicy": "read-only",
                "credentialClass": "none",
                "cwd": ".",
                "environment": {
                    "allowlist": ["PATH"],
                    "classes": ["local-python"],
                    "inherit": "allowlist",
                },
                "exitCodePolicy": {"kind": "zero", "successCodes": [0]},
                "id": command_id,
                "networkPolicy": "none",
                "timeoutMs": 4100 if command_id == "quality.branch-head" else 1000,
            }
            for command_id in (
                "python.lock-check",
                "python.install-check",
                "python.test",
                "python.coverage",
                "mutation.probe",
                "quality.branch-head",
            )
        ],
    }
    (proofkit / "witness-plan-input.json").write_text(json.dumps(witness), encoding="utf-8")


def _quality() -> dict[str, object]:
    return {
        "branchHeadAdditionalCommandIds": ["mutation.probe"],
        "directMutationJob": {
            "reserveMs": 100,
            "timeoutMinutes": 1,
        },
        "localCommandIds": [
            "python.lock-check",
            "python.install-check",
            "python.coverage",
        ],
        "orchestrationReserveMs": 100,
        "planId": "probe",
        "portableCommandIds": ["python.test"],
        "schemaVersion": 1,
    }


def test_quality_plan_preserves_declared_execution_order(tmp_path: Path) -> None:
    _write_documents(tmp_path, quality=_quality())
    plan = load_quality_plan(tmp_path)
    executed: list[str] = []

    execute_commands(
        plan.branch_head_commands(),
        executor=lambda command: executed.append(command.command_id),
    )

    assert executed == [
        "python.lock-check",
        "python.install-check",
        "python.coverage",
        "mutation.probe",
    ]


def test_quality_plan_preserves_execution_policy(tmp_path: Path) -> None:
    _write_documents(tmp_path, quality=_quality())

    command = load_quality_plan(tmp_path).commands["python.coverage"]

    assert command.environment_allowlist == ("PATH",)
    assert command.environment_classes == ("local-python",)
    assert command.environment_inherit == "allowlist"
    assert command.credential_class == "none"
    assert command.network_policy == "none"
    assert command.cache_policy == "read-only"


def test_current_direct_mutation_job_budget_is_owner_admitted() -> None:
    plan = load_quality_plan()

    assert plan.direct_mutation_job_reserve_ms == 1_800_000
    assert plan.direct_mutation_job_timeout_minutes == 258


def test_current_coverage_witness_admits_its_range_selection_inputs() -> None:
    plan = load_quality_plan()
    coverage = plan.commands["python.coverage"]
    branch_head = plan.commands["quality.branch-head"]

    expected = {
        "CI",
        "GITHUB_ACTIONS",
        "GITLEAKS_TEST_BINARY",
        "PATH",
        "PROOFKIT_BASE_REF",
        "PROOFKIT_HEAD_REF",
    }
    assert set(coverage.environment_allowlist) == expected
    assert expected < set(branch_head.environment_allowlist)


def test_local_quality_plan_excludes_network_vulnerability_audit() -> None:
    plan = load_quality_plan()

    assert "dependency.audit" not in plan.local_command_ids


def test_diagram_quality_separates_inventory_from_browser_preparation() -> None:
    plan = load_quality_plan()

    assert "documentation.diagrams-inventory" in plan.local_command_ids
    assert "documentation.diagrams-inventory" in plan.portable_command_ids
    assert plan.commands["documentation.diagrams-inventory"].environment_classes == (
        "local-python",
    )
    for command_id in (
        "documentation.diagrams",
        "documentation.diagrams-falsifiers",
        "documentation.diagrams-process",
    ):
        assert command_id in plan.branch_head_command_ids
        assert command_id not in plan.local_command_ids
        assert command_id not in plan.portable_command_ids
        command = plan.commands[command_id]
        assert command.network_policy == "none"
        assert command.credential_class == "none"
        assert set(command.environment_allowlist) == {
            "HOME",
            "PATH",
            "PLAYWRIGHT_BROWSERS_PATH",
            "TMPDIR",
        }
        assert set(command.environment_allowlist) <= set(
            plan.commands["quality.branch-head"].environment_allowlist
        )
    process = plan.commands["documentation.diagrams-process"]
    assert process.argv == (
        "backend/.venv/bin/python",
        "scripts/tests/test_diagram_process.py",
        "--qualify",
    )
    assert process.cwd == Path(__file__).resolve().parents[2]
    assert process.environment_classes == ("local-python-node",)
    assert process.timeout_ms == 120_000


def test_branch_head_covers_every_non_subsumed_witness_command() -> None:
    profile, plan = load_matrix(Path(__file__).resolve().parents[2])
    selected = {command.command_id for command in plan.branch_head_commands()}
    provider_selected = {
        command_id for group in profile.utilityGroups for command_id in group.commandIds
    }

    assert provider_selected == {
        "utility.build-checks",
        "utility.go-vet",
        "utility.gofmt",
        "utility.govulncheck",
        "utility.hadolint",
        "utility.schemas",
        "utility.spelling",
        "utility.staticcheck",
        "utility.yaml",
    }
    assert selected.isdisjoint(provider_selected)
    for command_id in provider_selected:
        command = plan.commands[command_id]
        assert command.argv == (
            "tooling/quality/.venv/bin/python",
            "-m",
            "scripts.ci_utility_checks",
            command_id.removeprefix("utility."),
        )
        assert command.credential_class == "none"
    assert set(plan.commands) - selected - provider_selected == {
        "python.test",
        "quality.branch-head",
        "runtime026.persistence-test",
        "runtime026.test",
        "runtime027.test",
        "runtime028.test",
    }
    assert {"dependency.audit", "container.smoke", "development.stack"} <= selected
    assert {
        "python.coverage",
        "python.import-boundary",
        "python.package-check",
        "target-control-bundle.check",
    } <= selected
    runtime028 = plan.commands["runtime028.test"]
    assert runtime028.argv[:2] == ("backend/.venv/bin/pytest", "-q")
    assert all(
        path.startswith(("backend/tests/", "scripts/tests/")) for path in runtime028.argv[2:]
    )


def test_aggregate_preserves_the_distinct_persistence_envelope() -> None:
    plan = load_quality_plan()
    aggregate = [command.command_id for command in plan.branch_head_commands()]
    assert aggregate.count("python.coverage") == 1
    assert aggregate.count("python.persistence-test") == 1
    assert plan.commands["python.persistence-test"].environment_allowlist == ("PATH",)
    assert plan.commands["python.persistence-test"].argv == (
        "backend/.venv/bin/python",
        "-m",
        "scripts.python_witness",
        "persistence-test",
    )


def test_branch_head_preserves_connected_stack_docker_context_home() -> None:
    plan = load_quality_plan()
    stack = plan.commands["development.stack"]
    branch_head = plan.commands["quality.branch-head"]

    assert {"CI", "HOME", "PATH"} <= set(stack.environment_allowlist)
    assert set(stack.environment_allowlist) <= set(branch_head.environment_allowlist)


def test_portable_quality_plan_is_provider_free() -> None:
    plan = load_quality_plan()

    assert plan.portable_command_ids
    assert "python.test" in plan.portable_command_ids
    assert "python.coverage" not in plan.portable_command_ids
    assert all(command.network_policy == "none" for command in plan.portable_commands())


def test_selective_plan_receives_only_the_exact_branch_range_inputs() -> None:
    command = load_quality_plan().commands["selective.plan"]

    assert project_command_environment(
        command,
        {
            "PATH": "/bin",
            "PROOFKIT_BASE_REF": "a" * 40,
            "PROOFKIT_HEAD_REF": "b" * 40,
            "GITHUB_TOKEN": "secret",
        },
    ) == {
        "PATH": "/bin",
        "PROOFKIT_BASE_REF": "a" * 40,
        "PROOFKIT_HEAD_REF": "b" * 40,
    }


def test_quality_plan_projects_only_allowlisted_environment(tmp_path: Path) -> None:
    marker = tmp_path / "environment.txt"
    command = WitnessCommand(
        argv=(
            sys.executable,
            "-c",
            (
                "import os; from pathlib import Path; "
                f"Path({str(marker)!r}).write_text("
                "os.getenv('HIDDEN', 'absent') + ':' + os.environ['VISIBLE'])"
            ),
        ),
        cache_policy="read-only",
        command_id="environment.probe",
        credential_class="none",
        cwd=tmp_path,
        environment_allowlist=("VISIBLE",),
        environment_classes=("local-python",),
        environment_inherit="allowlist",
        network_policy="none",
        timeout_ms=2000,
    )

    execute_commands(
        (command,),
        environment={"HIDDEN": "secret", "VISIBLE": "present"},
    )

    assert marker.read_text(encoding="utf-8") == "absent:present"


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["localCommandIds"].append("python.coverage"),
            "duplicates",
        ),
        (lambda value: value["localCommandIds"].append("missing"), "unknown commands"),
        (
            lambda value: value["branchHeadAdditionalCommandIds"].append("quality.branch-head"),
            "recursively invoke",
        ),
        (
            lambda value: value.__setitem__("orchestrationReserveMs", 101),
            "orchestration reserve",
        ),
        (
            lambda value: value["directMutationJob"].__setitem__("reserveMs", 0),
            "reserveMs must be a positive integer",
        ),
        (
            lambda value: value["directMutationJob"].__setitem__("timeoutMinutes", 361),
            "within the GitHub job limit",
        ),
    ],
)
def test_quality_plan_rejects_invalid_command_graph(
    tmp_path: Path,
    mutation: object,
    message: str,
) -> None:
    quality = _quality()
    assert callable(mutation)
    mutation(quality)
    _write_documents(tmp_path, quality=quality)

    with pytest.raises(ValueError, match=message):
        load_quality_plan(tmp_path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (
            lambda command: command.__setitem__("networkPolicy", "external"),
            "policy is not admitted",
        ),
        (
            lambda command: command["environment"].__setitem__("allowlist", ["GITHUB_TOKEN"]),
            "credential-shaped names",
        ),
        (
            lambda command: command["environment"].__setitem__("inherit", "all"),
            "inheritance must equal allowlist or none",
        ),
    ],
)
def test_quality_plan_rejects_unsafe_execution_policy(
    tmp_path: Path,
    mutate: object,
    message: str,
) -> None:
    _write_documents(tmp_path, quality=_quality())
    witness_path = tmp_path / "proofkit" / "witness-plan-input.json"
    witness = json.loads(witness_path.read_text(encoding="utf-8"))
    command = witness["commands"][0]
    assert callable(mutate)
    mutate(command)
    witness_path.write_text(json.dumps(witness), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_quality_plan(tmp_path)


def test_quality_plan_rejects_environment_input_dropped_by_branch_head(
    tmp_path: Path,
) -> None:
    _write_documents(tmp_path, quality=_quality())
    witness_path = tmp_path / "proofkit" / "witness-plan-input.json"
    witness = json.loads(witness_path.read_text(encoding="utf-8"))
    command = next(item for item in witness["commands"] if item["id"] == "mutation.probe")
    command["environment"]["allowlist"].append("CI")
    witness_path.write_text(json.dumps(witness), encoding="utf-8")

    with pytest.raises(ValueError, match="cannot forward child environment inputs: CI"):
        load_quality_plan(tmp_path)


@pytest.mark.parametrize("runner_environment", ["github-hosted", "self-hosted"])
def test_connected_host_identity_survives_both_execution_environment_boundaries(
    runner_environment: str,
) -> None:
    plan = load_quality_plan()
    caller = {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": runner_environment,
        "GITHUB_SHA": "a" * 40,
        "UNRELATED_SECRET": "must-not-be-forwarded",
    }
    outer = project_command_environment(plan.commands["quality.branch-head"], caller)
    connected = project_command_environment(plan.commands["frontend.connected"], outer)
    assert connected == {key: value for key, value in caller.items() if key != "UNRELATED_SECRET"}

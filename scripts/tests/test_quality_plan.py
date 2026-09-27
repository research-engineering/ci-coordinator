from __future__ import annotations

import hashlib
import json
import os
import signal
import sys
from contextlib import suppress
from pathlib import Path

import pytest
from scripts import quality_plan
from scripts.ci_matrix_contract import load_matrix
from scripts.dev_environment.diagnostics import Reason
from scripts.dev_environment.environment import (
    EnvironmentError,
    admit_dependencies,
    dependency_lease,
    managed_dependency_context,
)
from scripts.dev_environment.lifecycle import TerminationRequest
from scripts.quality_plan import (
    MANAGED_DEPENDENCY_ARGUMENT,
    QualityPlanUsageError,
    WitnessCommand,
    execute_commands,
    load_quality_plan,
    project_command_environment,
    select_quality_plan,
)
from scripts.tests.test_dev_environment_dependencies import (
    pending_scopes,
    prepared_quality_identity,
    quality_dependency_borrow,
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


@pytest.mark.parametrize(
    "arguments,mode", [([], "local"), (["local"], "local"), (["portable"], "portable")]
)
def test_shared_quality_selection_has_exact_modes_and_install_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, arguments: list[str], mode: str
) -> None:
    identity = prepared_quality_identity(tmp_path, monkeypatch)
    selected = select_quality_plan(arguments, identity.repo_root)
    assert selected.mode == mode
    assert selected.write_scopes == (("backend", "frontend") if mode == "local" else ())
    assert [command.command_id for command in selected.commands] == (
        ["python.lock-check", "python.install-check", "frontend.install", "python.lint"]
        if mode == "local"
        else ["python.test"]
    )
    assert pending_scopes(identity) == set()


@pytest.mark.parametrize(
    "arguments",
    [
        ["portable", "local"],
        ["portable", "portable"],
        ["unknown"],
        [MANAGED_DEPENDENCY_ARGUMENT, "{}"],
    ],
)
def test_public_quality_selection_rejects_private_or_invalid_forwarding(
    tmp_path: Path, arguments: list[str]
) -> None:
    with pytest.raises(QualityPlanUsageError):
        select_quality_plan(arguments, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_portable_selection_rejects_an_installer_even_with_provider_free_metadata(
    tmp_path: Path,
) -> None:
    plan = _quality()
    plan["portableCommandIds"] = ["python.install-check"]
    _write_documents(tmp_path, quality=plan)
    with pytest.raises(ValueError, match="must not install dependencies"):
        select_quality_plan(["portable"], tmp_path)


def test_selection_identity_binds_the_complete_command_and_effect_projection(
    tmp_path: Path,
) -> None:
    _write_documents(tmp_path, quality=_quality())
    selected = select_quality_plan([], tmp_path)
    literal = {
        "mode": "local",
        "commands": [
            {
                "argv": ["true"],
                "cache_policy": "read-only",
                "command_id": command_id,
                "credential_class": "none",
                "cwd": str(tmp_path),
                "environment_allowlist": ["PATH"],
                "environment_classes": ["local-python"],
                "environment_inherit": "allowlist",
                "network_policy": "none",
                "timeout_ms": 1000,
            }
            for command_id in ("python.lock-check", "python.install-check", "python.coverage")
        ],
        "dependencyEffects": [["python.install-check", "backend"]],
    }
    expected = hashlib.sha256(
        json.dumps(literal, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    assert selected.sha256 == expected


@pytest.mark.parametrize(
    "failure,pending",
    [
        (None, set()),
        ("python.lock-check", set()),
        ("python.install-check", {"backend"}),
        ("frontend.install", {"frontend"}),
        ("python.lint", set()),
    ],
)
def test_managed_quality_fences_only_reached_install_phases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    failure: str | None,
    pending: set[str],
) -> None:
    identity = prepared_quality_identity(tmp_path, monkeypatch)
    commands = select_quality_plan([], identity.repo_root).commands
    executed: list[str] = []

    def execute(command: WitnessCommand) -> None:
        expected = {"python.install-check": {"backend"}, "frontend.install": {"frontend"}}
        assert pending_scopes(identity) == expected.get(command.command_id, set())
        executed.append(command.command_id)
        if command.command_id == failure:
            raise RuntimeError("command failure")

    with quality_dependency_borrow(identity) as borrow:
        if failure is None:
            execute_commands(commands, executor=execute, managed_dependencies=borrow)
        else:
            with pytest.raises(RuntimeError, match="command failure"):
                execute_commands(commands, executor=execute, managed_dependencies=borrow)
    expected_ids = [command.command_id for command in commands]
    assert executed == (
        expected_ids if failure is None else expected_ids[: expected_ids.index(failure) + 1]
    )
    receipts = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["qualityCommand"] for row in receipts] == executed
    assert [row["succeeded"] for row in receipts] == [name != failure for name in executed]
    assert pending_scopes(identity) == pending
    for scope in ("backend", "frontend"):
        if scope in pending:
            with pytest.raises(EnvironmentError, match=Reason.ENVIRONMENT_STALE):
                admit_dependencies(identity, scope)
        else:
            admit_dependencies(identity, scope)


@pytest.mark.parametrize("signal_number", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize(
    "cut,pending",
    [
        ("python.lock-check", set()),
        ("python.install-check", {"backend"}),
        ("frontend.install", {"frontend"}),
        ("python.lint", set()),
    ],
)
def test_managed_quality_cancellation_does_not_reopen_completed_scopes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    signal_number: int,
    cut: str,
    pending: set[str],
) -> None:
    identity = prepared_quality_identity(tmp_path, monkeypatch)

    def execute(command: WitnessCommand) -> None:
        if command.command_id == cut:
            if signal_number == signal.SIGINT:
                raise KeyboardInterrupt
            raise TerminationRequest(signal_number)

    with (
        quality_dependency_borrow(identity) as borrow,
        pytest.raises((KeyboardInterrupt, TerminationRequest)),
    ):
        execute_commands(
            select_quality_plan([], identity.repo_root).commands,
            executor=execute,
            managed_dependencies=borrow,
        )
    assert pending_scopes(identity) == pending


def test_export_changed_after_lock_check_is_rejected_before_install_dispatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    identity = prepared_quality_identity(tmp_path, monkeypatch)
    executed: list[str] = []

    def execute(command: WitnessCommand) -> None:
        executed.append(command.command_id)
        if command.command_id == "python.lock-check":
            (identity.repo_root / "backend/requirements-dev.lock").write_text("changed\n")

    with (
        quality_dependency_borrow(identity) as borrow,
        pytest.raises(EnvironmentError, match=Reason.ENVIRONMENT_STALE),
    ):
        execute_commands(
            select_quality_plan([], identity.repo_root).commands,
            executor=execute,
            managed_dependencies=borrow,
        )
    assert executed == ["python.lock-check"]
    assert pending_scopes(identity) == set()
    receipts = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert receipts[-1]["qualityCommand"] == "python.install-check"
    assert receipts[-1]["succeeded"] is False


def test_managed_completion_failure_cannot_emit_success_or_dispatch_next_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    identity = prepared_quality_identity(tmp_path, monkeypatch)
    executed: list[str] = []

    def execute(command: WitnessCommand) -> None:
        executed.append(command.command_id)
        if command.command_id == "python.install-check":
            (identity.repo_root / "backend/uv.lock").write_text("changed\n")

    with (
        quality_dependency_borrow(identity) as borrow,
        pytest.raises(EnvironmentError, match=Reason.ENVIRONMENT_STALE),
    ):
        execute_commands(
            select_quality_plan([], identity.repo_root).commands,
            executor=execute,
            managed_dependencies=borrow,
        )
    assert executed == ["python.lock-check", "python.install-check"]
    assert pending_scopes(identity) == {"backend"}
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["succeeded"] is False


@pytest.mark.parametrize("managed_context", [None, "{}", "private-invalid-json"])
def test_quality_entry_has_no_implicit_managed_mode_or_malformed_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    managed_context: str | None,
) -> None:
    identity = prepared_quality_identity(tmp_path, monkeypatch)
    monkeypatch.setattr(quality_plan, "REPO_ROOT", identity.repo_root)
    monkeypatch.setenv("CI_COORDINATOR_DEV_STATE_HOME", str(identity.state_home))
    executed: list[str] = []
    monkeypatch.setattr(
        quality_plan,
        "_execute_command",
        lambda command, **_options: executed.append(command.command_id),
    )
    arguments = [] if managed_context is None else [MANAGED_DEPENDENCY_ARGUMENT, managed_context]
    assert quality_plan.main(arguments) == (0 if managed_context is None else 1)
    assert executed == (
        ["python.lock-check", "python.install-check", "frontend.install", "python.lint"]
        if managed_context is None
        else []
    )
    assert pending_scopes(identity) == set()
    captured = capsys.readouterr()
    if managed_context is not None:
        assert captured.err.strip() == Reason.INVALID_STATE
        assert managed_context not in captured.err


@pytest.mark.parametrize("changed", ["metadata", "mode"])
def test_quality_entry_rejects_selection_drift_before_any_command(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    changed: str,
) -> None:
    identity = prepared_quality_identity(tmp_path, monkeypatch)
    monkeypatch.setattr(quality_plan, "REPO_ROOT", identity.repo_root)
    monkeypatch.setenv("CI_COORDINATOR_DEV_STATE_HOME", str(identity.state_home))
    selected = select_quality_plan([], identity.repo_root)
    executed: list[str] = []
    monkeypatch.setattr(
        quality_plan,
        "_execute_command",
        lambda command, **_options: executed.append(command.command_id),
    )
    with dependency_lease(identity, exclusive=True) as descriptor:
        positive_context = managed_dependency_context(identity, os.dup(descriptor), selected.sha256)
        assert quality_plan.main([MANAGED_DEPENDENCY_ARGUMENT, positive_context]) == 0
        assert executed == [command.command_id for command in selected.commands]
        assert pending_scopes(identity) == set()
        executed.clear()
        capsys.readouterr()
        child_descriptor = os.dup(descriptor)
        try:
            context = managed_dependency_context(identity, child_descriptor, selected.sha256)
            arguments = ["portable"] if changed == "mode" else []
            if changed == "metadata":
                path = identity.repo_root / "proofkit/witness-plan-input.json"
                catalog = json.loads(path.read_text())
                command = next(
                    row for row in catalog["commands"] if row["id"] == "python.install-check"
                )
                command["timeoutMs"] += 1
                path.write_text(json.dumps(catalog))
            assert quality_plan.main([*arguments, MANAGED_DEPENDENCY_ARGUMENT, context]) == 1
            assert executed == []
            assert pending_scopes(identity) == set()
            captured = capsys.readouterr()
            assert captured.out == "" and captured.err.strip() == Reason.INVALID_STATE
        finally:
            with suppress(OSError):
                os.close(child_descriptor)


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

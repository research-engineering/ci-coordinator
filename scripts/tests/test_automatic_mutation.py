from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from scripts import automatic_mutation as mutation
from scripts.bounded_process import CommandResult
from scripts.quality_plan import load_quality_plan


def _mutmut(**changes: int) -> bytes:
    counts = {
        "killed": 0,
        "survived": 0,
        "total": 0,
        "no_tests": 0,
        "skipped": 0,
        "suspicious": 0,
        "timeout": 0,
        "check_was_interrupted_by_user": 0,
        "segfault": 0,
    }
    return json.dumps(
        counts | {"killed": 2, "survived": 1, "total": 3} | changes, sort_keys=True
    ).encode()


def _stryker(*statuses: str) -> bytes:
    return json.dumps(
        {
            "schemaVersion": "1.0",
            "files": {
                "src/api/shared/operationId.ts": {
                    "mutants": [
                        {"id": str(index), "status": status}
                        for index, status in enumerate(statuses)
                    ]
                }
            },
        }
    ).encode()


@pytest.mark.parametrize(
    "tool,payload,expected",
    [
        (
            "python",
            _mutmut(no_tests=4, skipped=5, timeout=3, total=15),
            {
                "killed": 2,
                "survived": 1,
                "total": 15,
                "no_tests": 4,
                "skipped": 5,
                "suspicious": 0,
                "timeout": 3,
                "check_was_interrupted_by_user": 0,
                "segfault": 0,
            },
        ),
        (
            "frontend",
            _stryker(
                "Killed",
                "Killed",
                "Survived",
                "Timeout",
                "Timeout",
                "NoCoverage",
                "CompileError",
                "Ignored",
            ),
            {
                "total": 8,
                "Killed": 2,
                "Survived": 1,
                "Timeout": 2,
                "NoCoverage": 1,
                "CompileError": 1,
                "Ignored": 1,
            },
        ),
    ],
)
def test_native_diagnostics_do_not_relabel_survivors_or_timeouts(
    tool: str,
    payload: bytes,
    expected: dict[str, int],
) -> None:
    assert mutation.admit_counts(tool, payload) == expected


@pytest.mark.parametrize(
    "tool,payload",
    [
        ("python", b"{}"),
        ("python", b"null"),
        ("python", _mutmut(total=4)),
        ("python", _mutmut(killed=0, survived=0, total=0)),
        ("python", _mutmut(killed=0, survived=0, no_tests=3)),
        ("python", _mutmut(suspicious=1, total=4)),
        ("python", _mutmut(segfault=1, total=4)),
        ("python", _mutmut(check_was_interrupted_by_user=1, total=4)),
        ("python", _mutmut(killed=-1, total=0)),
        ("python", _mutmut().replace(b'"killed": 2', b'"killed": true')),
        ("python", _mutmut().replace(b'"killed": 2', b'"killed": 2.0')),
        ("frontend", b"{}"),
        ("frontend", _stryker()),
        ("frontend", _stryker("Killed", "Pending")),
        ("frontend", _stryker("Killed", "RuntimeError")),
        ("frontend", _stryker("Timeout")),
        ("frontend", _stryker("unknown")),
        ("frontend", _stryker("Killed").replace(b'"1.0"', b'"2.0"', 1)),
        ("frontend", _stryker("Killed").replace(b"operationId.ts", b"other.ts")),
        ("frontend", _stryker("Killed", "Survived").replace(b'"id": "1"', b'"id": "0"')),
        ("unknown", _mutmut()),
    ],
)
def test_empty_malformed_foreign_or_incomplete_reports_are_rejected(
    tool: str,
    payload: bytes,
) -> None:
    with pytest.raises(ValueError):
        mutation.admit_counts(tool, payload)


@pytest.mark.parametrize("tool", ["python", "frontend"])
def test_existing_output_is_not_reused_or_deleted(tmp_path: Path, tool: str) -> None:
    relative = "backend/mutants" if tool == "python" else "frontend/reports/mutation"
    scratch = tmp_path / relative
    scratch.mkdir(parents=True)
    sentinel = scratch / "keep"
    sentinel.write_text("old report", encoding="utf-8")
    with pytest.raises(ValueError, match="fresh disposable"):
        mutation.run(tool, root=tmp_path)
    assert sentinel.read_text(encoding="utf-8") == "old report"


def test_runner_preserves_observation_only_semantics_and_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def execute(command: str, args: tuple[str, ...], **kwargs: object) -> CommandResult:
        calls.append({"command": command, "args": args, **kwargs})
        if args[-1] == "export-cicd-stats":
            target = tmp_path / "backend/mutants/mutmut-cicd-stats.json"
            target.parent.mkdir(parents=True)
            target.write_bytes(_mutmut())
        return CommandResult(0, "", "")

    monkeypatch.setenv("GITHUB_TOKEN", "must-not-reach-mutants")
    monkeypatch.setenv("PATH", "/test/bin")
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(mutation, "spawn", execute)
    result = mutation.run("python", root=tmp_path)
    assert result["state"] == "observed"
    assert result["upstreamCounts"] == json.loads(_mutmut())
    assert len(calls) == 2
    assert all(call["timeout_seconds"] == 240 for call in calls)
    assert all(call["max_buffer"] == mutation.MAX_REPORT_BYTES for call in calls)
    assert all(
        call["env"]
        == {
            "PATH": "/test/bin",
            "CI": "true",
            "NO_COLOR": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        for call in calls
    )


@pytest.mark.parametrize(
    "result",
    [
        CommandResult(1, "", ""),
        CommandResult(None, "", "", error="timeout", failure_kind="timeout"),
        CommandResult(0, "", "", error="residual", failure_kind="residual-descendant"),
        CommandResult(0, "", "", failure_kind="timeout"),
    ],
)
def test_command_failures_never_produce_an_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: CommandResult,
) -> None:
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(mutation, "spawn", lambda *args, **kwargs: result)
    with pytest.raises(RuntimeError, match="execution failed"):
        mutation.run("python", root=tmp_path)


def test_workflow_is_disposable_bounded_read_only_and_preserves_failure_reports() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = YAML(typ="safe").load(root / ".github/workflows/automatic-mutation.yml")
    assert workflow["permissions"] == {"contents": "read"}
    assert set(workflow["on"]) == {"pull_request", "workflow_dispatch"}
    job = workflow["jobs"]["discovery"]
    assert job["timeout-minutes"] == 12
    assert job["strategy"] == {"fail-fast": False, "matrix": {"profile": ["python", "frontend"]}}
    assert job["steps"][0]["with"]["persist-credentials"] is False
    assert job["steps"][-1]["if"] == "${{ !cancelled() }}"
    assert job["steps"][-1]["with"]["if-no-files-found"] == "error"
    assert (
        "backend/mutants/src/ci_coordinator/kernel/admission.py*"
        in (job["steps"][-1]["with"]["path"])
    )
    assert not any("continue-on-error" in step for step in job["steps"])


def test_declared_scope_and_complete_branch_head_plan_preserve_discovery_boundaries() -> None:
    root = Path(__file__).resolve().parents[2]
    config = tomllib.loads((root / "backend/pyproject.toml").read_text(encoding="utf-8"))
    assert "mutmut==3.8.0" in config["dependency-groups"]["dev"]
    assert not any("mutmut" in dependency for dependency in config["project"]["dependencies"])
    assert config["tool"]["mutmut"] == {
        "source_paths": ["src/"],
        "only_mutate": ["src/ci_coordinator/kernel/admission.py"],
        "pytest_add_cli_args_test_selection": ["tests/unit/kernel/test_kernel_admission.py"],
        "process_isolation": "forkserver",
        "on_dependency_change": "rerun",
    }
    plan = load_quality_plan()
    discovery = {"discovery.mutation-python", "discovery.mutation-frontend"}
    assert discovery <= {command.command_id for command in plan.branch_head_commands()}
    assert discovery.isdisjoint(plan.local_command_ids)
    assert discovery.isdisjoint(plan.portable_command_ids)

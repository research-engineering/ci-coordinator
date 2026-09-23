from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
from scripts.dev_environment import cli
from scripts.dev_environment.cli import run
from scripts.dev_environment.compose import ComposeProject
from scripts.dev_environment.identity import derive_instance_identity
from scripts.dev_environment.lifecycle import InstanceMutationLease
from scripts.dev_environment.secrets import admit_instance_state, ensure_instance_state


def test_main_forwards_the_explicit_ci_coordinator_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    def capture_run(argv: object, **kwargs: object) -> int:
        observed["argv"] = argv
        observed.update(kwargs)
        return 0

    monkeypatch.setenv(
        "CI_COORDINATOR_OUTBOUND_PROXY_URL",
        "http://proxy.example.test:3128",
    )
    monkeypatch.setattr(cli, "run", capture_run)

    assert cli.main(["status"]) == 0
    assert observed["argv"] == ["status"]
    assert observed["environment"] is os.environ


def test_prepare_persists_the_explicit_outbound_proxy_from_the_public_cli_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_home = tmp_path.parent / f"{tmp_path.name}-user-state"
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(ComposeProject, "validate", lambda _self: None)

    status = run(
        ["prepare"],
        repo_root=tmp_path,
        stdout=stdout,
        stderr=stderr,
        state_home=state_home,
        environment={"CI_COORDINATOR_OUTBOUND_PROXY_URL": "http://proxy.example.test:3128/"},
    )

    identity = derive_instance_identity(tmp_path, state_home=state_home)
    assert status == 0
    assert stderr.getvalue() == ""
    assert admit_instance_state(identity)["CI_COORDINATOR_OUTBOUND_PROXY_URL"] == (
        "http://proxy.example.test:3128"
    )


def test_missing_state_failure_is_stable_and_secret_free(tmp_path: Path) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    status = run(
        ["down"],
        repo_root=tmp_path,
        stdout=stdout,
        stderr=stderr,
        state_home=tmp_path.parent / f"{tmp_path.name}-user-state",
    )

    assert status == 2
    assert stdout.getvalue() == ""
    failure = json.loads(stderr.getvalue())
    assert failure["code"] == "development_environment_unavailable"
    assert failure["diagnostic"]["reason"] == "missing_state"


def test_invalid_state_failure_does_not_emit_existing_secret(tmp_path: Path) -> None:
    state_home = tmp_path.parent / f"{tmp_path.name}-user-state"
    identity = derive_instance_identity(tmp_path, state_home=state_home)
    ensure_instance_state(identity)
    token = (identity.secrets_directory / "break-glass-bearer-token").read_text(encoding="utf-8")
    identity.metadata_path.write_text("{}\n", encoding="utf-8")
    identity.metadata_path.chmod(0o600)
    stdout = io.StringIO()
    stderr = io.StringIO()

    status = run(
        ["status"],
        repo_root=tmp_path,
        stdout=stdout,
        stderr=stderr,
        state_home=state_home,
    )

    assert status == 2
    assert token not in stdout.getvalue()
    assert token not in stderr.getvalue()


@pytest.mark.parametrize("failure,expected", [("attach", 2), ("interrupt", 130), ("none", 0)])
def test_debug_session_restores_ordinary_mode_after_each_handled_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, expected: int
) -> None:
    from scripts.dev_environment.compose import ComposeError, LocalEndpoints
    from scripts.dev_environment.debug import DebugEndpoint

    state_home = tmp_path.parent / f"{tmp_path.name}-state"
    identity = derive_instance_identity(tmp_path, state_home=state_home)
    ensure_instance_state(identity)
    events: list[str] = []

    class Debugger:
        def __init__(self, project: object, *, operation_lease: InstanceMutationLease) -> None:
            assert operation_lease.root_digest == identity.root_digest
            assert len(operation_lease.inherited_fds) == 1
            os.fstat(operation_lease.inherited_fds[0])

        def start(self) -> DebugEndpoint:
            events.append("start")
            return DebugEndpoint(
                "127.0.0.1",
                45678,
                "a" * 64,
                "awaiting_debugger",
                {"connect": {"host": "127.0.0.1", "port": 45678}},
            )

        def wait_ready(self, *, timeout_seconds: float) -> DebugEndpoint:
            assert timeout_seconds == 3
            if failure == "attach":
                raise ComposeError("sensitive adapter output")
            if failure == "interrupt":
                raise KeyboardInterrupt
            return DebugEndpoint("127.0.0.1", 45678, "a" * 64, "ready", {})

        def restore(self) -> LocalEndpoints:
            events.append("restore")
            return LocalEndpoints(
                "http://127.0.0.1:1", "http://127.0.0.1:2", "postgresql://127.0.0.1:3"
            )

    class SessionWait:
        def wait(self, timeout: float) -> None:
            assert timeout == 4
            events.append("session")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(cli, "BackendDebugger", Debugger)
    monkeypatch.setattr("scripts.dev_environment.cli.threading.Event", SessionWait)
    out, error = io.StringIO(), io.StringIO()
    assert (
        run(
            ["debug-backend", "--attach-timeout", "3", "--session-timeout", "4"],
            repo_root=tmp_path,
            state_home=state_home,
            stdout=out,
            stderr=error,
        )
        == expected
    )
    assert events[0] == "start" and events[-1] == "restore"
    messages = [json.loads(line) for line in out.getvalue().splitlines()]
    assert messages[0]["state"] == "awaiting_debugger"
    assert messages[-1]["state"] == "restored"
    assert "sensitive adapter output" not in out.getvalue() + error.getvalue()


def test_foreign_metadata_reports_ownership_without_echoing_its_contents(tmp_path: Path) -> None:
    state_home = tmp_path.parent / f"{tmp_path.name}-state"
    identity = derive_instance_identity(tmp_path, state_home=state_home)
    ensure_instance_state(identity)
    metadata = json.loads(identity.metadata_path.read_text())
    metadata["repositoryRoot"] = "/foreign/private-customer-root"
    identity.metadata_path.write_text(json.dumps(metadata))
    identity.metadata_path.chmod(0o600)
    out, error = io.StringIO(), io.StringIO()
    assert run(["status"], repo_root=tmp_path, state_home=state_home, stdout=out, stderr=error) == 2
    assert out.getvalue() == ""
    assert json.loads(error.getvalue())["diagnostic"]["reason"] == "foreign_state"
    assert "private-customer-root" not in error.getvalue()

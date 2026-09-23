from __future__ import annotations

import io
import json
import shutil
import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest
from scripts.bounded_process import InteractiveResult
from scripts.dev_environment import ci_witness
from scripts.dev_environment import watch_session as watch_owner
from scripts.dev_environment.ci_witness import execute_ci_witness, run
from scripts.dev_environment.compose import (
    ComposeError,
    ComposeProject,
    LocalEndpoints,
    ProviderInvocation,
    ServiceRuntimeIdentity,
    ServiceStatus,
)
from scripts.dev_environment.debug import DebugEndpoint, DebugProviderFailure
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity
from scripts.dev_environment.lifecycle import (
    InstanceMutationLease,
    TerminationRequest,
    operation_paths,
    sigterm_guard,
)
from scripts.dev_environment.watch_client_witness import WatchClientWitnessError
from scripts.mutation.detached_worktree_lifecycle import CleanupResult


class FakeProject:
    def __init__(
        self,
        repo_root: Path,
        *,
        fail_reset: bool = False,
        fail_up: bool = False,
    ) -> None:
        self.calls: list[str] = []
        self.fail_reset = fail_reset
        self.fail_up = fail_up
        self.repo_root = repo_root

    def assert_unallocated(self) -> None:
        self.calls.append("assert_unallocated")

    def up(self) -> LocalEndpoints:
        self.calls.append("up")
        if self.fail_up:
            raise ComposeError("Docker Compose up failed with status 1")
        return LocalEndpoints("http://127.0.0.1:1", "http://127.0.0.1:2", "postgres")

    def diagnostic_status(self) -> tuple[ServiceStatus, ...]:
        self.calls.append("diagnostic_status")
        return (ServiceStatus("migrate", "exited", "", 1),)

    def rendered_config(self) -> str:
        self.calls.append("rendered_config")
        return "services: {}\n"

    def assert_runtime_boundary(
        self,
        service: str,
        *,
        expected_uid: int,
        protected_path: str | None,
    ) -> None:
        self.calls.append(f"runtime:{service}:{expected_uid}:{protected_path}")

    def reset(self) -> None:
        self.calls.append("reset")
        if self.fail_reset:
            raise RuntimeError("cleanup failed")

    def watch_invocation(self) -> ProviderInvocation:
        raise AssertionError("watch verifier was replaced")

    def service_runtime_identity(self, service: str) -> ServiceRuntimeIdentity:
        raise AssertionError(f"watch verifier was replaced for {service}")

    def service_file(self, service: str, path: str) -> bytes | None:
        raise AssertionError(f"watch verifier was replaced for {service}:{path}")


def test_ci_witness_cleans_the_owned_project_after_success(tmp_path: Path) -> None:
    identity = derive_instance_identity(
        tmp_path,
        state_home=tmp_path.parent / f"{tmp_path.name}-state",
    )
    project = FakeProject(tmp_path)

    execute_ci_witness(
        identity,
        project,
        verifier=lambda *_args: None,
        watch_verifier=lambda _project: None,
    )

    assert project.calls == [
        "assert_unallocated",
        "up",
        "runtime:backend:65532:/run/ci-coordinator-secrets/runtime-dsn",
        "runtime:frontend:1000:None",
        "rendered_config",
        "reset",
    ]


def test_ci_witness_cleans_the_owned_project_after_failure(tmp_path: Path) -> None:
    identity = derive_instance_identity(
        tmp_path,
        state_home=tmp_path.parent / f"{tmp_path.name}-state",
    )
    project = FakeProject(tmp_path)

    with pytest.raises(RuntimeError, match="witness failed"):
        execute_ci_witness(
            identity,
            project,
            verifier=_fail_verifier,
        )

    assert project.calls[-1] == "reset"


def test_ci_witness_reports_bounded_status_after_startup_failure(tmp_path: Path) -> None:
    identity = derive_instance_identity(
        tmp_path,
        state_home=tmp_path.parent / f"{tmp_path.name}-state",
    )
    project = FakeProject(tmp_path, fail_up=True)

    with pytest.raises(
        ComposeError,
        match=(
            r"^Docker Compose up failed with status 1; "
            r"services=migrate=exited/none/exit=1$"
        ),
    ):
        execute_ci_witness(identity, project)

    assert project.calls == [
        "assert_unallocated",
        "up",
        "diagnostic_status",
        "reset",
    ]


def test_ci_witness_cleans_the_owned_project_after_sigterm(tmp_path: Path) -> None:
    identity = derive_instance_identity(
        tmp_path,
        state_home=tmp_path.parent / f"{tmp_path.name}-state",
    )
    project = FakeProject(tmp_path)

    with pytest.raises(TerminationRequest), sigterm_guard():
        execute_ci_witness(
            identity,
            project,
            verifier=lambda *_args: signal.raise_signal(signal.SIGTERM),
        )

    assert project.calls[-1] == "reset"


def test_ci_witness_rejects_local_execution_before_mutation(tmp_path: Path) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    status = run(
        repo_root=tmp_path,
        environment={},
        stdout=stdout,
        stderr=stderr,
    )

    assert status == 2
    assert stdout.getvalue() == ""
    assert "ci_environment_required" in stderr.getvalue()


def test_ci_witness_uses_an_isolated_source_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository_root = tmp_path / "repository"
    source_root = tmp_path / "isolated-source"
    state_home = tmp_path / "isolated-state"
    repository_root.mkdir()
    source_root.mkdir()
    observed: dict[str, object] = {}

    @contextmanager
    def checkout(root: Path) -> Iterator[tuple[Path, Path]]:
        observed["requestedRoot"] = root
        yield source_root, state_home

    def execute(identity: object, project: object, **kwargs: object) -> None:
        observed["identity"] = identity
        observed["project"] = project
        assert callable(kwargs["debug_verifier"])

    monkeypatch.setattr(ci_witness, "isolated_source_checkout", checkout)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(ci_witness, "ensure_instance_state", lambda _identity: {})
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        ci_witness,
        "ComposeProject",
        lambda identity, environment: (identity, environment),
    )
    monkeypatch.setattr(ci_witness, "execute_ci_witness", execute)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(ci_witness, "verify_isolated_watch_client", lambda *_args: None)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(ci_witness, "verify_watch_abandonment", lambda *_args: None)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(ci_witness, "verify_debug_provider_abandonment", lambda *_args: None)
    stdout = io.StringIO()

    assert run(repo_root=repository_root, environment={"CI": "true"}, stdout=stdout) == 0
    identity = observed["identity"]
    assert isinstance(identity, InstanceIdentity)
    assert observed["requestedRoot"] == repository_root
    assert identity.repo_root == source_root
    assert identity.state_home == state_home
    assert observed["project"] == (identity, {})
    assert json.loads(stdout.getvalue()) == {
        "projectName": identity.project_name,
        "state": "passed",
        "qualification": {
            "watchCancellation": None,
            "watchControllerDeath": None,
            "debugControllerDeath": None,
        },
    }


def test_ci_witness_reports_structured_failure_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        ci_witness,
        "ensure_instance_state",
        _fail_instance_state,
    )
    monkeypatch.setattr(ci_witness, "isolated_source_checkout", _same_source_checkout)

    status = run(
        repo_root=tmp_path,
        environment={"CI": "true"},
        stdout=stdout,
        stderr=stderr,
    )

    assert status == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == (
        '{"code":"connected_stack_witness_failed","completedPhases":[],"kind":"ComposeError",'
        '"lastStartedPhase":"instance-state",'
        '"reason":"provider unavailable"}\n'
    )


def _fail_verifier(*_args: object) -> None:
    raise RuntimeError("witness failed")


@pytest.mark.parametrize("failed_phase", ["none", "start", "attach", "ready", "cancel"])
@pytest.mark.parametrize("restore_outcome", ["passed", "failed", "cancelled"])
def test_debug_session_preserves_primary_restoration_and_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_phase: str, restore_outcome: str
) -> None:
    primary = KeyboardInterrupt() if failed_phase == "cancel" else RuntimeError("private-primary")
    restoration = (
        KeyboardInterrupt()
        if restore_outcome == "cancelled"
        else DebugProviderFailure("restore", 17, elapsed_ms=3)
    )
    events: list[str] = []
    endpoint = DebugEndpoint("127.0.0.1", 1234, "a" * 64, "ready", {})
    endpoints = LocalEndpoints("backend", "frontend", "postgres")

    def enter(phase: str) -> None:
        events.append(phase)
        if phase == failed_phase or (phase == "attach" and failed_phase == "cancel"):
            raise primary

    class Debugger:
        def start(self) -> DebugEndpoint:
            enter("start")
            return endpoint

        def wait_ready(self, *, timeout_seconds: float) -> DebugEndpoint:
            assert timeout_seconds == 60
            enter("ready")
            return endpoint

        def restore(self) -> LocalEndpoints:
            events.append("restore")
            if restore_outcome != "passed":
                raise restoration
            return endpoints

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(ci_witness, "BackendDebugger", lambda *_args, **_kwargs: Debugger())
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        ci_witness, "verify_backend_debugger", lambda *_args, **_kwargs: enter("attach")
    )
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(ci_witness, "verify_local_stack", lambda *_args: enter("verify-restored"))
    identity = derive_instance_identity(tmp_path)
    project = Mock(spec=ComposeProject)
    operation_lease = InstanceMutationLease(identity.root_digest, 99)
    if failed_phase == "none" and restore_outcome == "passed":
        ci_witness._verify_debug_session(identity, project, operation_lease)
        assert events == ["start", "attach", "ready", "restore", "verify-restored"]
        return
    with pytest.raises(BaseException) as caught:
        ci_witness._verify_debug_session(identity, project, operation_lease)
    assert events.count("restore") == 1 and "verify-restored" not in events
    if failed_phase == "cancel":
        assert caught.value is primary
    elif restore_outcome == "cancelled" or failed_phase == "none":
        assert caught.value is restoration
    elif restore_outcome == "passed":
        assert caught.value is primary
    else:
        assert isinstance(caught.value, ci_witness.DebugSessionFailure)
        assert caught.value.primary is primary and caught.value.restoration is restoration
        assert "private-primary" not in str(caught.value)
        assert "operation=restore; status=17;" in str(caught.value)


def test_multiple_retained_instances_preserve_every_location_and_original_watch_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other_root = tmp_path / "other"
    other_root.mkdir()
    first = derive_instance_identity(
        tmp_path, state_home=tmp_path.parent / f"{tmp_path.name}-state-a"
    )
    second = derive_instance_identity(
        other_root, state_home=tmp_path.parent / f"{tmp_path.name}-state-b"
    )
    process = InteractiveResult(-9, True, "cancelled", escalated=True)
    failure = ci_witness.RetainedInstanceError(
        first,
        ci_witness.RetainedInstanceError(
            second, WatchClientWitnessError("watch outcome unproved", process=process)
        ),
    )

    def fail(_identity: object) -> dict[str, str]:
        raise failure

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(ci_witness, "ensure_instance_state", fail)
    monkeypatch.setattr(ci_witness, "isolated_source_checkout", _same_source_checkout)
    stdout, stderr = io.StringIO(), io.StringIO()
    assert run(repo_root=tmp_path, environment={"CI": "true"}, stdout=stdout, stderr=stderr) == 2
    report = json.loads(stderr.getvalue())
    assert stdout.getvalue() == ""
    assert report["causeKind"] == "WatchClientWitnessError"
    assert report["process"]["escalated"] is True
    assert [item["sourceRoot"] for item in report["retainedInstances"]] == [
        str(first.repo_root),
        str(second.repo_root),
    ]
    assert report["retainedInstance"] == report["retainedInstances"][0]


def _fail_instance_state(_identity: object) -> dict[str, str]:
    raise ComposeError("provider unavailable")


@contextmanager
def _same_source_checkout(repo_root: Path) -> Iterator[tuple[Path, Path]]:
    yield repo_root, repo_root.parent / f"{repo_root.name}-state"


def test_debug_effect_failure_still_resets_the_owned_ci_instance(tmp_path: Path) -> None:
    identity = derive_instance_identity(
        tmp_path, state_home=tmp_path.parent / f"{tmp_path.name}-state"
    )
    project = FakeProject(tmp_path)

    def fail_debug(_lease: InstanceMutationLease) -> None:
        raise RuntimeError("mapped breakpoint was not reached")

    with pytest.raises(RuntimeError, match="mapped breakpoint"):
        execute_ci_witness(
            identity,
            project,
            verifier=lambda *_args: None,
            watch_verifier=lambda _project: None,
            debug_verifier=fail_debug,
        )
    assert project.calls[-1] == "reset"


@pytest.mark.parametrize("phase", ["feedback", "watch_client"])
@pytest.mark.parametrize(
    "process",
    [
        InteractiveResult(1, True),
        InteractiveResult(-9, True, "cancelled", escalated=True),
        InteractiveResult(None, False, "lifecycle", escalated=True),
        InteractiveResult(130, True, "timeout", cancellation_signal_sent=True),
        InteractiveResult(130, True, "cancelled", cancellation_signal_sent=True),
    ],
    ids=["unadmitted-exit", "escalation", "non-quiescent", "timeout", "clean-stop-assertion"],
)
def test_complete_ci_cleanup_follows_watch_completion_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    process: InteractiveResult,
) -> None:
    lifecycle = _FixtureLifecycle(tmp_path / "disposable")
    project = FakeProject(lifecycle.worktree)
    nonces: list[str] = []

    def state(identity: InstanceIdentity) -> dict[str, str]:
        identity.secrets_directory.mkdir(parents=True, mode=0o700)
        (identity.secrets_directory / "retained-canary").write_text("synthetic-credential")
        return {}

    def failed_watch(identity: InstanceIdentity) -> None:
        with watch_owner.owned_watch_session(identity) as session:
            session.run(
                ["synthetic-provider"],
                cwd=identity.repo_root,
                env={},
                client_contract=watch_owner.WatchClientContract.COMPOSE_JOINED_WATCH,
            )
        nonces.append(session.nonce)
        project.calls.append("watch_result")
        raise WatchClientWitnessError(
            "synthetic stop failure", process=process, cancellation=session.outcome
        )

    identity = derive_instance_identity(
        lifecycle.worktree, state_home=lifecycle.temp_root / "state"
    )
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(ci_witness, "DetachedWorktreeLifecycle", lambda **_: lifecycle)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(ci_witness, "ensure_instance_state", state)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(ci_witness, "ComposeProject", lambda *_: project)
    monkeypatch.setattr(ci_witness, "_verify_debug_session", lambda *_: None)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(ci_witness, "verify_application_logs", lambda *_: None)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(ci_witness, "verify_localhost_ssh_logs", lambda *_: None)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(watch_owner, "run_interactive", lambda *_args, **_kwargs: process)
    monkeypatch.setattr(
        ci_witness,
        "_verify_material_feedback",
        lambda *_args, **_kwargs: failed_watch(identity) if phase == "feedback" else None,
    )
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        ci_witness,
        "verify_isolated_watch_client",
        lambda *_: failed_watch(identity) if phase == "watch_client" else None,
    )
    # Bind the real execute owner while replacing only the unrelated HTTP smoke.
    execute = ci_witness.execute_ci_witness

    def without_http(
        current: InstanceIdentity,
        current_project: ci_witness.StackProject,
        *,
        watch_verifier: ci_witness.WatchVerifier,
        debug_verifier: Callable[[InstanceMutationLease], None] | None = None,
        log_verifier: Callable[[], object] | None = None,
    ) -> None:
        execute(
            current,
            current_project,
            verifier=lambda *_: None,
            watch_verifier=watch_verifier,
            debug_verifier=debug_verifier,
            log_verifier=log_verifier,
        )

    monkeypatch.setattr(ci_witness, "execute_ci_witness", without_http)
    stdout, stderr = io.StringIO(), io.StringIO()
    assert (
        run(
            repo_root=tmp_path,
            environment={"CI": "true"},
            stdout=stdout,
            stderr=stderr,
        )
        == 2
    )
    assert stdout.getvalue() == ""
    failure = json.loads(stderr.getvalue())
    assert failure["lastStartedPhase"] == (
        "material-feedback" if phase == "feedback" else "watch-cancellation"
    )
    assert failure["completedPhases"] == (
        ["applicationLogs", "sshApplicationLogs"]
        if phase == "feedback"
        else ["applicationLogs", "debugSession", "materialInputs", "sshApplicationLogs"]
    )
    if process.failure_kind == "cancelled" and not process.escalated:
        assert failure["kind"] == "WatchClientWitnessError"
        assert failure["cancellation"]["state"] == "quiescent"
        assert lifecycle.cleanup_calls == 1
        assert not lifecycle.temp_root.exists()
        assert "retainedInstance" not in failure
        return
    assert failure["kind"] == "RetainedInstanceError"
    assert failure["causeKind"] == "WatchClientWitnessError"
    assert failure["retainedInstance"]["watch"]["nonce"] == nonces[0]
    assert failure["process"]["process_group_quiescent"] is process.process_group_quiescent
    assert failure["cancellation"]["state"] == "blocked"
    assert "synthetic-credential" not in stderr.getvalue()
    assert project.calls[-1] == "watch_result"
    assert lifecycle.cleanup_calls == 0
    assert lifecycle.worktree.is_dir()
    assert operation_paths(identity, create=False).session.is_file()
    assert (identity.secrets_directory / "retained-canary").read_text() == "synthetic-credential"


def test_source_context_cleans_after_ordinary_failure_with_no_unproved_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifecycle = _FixtureLifecycle(tmp_path / "disposable")
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(ci_witness, "DetachedWorktreeLifecycle", lambda **_: lifecycle)
    with (
        pytest.raises(RuntimeError, match="ordinary assertion"),
        ci_witness.isolated_source_checkout(tmp_path),
    ):
        raise RuntimeError("ordinary assertion")
    assert lifecycle.cleanup_calls == 1
    assert not lifecycle.temp_root.exists()


class _FixtureLifecycle:
    def __init__(self, root: Path) -> None:
        self.temp_root = root
        self.worktree = root / "source"
        self.worktree.mkdir(parents=True)
        self.cleanup_calls = 0

    def add_detached_worktree(self, revision: str) -> None:
        assert revision == "HEAD"

    def cleanup(self) -> CleanupResult:
        self.cleanup_calls += 1
        shutil.rmtree(self.temp_root)
        return CleanupResult("passed", "removed")

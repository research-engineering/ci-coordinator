"""Protocol/control tests; only verify_watch_client against Compose is native proof."""

from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

import pytest
from scripts.bounded_process import InteractiveResult
from scripts.dev_environment import watch_client_witness, watch_session
from scripts.dev_environment.compose import (
    LocalEndpoints,
    ProviderInvocation,
    ServiceRuntimeIdentity,
    ServiceStatus,
)
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity
from scripts.dev_environment.lifecycle import (
    OperationBlocked,
    instance_operation_lock,
    operation_paths,
)
from scripts.dev_environment.private_files import atomic_write_private_text, bounded_private_lock
from scripts.dev_environment.watch_client_witness import (
    WatchClientWitnessError,
    verify_isolated_watch_client,
    verify_watch_client,
)
from scripts.dev_environment.watch_session import CancelResult, owned_watch_session


class Project:
    def __init__(self, identity: InstanceIdentity, events: list[str]) -> None:
        self.repo_root = identity.repo_root
        self.events = events
        self.validation_failure: int | None = None
        self.validations = 0
        self.statuses = tuple(
            ServiceStatus(service, "running", "healthy", 0)
            for service in ("backend", "frontend", "postgres")
        )

    def validate(self) -> None:
        self.validations += 1
        self.events.append("validate")
        if self.validations == self.validation_failure:
            raise WatchClientWitnessError("provider_unavailable")

    @contextmanager
    def observation_budget(self, seconds: float = 30.0) -> Iterator[None]:
        assert 0 < seconds <= 30
        yield

    def diagnostic_status(self) -> tuple[ServiceStatus, ...]:
        return self.statuses

    def watch_invocation(self) -> ProviderInvocation:
        return ProviderInvocation(("docker", "compose", "watch", "--no-up"), self.repo_root, {})

    def assert_unallocated(self) -> None:
        self.events.append("assert_unallocated")

    def up(self) -> LocalEndpoints:
        self.events.append("up")
        return LocalEndpoints("api", "ui", "postgres")

    def reset(self) -> None:
        self.events.append("reset")

    def service_runtime_identity(self, service: str) -> ServiceRuntimeIdentity:
        raise AssertionError("protocol fixture is not a native provider identity")


@pytest.fixture
def identity(tmp_path: Path) -> InstanceIdentity:
    root = tmp_path / "worktree"
    root.mkdir()
    return derive_instance_identity(root, state_home=tmp_path / "state")


def _controlled_result(code: int) -> InteractiveResult:
    return InteractiveResult(code, True, "cancelled", cancellation_signal_sent=True)


def _install_protocol_fixture(
    identity: InstanceIdentity,
    monkeypatch: pytest.MonkeyPatch,
    events: list[str],
    process: InteractiveResult,
    *,
    stale_ack: bool = False,
) -> None:
    def run(_command: str, _args: object, **kwargs: object) -> InteractiveResult:
        assert threading.current_thread() is threading.main_thread()
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["stdout_fd"] == 99
        assert kwargs["graceful_seconds"] == watch_session.WATCH_GRACEFUL_SECONDS == 2
        assert kwargs["kill_seconds"] == watch_session.WATCH_KILL_SECONDS == 1
        events.append("run")
        return process

    class Ack(Future[dict[str, object]]):
        def __init__(self, nonce: str) -> None:
            super().__init__()
            self.nonce = nonce

        def result(self, timeout: float | None = None) -> dict[str, object]:
            assert timeout is not None and timeout > 0
            paths = operation_paths(identity)
            with bounded_private_lock(paths.control), bounded_private_lock(paths.mutation):
                events.append("ack_after_release")
            nonce = "0" * 64 if stale_ack else self.nonce
            return {
                "version": 1,
                "rootDigest": identity.root_digest,
                "nonce": nonce,
                "readiness": "compose-watch-enabled",
                "cancellation": asdict(CancelResult("quiescent", "watch_client_stopped", nonce)),
            }

    @contextmanager
    def requester(
        _identity: InstanceIdentity, nonce: str
    ) -> Iterator[tuple[int, Future[dict[str, object]]]]:
        yield 99, Ack(nonce)

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(watch_session, "run_interactive", run)
    monkeypatch.setattr(watch_client_witness, "_requester", requester)


def test_acknowledgment_precedes_fresh_mutation_admission(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    _install_protocol_fixture(identity, monkeypatch, events, _controlled_result(0))
    verify_watch_client(identity, Project(identity, events))
    assert events == ["validate", "validate", "run", "ack_after_release", "validate"]
    with instance_operation_lock(identity):
        pass


def test_isolated_native_phase_releases_up_lock_and_resets_only_after_ack(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    _install_protocol_fixture(identity, monkeypatch, events, _controlled_result(130))
    monkeypatch.setattr(
        watch_client_witness, "verify_watch_inflight", lambda *_args: events.append("inflight")
    )
    verify_isolated_watch_client(identity, Project(identity, events))
    assert events == [
        "assert_unallocated",
        "up",
        "validate",
        "validate",
        "run",
        "ack_after_release",
        "validate",
        "inflight",
        "reset",
    ]


def test_isolated_native_phase_preserves_an_abnormal_fence_instead_of_resetting(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    _install_protocol_fixture(
        identity, monkeypatch, events, InteractiveResult(130, True, "cancelled")
    )
    with pytest.raises(WatchClientWitnessError, match="clean_stop_unproved") as caught:
        verify_isolated_watch_client(identity, Project(identity, events))
    assert "reset" not in events
    assert caught.value.cancellation is not None
    assert caught.value.cancellation.nonce is not None
    with pytest.raises(OperationBlocked), instance_operation_lock(identity):
        pytest.fail("failed watch proof allowed cleanup to cross its fence")


@pytest.mark.parametrize(
    "process",
    [
        InteractiveResult(130, True, "cancelled"),
        InteractiveResult(-15, True, "cancelled"),
        InteractiveResult(0, True, "cancelled", escalated=True),
        InteractiveResult(0, False, "cancelled"),
        InteractiveResult(130, False, "cancelled", cancellation_signal_sent=True),
        InteractiveResult(0, True, "timeout"),
    ],
)
def test_abnormal_native_result_cannot_be_overridden_by_a_requester_receipt(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch, process: InteractiveResult
) -> None:
    events: list[str] = []
    _install_protocol_fixture(identity, monkeypatch, events, process)
    with pytest.raises(WatchClientWitnessError, match="clean_stop_unproved") as caught:
        verify_watch_client(identity, Project(identity, events))
    assert caught.value.process is process
    cancellation = caught.value.cancellation
    assert cancellation is not None and cancellation.state == "blocked"
    assert cancellation.recovery is not None and "separate Git worktree" in cancellation.recovery
    assert events.count("validate") == 2
    with pytest.raises(OperationBlocked), instance_operation_lock(identity):
        pytest.fail("the failed native client witness released its fence")


def test_a_stale_ack_cannot_witness_admission(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    _install_protocol_fixture(
        identity, monkeypatch, events, _controlled_result(130), stale_ack=True
    )
    with pytest.raises(WatchClientWitnessError, match="acknowledgment_unproved"):
        verify_watch_client(identity, Project(identity, events))
    assert events.count("validate") == 2


@pytest.mark.parametrize("failed_admission", [1, 2])
def test_read_only_admission_failure_does_not_publish_an_abandonment(
    identity: InstanceIdentity, failed_admission: int
) -> None:
    project = Project(identity, [])
    project.validation_failure = failed_admission
    with pytest.raises(WatchClientWitnessError, match="provider_unavailable"):
        verify_watch_client(identity, project)
    assert not operation_paths(identity).session.exists()
    with instance_operation_lock(identity):
        pass


def test_no_missing_or_stopped_service_is_silently_admitted(identity: InstanceIdentity) -> None:
    project = Project(identity, [])
    project.statuses = project.statuses[:2]
    with pytest.raises(WatchClientWitnessError, match="requires_running_services"):
        verify_watch_client(identity, project)
    with instance_operation_lock(identity):
        pass


def test_all_six_legitimate_services_are_admitted(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []
    project = Project(identity, events)
    project.statuses += tuple(
        ServiceStatus(service, "exited", "", 0)
        for service in ("database-provision", "migrate", "database-access")
    )
    _install_protocol_fixture(identity, monkeypatch, events, _controlled_result(130))
    verify_watch_client(identity, project)
    assert events[-2:] == ["ack_after_release", "validate"]


@pytest.mark.parametrize(
    "service",
    [
        ServiceStatus("foreign", "running", "healthy", 0),
        ServiceStatus("backend", "running", "healthy", 0),
        ServiceStatus("database-access", "exited", "", 1),
    ],
)
def test_foreign_duplicate_and_failed_oneshot_services_are_rejected(
    identity: InstanceIdentity, service: ServiceStatus
) -> None:
    project = Project(identity, [])
    project.statuses += (service,)
    with pytest.raises(WatchClientWitnessError, match="requires_running_services"):
        verify_watch_client(identity, project)


@pytest.mark.parametrize("state, health", [("exited", "healthy"), ("running", "unhealthy")])
def test_each_core_service_requires_both_running_and_healthy(
    identity: InstanceIdentity, state: str, health: str
) -> None:
    project = Project(identity, [])
    project.statuses = (*project.statuses[:2], ServiceStatus("postgres", state, health, 0))
    with pytest.raises(WatchClientWitnessError, match="requires_running_services"):
        verify_watch_client(identity, project)


def test_native_watcher_cannot_move_to_a_signal_handler_worker(identity: InstanceIdentity) -> None:
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(verify_watch_client, identity, Project(identity, []))
        with pytest.raises(WatchClientWitnessError, match="requires_main_thread"):
            future.result(timeout=2)


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (b"Watch enabled\n", None),
        (b"initial sync complete\nWatch enabled\n", None),
        (b"service | Watch enabled\n", "stream_closed"),
        (b"Watch enabled", "stream_closed"),
        (b"Watch disabled\n", "stream_closed"),
        (b"x" * 4097 + b"\nWatch enabled\n", "output_limit"),
    ],
)
def test_only_the_admitted_complete_provider_line_is_a_readiness_barrier(
    content: bytes, expected: str | None
) -> None:
    read_fd, write_fd = os.pipe()
    try:
        assert os.write(write_fd, content) == len(content)
        os.close(write_fd)
        write_fd = -1
        if expected is None:
            watch_client_witness._wait_for_readiness(read_fd)
        else:
            with pytest.raises(WatchClientWitnessError, match=expected):
                watch_client_witness._wait_for_readiness(read_fd)
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


def test_readiness_deadline_and_output_limit_are_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_fd, write_fd = os.pipe()
    try:
        monkeypatch.setattr(watch_client_witness, "_READY_SECONDS", 0)
        with pytest.raises(WatchClientWitnessError, match="readiness_timeout"):
            watch_client_witness._wait_for_readiness(read_fd)
        monkeypatch.setattr(watch_client_witness, "_READY_SECONDS", 1)
        monkeypatch.setattr(watch_client_witness, "_MAX_READY_BYTES", 4)
        os.write(write_fd, b"Watch enabled\n")
        with pytest.raises(WatchClientWitnessError, match="output_limit"):
            watch_client_witness._wait_for_readiness(read_fd)
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_cancel_waiter_drains_provider_output_before_the_owner_releases(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    request_written = threading.Event()
    drained = threading.Event()
    paths = operation_paths(identity)
    original_read = os.read

    def write(path: Path, content: str) -> None:
        atomic_write_private_text(path, content)
        if path == paths.stop:
            request_written.set()

    read_fd, write_fd = os.pipe()

    def read(descriptor: int, size: int) -> bytes:
        chunk = original_read(descriptor, size)
        if descriptor == read_fd and chunk:
            drained.set()
        return chunk

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(watch_session, "atomic_write_private_text", write)
    monkeypatch.setattr(os, "read", read)
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            with owned_watch_session(identity) as session:
                future = executor.submit(
                    watch_client_witness._cancel_while_draining, identity, session.nonce, read_fd
                )
                assert request_written.wait(timeout=2)
                assert os.write(write_fd, b"provider output during stop\n") > 0
                assert drained.wait(timeout=2)
                assert not future.done()
            assert future.result(timeout=2).state == "quiescent"
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_requester_is_stdlib_bounded_and_carries_the_exact_nonce(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    nonce = "a" * 64
    read_fd, write_fd = os.pipe()
    expected = {"only": "receipt"}

    def run(command: str, args: tuple[str, ...], **kwargs: object) -> InteractiveResult:
        assert command
        assert args[:3] == ("-S", "-m", "scripts.dev_environment.watch_client_witness")
        assert args[args.index("--nonce") + 1] == nonce
        assert args[args.index("--ready-fd") + 1] == str(read_fd)
        assert kwargs["inherited_fds"] == (read_fd,)
        assert kwargs["timeout_seconds"] == 50
        assert kwargs["stderr"] == subprocess.DEVNULL
        descriptor = kwargs["stdout_fd"]
        assert isinstance(descriptor, int)
        os.write(descriptor, b'{"only":"receipt"}')
        return InteractiveResult(0, True)

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(watch_client_witness, "run_interactive", run)
    try:
        assert (
            watch_client_witness._request_cancel(identity, nonce, read_fd, threading.Event())
            == expected
        )
        with pytest.raises(OSError):
            os.fstat(read_fd)
    finally:
        os.close(write_fd)

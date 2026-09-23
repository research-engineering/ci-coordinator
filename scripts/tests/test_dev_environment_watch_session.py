from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from scripts.bounded_process import InteractiveResult
from scripts.dev_environment import watch_session
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity
from scripts.dev_environment.lifecycle import (
    OperationBlocked,
    OperationBusy,
    instance_operation_lock,
    operation_paths,
)
from scripts.dev_environment.private_files import atomic_write_private_text, bounded_private_lock
from scripts.dev_environment.watch_session import (
    WatchClientContract,
    cancel_watch,
    inspect_watch,
    owned_watch_session,
)


@pytest.fixture
def identity(tmp_path: Path) -> InstanceIdentity:
    root = tmp_path / "worktree"
    root.mkdir()
    return derive_instance_identity(root, state_home=tmp_path / "state")


def test_cancel_releases_every_lock_before_returning(identity: InstanceIdentity) -> None:
    result = cancel_watch(identity)
    assert (result.state, result.reason) == ("quiescent", "no_watch")
    paths = operation_paths(identity)
    with bounded_private_lock(paths.control), bounded_private_lock(paths.mutation):
        pass
    with instance_operation_lock(identity):
        pass


def test_a_new_watcher_can_win_the_cancel_to_down_gap(identity: InstanceIdentity) -> None:
    stopped = cancel_watch(identity)
    assert stopped.state == "quiescent"
    paths = operation_paths(identity)
    with owned_watch_session(identity) as current:
        with pytest.raises(OperationBusy), instance_operation_lock(identity):
            pytest.fail("down crossed a newly admitted watcher")
        assert not current.stop_requested()
        assert not paths.stop.exists()
    assert current.outcome.reason == "watch_not_started"
    with instance_operation_lock(identity):
        pass


def test_stale_nonce_never_cancels_the_current_session(identity: InstanceIdentity) -> None:
    with owned_watch_session(identity) as current:
        stale = "0" * 64 if current.nonce != "0" * 64 else "1" * 64
        result = cancel_watch(identity, expected_nonce=stale)
        assert (result.state, result.reason, result.nonce) == ("blocked", "stale_nonce", stale)
        assert current.stop_requested() is False


def test_a_stale_stop_file_is_inert_for_a_fresh_nonce(identity: InstanceIdentity) -> None:
    paths = operation_paths(identity)
    stale = "1" * 64
    atomic_write_private_text(
        paths.stop,
        json.dumps({"version": 1, "rootDigest": identity.root_digest, "nonce": stale}),
    )
    with owned_watch_session(identity) as current:
        assert current.nonce != stale
        assert current.stop_requested() is False


def test_cancellation_deadline_retains_the_exact_stop_request(identity: InstanceIdentity) -> None:
    with owned_watch_session(identity) as current:
        result = cancel_watch(identity, expected_nonce=current.nonce, timeout_seconds=0.02)
        assert (result.state, result.reason) == ("blocked", "cancellation_timeout")
        assert result.nonce == current.nonce
        assert current.stop_requested() is True
    assert cancel_watch(identity, expected_nonce=current.nonce).state == "quiescent"


def test_racing_cancellers_do_not_invert_cleanup_locks(
    identity: InstanceIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published = threading.Event()
    request_written = threading.Event()
    finish_owner = threading.Event()
    paths = operation_paths(identity)
    original_write = atomic_write_private_text

    def observe_request(path: Path, content: str) -> None:
        original_write(path, content)
        if path == paths.stop:
            request_written.set()

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(watch_session, "atomic_write_private_text", observe_request)

    def watch() -> None:
        with owned_watch_session(identity) as current:
            published.set()
            assert request_written.wait(timeout=5)
            assert current.stop_requested()
            assert finish_owner.wait(timeout=5)

    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(watch)
        try:
            assert published.wait(timeout=5)
            first = executor.submit(cancel_watch, identity, timeout_seconds=3)
            assert request_written.wait(timeout=5)
            second = cancel_watch(identity, timeout_seconds=0)
            assert (second.state, second.reason) == ("blocked", "control_busy")
            with pytest.raises(OperationBusy), owned_watch_session(identity):
                pytest.fail("replacement watch crossed the cancel control interval")
        finally:
            finish_owner.set()
        owner.result(timeout=5)
        result = first.result(timeout=5)
    assert (result.state, result.reason) == ("quiescent", "watch_not_started")
    duplicate = cancel_watch(identity, expected_nonce=result.nonce)
    assert duplicate == result
    with bounded_private_lock(paths.control), bounded_private_lock(paths.mutation):
        pass


def test_a_non_watch_mutation_is_busy_and_never_receives_a_stop(identity: InstanceIdentity) -> None:
    with instance_operation_lock(identity):
        result = cancel_watch(identity, timeout_seconds=0)
        assert (result.state, result.reason) == ("blocked", "operation_busy")
        assert not operation_paths(identity).stop.exists()


def test_process_only_success_never_admits_another_provider_mutation(
    identity: InstanceIdentity,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        watch_session, "run_interactive", lambda *_args, **_kwargs: InteractiveResult(0, True)
    )
    with owned_watch_session(identity) as current:
        process = current.run(("provider",), cwd=identity.repo_root, env={})
        assert process.process_group_quiescent
    assert current.outcome.reason == "client_contract_unadmitted"
    assert cancel_watch(identity, expected_nonce=current.nonce) == current.outcome
    with pytest.raises(OperationBlocked), instance_operation_lock(identity):
        pytest.fail("a fake process result admitted an unsupported client contract")
    with pytest.raises(OperationBlocked):
        current.run(("provider",), cwd=identity.repo_root, env={})


def test_cleanup_cannot_overwrite_a_replacement_nonce(identity: InstanceIdentity) -> None:
    paths = operation_paths(identity)
    replacement: str | None = None
    with pytest.raises(OperationBlocked), owned_watch_session(identity) as current:
        replacement = "1" * 64 if current.nonce != "1" * 64 else "2" * 64
        record = json.loads(paths.session.read_text())
        record["nonce"] = replacement
        atomic_write_private_text(paths.session, json.dumps(record))
    assert json.loads(paths.session.read_text())["nonce"] == replacement
    assert cancel_watch(identity).state == "blocked"


@pytest.mark.parametrize("corruption", ["root", "nonce", "phase", "reason", "version", "json"])
def test_invalid_watch_state_is_preserved_and_blocks_admission(
    identity: InstanceIdentity,
    corruption: str,
) -> None:
    paths = operation_paths(identity)
    record: dict[str, object] = {
        "version": 1,
        "rootDigest": identity.root_digest,
        "nonce": "1" * 64,
        "phase": "active",
        "reason": "watch_active",
    }
    keys = {"root": "rootDigest", "nonce": "nonce", "phase": "phase", "reason": "reason"}
    if corruption in keys:
        record[keys[corruption]] = []
    if corruption == "version":
        record["version"] = True
    content = "{" if corruption == "json" else json.dumps(record)
    atomic_write_private_text(paths.session, content)
    assert cancel_watch(identity).reason == "session_unavailable"
    assert paths.session.read_text() == content
    with pytest.raises(OperationBlocked), instance_operation_lock(identity):
        pytest.fail("invalid watch state admitted a provider mutation")


@pytest.mark.parametrize("budget", [-1, float("inf"), float("nan"), 121, True])
def test_cancel_rejects_invalid_budgets_without_creating_state(
    identity: InstanceIdentity, budget: float
) -> None:
    with pytest.raises(ValueError):
        cancel_watch(identity, timeout_seconds=budget)
    assert not identity.state_home.exists()


@pytest.mark.parametrize("failure", ["unsupported Compose", "missing state"])
def test_read_only_admission_failure_without_run_does_not_poison_the_instance(
    identity: InstanceIdentity, failure: str
) -> None:
    with pytest.raises(ValueError, match=failure), owned_watch_session(identity) as current:
        raise ValueError(failure)
    assert current.outcome.state == "quiescent"
    assert current.outcome.reason == "watch_not_started"
    assert not operation_paths(identity).session.exists()
    assert cancel_watch(identity, expected_nonce=current.nonce) == current.outcome
    with instance_operation_lock(identity):
        pass


def test_spawn_failure_is_a_proven_no_start(identity: InstanceIdentity) -> None:
    with owned_watch_session(identity) as current:
        process = current.run(
            (str(identity.repo_root / "missing-provider"),), cwd=identity.repo_root, env={}
        )
        assert process.failure_kind == "spawn"
        assert process.started is False
    assert current.outcome.reason == "watch_not_started"
    assert cancel_watch(identity, expected_nonce=current.nonce).state == "quiescent"
    with instance_operation_lock(identity):
        pass


def test_cancel_before_run_prevents_spawn_without_poisoning_the_instance(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "scripts.bounded_process.subprocess.Popen",
        lambda *_args, **_kwargs: pytest.fail("cancelled watch spawned a provider"),
    )
    with owned_watch_session(identity) as current:
        assert cancel_watch(identity, timeout_seconds=0).reason == "cancellation_timeout"
        process = current.run(("provider",), cwd=identity.repo_root, env={})
        assert process.started is False
    assert cancel_watch(identity, expected_nonce=current.nonce) == current.outcome
    assert current.outcome.reason == "watch_not_started"


@pytest.mark.parametrize(
    "process, reason",
    [
        (InteractiveResult(0, True), "watch_client_stopped"),
        (InteractiveResult(0, True, "cancelled"), "watch_client_stopped"),
        (
            InteractiveResult(130, True, "cancelled", cancellation_signal_sent=True),
            "watch_client_stopped",
        ),
        (InteractiveResult(130, True), "watch_client_failed"),
        (InteractiveResult(130, True, "cancelled"), "watch_client_failed"),
        (
            InteractiveResult(130, True, "timeout", cancellation_signal_sent=True),
            "watch_client_failed",
        ),
        (
            InteractiveResult(-15, True, "cancelled", cancellation_signal_sent=True),
            "watch_client_failed",
        ),
        (
            InteractiveResult(1, True, "cancelled", cancellation_signal_sent=True),
            "watch_client_failed",
        ),
        (
            InteractiveResult(
                130, True, "cancelled", escalated=True, cancellation_signal_sent=True
            ),
            "watch_client_forced",
        ),
        (InteractiveResult(1, True), "watch_client_failed"),
        (InteractiveResult(0, True, "timeout"), "watch_client_failed"),
        (InteractiveResult(0, True, "residual-descendant"), "watch_client_failed"),
        (InteractiveResult(0, True, "cancelled", escalated=True), "watch_client_forced"),
        (InteractiveResult(0, False), "process_stop_unproven"),
    ],
)
def test_client_completion_admission_uses_all_independent_operands(
    identity: InstanceIdentity,
    monkeypatch: pytest.MonkeyPatch,
    process: InteractiveResult,
    reason: str,
) -> None:
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(watch_session, "run_interactive", lambda *_args, **_kwargs: process)
    with owned_watch_session(identity) as current:
        current.run(
            ("fixture-provider",),
            cwd=identity.repo_root,
            env={},
            client_contract=WatchClientContract.COMPOSE_JOINED_WATCH,
        )
    assert current.outcome.reason == reason
    assert cancel_watch(identity, expected_nonce=current.nonce) == current.outcome
    if reason == "watch_client_stopped":
        with instance_operation_lock(identity):
            pass
    else:
        with pytest.raises(OperationBlocked), instance_operation_lock(identity):
            pytest.fail("an abnormal client stop released mutation admission")


def test_a_completed_old_nonce_cannot_cancel_a_replacement(identity: InstanceIdentity) -> None:
    with owned_watch_session(identity) as previous:
        pass
    assert cancel_watch(identity, expected_nonce=previous.nonce).state == "quiescent"
    with owned_watch_session(identity) as current:
        result = cancel_watch(identity, expected_nonce=previous.nonce)
        assert result.reason == "stale_nonce"
        assert current.stop_requested() is False
        with pytest.raises(OperationBusy), instance_operation_lock(identity):
            pytest.fail("down crossed a replacement watch after successful cancel")


def test_failed_completion_publication_does_not_create_a_clean_receipt(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = operation_paths(identity)

    def fail_fsync(_path: Path) -> None:
        raise OSError("fixture directory fsync failure")

    with pytest.raises(OSError, match="fsync failure"), owned_watch_session(identity) as current:
        # noinspection PyUnresolvedReferences
        monkeypatch.setattr(watch_session, "_fsync_directory", fail_fsync)
    assert paths.completed.exists()
    assert paths.session.exists()
    assert cancel_watch(identity, expected_nonce=current.nonce).state == "blocked"
    with pytest.raises(OperationBlocked), instance_operation_lock(identity):
        pytest.fail("a completion receipt overrode the retained abnormal fence")


def test_inspection_neither_allocates_nor_admits_mutation(identity: InstanceIdentity) -> None:
    assert not identity.state_home.exists()
    assert inspect_watch(identity).phase == "absent"
    assert not identity.state_home.exists()
    with owned_watch_session(identity) as session:
        observation = inspect_watch(identity)
        assert observation.phase == "active"
        assert observation.reason == "watch_active"
        assert observation.nonce == session.nonce
        with pytest.raises(OperationBusy), instance_operation_lock(identity):
            pytest.fail("read-only inspection changed admission")
    assert inspect_watch(identity).phase == "absent"


def test_inspection_retains_the_exact_blocked_nonce(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        watch_session, "run_interactive", lambda *_args, **_kwargs: InteractiveResult(130, True)
    )
    with owned_watch_session(identity) as session:
        session.run(
            ("fixture",),
            cwd=identity.repo_root,
            env={},
            client_contract=WatchClientContract.COMPOSE_JOINED_WATCH,
        )
    observation = inspect_watch(identity)
    assert (observation.phase, observation.reason, observation.nonce) == (
        "blocked",
        "watch_client_failed",
        session.nonce,
    )
    with pytest.raises(OperationBlocked), instance_operation_lock(identity):
        pytest.fail("inspection silently recovered an abnormal session")


def test_inspection_rejects_foreign_or_unsafe_state_without_repair(
    identity: InstanceIdentity,
) -> None:
    paths = operation_paths(identity)
    atomic_write_private_text(
        paths.session,
        json.dumps(
            {
                "version": 1,
                "rootDigest": "0" * 64,
                "nonce": "a" * 64,
                "phase": "active",
                "reason": "watch_active",
            }
        ),
    )
    before = paths.session.read_bytes()
    assert inspect_watch(identity).phase == "unavailable"
    assert paths.session.read_bytes() == before
    paths.session.unlink()
    identity.state_home.chmod(0o755)
    assert inspect_watch(identity).phase == "unavailable"
    assert identity.state_home.stat().st_mode & 0o777 == 0o755

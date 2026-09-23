from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from scripts.dev_environment.identity import derive_instance_identity
from scripts.dev_environment.lifecycle import (
    OperationBlocked,
    OperationBusy,
    instance_operation_lock,
)


def test_operation_lock_rejects_a_conflict_and_survives_reset_boundary(
    tmp_path: Path,
) -> None:
    state_home = tmp_path.parent / f"{tmp_path.name}-state"
    identity = derive_instance_identity(tmp_path, state_home=state_home)
    first_entered = threading.Event()
    release_first = threading.Event()

    def hold_first() -> None:
        with instance_operation_lock(identity):
            first_entered.set()
            assert release_first.wait(timeout=5)

    def reject_second() -> None:
        with (
            pytest.raises(OperationBusy, match="operation_busy"),
            instance_operation_lock(identity),
        ):
            pytest.fail("a conflicting mutation was admitted")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(hold_first)
        try:
            assert first_entered.wait(timeout=5)
            executor.submit(reject_second).result(timeout=2)
        finally:
            release_first.set()
        first.result(timeout=5)

    with instance_operation_lock(identity):
        pass

    lock_path = state_home / "operation-locks" / f"{identity.project_name}.lock"
    assert lock_path.is_file()
    assert not lock_path.is_relative_to(identity.state_directory)


def test_operation_locks_do_not_serialize_different_worktrees(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    state_home = tmp_path / "state"
    first = derive_instance_identity(first_root, state_home=state_home)
    second = derive_instance_identity(second_root, state_home=state_home)
    with instance_operation_lock(first), instance_operation_lock(second):
        assert first.project_name != second.project_name


def test_mutation_lease_exposes_only_the_current_context_descriptor(tmp_path: Path) -> None:
    identity = derive_instance_identity(
        tmp_path, state_home=tmp_path.parent / f"{tmp_path.name}-state"
    )
    with instance_operation_lock(identity) as lease:
        assert lease.root_digest == identity.root_digest
        assert len(lease.inherited_fds) == 1
        descriptor = lease.inherited_fds[0]
        os.fstat(descriptor)
        with pytest.raises(OperationBusy), instance_operation_lock(identity):
            pytest.fail("exposing the lease released the existing owner")
    with pytest.raises(OperationBlocked):
        _ = lease.inherited_fds
    with pytest.raises(OSError):
        os.fstat(descriptor)

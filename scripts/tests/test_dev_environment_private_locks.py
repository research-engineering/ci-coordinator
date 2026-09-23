from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from scripts.dev_environment.private_files import (
    PrivateFileError,
    PrivateLockBusy,
    bounded_private_lock,
    ensure_private_directory,
    exclusive_private_lock,
)


def test_bounded_lock_times_out_while_the_owner_still_holds_it(tmp_path: Path) -> None:
    ensure_private_directory(tmp_path)
    path = tmp_path / "mutation.lock"
    with bounded_private_lock(path):
        start = time.monotonic()
        with pytest.raises(PrivateLockBusy), bounded_private_lock(path, timeout_seconds=0.05):
            pytest.fail("overlapping lock owner")
        assert time.monotonic() - start < 1


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan"), True])
def test_lock_budget_is_finite_before_any_open(tmp_path: Path, timeout: float) -> None:
    path = tmp_path / "mutation.lock"
    with pytest.raises(ValueError), bounded_private_lock(path, timeout_seconds=timeout):
        pytest.fail("invalid lock budget admitted")
    assert not path.exists()


def test_original_lock_callers_keep_their_contract(tmp_path: Path) -> None:
    ensure_private_directory(tmp_path)
    path = tmp_path / "mutation.lock"
    with exclusive_private_lock(path) as original:
        assert original is None
        with pytest.raises(PrivateLockBusy), bounded_private_lock(path):
            pytest.fail("new lock bypassed the original owner")
    with bounded_private_lock(path) as descriptor:
        assert os.get_inheritable(descriptor) is False


@pytest.mark.parametrize("unsafe_kind", ["symlink", "hardlink", "mode"])
def test_unsafe_lock_never_changes_a_foreign_inode(tmp_path: Path, unsafe_kind: str) -> None:
    ensure_private_directory(tmp_path)
    target = tmp_path / "target"
    target.write_text("preserved")
    target.chmod(0o644)
    path = tmp_path / "mutation.lock"
    if unsafe_kind == "symlink":
        path.symlink_to(target)
    elif unsafe_kind == "hardlink":
        os.link(target, path)
    else:
        path = target
    with pytest.raises(PrivateFileError), bounded_private_lock(path):
        pytest.fail("unsafe lock admitted")
    assert target.read_text() == "preserved"
    assert target.stat().st_mode & 0o777 == 0o644


def test_closing_the_parent_descriptor_does_not_unlock_the_inheriting_child(tmp_path: Path) -> None:
    ensure_private_directory(tmp_path)
    path = tmp_path / "mutation.lock"
    child: subprocess.Popen[bytes] | None = None
    try:
        with bounded_private_lock(path) as descriptor:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-S",
                    "-c",
                    "import os, select; select.select([0], [], [], 10); os.read(0, 1)",
                ],
                stdin=subprocess.PIPE,
                pass_fds=(descriptor,),
            )
        # The direct Popen child inherits the descriptor at exec. This proves
        # the lock primitive, not inheritance through Docker/Compose plugins.
        with pytest.raises(PrivateLockBusy), bounded_private_lock(path):
            pytest.fail("parent close released the child's lock")
    finally:
        if child is not None:
            child.communicate(b"x", timeout=5)
    with bounded_private_lock(path):
        pass

from __future__ import annotations

import json
import os
import selectors
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from scripts.bounded_process import spawn
from scripts.dev_environment.identity import derive_instance_identity
from scripts.dev_environment.lifecycle import (
    OperationBusy,
    instance_operation_lock,
    operation_paths,
)
from scripts.dev_environment.private_files import PrivateLockBusy, bounded_private_lock

_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_PYTHON = getattr(sys, "_base_executable", sys.executable)


@pytest.mark.parametrize("kill_parent", [False, True])
@pytest.mark.parametrize("instance_lease", [False, True])
def test_captured_child_keeps_its_admitted_lease_until_its_own_exit(
    tmp_path: Path, kill_parent: bool, instance_lease: bool
) -> None:
    lock = tmp_path / "environment.lock"
    root = tmp_path / "worktree"
    root.mkdir()
    identity = derive_instance_identity(root, state_home=tmp_path / "state")
    if instance_lease:
        lock = operation_paths(identity).mutation
    ready_read, ready_write = os.pipe()
    release_read, release_write = os.pipe()
    process: subprocess.Popen[bytes] | None = None
    try:
        process = subprocess.Popen(
            [
                _PYTHON,
                "-S",
                "-m",
                "scripts.tests.bounded_process_lock_witness",
                "--lock",
                str(lock),
                "--ready-fd",
                str(ready_write),
                "--release-fd",
                str(release_read),
                *(
                    ["--instance-root", str(root), "--state-home", str(identity.state_home)]
                    if instance_lease
                    else []
                ),
            ],
            cwd=_SOURCE_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            pass_fds=(ready_write, release_read),
        )
        os.close(ready_write)
        ready_write = -1
        os.close(release_read)
        release_read = -1
        with selectors.DefaultSelector() as selector:
            selector.register(ready_read, selectors.EVENT_READ)
            assert selector.select(timeout=5), "captured child did not reach its barrier"
            assert os.read(ready_read, 1) == b"R"
        if kill_parent:
            process.kill()
            assert process.wait(timeout=5) == -signal.SIGKILL
        with pytest.raises(PrivateLockBusy), bounded_private_lock(lock):
            pytest.fail("installation acquired the live child's environment lock")
        if instance_lease:
            with pytest.raises(OperationBusy), instance_operation_lock(identity):
                pytest.fail("controller death released the live provider's mutation lease")
            other_root = tmp_path / "other-worktree"
            other_root.mkdir()
            other = derive_instance_identity(other_root, state_home=identity.state_home)
            with instance_operation_lock(other):
                pass
        os.write(release_write, b"x")
        if not kill_parent:
            stdout, stderr = process.communicate(timeout=5)
            assert process.returncode == 0, stderr
            result = json.loads(stdout)
            assert result["status"] == 0
            assert result["stdout"] == "owned-output\n"
            assert result["stderr"] == "owned-error\n"
        with bounded_private_lock(lock, timeout_seconds=5):
            pass
    finally:
        for descriptor in (ready_read, ready_write, release_read, release_write):
            if descriptor >= 0:
                os.close(descriptor)
        if process is not None:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()


@pytest.mark.parametrize("descriptors", [(True,), (-1,), (0,), (1,), (2,), "bad"])
def test_finite_capture_rejects_invalid_descriptor_capabilities_before_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, descriptors: tuple[int, ...]
) -> None:
    monkeypatch.setattr(
        "scripts.bounded_process.subprocess.Popen",
        lambda *_args, **_kwargs: pytest.fail("invalid descriptor capability reached spawn"),
    )
    with pytest.raises(ValueError, match="descriptor"):
        spawn(_PYTHON, (), cwd=tmp_path, max_buffer=128, inherited_fds=descriptors)


def test_finite_capture_rejects_duplicate_and_closed_descriptors(tmp_path: Path) -> None:
    descriptor = os.open(tmp_path / "lease", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        with pytest.raises(ValueError, match="unique"):
            spawn(
                _PYTHON,
                (),
                cwd=tmp_path,
                max_buffer=128,
                inherited_fds=(descriptor, descriptor),
            )
    finally:
        os.close(descriptor)
    with pytest.raises(ValueError, match="unavailable"):
        spawn(_PYTHON, (), cwd=tmp_path, max_buffer=128, inherited_fds=(descriptor,))

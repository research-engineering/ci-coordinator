"""Signal and detached-worktree adapter over the bounded process owner."""

from __future__ import annotations

import os
import shutil
import signal
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Final, Literal

from scripts.bounded_git import BoundedGitError, BoundedGitResult, run_git
from scripts.bounded_process import SUPPORTED_PLATFORMS, CommandResult, spawn

DEFAULT_MAX_BUFFER_BYTES: Final = 10 * 1024 * 1024
DEFAULT_TIMEOUT_MS: Final = 600_000
SIGNAL_RETHROW_DELAY_SECONDS: Final = 1.0

type SignalHandler = Callable[[int, FrameType | None], object] | int | None


class UnsupportedPlatformError(RuntimeError):
    """Raised when process-group behavior has not been proved for the host."""


@dataclass(frozen=True, slots=True)
class CleanupResult:
    state: Literal["passed", "failed"]
    worktree_removal: Literal["not-needed", "removed", "failed"]
    removal_exit_code: int | None = None
    prune_exit_code: int | None = None
    residual_registration: bool | None = None
    output: str | None = None

    def to_report(self) -> dict[str, object]:
        report: dict[str, object] = {
            "state": self.state,
            "worktreeRemoval": self.worktree_removal,
        }
        if self.state == "failed":
            report["removalExitCode"] = self.removal_exit_code
            report["pruneExitCode"] = self.prune_exit_code
            report["residualRegistration"] = self.residual_registration
        if self.output is not None:
            report["output"] = self.output
        return report


class DetachedWorktreeLifecycle:
    """Own signal state and one temporary detached worktree."""

    def __init__(self, *, repo_root: str | Path, temp_prefix: str) -> None:
        _assert_supported_platform()
        self.repo_root = Path(repo_root).resolve()
        self._temp_prefix = temp_prefix
        self._temp_root: Path | None = None
        self.worktree_registration_attempted = False
        self.received_signal: signal.Signals | None = None
        self.stopping = False
        self._signal_handlers: dict[signal.Signals, SignalHandler] = {}
        self._cleanup_started = False
        self._cleanup_result: CleanupResult | None = None

    @property
    def temp_root(self) -> Path:
        if self._temp_root is None:
            if self._cleanup_started:
                raise RuntimeError("temporary root was not allocated before cleanup")
            self.assert_running()
            self._temp_root = Path(tempfile.mkdtemp(prefix=self._temp_prefix))
        return self._temp_root

    @property
    def worktree(self) -> Path:
        return self.temp_root / "worktree"

    def install_signal_handlers(self) -> None:
        if self._signal_handlers:
            return
        try:
            for handled_signal in (signal.SIGINT, signal.SIGTERM):
                previous = signal.getsignal(handled_signal)
                self._signal_handlers[handled_signal] = previous

                def handler(
                    _signum: int,
                    _frame: FrameType | None,
                    *,
                    received: signal.Signals = handled_signal,
                ) -> None:
                    self.request_stop(received)

                signal.signal(handled_signal, handler)
        except BaseException:
            self.dispose_signal_handlers()
            raise

    def dispose_signal_handlers(self) -> None:
        for handled_signal, previous in self._signal_handlers.items():
            signal.signal(handled_signal, previous)
        self._signal_handlers.clear()

    def assert_running(self) -> None:
        if self.stopping:
            reason = self.received_signal.name if self.received_signal is not None else "shutdown"
            raise RuntimeError(f"mutation run interrupted by {reason}")

    def add_detached_worktree(self, source_revision: str) -> None:
        self.assert_running()
        worktree = self.worktree
        self.worktree_registration_attempted = True
        self.git(("worktree", "add", "--detach", str(worktree), source_revision))

    def git(
        self,
        args: Sequence[str],
    ) -> BoundedGitResult:
        self.assert_running()
        try:
            result = run_git(
                self.repo_root,
                args,
                source_environment=os.environ,
                stop_requested=self._stop_requested,
            )
        except (BoundedGitError, OSError) as error:
            raise RuntimeError(str(error)) from error
        self.assert_running()
        return result

    def run(
        self,
        command: str,
        args: Sequence[str],
        *,
        cwd: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        max_buffer: int = DEFAULT_MAX_BUFFER_BYTES,
        timeout_ms: int = DEFAULT_TIMEOUT_MS,
    ) -> CommandResult:
        if type(timeout_ms) is not int or timeout_ms <= 0:
            raise ValueError("timeout_ms must be a positive integer")
        self.assert_running()
        result = spawn(
            command,
            args,
            cwd=Path(cwd).resolve() if cwd is not None else self.repo_root,
            env=env,
            max_buffer=max_buffer,
            stop_requested=self._stop_requested,
            timeout_seconds=timeout_ms / 1_000,
        )
        if self.stopping and result.error is None:
            return CommandResult(
                status=None,
                stdout=result.stdout,
                stderr=result.stderr,
                error=f"{command}: execution cancelled",
                failure_kind="cancelled",
                signal=result.signal,
            )
        return result

    def request_stop(self, received_signal: signal.Signals) -> None:
        if self.received_signal is None:
            self.received_signal = received_signal
        self.stopping = True

    def cleanup(self) -> CleanupResult:
        if self._cleanup_started:
            if self._cleanup_result is None:
                raise RuntimeError("cleanup is already running")
            return self._cleanup_result

        self._cleanup_started = True
        try:
            result = self._perform_cleanup()
        except Exception as error:
            result = CleanupResult(
                state="failed",
                worktree_removal=(
                    "failed" if self.worktree_registration_attempted else "not-needed"
                ),
                output=f"{type(error).__name__}: {error}",
            )
        self._cleanup_result = result
        return result

    def _perform_cleanup(self) -> CleanupResult:
        temp_root = self._temp_root
        if temp_root is None:
            return CleanupResult(state="passed", worktree_removal="not-needed")
        if not self.worktree_registration_attempted:
            _remove_path(temp_root)
            return CleanupResult(state="passed", worktree_removal="not-needed")

        worktree = temp_root / "worktree"
        removal = self._cleanup_git(("worktree", "remove", "--force", str(worktree)))
        if removal.status == 0:
            _remove_path(temp_root)
            return CleanupResult(state="passed", worktree_removal="removed")

        _remove_path(worktree)
        prune = self._cleanup_git(("worktree", "prune"))
        listed = self._cleanup_git(("worktree", "list", "--porcelain"))
        _remove_path(temp_root)
        return CleanupResult(
            state="failed",
            worktree_removal="failed",
            removal_exit_code=removal.status,
            prune_exit_code=prune.status,
            residual_registration=(
                listed.status != 0 or f"worktree {worktree}" in listed.stdout.split("\n")
            ),
            output="\n".join(
                part for part in (removal.stdout, removal.stderr, removal.error) if part
            ),
        )

    def _cleanup_git(self, args: Sequence[str]) -> CommandResult:
        try:
            result = run_git(
                self.repo_root,
                args,
                check=False,
                source_environment=os.environ,
            )
        except (BoundedGitError, OSError) as error:
            return CommandResult(None, "", "", str(error), "lifecycle")
        return CommandResult(result.status, result.stdout, result.stderr)

    def _stop_requested(self) -> bool:
        return self.stopping

    def rethrow_signal_if_needed(self) -> None:
        if self.received_signal is None:
            return
        received_signal = self.received_signal
        self.dispose_signal_handlers()
        signal.signal(received_signal, signal.SIG_DFL)
        try:
            os.kill(os.getpid(), received_signal)
            time.sleep(SIGNAL_RETHROW_DELAY_SECONDS)
        except OSError:
            pass
        raise SystemExit(128 + received_signal.value)


def _assert_supported_platform() -> None:
    if sys.platform not in SUPPORTED_PLATFORMS:
        raise UnsupportedPlatformError(
            "mutation lifecycle is proved only for Linux and macOS POSIX process groups; "
            f"unsupported platform: {sys.platform}"
        )


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)

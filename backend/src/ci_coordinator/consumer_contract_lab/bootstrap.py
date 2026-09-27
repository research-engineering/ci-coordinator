"""Trusted launcher for an exact-commit consumer contract lab image."""

from __future__ import annotations

import argparse
import ast
import errno
import hashlib
import io
import json
import math
import os
import select
import selectors
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tarfile
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager, nullcontext, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory, mkdtemp
from types import FrameType
from typing import Final, Protocol, cast

_COORDINATOR_PACKAGE_PATH: Final = "backend/src/ci_coordinator"
_GIT_TIMEOUT_SECONDS: Final = 30
_LAB_TIMEOUT_SECONDS: Final = 180
_MAX_ARCHIVE_BYTES: Final = 20_971_520
_MAX_GIT_METADATA_BYTES: Final = 2_097_152
_MAX_GIT_STDERR_BYTES: Final = 65_536
_MAX_PACKAGE_BYTES: Final = 16_777_216
_MAX_PACKAGE_ENTRIES: Final = 1_024
LAB_LIFETIME_PROTOCOL: Final = 1
_LIFETIME_ARGUMENT: Final = "--managed-lab-lifetime"
_STOP_FRAME: Final = struct.Struct("!4sd")
_SUPPORTED_PLATFORMS: Final = frozenset({"darwin", "linux"})
_DELETE_TREE: Final = "import shutil,sys;shutil.rmtree(sys.argv[1])"
_MANAGED_LAUNCHER: Final = (
    "import sys;source=sys.argv.pop(1);sys.path.insert(0,source);"
    "from ci_coordinator.consumer_contract_lab.process import managed_lifetime_entrypoint;"
    "raise SystemExit(managed_lifetime_entrypoint(lambda: "
    "__import__('ci_coordinator.consumer_contract_lab.cli',fromlist=['main']).main(sys.argv[1:])))"
)
_INTERNAL_LAUNCHER: Final = (
    "import sys;"
    "source=sys.argv.pop(1);"
    "sys.path.insert(0,source);"
    "from ci_coordinator.consumer_contract_lab.cli import main;"
    "raise SystemExit(main(sys.argv[1:]))"
)


class ConsumerLabBootstrapError(RuntimeError):
    """The trusted launcher cannot create or execute one exact source image."""


class _ImageExit(BaseException):
    def __init__(self, status: int) -> None:
        self.status = status


_POLL_INTERVAL_SECONDS: Final = 0.02


class LifetimeScope(Protocol):
    @property
    def inherited_fds(self) -> tuple[int, ...]: ...

    @property
    def deadline(self) -> float | None: ...

    @property
    def cancellation_deadline(self) -> float | None: ...

    def stop_requested(self) -> bool: ...


class LifetimeCancelled(BaseException):
    def __init__(self, signum: int = 0) -> None:
        self.exit_code = 128 + signum if signum else 1
        super().__init__("consumer lab execution stopped")


_DEFERRED_CANCELLATION: ContextVar[list[LifetimeCancelled] | None] = ContextVar(
    "lab_acquisition_cancellation", default=None
)
_INTERRUPTIBLE: ContextVar[bool] = ContextVar("lab_interruptible", default=True)


@contextmanager
def _defer_cancellation() -> Iterator[None]:
    pending = _DEFERRED_CANCELLATION.get()
    token = None
    if pending is None:
        pending = []
        token = _DEFERRED_CANCELLATION.set(pending)
    mode_token = _INTERRUPTIBLE.set(False)
    primary: BaseException | None = None
    try:
        yield
    except BaseException as error:
        primary = error
        raise
    finally:
        _INTERRUPTIBLE.reset(mode_token)
        if token is not None:
            _DEFERRED_CANCELLATION.reset(token)
        if token is not None and pending:
            if primary is None:
                raise pending[0]
            primary.add_note("Managed cancellation was received during owned cleanup")


@contextmanager
def _interruptible_cancellation() -> Iterator[None]:
    pending = _DEFERRED_CANCELLATION.get()
    token = _INTERRUPTIBLE.set(True)
    primary: BaseException | None = None
    try:
        if pending:
            raise pending[0]
        yield
    except BaseException as error:
        primary = error
        raise
    finally:
        _INTERRUPTIBLE.reset(token)
        if primary is None and pending:
            raise pending[0]


def current_lifetime() -> LifetimeScope | None:
    return _LIFETIME.get()


@contextmanager
def borrowed_lifetime(scope: LifetimeScope) -> Iterator[None]:
    token = _LIFETIME.set(scope)
    try:
        yield
    finally:
        _LIFETIME.reset(token)


def assert_lifetime_running() -> None:
    scope = current_lifetime()
    if scope is not None and (
        scope.stop_requested()
        or (scope.deadline is not None and time.monotonic() >= scope.deadline)
    ):
        if isinstance(scope, _SourceCleanupScope):
            raise ConsumerLabBootstrapError("owned source cleanup allowance exhausted")
        raise LifetimeCancelled(getattr(scope, "signum", 0))


@dataclass(slots=True)
class _ReceivedLifetime:
    inherited_fds: tuple[int, ...]
    deadline: float | None
    reader: int
    grace: float
    signum: int = 0
    signal_at: float | None = None
    first_force: float | None = None
    observed: bool = False
    revoked: bool = False

    def _receive(self) -> None:
        if self.revoked:
            return
        try:
            if not select.select([self.reader], [], [], 0)[0]:
                return
            if self.observed:
                self.revoked = True
                return
            self.observed = self.revoked = True
            data = os.read(self.reader, _STOP_FRAME.size)
            if len(data) != _STOP_FRAME.size:
                return
            tag, force = _STOP_FRAME.unpack(data)
            if tag != b"CF1:" or not math.isfinite(force) or force > time.monotonic() + self.grace:
                return
            self.first_force = force
            self.revoked = bool(select.select([self.reader], [], [], 0)[0])
        except (OSError, ValueError):
            self.observed = self.revoked = True

    def stop_requested(self) -> bool:
        self._receive()
        return self.observed or bool(self.signum)

    @property
    def cancellation_deadline(self) -> float | None:
        self._receive()
        bounds = [self.first_force]
        if self.revoked:
            bounds.append(0.0)
        if self.signal_at is not None:
            bounds.append(self.signal_at + self.grace)
        admitted = [value for value in bounds if value is not None]
        return min(admitted) if admitted else None

    def signal(self, signum: int, _frame: FrameType | None) -> None:
        if not self.signum:
            self.signum = signum
            self.signal_at = time.monotonic()
            error = LifetimeCancelled(signum)
            pending = _DEFERRED_CANCELLATION.get()
            if pending is None:
                raise error
            pending.append(error)
            if _INTERRUPTIBLE.get() and sys.exception() is None:
                raise error


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate lifetime field")
        result[key] = value
    return result


def _decode_lifetime(payload: str) -> _ReceivedLifetime:
    if sys.platform not in _SUPPORTED_PLATFORMS or len(payload.encode()) > os.sysconf("SC_ARG_MAX"):
        raise ValueError("unsupported lifetime transport")
    import fcntl

    value = json.loads(payload, object_pairs_hook=_unique_object)
    if not isinstance(value, dict) or set(value) != {
        "version",
        "leases",
        "stopFd",
        "stopGrace",
        "deadline",
    }:
        raise ValueError("invalid lifetime fields")
    deadline, grace, reader, rows = (
        value[key] for key in ("deadline", "stopGrace", "stopFd", "leases")
    )
    if type(value["version"]) is not int or value["version"] != LAB_LIFETIME_PROTOCOL:
        raise ValueError("unsupported lifetime version")
    if deadline is not None and (
        type(deadline) not in {int, float}
        or not math.isfinite(deadline)
        or deadline <= time.monotonic()
    ):
        raise ValueError("invalid lifetime deadline")
    if type(grace) not in {int, float} or not math.isfinite(grace) or not 0 < grace <= 1:
        raise ValueError("invalid lifetime stop allowance")
    if type(reader) is not int or reader < 3 or not stat.S_ISFIFO(os.fstat(reader).st_mode):
        raise ValueError("invalid lifetime stop reader")
    flags = fcntl.fcntl(reader, fcntl.F_GETFL)
    if flags & os.O_ACCMODE != os.O_RDONLY or not flags & os.O_NONBLOCK:
        raise ValueError("invalid lifetime stop direction")
    if not isinstance(rows, list) or not rows:
        raise ValueError("missing lifetime leases")
    descriptors = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"fd", "device", "inode"}
            or any(type(item) is not int for item in row.values())
        ):
            raise ValueError("invalid lifetime lease")
        descriptor = row["fd"]
        if descriptor < 3:
            raise ValueError("stdio is not a lifetime lease")
        identity = os.fstat(descriptor)
        if fcntl.fcntl(descriptor, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDWR:
            raise ValueError("lifetime lease access changed")
        if not stat.S_ISREG(identity.st_mode) or (identity.st_dev, identity.st_ino) != (
            row["device"],
            row["inode"],
        ):
            raise ValueError("lifetime lease identity changed")
        descriptors.append(descriptor)
    if len(set((reader, *descriptors))) != len(descriptors) + 1:
        raise ValueError("duplicate lifetime descriptor")
    return _ReceivedLifetime(tuple(descriptors), deadline, reader, grace)


@contextmanager
def received_lifetime(payload: str) -> Iterator[_ReceivedLifetime]:
    scope = _decode_lifetime(payload)
    parent = current_lifetime()
    if parent is not None:
        raise ValueError("exec receiver cannot replace an active borrowed scope")
    previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
    primary: BaseException | None = None
    try:
        for number in previous:
            signal.signal(number, scope.signal)
        with borrowed_lifetime(scope):
            assert_lifetime_running()
            yield scope
    except BaseException as error:
        primary = error
        raise
    finally:
        close_error: BaseException | None = None
        for number, handler in previous.items():
            try:
                signal.signal(number, handler)
            except BaseException as error:
                if close_error is None:
                    close_error = error
        for descriptor in (scope.reader, *scope.inherited_fds):
            try:
                os.close(descriptor)
            except BaseException as error:
                if close_error is None:
                    close_error = error
        if close_error is not None:
            if primary is None:
                raise close_error
            primary.add_note("consumer lab lifetime cleanup raised a secondary exception")


def managed_lifetime_entrypoint(main: Callable[[], int]) -> int:
    original = sys.argv
    present = [
        arg
        for arg in original[1:]
        if arg == _LIFETIME_ARGUMENT or arg.startswith(_LIFETIME_ARGUMENT + "=")
    ]
    if not present:
        return main()
    if len(original) < 3 or original[-2] != _LIFETIME_ARGUMENT or present != [_LIFETIME_ARGUMENT]:
        return 2
    try:
        with ExitStack() as lifetime:
            try:
                lifetime.enter_context(received_lifetime(original[-1]))
            except (OSError, ValueError, TypeError):
                return 2
            try:
                sys.argv = original[:-2]
                try:
                    status = main()
                except SystemExit as error:
                    if error.code is None or error.code == 0:
                        assert_lifetime_running()
                    raise
                assert_lifetime_running()
                return status
            finally:
                sys.argv = original
    except LifetimeCancelled as error:
        return error.exit_code


@dataclass(frozen=True, slots=True)
class LifetimeInvocation:
    arguments: tuple[str, ...]
    inherited_fds: tuple[int, ...]
    cancellation_fd: int
    deadline: float


@contextmanager
def lifetime_invocation(
    arguments: Sequence[str], *, timeout_seconds: float
) -> Iterator[LifetimeInvocation]:
    assert_lifetime_running()
    scope = current_lifetime()
    if scope is None:
        raise ValueError("lifetime invocation requires an explicit scope")
    descriptors = scope.inherited_fds
    deadline = time.monotonic() + timeout_seconds
    if scope.deadline is not None:
        deadline = min(deadline, scope.deadline)
    reader, writer = os.pipe()
    try:
        os.set_blocking(reader, False)
        os.set_blocking(writer, False)
        payload = json.dumps(
            {
                "version": LAB_LIFETIME_PROTOCOL,
                "deadline": deadline,
                "stopFd": reader,
                "stopGrace": 1,
                "leases": [
                    {"fd": fd, "device": os.fstat(fd).st_dev, "inode": os.fstat(fd).st_ino}
                    for fd in descriptors
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        if len(payload.encode()) > os.sysconf("SC_ARG_MAX"):
            raise ValueError("lifetime transport exceeds native argv bound")
        yield LifetimeInvocation(
            (*arguments, _LIFETIME_ARGUMENT, payload), (*descriptors, reader), writer, deadline
        )
    finally:
        os.close(reader)
        os.close(writer)


@dataclass(slots=True)
class _StopSchedule:
    scope: LifetimeScope
    writer: int | None
    limit: float
    term_cap: float = 1.0
    force: float | None = None
    hard: float | None = None
    first_force: float | None = None
    revoked: bool = False

    def bounds(self) -> tuple[float, float]:
        now = time.monotonic()
        limits = [
            value
            for value in (self.limit, self.scope.deadline, self.scope.cancellation_deadline)
            if value is not None
        ]
        limit = min(limits)
        remaining = max(0.0, limit - now)
        force = min(limit, now + min(self.term_cap, remaining / 2))
        hard = min(limit, force + min(1.0, remaining / 4))
        if self.force is not None:
            force, hard = min(force, self.force), min(hard, cast(float, self.hard))
        if self.first_force is None:
            self.first_force = force
            if self.writer is not None:
                with suppress(BrokenPipeError, BlockingIOError):
                    os.write(self.writer, _STOP_FRAME.pack(b"CF1:", force))
        elif self.force is not None and force < self.force and not self.revoked:
            self.revoked = True
            if self.writer is not None:
                with suppress(BrokenPipeError, BlockingIOError):
                    os.write(self.writer, b"!")
        self.force, self.hard = force, hard
        return force, hard


def _group_alive(group: int, *, deadline: float | None = None) -> bool:
    try:
        os.killpg(group, 0)
    except OSError as error:
        return error.errno != errno.ESRCH
    if sys.platform != "linux":
        return True
    try:
        with os.scandir("/proc") as entries:
            count = 0
            for entry in entries:
                if deadline is not None and time.monotonic() >= deadline:
                    return True
                if not entry.name.isascii() or not entry.name.isdecimal():
                    continue
                count += 1
                if count > 65_536:
                    return True
                try:
                    with open(Path(entry.path) / "stat", "rb", buffering=0) as stream:
                        data = stream.read(4097)
                except FileNotFoundError:
                    continue
                if len(data) > 4096 or (end := data.rfind(b") ")) < 0:
                    return True
                fields = data[end + 2 :].split()
                if len(fields) < 18:
                    return True
                if int(fields[2]) == group and (
                    fields[0] not in {b"Z", b"X", b"x"} or int(fields[17]) != 1
                ):
                    return True
    except (OSError, ValueError):
        return True
    return False


def _drain_managed(process: subprocess.Popen[bytes], schedule: _StopSchedule) -> bool:
    first = schedule.first_force is None
    force, hard = schedule.bounds()
    if first and process.returncode is None:
        _signal_process_group(process.pid, signal.SIGTERM)
    killed = False
    while True:
        force, hard = schedule.bounds()
        now = time.monotonic()
        if process.returncode is None:
            exited = _child_exited_without_reaping(process)
            if not killed and (now >= force or exited):
                _signal_process_group(process.pid, signal.SIGKILL)
                killed = True
            if exited and (sys.platform != "linux" or not _group_alive(process.pid, deadline=hard)):
                process.wait(timeout=0)
        if process.returncode is not None and not _group_alive(process.pid, deadline=hard):
            return True
        if now >= hard:
            if process.returncode is None:
                _signal_process_group(process.pid, signal.SIGKILL)
                process.poll()
            return process.returncode is not None and not _group_alive(process.pid, deadline=hard)
        time.sleep(min(_POLL_INTERVAL_SECONDS, max(0, hard - time.monotonic())))


def _signal_process_group(process_group_id: int, process_signal: signal.Signals) -> bool:
    try:
        os.killpg(process_group_id, process_signal)
        return True
    except OSError as error:
        if error.errno in {errno.ESRCH, errno.EPERM}:
            return False
        raise


def _child_exited_without_reaping(process: subprocess.Popen[bytes]) -> bool:
    if process.returncode is not None:
        return True
    try:
        result = os.waitid(
            os.P_PID,
            process.pid,
            os.WEXITED | os.WNOHANG | os.WNOWAIT,
        )
    except ChildProcessError:
        return process.returncode is not None
    return result is not None


_LIFETIME: ContextVar[LifetimeScope | None] = ContextVar("bootstrap_lifetime", default=None)


@dataclass(frozen=True, slots=True)
class _Blob:
    path: str
    object_id: str
    size: int


def main(argv: Sequence[str] | None = None) -> int:
    original = sys.argv
    try:
        if argv is not None:
            sys.argv = [original[0], *argv]
        return managed_lifetime_entrypoint(_main)
    finally:
        sys.argv = original


def _main() -> int:
    arguments = _parser().parse_args()
    coordinator_root = Path(arguments.coordinator_root).expanduser().resolve()
    target_root = Path(arguments.target_root).expanduser().resolve()
    try:
        commit = _commit(coordinator_root)
        blobs = _package_blobs(coordinator_root, commit)
        archive = _archive(coordinator_root, commit)
        with _owned_source_directory() as temporary:
            image_root = (Path(temporary) / "source").resolve()
            _materialize(image_root, archive, blobs)
            assert_lifetime_running()
            source_root = image_root / "backend/src"
            package_root = source_root / "ci_coordinator"
            cache_root = (Path(temporary) / "pycache").resolve()
            cache_root.mkdir()
            status = _run_exact_image(
                source_root=source_root,
                package_root=package_root,
                cache_root=cache_root,
                coordinator_root=coordinator_root,
                coordinator_commit=commit,
                target_root=target_root,
                profile=arguments.profile,
                output=arguments.output,
            )
            if current_lifetime() is not None and status != 0:
                raise _ImageExit(status)
            return status
    except _ImageExit as error:
        return error.status
    except ConsumerLabBootstrapError as error:
        return _reject(type(error).__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ci-coordinator-consumer-lab")
    parser.add_argument("--coordinator-root", required=True)
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", required=True)
    return parser


def _commit(root: Path) -> str:
    if not root.is_dir():
        raise ConsumerLabBootstrapError("coordinator repository is unavailable")
    content = _git(root, ("rev-parse", "--verify", "HEAD^{commit}"), 128)
    try:
        commit = content.decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ConsumerLabBootstrapError("coordinator commit is not ASCII") from error
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ConsumerLabBootstrapError("coordinator commit is not a canonical SHA-1 object id")
    return commit


def _package_blobs(root: Path, commit: str) -> tuple[_Blob, ...]:
    content = _git(
        root,
        (
            "--literal-pathspecs",
            "ls-tree",
            "-lrz",
            "--full-tree",
            commit,
            "--",
            _COORDINATOR_PACKAGE_PATH,
        ),
        _MAX_GIT_METADATA_BYTES,
    )
    if not content or not content.endswith(b"\x00"):
        raise ConsumerLabBootstrapError("coordinator package tree is empty or malformed")
    blobs: list[_Blob] = []
    total_bytes = 0
    for item in content[:-1].split(b"\x00"):
        metadata, separator, raw_path = item.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 4:
            raise ConsumerLabBootstrapError("coordinator package tree entry is malformed")
        mode, object_type, raw_object_id, raw_size = fields
        try:
            path = raw_path.decode("utf-8")
            object_id = raw_object_id.decode("ascii")
            size = int(raw_size)
        except (UnicodeDecodeError, ValueError) as error:
            raise ConsumerLabBootstrapError(
                "coordinator package tree entry identity is invalid"
            ) from error
        if (
            object_type != b"blob"
            or mode not in {b"100644", b"100755"}
            or not _is_package_path(path)
            or _is_python_cache_path(path)
            or len(object_id) != 40
            or any(character not in "0123456789abcdef" for character in object_id)
            or size < 0
        ):
            raise ConsumerLabBootstrapError("coordinator package tree contains an unsafe entry")
        total_bytes += size
        if total_bytes > _MAX_PACKAGE_BYTES:
            raise ConsumerLabBootstrapError("coordinator package exceeds its byte bound")
        blobs.append(_Blob(path, object_id, size))
        if len(blobs) > _MAX_PACKAGE_ENTRIES:
            raise ConsumerLabBootstrapError("coordinator package exceeds its entry bound")
    if not blobs or tuple(blob.path for blob in blobs) != tuple(
        sorted({blob.path for blob in blobs})
    ):
        raise ConsumerLabBootstrapError("coordinator package inventory is not canonical")
    return tuple(blobs)


def _archive(root: Path, commit: str) -> bytes:
    return _git(
        root,
        (
            "--literal-pathspecs",
            "archive",
            "--format=tar",
            commit,
            "--",
            _COORDINATOR_PACKAGE_PATH,
        ),
        _MAX_ARCHIVE_BYTES,
    )


def _materialize(image_root: Path, archive: bytes, blobs: tuple[_Blob, ...]) -> None:
    expected = {blob.path: blob for blob in blobs}
    observed: set[str] = set()
    image_root.mkdir()
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source:
            for member in source:
                assert_lifetime_running()
                path = member.name.rstrip("/")
                if member.isdir():
                    if not _is_package_directory(path):
                        raise ConsumerLabBootstrapError(
                            "coordinator archive contains an unsafe directory"
                        )
                    continue
                blob = expected.get(path)
                if (
                    blob is None
                    or path in observed
                    or not member.isfile()
                    or member.size != blob.size
                ):
                    raise ConsumerLabBootstrapError(
                        "coordinator archive inventory differs from its commit"
                    )
                stream = source.extractfile(member)
                if stream is None:
                    raise ConsumerLabBootstrapError("coordinator archive member is unavailable")
                content = stream.read(blob.size + 1)
                if len(content) != blob.size or _git_blob_id(content) != blob.object_id:
                    raise ConsumerLabBootstrapError(
                        "coordinator archive bytes differ from their commit"
                    )
                destination = image_root / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as output:
                    output.write(content)
                destination.chmod(0o400)
                observed.add(path)
    except (OSError, tarfile.TarError) as error:
        raise ConsumerLabBootstrapError("coordinator archive cannot be materialized") from error
    if observed != set(expected):
        raise ConsumerLabBootstrapError("coordinator archive is incomplete")


def _run_exact_image(
    *,
    source_root: Path,
    package_root: Path,
    cache_root: Path,
    coordinator_root: Path,
    coordinator_commit: str,
    target_root: Path,
    profile: str,
    output: str,
) -> int:
    scope = current_lifetime()
    if scope is not None:
        assert_lifetime_running()
        _require_lifetime_receiver(package_root)
    command = (
        sys.executable,
        "-I",
        "-B",
        "-X",
        f"pycache_prefix={cache_root}",
        "-c",
        _INTERNAL_LAUNCHER if scope is None else _MANAGED_LAUNCHER,
        str(source_root),
        "--coordinator-root",
        str(coordinator_root),
        "--coordinator-commit",
        coordinator_commit,
        "--coordinator-package-root",
        str(package_root),
        "--target-root",
        str(target_root),
        "--profile",
        profile,
        "--output",
        output,
    )
    if scope is not None:
        with lifetime_invocation(command[1:], timeout_seconds=_LAB_TIMEOUT_SECONDS) as invocation:
            deadline = min(time.monotonic() + _LAB_TIMEOUT_SECONDS, scope.deadline or float("inf"))
            schedule = _StopSchedule(scope, invocation.cancellation_fd, deadline)
            child: subprocess.Popen[bytes] | None = None
            completed = False
            with _defer_cancellation():
                try:
                    child = subprocess.Popen(  # noqa: S603
                        (command[0], *invocation.arguments),
                        cwd=target_root,
                        start_new_session=True,
                        pass_fds=invocation.inherited_fds,
                    )
                    with _interruptible_cancellation():
                        result = _wait_managed(child, schedule)
                    completed = True
                    return result
                finally:
                    primary = sys.exception()
                    try:
                        if (
                            child is not None
                            and not completed
                            and not _drain_managed(child, schedule)
                        ):
                            raise ConsumerLabBootstrapError("owned group drain unobserved")
                    except BaseException:
                        if primary is None:
                            raise
                        primary.add_note("bootstrap image drain incomplete")
    try:
        process = subprocess.Popen(  # noqa: S603
            command,
            cwd=target_root,
            start_new_session=True,
        )
        try:
            return process.wait(timeout=_LAB_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise ConsumerLabBootstrapError("exact source image execution timed out") from error
    except OSError as error:
        raise ConsumerLabBootstrapError("exact source image cannot be executed") from error


def _git(root: Path, arguments: tuple[str, ...], max_output_bytes: int) -> bytes:
    executable = shutil.which("git")
    if executable is None:
        raise ConsumerLabBootstrapError("Git executable is unavailable")
    environment = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": str(root),
        "LANG": "C",
        "LC_ALL": "C",
    }
    stdout, _stderr, status = _capture_bounded(
        (executable, "-C", str(root), *arguments),
        cwd=root,
        env=environment,
        stdout_limit=max_output_bytes,
    )
    if status != 0:
        raise ConsumerLabBootstrapError("Git inspection rejected the coordinator source")
    return stdout


def _capture_bounded(
    command: tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
    stdout_limit: int,
) -> tuple[bytes, bytes, int]:
    deadline = time.monotonic() + _GIT_TIMEOUT_SECONDS
    scope = current_lifetime()
    schedule = None
    if scope is not None:
        assert_lifetime_running()
        if isinstance(scope, _SourceCleanupScope):
            scope.admit(command, cwd)
        if scope.deadline is not None:
            deadline = min(deadline, scope.deadline)
        hard_limit = min(deadline + 1, scope.deadline or float("inf"))
        schedule = _StopSchedule(scope, None, hard_limit, term_cap=0)
        deadline = min(deadline, hard_limit - min(1.0, max(0.0, hard_limit - time.monotonic()) / 2))
    process: subprocess.Popen[bytes] | None = None
    selector: selectors.BaseSelector | None = None
    completed = False
    primary: BaseException | None = None
    with _defer_cancellation() if scope is not None else nullcontext():
        try:
            process = subprocess.Popen(  # noqa: S603
                command,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                pass_fds=() if scope is None else scope.inherited_fds,
            )
            with _interruptible_cancellation() if scope is not None else nullcontext():
                if process.stdout is None or process.stderr is None:
                    raise ConsumerLabBootstrapError("bounded Git inspection pipes are unavailable")
                stdout_descriptor = process.stdout.fileno()
                stderr_descriptor = process.stderr.fileno()
                output = {stdout_descriptor: bytearray(), stderr_descriptor: bytearray()}
                limits = {
                    stdout_descriptor: stdout_limit,
                    stderr_descriptor: _MAX_GIT_STDERR_BYTES,
                }
                selector = selectors.DefaultSelector()
                selector.register(process.stdout, selectors.EVENT_READ)
                selector.register(process.stderr, selectors.EVENT_READ)
                while selector.get_map():
                    assert_lifetime_running()
                    if scope is not None and scope.deadline is not None:
                        remaining = max(0.0, scope.deadline - time.monotonic())
                        deadline = min(deadline, scope.deadline - min(2.0, remaining / 2))
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ConsumerLabBootstrapError("bounded Git inspection timed out")
                    events = selector.select(remaining if scope is None else min(0.02, remaining))
                    if not events:
                        if scope is None:
                            raise ConsumerLabBootstrapError("bounded Git inspection timed out")
                        continue
                    for key, _ in events:
                        descriptor = key.fd
                        try:
                            chunk = os.read(descriptor, 65_536)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        output[descriptor].extend(chunk)
                        if (
                            len(output[descriptor]) > limits[descriptor]
                            or sum(map(len, output.values())) > stdout_limit + _MAX_GIT_STDERR_BYTES
                        ):
                            raise ConsumerLabBootstrapError(
                                "bounded Git inspection exceeded output limits"
                            )
                status = (
                    process.wait(timeout=max(0.001, deadline - time.monotonic()))
                    if schedule is None
                    else _wait_managed(process, schedule)
                )
                completed = True
        except (OSError, subprocess.TimeoutExpired) as error:
            primary = ConsumerLabBootstrapError("bounded Git inspection failed")
            raise primary from error
        except BaseException as error:
            primary = error
            raise
        finally:
            if process is not None:
                cleanup_primary = primary
                try:
                    try:
                        if not completed:
                            if schedule is None:
                                _kill_process_group(process)
                            elif not _drain_managed(process, schedule):
                                raise ConsumerLabBootstrapError("owned group drain unobserved")
                    except BaseException as error:
                        if cleanup_primary is None:
                            cleanup_primary = error
                        raise
                    finally:
                        _close_capture(process, selector, cleanup_primary)
                except BaseException:
                    if primary is None:
                        raise
                    primary.add_note("bootstrap owned group cleanup incomplete")
    return (
        bytes(output[stdout_descriptor]),
        bytes(output[stderr_descriptor]),
        status,
    )


def _close_capture(
    process: subprocess.Popen[bytes],
    selector: selectors.BaseSelector | None,
    primary: BaseException | None,
) -> None:
    failure: BaseException | None = None
    for resource in (selector, process.stdin, process.stdout, process.stderr):
        if resource is not None:
            try:
                resource.close()
            except BaseException as error:
                if failure is None:
                    failure = error
                else:
                    failure.add_note("Another consumer lab pipe close raised a secondary exception")
    if failure is not None:
        if primary is None:
            raise failure
        primary.add_note("consumer lab pipe close raised a secondary exception")


def _require_lifetime_receiver(package_root: Path) -> None:
    try:
        tree = ast.parse((package_root / "consumer_contract_lab/process.py").read_bytes())
    except (OSError, SyntaxError) as error:
        raise ConsumerLabBootstrapError("historical image receiver is unavailable") from error
    versions = [
        node.value.value
        for node in tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id == "LAB_LIFETIME_PROTOCOL"
        and isinstance(node.value, ast.Constant)
    ]
    receivers = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "managed_lifetime_entrypoint"
    ]
    if versions != [1] or type(versions[0]) is not int or len(receivers) != 1:
        raise ConsumerLabBootstrapError("historical image has no managed lifetime receiver")


def _wait_managed(process: subprocess.Popen[bytes], schedule: _StopSchedule) -> int:
    deadline = schedule.limit - min(2.0, max(0.0, schedule.limit - time.monotonic()) / 2)
    quiescent = False
    try:
        while True:
            assert_lifetime_running()
            if schedule.scope.deadline is not None:
                remaining = max(0.0, schedule.scope.deadline - time.monotonic())
                deadline = min(deadline, schedule.scope.deadline - min(2.0, remaining / 2))
            if process.returncode is None and _child_exited_without_reaping(process):
                _signal_process_group(process.pid, signal.SIGKILL)
                if sys.platform != "linux" or not _group_alive(process.pid, deadline=deadline):
                    process.wait(timeout=0)
            if process.returncode is not None and not _group_alive(process.pid, deadline=deadline):
                quiescent = True
                return process.returncode
            if time.monotonic() >= deadline:
                raise ConsumerLabBootstrapError("owned process execution timed out")
            time.sleep(min(_POLL_INTERVAL_SECONDS, max(0, deadline - time.monotonic())))
    finally:
        primary = sys.exception()
        try:
            if not quiescent and not _drain_managed(process, schedule):
                raise ConsumerLabBootstrapError("owned process group drain unobserved")
        except BaseException:
            if primary is None:
                raise
            primary.add_note("bootstrap owned process drain incomplete")


@dataclass(slots=True)
class _SourceCleanupScope:
    parent: LifetimeScope
    root: Path
    identity: tuple[int, int]
    _ceiling: float | None = None

    @property
    def inherited_fds(self) -> tuple[int, ...]:
        return self.parent.inherited_fds

    @property
    def deadline(self) -> float | None:
        limits = [
            value
            for value in (self._ceiling, self.parent.deadline, self.parent.cancellation_deadline)
            if value is not None
        ]
        self._ceiling = min(limits) if limits else 0.0
        return self._ceiling

    @property
    def cancellation_deadline(self) -> float | None:
        return self.parent.cancellation_deadline

    def stop_requested(self) -> bool:
        return False

    def admit(self, command: tuple[str, ...], cwd: Path) -> None:
        identity = self.root.lstat()
        if (
            command != (sys.executable, "-I", "-S", "-c", _DELETE_TREE, str(self.root))
            or cwd != self.root.parent
            or not stat.S_ISDIR(identity.st_mode)
            or (identity.st_dev, identity.st_ino) != self.identity
        ):
            raise ValueError("command is outside owned source cleanup")


@contextmanager
def _owned_source_directory() -> Iterator[str]:
    parent = current_lifetime()
    if parent is None:
        with TemporaryDirectory(prefix="ci-consumer-lab-source-") as temporary:
            yield temporary
        return
    assert_lifetime_running()
    root = Path(mkdtemp(prefix="ci-consumer-lab-source-")).resolve()
    identity = root.stat()
    primary: BaseException | None = None
    try:
        yield str(root)
    except BaseException as error:
        primary = error
        raise
    finally:
        try:
            scope = _SourceCleanupScope(parent, root, (identity.st_dev, identity.st_ino))
            with borrowed_lifetime(scope):
                _, _, status = _capture_bounded(
                    (sys.executable, "-I", "-S", "-c", _DELETE_TREE, str(root)),
                    cwd=root.parent,
                    env={},
                    stdout_limit=4096,
                )
            bound = scope.deadline
            if status != 0 or root.exists() or bound is None or time.monotonic() > bound:
                raise ConsumerLabBootstrapError("owned source directory remains")
        except BaseException as error:
            if primary is None:
                if not isinstance(error, Exception):
                    raise
                raise ConsumerLabBootstrapError("owned source cleanup incomplete") from error
            primary.add_note("bootstrap owned source cleanup incomplete")


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired as error:
        raise ConsumerLabBootstrapError("bounded Git process group cannot be reaped") from error


def _is_package_path(path: str) -> bool:
    return path.startswith(f"{_COORDINATOR_PACKAGE_PATH}/") and _is_safe_relative(path)


def _is_package_directory(path: str) -> bool:
    return (
        path
        in {
            "backend",
            "backend/src",
            _COORDINATOR_PACKAGE_PATH,
        }
        or path.startswith(f"{_COORDINATOR_PACKAGE_PATH}/")
    ) and _is_safe_relative(path)


def _is_safe_relative(path: str) -> bool:
    return (
        bool(path)
        and not path.startswith("/")
        and "\\" not in path
        and "\x00" not in path
        and all(part not in {"", ".", ".."} for part in path.split("/"))
        and PurePosixPath(path).as_posix() == path
    )


def _is_python_cache_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return "__pycache__" in parts or path.endswith((".pyc", ".pyo"))


def _git_blob_id(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def _reject(detail: str) -> int:
    print(
        f'{{"code":"consumer_contract_lab_bootstrap_failed","detail":"{detail}"}}',
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

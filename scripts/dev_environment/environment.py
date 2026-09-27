from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import platform
import select
import stat
import sys
import threading
import time
import tomllib
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import unquote, urlsplit

from scripts.bounded_process import (
    CANCELLATION_FRAME,
    CANCELLATION_TAG,
    borrowed_process_scope,
    current_process_scope,
    spawn,
)
from scripts.dev_environment.diagnostics import Reason
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity
from scripts.dev_environment.private_files import (
    atomic_write_private_text,
    ensure_private_directory,
    read_private_text,
    require_private_directory,
)
from scripts.diagram_process import DiagramCancellation, cancellation_signals
from scripts.proofkit_common import parse_json_object

DependencyScope = Literal["backend", "frontend"]
_MARKER_DIRECTORY = ".ci-coordinator-dependencies"
_IMMUTABLE_IDENTITY = ("schemaVersion", "rootDigest", "platform", "architecture", "scope")
_MAX_INPUT_BYTES = 8_388_608
_MAX_BORROW_BYTES = 512
MANAGED_PROCESS_ARGUMENT = "--managed-process-context"
_MANAGED_PYTEST_PLUGIN = "scripts.python_coverage_diagnostics"


def _process_context_byte_limit() -> int:
    limit = os.sysconf("SC_ARG_MAX")
    if type(limit) is not int or limit <= 0:
        raise EnvironmentError(Reason.INVALID_STATE)
    return limit


@dataclass(frozen=True, slots=True)
class _ProcessLease:
    descriptor: int
    root: Path
    lock: Path
    selection_sha256: str

    def admit(self) -> None:
        digest = hashlib.sha256(str(self.root).encode("utf-8")).hexdigest()
        if (
            type(self.descriptor) is not int
            or self.descriptor < 3
            or not self.root.is_absolute()
            or self.root.resolve(strict=True) != self.root
            or not self.root.is_dir()
            or not self.lock.is_absolute()
            or self.lock.resolve(strict=True) != self.lock
            or self.lock.parent.name != "dependency-locks"
            or self.lock.name != f"ci-coordinator-{digest[:12]}.lock"
            or not _is_digest(self.selection_sha256)
        ):
            raise EnvironmentError(Reason.INVALID_STATE)
        require_private_directory(self.lock.parent.parent)
        require_private_directory(self.lock.parent)
        _admit_lock(self.lock, self.descriptor)


@dataclass(slots=True)
class ManagedProcessBorrow:
    _leases: tuple[_ProcessLease, ...]
    _deadline: float | None
    _cancellation: DiagramCancellation
    _stop_descriptor: int | None = None
    _parent: ManagedProcessBorrow | None = None
    _active: bool = True
    _stop_grace: float | None = None
    _wire_first_force: float | None = None
    _wire_observed: bool = False
    _wire_revoked: bool = False
    _stop_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def inherited_fds(self) -> tuple[int, ...]:
        if not self._active:
            raise EnvironmentError(Reason.INVALID_STATE)
        for lease in self._leases:
            lease.admit()
        return tuple(lease.descriptor for lease in self._leases)

    @property
    def deadline(self) -> float | None:
        return self._deadline

    @property
    def term_grace_cap(self) -> float | None:
        return None

    def stop_requested(self) -> bool:
        self._read_stop()
        return (
            not self._active
            or self._cancellation.requested()
            or (self._parent is not None and self._parent.stop_requested())
            or self._wire_observed
        )

    def _read_stop(self) -> None:
        if self._stop_descriptor is None:
            return
        with self._stop_lock:
            if self._wire_revoked:
                return
            try:
                if not select.select([self._stop_descriptor], [], [], 0)[0]:
                    return
                if self._wire_observed:
                    self._wire_revoked = True
                    return
                self._wire_observed = True
                self._wire_revoked = True
                frame = os.read(self._stop_descriptor, CANCELLATION_FRAME.size)
                if len(frame) != CANCELLATION_FRAME.size:
                    self._wire_revoked = True
                    return
                tag, force = CANCELLATION_FRAME.unpack(frame)
                if (
                    tag != CANCELLATION_TAG
                    or not math.isfinite(force)
                    or self._stop_grace is None
                    or force > time.monotonic() + self._stop_grace
                ):
                    self._wire_revoked = True
                    return
                self._wire_first_force = force
                self._wire_revoked = bool(select.select([self._stop_descriptor], [], [], 0)[0])
            except (OSError, ValueError):
                self._wire_observed = True
                self._wire_revoked = True

    @property
    def cancellation_deadline(self) -> float | None:
        self._read_stop()
        limits: list[float] = []
        if not self._active or self._wire_revoked:
            limits.append(0.0)
        elif self._wire_first_force is not None:
            limits.append(self._wire_first_force)
        if (
            self._parent is not None
            and (parent_limit := self._parent.cancellation_deadline) is not None
        ):
            limits.append(parent_limit)
        if self._cancellation.requested() and self._stop_grace is not None:
            observed = self._cancellation.received_at
            limits.append(0.0 if observed is None else observed + self._stop_grace)
        return min(limits) if limits else None

    def admit_command(self, command: str, args: Sequence[str], cwd: Path) -> None:
        if not self._active:
            raise EnvironmentError(Reason.INVALID_STATE)

    def assert_running(self) -> None:
        if self.stop_requested():
            raise RuntimeError("managed execution cancelled")
        if self._deadline is not None and time.monotonic() >= self._deadline:
            raise TimeoutError("managed execution deadline expired")

    def completion_status(self, status: int) -> int:
        if self._cancellation.requested():
            return self._cancellation.exit_code(status)
        if status == 0 and (
            self.stop_requested()
            or (self.deadline is not None and time.monotonic() >= self.deadline)
        ):
            return 1
        return status


@dataclass(frozen=True, slots=True)
class ManagedProcessInvocation:
    arguments: tuple[str, ...]
    inherited_fds: tuple[int, ...] = ()
    cancellation_fd: int | None = None


def current_managed_process() -> ManagedProcessBorrow | None:
    scope = current_process_scope()
    if scope is not None and not isinstance(scope, ManagedProcessBorrow):
        raise EnvironmentError(Reason.INVALID_STATE)
    return scope


@contextmanager
def managed_process_invocation(
    arguments: Sequence[str],
    *,
    timeout_seconds: float | None,
    graceful_seconds: float,
    pytest_participant: bool = False,
) -> Iterator[ManagedProcessInvocation]:
    scope = current_managed_process()
    if scope is None:
        yield ManagedProcessInvocation(tuple(arguments))
        return
    scope.assert_running()
    deadline = scope.deadline if timeout_seconds is None else time.monotonic() + timeout_seconds
    if scope.deadline is not None:
        deadline = min(
            scope.deadline if deadline is None else deadline, scope.deadline - graceful_seconds - 2
        )
    if deadline is not None and time.monotonic() >= deadline:
        raise TimeoutError("managed execution deadline expired")
    if type(graceful_seconds) not in {int, float} or not 0 < graceful_seconds <= 30:
        raise EnvironmentError(Reason.INVALID_STATE)
    read_descriptor, write_descriptor = os.pipe()
    try:
        os.set_blocking(write_descriptor, False)
        os.set_blocking(read_descriptor, False)
        payload = json.dumps(
            {
                "version": 1,
                "deadline": deadline,
                "stopFd": read_descriptor,
                "stopGrace": graceful_seconds,
                "leases": [
                    {
                        "fd": lease.descriptor,
                        "root": str(lease.root),
                        "lock": str(lease.lock),
                        "selectionSha256": lease.selection_sha256,
                    }
                    for lease in scope._leases
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        if len(payload.encode("utf-8")) > _process_context_byte_limit():
            raise EnvironmentError(Reason.INVALID_STATE)
        private_loader: tuple[str, ...] = ()
        if pytest_participant:
            if tuple(arguments[:2]) != ("-m", "pytest"):
                raise EnvironmentError(Reason.INVALID_STATE)
            if not any(
                tuple(arguments[index : index + 2]) == ("-p", _MANAGED_PYTEST_PLUGIN)
                for index in range(len(arguments) - 1)
            ):
                private_loader = ("-p", _MANAGED_PYTEST_PLUGIN)
        yield ManagedProcessInvocation(
            (*arguments, *private_loader, MANAGED_PROCESS_ARGUMENT, payload),
            (*scope.inherited_fds, read_descriptor),
            write_descriptor,
        )
    finally:
        for descriptor in (read_descriptor, write_descriptor):
            with suppress(OSError):
                os.close(descriptor)


@contextmanager
def borrow_managed_process(
    context: str, *, interrupt: bool = False
) -> Iterator[ManagedProcessBorrow]:
    try:
        maximum = _process_context_byte_limit()
        if len(context) > maximum or len(context.encode()) > maximum:
            raise ValueError("oversized process context")
        value = parse_json_object(context, "managed process context")
        deadline = value.get("deadline")
        stop = value.get("stopFd")
        stop_grace = value.get("stopGrace")
        raw_leases = value.get("leases")
        if (
            set(value) != {"version", "deadline", "stopFd", "stopGrace", "leases"}
            or type(value.get("version")) is not int
            or value["version"] != 1
            or (
                deadline is not None
                and (
                    type(deadline) not in {int, float}
                    or not math.isfinite(deadline)
                    or deadline <= time.monotonic()
                )
            )
            or isinstance(stop_grace, bool)
            or not isinstance(stop_grace, (int, float))
            or not math.isfinite(stop_grace)
            or not 0 < stop_grace <= 30
            or type(stop) is not int
            or stop < 3
            or not stat.S_ISFIFO(os.fstat(stop).st_mode)
            or fcntl.fcntl(stop, fcntl.F_GETFL) & os.O_ACCMODE != os.O_RDONLY
            or not fcntl.fcntl(stop, fcntl.F_GETFL) & os.O_NONBLOCK
            or not isinstance(raw_leases, list)
            or not raw_leases
        ):
            raise ValueError("invalid process context")
        leases: list[_ProcessLease] = []
        for row in raw_leases:
            if (
                not isinstance(row, dict)
                or set(row) != {"fd", "root", "lock", "selectionSha256"}
                or type(row["fd"]) is not int
                or not isinstance(row["root"], str)
                or not isinstance(row["lock"], str)
                or not isinstance(row["selectionSha256"], str)
            ):
                raise ValueError("invalid process lease")
            lease = _ProcessLease(
                row["fd"], Path(row["root"]), Path(row["lock"]), row["selectionSha256"]
            )
            lease.admit()
            leases.append(lease)
        descriptors = (stop, *(lease.descriptor for lease in leases))
        if len(set(descriptors)) != len(descriptors):
            raise ValueError("duplicate process descriptors")
        parent = current_managed_process()
        if parent is not None:
            parent.assert_running()
            ancestor: ManagedProcessBorrow | None = parent
            owned = set(parent.inherited_fds)
            while ancestor is not None:
                if ancestor._stop_descriptor is not None:
                    owned.add(ancestor._stop_descriptor)
                ancestor = ancestor._parent
            if owned.intersection(descriptors):
                raise ValueError("process borrow overlaps ancestor descriptor ownership")
            leases = [*parent._leases, *leases]
            if parent.deadline is not None:
                deadline = parent.deadline if deadline is None else min(deadline, parent.deadline)
    except (OSError, OverflowError, TypeError, ValueError) as error:
        raise EnvironmentError(Reason.INVALID_STATE) from error
    with cancellation_signals(interrupt=interrupt) as cancellation:
        borrow = ManagedProcessBorrow(
            tuple(leases),
            None if deadline is None else float(deadline),
            cancellation,
            stop,
            _parent=parent,
            _stop_grace=float(stop_grace),
        )
        primary_error: BaseException | None = None
        try:
            with borrowed_process_scope(borrow):
                borrow.assert_running()
                yield borrow
        except BaseException as error:
            primary_error = error
            raise
        finally:
            borrow._active = False
            close_error: BaseException | None = None
            for descriptor in descriptors:
                try:
                    os.close(descriptor)
                except BaseException as error:
                    if close_error is None:
                        close_error = error
            if close_error is not None:
                if primary_error is None:
                    raise close_error
                primary_error.add_note(
                    "Managed process descriptor cleanup raised a secondary exception"
                )


def managed_process_argument(arguments: Sequence[str]) -> str | None:
    present = [
        item
        for item in arguments
        if item == MANAGED_PROCESS_ARGUMENT or item.startswith(MANAGED_PROCESS_ARGUMENT + "=")
    ]
    if not present:
        return None
    if (
        len(arguments) < 2
        or arguments[-2] != MANAGED_PROCESS_ARGUMENT
        or present != [MANAGED_PROCESS_ARGUMENT]
    ):
        raise ValueError("invalid managed process invocation")
    return arguments[-1]


def managed_process_entrypoint(main: Callable[[], int], *, interrupt: bool = False) -> int:
    arguments = sys.argv[1:]
    try:
        context = managed_process_argument(arguments)
    except ValueError:
        sys.stderr.write("invalid managed process invocation\n")
        return 2
    if context is None:
        return main()
    with ExitStack() as lifetime:
        try:
            borrow = lifetime.enter_context(borrow_managed_process(context, interrupt=interrupt))
        except (OSError, RuntimeError, TypeError, ValueError):
            sys.stderr.write("managed process admission failed\n")
            return 2
        original = sys.argv
        try:
            sys.argv = [original[0], *arguments[:-2]]
            try:
                result = main()
            except SystemExit as error:
                if error.code is None or (isinstance(error.code, int) and error.code == 0):
                    status = borrow.completion_status(0)
                    if status != 0:
                        raise SystemExit(status) from None
                raise
            return borrow.completion_status(result)
        finally:
            sys.argv = original


class EnvironmentError(RuntimeError):
    def __init__(self, reason: Reason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _DependencySnapshot:
    expected: dict[str, object]
    export_sha256: str | None


@dataclass(slots=True)
class ManagedDependencyBorrow:
    identity: InstanceIdentity
    _descriptor: int | None
    _snapshots: dict[DependencyScope, _DependencySnapshot]
    _active: DependencyScope | None = None

    def begin(self, scope: DependencyScope) -> None:
        snapshot = self._snapshot(scope)
        if self._active is not None:
            raise EnvironmentError(Reason.INVALID_STATE)
        admit_dependencies(self.identity, scope)
        self._admit_snapshot(scope, snapshot)
        pending = _pending_path(self.identity, scope)
        ensure_private_directory(pending.parent)
        atomic_write_private_text(pending, _json(snapshot.expected))
        self._active = scope

    def complete(self, scope: DependencyScope) -> None:
        snapshot = self._snapshot(scope)
        if self._active != scope:
            raise EnvironmentError(Reason.INVALID_STATE)
        self._admit_snapshot(scope, snapshot)
        pending = _pending_path(self.identity, scope)
        observed_pending = _read_record(pending)
        _admit_owner(observed_pending, snapshot.expected)
        if observed_pending != snapshot.expected:
            raise EnvironmentError(Reason.ENVIRONMENT_STALE)
        destination = _destination(self.identity, scope)
        _admit_destination(self.identity, destination, required=True)
        observed = _read_marker(self.identity, destination)
        if observed is not None:
            _admit_owner(observed, snapshot.expected)
            if observed != snapshot.expected:
                raise EnvironmentError(Reason.ENVIRONMENT_STALE)
        if scope == "backend":
            _admit_python(self.identity)
        else:
            _admit_destination(
                self.identity, self.identity.repo_root / "frontend/node_modules", required=True
            )
        if observed is None:
            _write_marker(self.identity, destination, snapshot.expected)
        pending.unlink()
        self._active = None

    def _snapshot(self, scope: DependencyScope) -> _DependencySnapshot:
        if self._descriptor is None or scope not in self._snapshots:
            raise EnvironmentError(Reason.INVALID_STATE)
        _admit_lock(_dependency_lock_path(self.identity), self._descriptor)
        return self._snapshots[scope]

    def _admit_snapshot(self, scope: DependencyScope, snapshot: _DependencySnapshot) -> None:
        if dependency_identity(self.identity, scope) != snapshot.expected or (
            snapshot.export_sha256 is not None
            and _requirements_digest(self.identity) != snapshot.export_sha256
        ):
            raise EnvironmentError(Reason.ENVIRONMENT_STALE)


@contextmanager
def managed_dependency_process_scope(
    dependencies: ManagedDependencyBorrow,
    selection_sha256: str,
    cancellation: DiagramCancellation,
) -> Iterator[ManagedProcessBorrow]:
    descriptor = dependencies._descriptor
    if descriptor is None:
        raise EnvironmentError(Reason.INVALID_STATE)
    parent = current_managed_process()
    lease = _ProcessLease(
        descriptor,
        dependencies.identity.repo_root,
        _dependency_lock_path(dependencies.identity),
        selection_sha256,
    )
    lease.admit()
    leases = {item.descriptor: item for item in (() if parent is None else parent._leases)}
    if descriptor in leases and leases[descriptor] != lease:
        raise EnvironmentError(Reason.INVALID_STATE)
    leases[descriptor] = lease
    borrow = ManagedProcessBorrow(
        tuple(leases.values()),
        None if parent is None else parent.deadline,
        cancellation,
        _parent=parent,
        _stop_grace=None if parent is None else parent._stop_grace,
    )
    try:
        with borrowed_process_scope(borrow):
            borrow.assert_running()
            yield borrow
    finally:
        borrow._active = False


@contextmanager
def managed_task_process_scope(
    identity: InstanceIdentity,
    descriptor: int,
    selection_sha256: str,
    cancellation: DiagramCancellation,
) -> Iterator[ManagedProcessBorrow]:
    parent = current_managed_process()
    lifetime_descriptor = os.dup(descriptor)
    try:
        lease = _ProcessLease(
            lifetime_descriptor,
            identity.repo_root,
            _dependency_lock_path(identity),
            selection_sha256,
        )
        lease.admit()
        borrow = ManagedProcessBorrow(
            (*(() if parent is None else parent._leases), lease),
            None if parent is None else parent.deadline,
            cancellation,
            _parent=parent,
            _stop_grace=None if parent is None else parent._stop_grace,
        )
        try:
            with borrowed_process_scope(borrow):
                borrow.assert_running()
                yield borrow
        finally:
            borrow._active = False
    finally:
        os.close(lifetime_descriptor)


def managed_dependency_context(
    identity: InstanceIdentity, descriptor: int, selection_sha256: str
) -> str:
    if type(descriptor) is not int or descriptor < 3 or not _is_digest(selection_sha256):
        raise EnvironmentError(Reason.INVALID_STATE)
    _admit_lock(_dependency_lock_path(identity), descriptor)
    return json.dumps(
        {
            "version": 1,
            "fd": descriptor,
            "rootDigest": identity.root_digest,
            "selectionSha256": selection_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


@contextmanager
def borrow_managed_dependencies(
    repo_root: Path,
    context: str,
    *,
    selection_sha256: str,
    write_scopes: Sequence[DependencyScope],
    environment: Mapping[str, str],
) -> Iterator[ManagedDependencyBorrow]:
    try:
        if len(context) > _MAX_BORROW_BYTES or len(context.encode("utf-8")) > _MAX_BORROW_BYTES:
            raise ValueError("oversized context")
        value = parse_json_object(context, "managed dependency context")
        descriptor = value.get("fd")
        if (
            set(value) != {"version", "fd", "rootDigest", "selectionSha256"}
            or type(value.get("version")) is not int
            or value["version"] != 1
            or type(descriptor) is not int
            or descriptor < 3
            or not _is_digest(value.get("rootDigest"))
            or not _is_digest(selection_sha256)
            or value.get("selectionSha256") != selection_sha256
            or not environment.get("CI_COORDINATOR_DEV_STATE_HOME")
            or len(set(write_scopes)) != len(write_scopes)
            or any(scope not in {"backend", "frontend"} for scope in write_scopes)
        ):
            raise ValueError("invalid context")
        identity = derive_instance_identity(repo_root, environment=environment)
        if value["rootDigest"] != identity.root_digest:
            raise ValueError("foreign context")
        require_private_directory(identity.state_home)
        require_private_directory(identity.state_home / "dependency-locks")
        _admit_lock(_dependency_lock_path(identity), descriptor)
    except (OSError, OverflowError, TypeError, ValueError) as error:
        raise EnvironmentError(Reason.INVALID_STATE) from error
    borrow = ManagedDependencyBorrow(identity, descriptor, {})
    try:
        for scope in ("backend", "frontend"):
            admit_dependencies(identity, scope)
        for write_scope in write_scopes:
            expected = dependency_identity(identity, write_scope)
            if _read_marker(identity, _destination(identity, write_scope)) != expected:
                raise EnvironmentError(Reason.ENVIRONMENT_STALE)
            borrow._snapshots[write_scope] = _DependencySnapshot(
                expected,
                _requirements_digest(identity) if write_scope == "backend" else None,
            )
        yield borrow
    finally:
        borrow._descriptor = None
        os.close(descriptor)


def _requirements_digest(identity: InstanceIdentity) -> str:
    return hashlib.sha256(
        _read_input(identity, identity.repo_root / "backend/requirements-dev.lock")
    ).hexdigest()


def _dependency_lock_path(identity: InstanceIdentity) -> Path:
    return identity.state_home / "dependency-locks" / f"{identity.project_name}.lock"


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


@contextmanager
def dependency_lease(identity: InstanceIdentity, *, exclusive: bool = False) -> Iterator[int]:
    directory = identity.state_home / "dependency-locks"
    ensure_private_directory(identity.state_home)
    ensure_private_directory(directory)
    path = _dependency_lock_path(identity)
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        _admit_lock(path, descriptor)
        try:
            fcntl.flock(descriptor, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise EnvironmentError(Reason.PREPARATION_IN_PROGRESS) from error
        _admit_lock(path, descriptor)
        yield descriptor
    finally:
        # Unlocking the shared open-file description would release an inherited child's lease.
        os.close(descriptor)


def admit_dependencies(identity: InstanceIdentity, scope: DependencyScope) -> None:
    destination = _destination(identity, scope)
    _admit_destination(identity, destination, required=True)
    expected = dependency_identity(identity, scope)
    observed = _read_marker(identity, destination)
    if observed is None:
        raise EnvironmentError(Reason.DEPENDENCIES_MISSING)
    _admit_owner(observed, expected)
    pending = _pending_path(identity, scope)
    if observed != expected or pending.exists() or pending.is_symlink():
        raise EnvironmentError(Reason.ENVIRONMENT_STALE)
    if scope == "backend":
        _admit_python(identity)
    else:
        _admit_destination(identity, identity.repo_root / "frontend/node_modules", required=True)


def prepare_dependencies(
    identity: InstanceIdentity,
    scopes: Sequence[DependencyScope],
    *,
    environment: Mapping[str, str] | None = None,
) -> None:
    if (
        not scopes
        or len(set(scopes)) != len(scopes)
        or any(scope not in {"backend", "frontend"} for scope in scopes)
    ):
        raise EnvironmentError(Reason.INVALID_ARGUMENT)
    with dependency_lease(identity, exclusive=True) as descriptor:
        requests = [_preparation_request(identity, scope) for scope in scopes]
        for scope, expected, force_frontend in requests:
            destination = _destination(identity, scope)
            provider_environment = _provider_environment(identity, scope, environment)
            _admit_tools(identity, scope, provider_environment, descriptor)
            pending = _pending_path(identity, scope)
            ensure_private_directory(pending.parent)
            atomic_write_private_text(pending, _json(expected))
            command: tuple[str, ...]
            if scope == "backend":
                command = (
                    "uv",
                    "sync",
                    "--project",
                    "backend",
                    "--frozen",
                    "--all-groups",
                    "--python",
                    str(_base_interpreter(identity)),
                    "--no-python-downloads",
                )
            else:
                command = (
                    "pnpm",
                    "install",
                    "--frozen-lockfile",
                    *(["--force"] if force_frontend else []),
                )
            result = spawn(
                command[0],
                command[1:],
                cwd=identity.repo_root,
                env=provider_environment,
                max_buffer=1_048_576,
                timeout_seconds=600,
                inherited_fds=(descriptor,),
            )
            if result.error is not None or result.status != 0:
                raise EnvironmentError(Reason.DEPENDENCIES_MISSING)
            _admit_destination(identity, destination, required=True)
            if expected != dependency_identity(identity, scope):
                raise EnvironmentError(Reason.ENVIRONMENT_STALE)
            if scope == "backend":
                _admit_python(identity)
            else:
                _admit_destination(
                    identity, identity.repo_root / "frontend/node_modules", required=True
                )
            _write_marker(identity, destination, expected)
            pending.unlink()


def dependency_identity(identity: InstanceIdentity, scope: DependencyScope) -> dict[str, object]:
    root = identity.repo_root
    if scope == "backend":
        files = ["backend/pyproject.toml", "backend/uv.lock"]
        names = ("python", "uv")
    elif scope == "frontend":
        files = [
            "package.json",
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
            ".npmrc",
            "frontend/package.json",
        ]
        _admit_path(identity, root / "patches", required=False)
        patches = sorted((root / "patches").glob("*.patch"))
        if len(patches) > 128:
            raise EnvironmentError(Reason.INVALID_STATE)
        files.extend(str(path.relative_to(root)) for path in patches)
        names = ("node", "pnpm")
    else:
        raise EnvironmentError(Reason.INVALID_ARGUMENT)
    digests = {
        relative: hashlib.sha256(_read_input(identity, root / relative)).hexdigest()
        for relative in files
    }
    tools = _tool_versions(identity)
    return {
        "schemaVersion": 1,
        "rootDigest": identity.root_digest,
        "platform": sys.platform,
        "architecture": platform.machine(),
        "scope": scope,
        "tools": {name: tools[name] for name in names},
        "inputs": digests,
    }


def _preparation_request(
    identity: InstanceIdentity,
    scope: DependencyScope,
) -> tuple[DependencyScope, dict[str, object], bool]:
    destination = _destination(identity, scope)
    _admit_destination(identity, destination, required=False)
    expected = dependency_identity(identity, scope)
    observed = _read_marker(identity, destination)
    pending = _pending_path(identity, scope)
    if pending.exists() or pending.is_symlink():
        _admit_owner(_read_record(pending), expected)
    if observed is not None:
        _admit_owner(observed, expected)
    elif scope == "backend" and destination.exists() and any(destination.iterdir()):
        _admit_python(identity)
        _admit_legacy_editable(identity)
    if scope == "frontend":
        for relative in ("frontend/node_modules", ".pnpm-store"):
            _admit_destination(identity, identity.repo_root / relative, required=False)
    return scope, expected, scope == "frontend" and observed is None and destination.exists()


def _read_marker(identity: InstanceIdentity, destination: Path) -> dict[str, object] | None:
    directory = destination / _MARKER_DIRECTORY
    _admit_destination(identity, directory, required=False)
    path = directory / "identity.json"
    if not path.exists() and not path.is_symlink():
        return None
    return _read_record(path)


def _read_record(path: Path) -> dict[str, object]:
    try:
        require_private_directory(path.parent)
        observed = json.loads(read_private_text(path))
    except (OSError, ValueError) as error:
        raise EnvironmentError(Reason.INVALID_STATE) from error
    if not isinstance(observed, dict):
        raise EnvironmentError(Reason.INVALID_STATE)
    return observed


def _write_marker(identity: InstanceIdentity, destination: Path, value: dict[str, object]) -> None:
    directory = destination / _MARKER_DIRECTORY
    _admit_destination(identity, directory, required=False)
    ensure_private_directory(directory)
    atomic_write_private_text(directory / "identity.json", _json(value))


def _json(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"


def _pending_path(identity: InstanceIdentity, scope: DependencyScope) -> Path:
    return identity.state_home / "dependency-preparation" / f"{identity.project_name}-{scope}.json"


def _admit_owner(observed: dict[str, object], expected: dict[str, object]) -> None:
    if type(observed.get("schemaVersion")) is not int:
        raise EnvironmentError(Reason.INVALID_STATE)
    if any(observed.get(key) != expected[key] for key in _IMMUTABLE_IDENTITY):
        raise EnvironmentError(Reason.FOREIGN_STATE)


def _destination(identity: InstanceIdentity, scope: DependencyScope) -> Path:
    return identity.repo_root / ("backend/.venv" if scope == "backend" else "node_modules")


def _admit_path(identity: InstanceIdentity, path: Path, *, required: bool = True) -> None:
    try:
        relative = path.relative_to(identity.repo_root)
    except ValueError as error:
        raise EnvironmentError(Reason.INVALID_STATE) from error
    current = identity.repo_root
    for index, part in enumerate(relative.parts):
        current /= part
        try:
            status = current.lstat()
        except FileNotFoundError as error:
            if not required:
                return
            raise EnvironmentError(Reason.DEPENDENCIES_MISSING) from error
        if stat.S_ISLNK(status.st_mode) or (
            index < len(relative.parts) - 1 and not stat.S_ISDIR(status.st_mode)
        ):
            raise EnvironmentError(Reason.INVALID_STATE)


def _admit_destination(identity: InstanceIdentity, path: Path, *, required: bool) -> None:
    _admit_path(identity, path, required=required)
    if not path.exists():
        return
    status = path.lstat()
    if not stat.S_ISDIR(status.st_mode) or status.st_uid != os.getuid():
        raise EnvironmentError(Reason.INVALID_STATE)


def _read_input(
    identity: InstanceIdentity, path: Path, *, maximum: int = _MAX_INPUT_BYTES
) -> bytes:
    _admit_path(identity, path)
    status = path.stat()
    if not stat.S_ISREG(status.st_mode) or status.st_size > maximum:
        raise EnvironmentError(Reason.INVALID_STATE)
    return path.read_bytes()


def _tool_versions(identity: InstanceIdentity) -> dict[str, str]:
    value = tomllib.loads(_read_input(identity, identity.repo_root / "mise.toml").decode("utf-8"))
    tools = value.get("tools")
    if not isinstance(tools, dict) or any(
        not isinstance(tools.get(name), str) for name in ("python", "uv", "node", "pnpm")
    ):
        raise EnvironmentError(Reason.INVALID_STATE)
    return {name: str(tools[name]) for name in ("python", "uv", "node", "pnpm")}


def _base_interpreter(identity: InstanceIdentity) -> Path:
    expected = _tool_versions(identity)["python"]
    if (
        sys.platform not in {"darwin", "linux"}
        or sys.implementation.name != "cpython"
        or ".".join(map(str, sys.version_info[:3])) != expected
    ):
        raise EnvironmentError(Reason.UNSUPPORTED_TOOL)
    return Path(getattr(sys, "_base_executable", sys.executable)).resolve(strict=True)


def _admit_python(identity: InstanceIdentity) -> None:
    destination = _destination(identity, "backend")
    config = dict(
        line.split(" = ", 1)
        for line in _read_input(identity, destination / "pyvenv.cfg", maximum=8192)
        .decode()
        .splitlines()
        if " = " in line
    )
    expected = _tool_versions(identity)["python"]
    if config.get("implementation") != "CPython" or config.get("version_info") != expected:
        raise EnvironmentError(Reason.ENVIRONMENT_STALE)
    _admit_destination(identity, destination / "bin", required=True)
    executable = destination / "bin/python"
    try:
        actual = executable.resolve(strict=True)
    except OSError as error:
        raise EnvironmentError(Reason.DEPENDENCIES_MISSING) from error
    if actual != _base_interpreter(identity) or not os.access(actual, os.X_OK):
        raise EnvironmentError(Reason.FOREIGN_STATE)


def _admit_legacy_editable(identity: InstanceIdentity) -> None:
    library = _destination(identity, "backend") / "lib"
    _admit_destination(identity, library, required=False)
    records = list(
        library.glob("python*/site-packages/ci_coordinator_backend-*.dist-info/direct_url.json")
    )
    if len(records) > 1:
        raise EnvironmentError(Reason.FOREIGN_STATE)
    for path in records:
        try:
            record = json.loads(_read_input(identity, path, maximum=8192))
            source = urlsplit(record["url"])
        except (ValueError, KeyError, TypeError) as error:
            raise EnvironmentError(Reason.INVALID_STATE) from error
        if (
            source.scheme != "file"
            or source.netloc
            or Path(unquote(source.path)).resolve() != identity.repo_root / "backend"
        ):
            raise EnvironmentError(Reason.FOREIGN_STATE)


def _admit_tools(
    identity: InstanceIdentity,
    scope: DependencyScope,
    environment: Mapping[str, str],
    descriptor: int,
) -> None:
    _base_interpreter(identity)
    versions = _tool_versions(identity)
    names = ("uv",) if scope == "backend" else ("node", "pnpm")
    for name in names:
        result = spawn(
            name,
            ("--version",),
            cwd=identity.repo_root,
            env=environment,
            max_buffer=8192,
            timeout_seconds=10,
            inherited_fds=(descriptor,),
        )
        output = result.stdout.strip()
        expected = ("uv " if name == "uv" else "v" if name == "node" else "") + versions[name]
        if (
            result.error is not None
            or result.status != 0
            or (output != expected and not (name == "uv" and output.startswith(expected + " (")))
        ):
            raise EnvironmentError(Reason.UNSUPPORTED_TOOL)


def _provider_environment(
    identity: InstanceIdentity, scope: DependencyScope, environment: Mapping[str, str] | None
) -> dict[str, str]:
    result = dict(os.environ if environment is None else environment)
    for name in tuple(result):
        if (
            name.startswith("UV_") and name not in {"UV_CACHE_DIR", "UV_NO_CACHE", "UV_OFFLINE"}
        ) or name in {
            "VIRTUAL_ENV",
            "CONDA_PREFIX",
            "PYTHONHOME",
            "PYTHONPATH",
        }:
            result.pop(name)
    if scope == "backend":
        result["UV_PROJECT_ENVIRONMENT"] = str(_destination(identity, "backend"))
    result["MISE_AUTO_INSTALL"] = "false"
    return result


def _admit_lock(path: Path, descriptor: int) -> None:
    opened = os.fstat(descriptor)
    current = path.lstat()
    if (
        not stat.S_ISREG(opened.st_mode)
        or opened.st_uid != os.getuid()
        or stat.S_IMODE(opened.st_mode) != 0o600
        or opened.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
    ):
        raise EnvironmentError(Reason.INVALID_STATE)

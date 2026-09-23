"""Native joined-batch and controller-death probes for disposable CI instances."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import selectors
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from scripts.bounded_git import BoundedGitError, run_git
from scripts.bounded_process import spawn
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity
from scripts.dev_environment.lifecycle import (
    InstanceMutationLease,
    OperationBlocked,
    OperationBusy,
    instance_operation_lock,
    operation_paths,
    sigterm_guard,
)
from scripts.dev_environment.private_files import (
    PrivateLockBusy,
    bounded_private_lock,
    read_private_text,
    require_private_directory,
)
from scripts.dev_environment.watch_client_witness import (
    WatchClientProject,
    WatchClientWitnessError,
    _admit_running_project,
    _wait_for_readiness,
)
from scripts.dev_environment.watch_session import (
    WATCH_GRACEFUL_SECONDS,
    WATCH_KILL_SECONDS,
    CancelResult,
    WatchClientContract,
    cancel_watch,
    inspect_watch,
    owned_watch_session,
)

if TYPE_CHECKING:
    from scripts.dev_environment.compose import ProviderInvocation, ServiceRuntimeIdentity

_ROOT = Path(__file__).resolve().parents[2]
_BUILD_SECONDS = 120.0
_CONTROLLER_SECONDS = 180.0
_SOURCE_LIMIT = 131_072
_LABEL = "io.ci-coordinator.root-digest"


class BatchWatchProject(WatchClientProject, Protocol):
    def service_runtime_identity(self, service: str) -> ServiceRuntimeIdentity: ...


def _checked(
    invocation: ProviderInvocation,
    args: Sequence[str],
    *,
    lease: InstanceMutationLease | None = None,
) -> str:
    result = spawn(
        "docker",
        args,
        cwd=invocation.cwd,
        env=invocation.environment,
        timeout_seconds=30,
        max_buffer=65_536,
        inherited_fds=() if lease is None else lease.inherited_fds,
    )
    if result.status != 0 or result.failure_kind is not None:
        raise WatchClientWitnessError("native_watch_provider_observation_failed")
    return result.stdout.strip()


def _owned_gateway(identity: InstanceIdentity, invocation: ProviderInvocation) -> str:
    value = json.loads(
        _checked(invocation, ("network", "inspect", f"{identity.project_name}_default"))
    )
    if not isinstance(value, list) or len(value) != 1 or not isinstance(value[0], dict):
        raise WatchClientWitnessError("native_watch_network_unavailable")
    network = value[0]
    labels = network.get("Labels")
    if (
        not isinstance(labels, dict)
        or labels.get("com.docker.compose.project") != identity.project_name
        or labels.get(_LABEL) != identity.root_digest
        or network.get("Driver") != "bridge"
    ):
        raise WatchClientWitnessError("native_watch_network_not_owned")
    ipam = network.get("IPAM")
    configs = ipam.get("Config") if isinstance(ipam, dict) else None
    if not isinstance(configs, list) or not configs or not isinstance(configs[0], dict):
        raise WatchClientWitnessError("native_watch_network_unavailable")
    gateway = configs[0].get("Gateway")
    if not isinstance(gateway, str) or not ipaddress.IPv4Address(gateway).is_private:
        raise WatchClientWitnessError("native_watch_network_unavailable")
    return gateway


class BuildStepBarrier:
    """An actual BuildKit RUN waits for one nonce-bound HTTP response.

    The fixture owns only its source delta and its private bridge listener.
    Closing releases the listener; source restoration requires fresh mutation
    admission. This is a client/batch barrier, not a daemon drain certificate.
    """

    def __init__(
        self, identity: InstanceIdentity, gateway: str, *, target: str = "development"
    ) -> None:
        if target not in {"development", "debug"}:
            raise WatchClientWitnessError("native_watch_target_unadmitted")
        self.path = identity.repo_root / "docker/development/backend.Dockerfile"
        self._root_digest = identity.root_digest
        if self.path.is_symlink() or not self.path.is_file():
            raise WatchClientWitnessError("native_watch_source_unavailable")
        if self.path.stat().st_size > _SOURCE_LIMIT:
            raise WatchClientWitnessError("native_watch_source_unavailable")
        self.original = self.path.read_bytes()
        self._mode = stat.S_IMODE(self.path.stat().st_mode)
        _inject_step(self.original, target, b"")
        self.token = secrets.token_hex(32)
        self.entered = threading.Event()
        self.release = threading.Event()
        self._stopping = threading.Event()
        barrier = self

        class Handler(BaseHTTPRequestHandler):
            def handle(self) -> None:
                self.connection.settimeout(2)
                super().handle()

            def do_GET(self) -> None:
                if self.path != f"/{barrier.token}":
                    self.send_error(404)
                    return
                barrier.entered.set()
                if not barrier.release.wait(timeout=_BUILD_SECONDS):
                    return
                with suppress(OSError):
                    self.send_response(200)
                    self.send_header("Content-Length", "1")
                    self.end_headers()
                    self.wfile.write(b"x")

            def log_message(self, format: str, *args: object) -> None:
                pass

        # Binding the owned bridge address also rejects an unqualified remote
        # daemon or Docker Desktop VM; Linux provider lanes qualify this path.
        self.server = HTTPServer((gateway, 0), Handler)
        self.server.timeout = 0.1
        url = f"http://{gateway}:{self.server.server_port}/{self.token}"
        program = (
            "import sys,urllib.request; "
            "opener=urllib.request.build_opener(urllib.request.ProxyHandler({})); "
            "response=opener.open(sys.argv[1],timeout=120); "
            "assert response.read(1)==b'x'"
        )
        instruction = "RUN " + json.dumps(["python", "-c", program, url]) + "\n"
        self.modified = _inject_step(self.original, target, instruction.encode())
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="native-build-barrier")
        self.worker = self.executor.submit(self._serve)

    def _serve(self) -> None:
        while not self._stopping.is_set():
            self.server.handle_request()

    def arm(self) -> None:
        if self.path.read_bytes() != self.original:
            raise WatchClientWitnessError("native_watch_source_changed_before_barrier")
        self._replace_source(self.modified)

    def wait(self) -> None:
        if not self.entered.wait(timeout=_BUILD_SECONDS):
            raise WatchClientWitnessError("native_watch_batch_barrier_unobserved")

    def restore(self, lease: InstanceMutationLease) -> None:
        if lease.root_digest != self._root_digest or not lease.inherited_fds:
            raise WatchClientWitnessError("native_watch_restore_lease_unavailable")
        if self.path.read_bytes() != self.modified:
            raise WatchClientWitnessError("native_watch_source_changed_before_restore")
        self._replace_source(self.original)

    def _replace_source(self, content: bytes) -> None:
        # Publish complete Dockerfile bytes in one watched rename. A partially
        # written source must not turn the batch barrier into a parse failure.
        descriptor, name = tempfile.mkstemp(dir=self.path.parent, prefix=".watch-build-")
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                os.fchmod(output.fileno(), self._mode)
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def close(self) -> None:
        self.release.set()
        self._stopping.set()
        try:
            self.worker.result(timeout=3)
        finally:
            self.server.server_close()
            self.executor.shutdown(wait=False, cancel_futures=True)


def _inject_step(source: bytes, target: str, instruction: bytes) -> bytes:
    lines = source.splitlines(keepends=True)
    starts = [
        i for i, line in enumerate(lines) if line.strip().lower().endswith(f" as {target}".encode())
    ]
    if len(starts) != 1 or not lines[starts[0]].lstrip().upper().startswith(b"FROM "):
        raise WatchClientWitnessError("native_watch_target_unavailable")
    end = next(
        (
            i
            for i in range(starts[0] + 1, len(lines))
            if lines[i].lstrip().upper().startswith(b"FROM ")
        ),
        len(lines),
    )
    return b"".join(lines[:end]) + b"\n" + instruction + b"".join(lines[end:])


def _state_digest(identity: InstanceIdentity) -> str:
    require_private_directory(identity.secrets_directory)
    paths = [
        identity.metadata_path,
        identity.environment_path,
        *sorted(identity.secrets_directory.iterdir()),
    ]
    if len(paths) > 64:
        raise WatchClientWitnessError("native_watch_state_unavailable")
    fingerprints = [
        (path.name, hashlib.sha256(read_private_text(path).encode()).hexdigest()) for path in paths
    ]
    return hashlib.sha256(json.dumps(fingerprints).encode()).hexdigest()


def _database(
    invocation: ProviderInvocation,
    container_id: str,
    sql: str,
    *,
    lease: InstanceMutationLease | None = None,
) -> str:
    return _checked(
        invocation,
        (
            "exec",
            "--user",
            "postgres",
            container_id,
            "psql",
            "-X",
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-v",
            "ON_ERROR_STOP=1",
            "-At",
            "-c",
            sql,
        ),
        lease=lease,
    )


def _provider_epoch(invocation: ProviderInvocation) -> dict[str, str]:
    version = _checked(invocation, ("compose", "version", "--short"))
    plugins = json.loads(_checked(invocation, ("info", "--format", "{{json .ClientInfo.Plugins}}")))
    if not isinstance(plugins, list):
        raise WatchClientWitnessError("native_watch_provider_identity_unavailable")
    compose = [item for item in plugins if isinstance(item, dict) and item.get("Name") == "compose"]
    if (
        len(compose) != 1
        or not isinstance(compose[0].get("Path"), str)
        or not isinstance(compose[0].get("Version"), str)
        or compose[0]["Version"].removeprefix("v") != version.removeprefix("v")
        or compose[0].get("Err")
    ):
        raise WatchClientWitnessError("native_watch_provider_identity_unavailable")
    docker = shutil.which("docker", path=invocation.environment.get("PATH"))
    if docker is None:
        raise WatchClientWitnessError("native_watch_provider_identity_unavailable")
    try:
        source = run_git(
            invocation.cwd,
            ("rev-parse", "HEAD"),
            max_buffer=128,
            source_environment=invocation.environment,
            timeout_seconds=10,
        ).stdout.strip()
    except (BoundedGitError, OSError) as error:
        raise WatchClientWitnessError("native_watch_source_identity_unavailable") from error
    if len(source) != 40 or any(c not in "0123456789abcdef" for c in source):
        raise WatchClientWitnessError("native_watch_source_identity_unavailable")
    return {
        "composeVersion": version,
        "composeBinarySha256": _binary_digest(Path(compose[0]["Path"])),
        "dockerBinarySha256": _binary_digest(Path(docker)),
        "sourceSha": source,
    }


def _binary_digest(path: Path) -> str:
    if not path.is_file() or path.stat().st_size > 268_435_456:
        raise WatchClientWitnessError("native_watch_provider_identity_unavailable")
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _assert_independent_root(identity: InstanceIdentity, invocation: ProviderInvocation) -> str:
    if identity.repo_root == _ROOT:
        raise WatchClientWitnessError("native_watch_requires_a_disposable_checkout")
    other = derive_instance_identity(_ROOT, state_home=identity.state_home)
    with instance_operation_lock(other) as lease:
        name = f"{other.project_name}-witness-{secrets.token_hex(6)}"
        network = _checked(
            invocation,
            (
                "network",
                "create",
                "--label",
                f"com.docker.compose.project={other.project_name}",
                "--label",
                f"{_LABEL}={other.root_digest}",
                name,
            ),
            lease=lease,
        )
        if len(network) != 64 or any(c not in "0123456789abcdef" for c in network):
            raise WatchClientWitnessError("native_watch_independent_root_unproved")
        _checked(invocation, ("network", "rm", network), lease=lease)
    return other.root_digest


class _Messages:
    def __init__(self, descriptor: int) -> None:
        self.descriptor = descriptor
        self.buffer = bytearray()

    def read(self, timeout: float = 10) -> dict[str, object]:
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            selector.register(self.descriptor, selectors.EVENT_READ)
            while b"\n" not in self.buffer:
                if not selector.select(max(0, deadline - time.monotonic())):
                    raise WatchClientWitnessError("native_watch_controller_barrier_timeout")
                chunk = os.read(self.descriptor, 4096)
                if not chunk or len(self.buffer) + len(chunk) > 16_384:
                    raise WatchClientWitnessError("native_watch_controller_receipt_unavailable")
                self.buffer.extend(chunk)
        line, _, remainder = self.buffer.partition(b"\n")
        self.buffer = bytearray(remainder)
        value = json.loads(line)
        if not isinstance(value, dict):
            raise WatchClientWitnessError("native_watch_controller_receipt_unavailable")
        return value


@contextmanager
def _controller(
    identity: InstanceIdentity,
    invocation: ProviderInvocation,
    *,
    mode: Literal["watch", "debug"] = "watch",
) -> Iterator[tuple[subprocess.Popen[bytes], _Messages]]:
    read_fd, write_fd = os.pipe()
    stopped = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="native-watch-output")
    process: subprocess.Popen[bytes] | None = None
    drain = None
    try:
        with tempfile.TemporaryFile() as request:
            request.write(
                json.dumps(
                    {
                        "repoRoot": str(identity.repo_root),
                        "stateHome": str(identity.state_home),
                        "argv": invocation.argv,
                        "environment": dict(invocation.environment),
                        "stdoutFd": write_fd,
                        "mode": mode,
                    }
                ).encode()
            )
            request.seek(0)
            process = subprocess.Popen(  # noqa: S603 - exact stdlib witness module, no shell
                [
                    str(getattr(sys, "_base_executable", sys.executable)),
                    "-S",
                    "-m",
                    "scripts.dev_environment.watch_batch_witness",
                ],
                cwd=_ROOT,
                stdin=request,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                pass_fds=(write_fd,),
                env={
                    key: value for key, value in os.environ.items() if not key.startswith("PYTHON")
                },
            )
        os.close(write_fd)
        write_fd = -1
        if process.stdout is None:
            raise WatchClientWitnessError("native_watch_controller_pipe_unavailable")
        messages = _Messages(process.stdout.fileno())
        if mode == "watch":
            _wait_for_readiness(read_fd)
            drain = executor.submit(_drain, read_fd, stopped)
        yield process, messages
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=WATCH_GRACEFUL_SECONDS + WATCH_KILL_SECONDS + 2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            if process.stdout is not None:
                process.stdout.close()
        stopped.set()
        try:
            if drain is not None:
                drain.result(timeout=2)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
            os.close(read_fd)
            if write_fd >= 0:
                os.close(write_fd)


def _drain(read_fd: int, stopped: threading.Event) -> None:
    with selectors.DefaultSelector() as selector:
        selector.register(read_fd, selectors.EVENT_READ)
        while not stopped.is_set():
            if selector.select(0.1) and not os.read(read_fd, 4096):
                return


def verify_watch_batch(
    identity: InstanceIdentity, project: BatchWatchProject, *, hard_parent_death: bool = False
) -> dict[str, object]:
    if os.environ.get("CI") != "true" or sys.platform != "linux" or identity.repo_root == _ROOT:
        raise WatchClientWitnessError("native_watch_host_or_checkout_unqualified")
    with instance_operation_lock(identity) as lease:
        invocation = _admit_running_project(identity, project)
        epoch = _provider_epoch(invocation)
        state = _state_digest(identity)
        postgres = project.service_runtime_identity("postgres")
        token = secrets.token_hex(16)
        table = f"watch_lifecycle_{token}"
        _database(
            invocation,
            postgres.container_id,
            f"CREATE TABLE {table}(value text); INSERT INTO {table} VALUES ('{token}');",  # noqa: S608 - identifiers and values contain only generated hex
            lease=lease,
        )
        gateway = _owned_gateway(identity, invocation)
    barrier = BuildStepBarrier(identity, gateway)
    try:
        with _controller(identity, invocation) as (controller, messages):
            published = messages.read()
            nonce = published.get("nonce")
            if not isinstance(nonce, str) or inspect_watch(identity).nonce != nonce:
                raise WatchClientWitnessError("native_watch_nonce_unproved")
            barrier.arm()
            barrier.wait()
            process_receipt: object = None
            if hard_parent_death:
                controller.kill()
                if controller.wait(timeout=5) != -signal.SIGKILL:
                    raise WatchClientWitnessError("native_watch_controller_death_unproved")
                cancellation = cancel_watch(identity, expected_nonce=nonce, timeout_seconds=0.2)
                if cancellation.state != "blocked" or inspect_watch(identity).nonce != nonce:
                    raise WatchClientWitnessError("native_watch_abandonment_unfenced")
                _assert_fenced(identity)
                process_receipt = None
            else:
                cancellation = cancel_watch(identity, expected_nonce=nonce, timeout_seconds=10)
                receipt = messages.read(timeout=5)
                _require_clean_stop(receipt, cancellation, controller.wait(timeout=5))
                process_receipt = receipt["process"]
            if (
                barrier.path.read_bytes() != barrier.modified
                or _state_digest(identity) != state
                or project.service_runtime_identity("postgres") != postgres
                or _database(invocation, postgres.container_id, f"SELECT value FROM {table};")  # noqa: S608 - table contains only generated hex
                != token
            ):
                raise WatchClientWitnessError("native_watch_retention_unproved")
            independent = _assert_independent_root(identity, invocation)
            result: dict[str, object] = {
                **epoch,
                "nonce": nonce,
                "cancellation": asdict(cancellation),
                "inFlightBarrier": "buildkit-http-request",
                "retained": hard_parent_death,
                "stateDigest": state,
                "dataRetained": True,
                "independentRootDigest": independent,
                "gracefulSeconds": WATCH_GRACEFUL_SECONDS,
                "killSeconds": WATCH_KILL_SECONDS,
                "controllerExitCode": controller.returncode,
                "process": process_receipt,
                "platform": sys.platform,
                "inspection": asdict(inspect_watch(identity)),
                "sourceOriginalDigest": hashlib.sha256(barrier.original).hexdigest(),
                "sourceRetainedDigest": hashlib.sha256(barrier.modified).hexdigest(),
            }
        if not hard_parent_death:
            with instance_operation_lock(identity) as lease:
                barrier.restore(lease)
                _database(invocation, postgres.container_id, f"DROP TABLE {table};", lease=lease)
        return result
    finally:
        barrier.close()


@contextmanager
def _live_provider_child(
    controller: subprocess.Popen[bytes], epoch: dict[str, str]
) -> Iterator[int]:
    """Observe one actual Linux child through a pidfd, never use a PID to signal.

    The PID is read only from this live Popen controller's direct-child list.
    An executable hash and a second child-list observation bind the pidfd before
    killing the controller. Later liveness uses the stable pidfd, not PID reuse.
    """
    children = Path(f"/proc/{controller.pid}/task/{controller.pid}/children")
    child_ids = children.read_text().split()
    if len(child_ids) != 1 or not child_ids[0].isdigit() or controller.poll() is not None:
        raise WatchClientWitnessError("native_debug_provider_child_unproved")
    child_id = int(child_ids[0])
    open_pidfd: Callable[[int], int] | None = getattr(os, "pidfd_open", None)
    if open_pidfd is None:
        raise WatchClientWitnessError("native_debug_pidfd_unavailable")
    descriptor = open_pidfd(child_id)
    try:
        digest = _binary_digest(Path(f"/proc/{child_id}/exe"))
        if (
            digest not in {epoch["dockerBinarySha256"], epoch["composeBinarySha256"]}
            or children.read_text().split() != child_ids
            or controller.poll() is not None
        ):
            raise WatchClientWitnessError("native_debug_provider_child_unproved")
        _require_live_provider(descriptor)
        yield descriptor
    finally:
        os.close(descriptor)


def _require_live_provider(descriptor: int) -> None:
    with selectors.DefaultSelector() as selector:
        selector.register(descriptor, selectors.EVENT_READ)
        if selector.select(0):
            raise WatchClientWitnessError("native_debug_provider_lifetime_unobserved")


def _require_provider_lease(identity: InstanceIdentity) -> None:
    # A watch marker could mask a lost descriptor. F1 requires the actual kernel
    # lock to remain busy while the admitted Docker/Compose child is still live.
    try:
        with bounded_private_lock(operation_paths(identity).mutation):
            raise WatchClientWitnessError("native_debug_provider_released_mutation_lease")
    except PrivateLockBusy:
        pass


def verify_debug_provider_batch(
    identity: InstanceIdentity, project: BatchWatchProject
) -> dict[str, object]:
    if os.environ.get("CI") != "true" or sys.platform != "linux" or identity.repo_root == _ROOT:
        raise WatchClientWitnessError("native_watch_host_or_checkout_unqualified")
    barrier: BuildStepBarrier | None = None
    try:
        with instance_operation_lock(identity):
            invocation = _admit_running_project(identity, project)
            epoch = _provider_epoch(invocation)
            state = _state_digest(identity)
            postgres = project.service_runtime_identity("postgres")
            gateway = _owned_gateway(identity, invocation)
            barrier = BuildStepBarrier(identity, gateway, target="debug")
            barrier.arm()
        with _controller(identity, invocation, mode="debug") as (controller, messages):
            if messages.read() != {"mutationRootDigest": identity.root_digest}:
                raise WatchClientWitnessError("native_debug_controller_lease_unproved")
            barrier.wait()
            with _live_provider_child(controller, epoch) as provider:
                _require_provider_lease(identity)
                controller.kill()
                if controller.wait(timeout=5) != -signal.SIGKILL:
                    raise WatchClientWitnessError("native_debug_controller_death_unproved")
                _require_live_provider(provider)
                _require_provider_lease(identity)
                _assert_fenced(identity)
                independent = _assert_independent_root(identity, invocation)
                if (
                    barrier.path.read_bytes() != barrier.modified
                    or _state_digest(identity) != state
                    or project.service_runtime_identity("postgres") != postgres
                ):
                    raise WatchClientWitnessError("native_debug_retention_unproved")
                # Read-only provider work separates two observed points; no
                # elapsed sleep is used as a claim that the child stayed alive.
                _require_live_provider(provider)
                _require_provider_lease(identity)
                return {
                    **epoch,
                    "inFlightBarrier": "buildkit-http-request",
                    "providerLifetime": "live-owned-pidfd",
                    "mutationDescriptorRetained": True,
                    "controllerExitCode": controller.returncode,
                    "retained": True,
                    "retentionReason": "controller-death-outcome-not-observed",
                    "stateDigest": state,
                    "postgresIdentityRetained": True,
                    "independentRootDigest": independent,
                    "sourceRetainedDigest": hashlib.sha256(barrier.modified).hexdigest(),
                    "platform": sys.platform,
                }
    finally:
        # The dead controller cannot report a provider outcome. Do not restore
        # source or reset the stack merely because the expected-fault probe passed.
        if barrier is not None:
            barrier.close()


def _require_clean_stop(
    receipt: dict[str, object], cancellation: CancelResult, controller_status: int
) -> None:
    if (
        cancellation.state != "quiescent"
        or cancellation.reason != "watch_client_stopped"
        or receipt.get("outcome") != asdict(cancellation)
        or controller_status != 0
    ):
        raise WatchClientWitnessError(
            "native_watch_inflight_cancel_unproved", cancellation=cancellation
        )
    process = receipt.get("process")
    if not isinstance(process, dict) or (
        type(process.get("returncode")) is not int
        or process["returncode"] not in (0, 130)
        or process.get("started") is not True
        or process.get("process_group_quiescent") is not True
        or process.get("cancellation_signal_sent") is not True
        or process.get("escalated") is not False
        or process.get("failure_kind") != "cancelled"
    ):
        raise WatchClientWitnessError("native_watch_inflight_process_unproved")


def _assert_fenced(identity: InstanceIdentity) -> None:
    try:
        with instance_operation_lock(identity):
            raise WatchClientWitnessError("native_watch_abandonment_admitted_mutation")
    except (OperationBlocked, OperationBusy):
        pass
    try:
        with owned_watch_session(identity):
            raise WatchClientWitnessError("native_watch_abandonment_admitted_watch")
    except (OperationBlocked, OperationBusy):
        pass


def verify_isolated_abandonment(
    reference: InstanceIdentity, invocation: ProviderInvocation, *, debug_provider: bool = False
) -> dict[str, object]:
    """Allocate outside the caller's disposable root and retain only our fixture."""
    from scripts.dev_environment.compose import ComposeProject
    from scripts.dev_environment.secrets import ensure_instance_state
    from scripts.mutation.detached_worktree_lifecycle import DetachedWorktreeLifecycle

    if os.environ.get("CI") != "true" or sys.platform != "linux":
        raise WatchClientWitnessError("native_watch_host_or_checkout_unqualified")
    kind = "debug" if debug_provider else "watch"
    lifecycle = DetachedWorktreeLifecycle(
        repo_root=reference.repo_root, temp_prefix=f"ci-coordinator-{kind}-abandoned-"
    )
    allocated_root = lifecycle.temp_root.resolve(strict=True)
    # Refuse a TMPDIR nested in the normal fixture before creating a worktree.
    if allocated_root.is_relative_to(reference.repo_root.parent):
        allocated_root.rmdir()
        raise WatchClientWitnessError("native_watch_retention_root_is_nested")
    identity: InstanceIdentity | None = None
    project: ComposeProject | None = None
    allocated = False
    probe_started = False
    retained: dict[str, str] = {"tempRoot": str(allocated_root)}
    try:
        lifecycle.add_detached_worktree("HEAD")
        identity = derive_instance_identity(lifecycle.worktree, state_home=allocated_root / "state")
        retained.update(
            {
                "repoRoot": str(identity.repo_root),
                "stateHome": str(identity.state_home),
                "projectName": identity.project_name,
                "rootDigest": identity.root_digest,
            }
        )
        with instance_operation_lock(identity):
            environment = ensure_instance_state(identity)
            project = ComposeProject(
                identity, environment, provider_environment=invocation.environment
            )
            project.assert_unallocated()
            allocated = True
            project.up()
        probe_started = True
        if debug_provider:
            result = verify_debug_provider_batch(identity, project)
        else:
            result = verify_watch_batch(identity, project, hard_parent_death=True)
        # Successful expected-fault assertions are not cleanup admission.
        if not debug_provider:
            _assert_fenced(identity)
        return {**result, "retainedFixture": retained}
    except BaseException as error:
        retained["probeReason"] = (
            error.reason if isinstance(error, WatchClientWitnessError) else type(error).__name__
        )
        if identity is not None:
            inspection = inspect_watch(identity)
            retained.update({"watchPhase": inspection.phase, "watchReason": inspection.reason})
            if inspection.nonce is not None:
                retained["watchNonce"] = inspection.nonce
        if debug_provider and probe_started:
            raise WatchClientWitnessError(
                "native_debug_abnormal_fixture_retained", retained_fixture=retained
            ) from error
        try:
            if identity is not None:
                with instance_operation_lock(identity):
                    if allocated and project is not None:
                        project.reset()
                    cleanup = lifecycle.cleanup()
            else:
                cleanup = lifecycle.cleanup()
            if cleanup.state != "passed":
                raise WatchClientWitnessError("native_watch_source_cleanup_unproved")
        except BaseException as cleanup_error:
            raise WatchClientWitnessError(
                "native_watch_abnormal_fixture_retained", retained_fixture=retained
            ) from cleanup_error
        raise error


def main() -> int:
    request: object = json.loads(sys.stdin.buffer.read(65_537))
    if not isinstance(request, dict):
        return 2
    root, home = request.get("repoRoot"), request.get("stateHome")
    argv, environment, output = (
        request.get("argv"),
        request.get("environment"),
        request.get("stdoutFd"),
    )
    mode = request.get("mode", "watch")
    if (
        not isinstance(root, str)
        or not isinstance(home, str)
        or not isinstance(argv, list)
        or not argv
        or not all(isinstance(v, str) for v in argv)
        or not isinstance(environment, dict)
        or not all(isinstance(k, str) and isinstance(v, str) for k, v in environment.items())
        or type(output) is not int
        or mode not in ("watch", "debug")
    ):
        return 2
    identity = derive_instance_identity(Path(root), state_home=Path(home))
    from scripts.dev_environment.compose import ComposeProject, ProviderInvocation

    project = ComposeProject(
        identity,
        {"CI_COORDINATOR_DEV_ROOT_DIGEST": identity.root_digest},
        provider_environment=environment,
    )
    binding = ProviderInvocation(tuple(argv), identity.repo_root, environment)
    if mode == "debug":
        from scripts.dev_environment.debug import BackendDebugger

        with sigterm_guard(), instance_operation_lock(identity) as lease:
            if _admit_running_project(identity, project) != binding:
                return 2
            print(json.dumps({"mutationRootDigest": identity.root_digest}), flush=True)
            debugger = BackendDebugger(project, operation_lease=lease)
            try:
                debugger.start()
            finally:
                debugger.restore()
        return 2  # The expected hard-death probe must never complete this controller.
    with sigterm_guard(), owned_watch_session(identity) as session:
        if _admit_running_project(identity, project) != binding:
            return 2
        print(json.dumps({"nonce": session.nonce}), flush=True)
        process = session.run(
            argv,
            cwd=identity.repo_root,
            env=environment,
            client_contract=WatchClientContract.COMPOSE_JOINED_WATCH,
            timeout_seconds=_CONTROLLER_SECONDS,
            stdout_fd=output,
        )
    print(json.dumps({"process": asdict(process), "outcome": asdict(session.outcome)}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

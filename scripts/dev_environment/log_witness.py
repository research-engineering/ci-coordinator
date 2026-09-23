from __future__ import annotations

import http.client
import http.server
import json
import os
import pwd
import re
import secrets
import select
import shlex
import shutil
import socket
import socketserver
import stat
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from itertools import islice
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from scripts.bounded_process import run_interactive, spawn
from scripts.dev_environment.compose import ComposeError, ComposeProject, ProviderInvocation
from scripts.dev_environment.diagnostics import Reason
from scripts.dev_environment.identity import InstanceIdentity
from scripts.dev_environment.log_transport import LOG_REQUEST_TIMEOUT_SECONDS
from scripts.dev_environment.logs import stream_logs

_OWNER_LABEL = "io.ci-coordinator.log-witness"
_MAX_OUTPUT_BYTES = 65536
_PROGRAM = """
import os, signal, sys
token = sys.argv[1]
def emit(phase):
    os.write(1, (token + '-stdout-' + phase + '\\n').encode())
    os.write(2, (token + '-stderr-' + phase + '\\n').encode())
signal.signal(signal.SIGUSR1, lambda *_: emit('live'))
signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
emit('initial')
while True:
    signal.pause()
"""


def verify_application_logs(
    identity: InstanceIdentity, project: ComposeProject
) -> dict[str, object]:
    selected = project.log_invocations(services=("backend",), tail=0, follow=False)
    if len(selected) != 1:
        raise ComposeError("log witness requires one owned backend")
    receipt = _verify_application_logs(identity, selected[0])
    return {**receipt, "configuredHeaders": verify_configured_log_headers(identity, project)}


def _verify_application_logs(
    identity: InstanceIdentity, base: ProviderInvocation
) -> dict[str, object]:
    image = _command(base, ("inspect", "--format", "{{.Image}}", base.argv[-1])).strip()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image) is None:
        raise ComposeError("log witness image identity is unavailable")
    nonce = secrets.token_hex(16)
    name = "ci-coordinator-logs-" + nonce
    owned: list[str] = []
    primary_error: BaseException | None = None
    try:
        first = _create(base, image, name, nonce, nonce + "-first", owned)
        with tempfile.TemporaryDirectory(prefix="ci-coordinator-log-witness-") as temporary:
            output = Path(temporary) / "output"
            _verify_follow(identity, base, first, nonce + "-first", output)
            finite = _capture(identity, base, first, tail=4, path=output)
            expected = (*_tokens(nonce + "-first", "initial"), *_tokens(nonce + "-first", "live"))
            if sorted(finite.splitlines()) != sorted(expected):
                raise ComposeError("finite logs did not preserve both application channels")
            if _capture(identity, base, first, tail=0, path=output):
                raise ComposeError("finite logs ignored the zero tail")
            peer = _create(base, image, name + "-peer", nonce, nonce + "-peer", owned)
            _verify_replacement(identity, base, image, first, peer, name, nonce, owned, output)
            try:
                _capture(identity, base, first, tail=1, path=output)
            except ComposeError as error:
                if error.reason != Reason.PROVIDER_UNAVAILABLE or _read_output(output):
                    raise ComposeError("log provider failure was not sanitized") from error
            else:
                raise ComposeError("missing log provider was reported successful")
    except BaseException as error:
        primary_error = error
        raise
    finally:
        cleanup_failed = False
        for container in tuple(owned):
            try:
                _remove(base, container, nonce, owned)
            except ComposeError:
                cleanup_failed = True
        if cleanup_failed:
            if primary_error is None:
                raise ComposeError("log witness fixture cleanup is unproven")
            primary_error.add_note("Log witness fixture cleanup is also unproven.")
    return {
        "state": "passed",
        "finiteChannels": ["stdout", "stderr"],
        "followChannels": ["stdout", "stderr"],
        "followIdlePastRequestTimeout": True,
        "zeroTail": True,
        "cancelledStreamsQuiescent": True,
        "replacementRequiresReconnect": True,
        "providerFailureSanitized": True,
    }


def verify_localhost_ssh_logs(
    identity: InstanceIdentity, project: ComposeProject
) -> dict[str, object]:
    selected = project.log_invocations(services=("backend",), tail=0, follow=False)
    if len(selected) != 1 or not Path("/proc/self/stat").is_file():
        raise ComposeError("SSH log witness requires Linux and one owned backend")
    base = selected[0]
    with _localhost_ssh(base) as environment:
        receipt = _verify_application_logs(
            identity, ProviderInvocation(base.argv, base.cwd, environment)
        )
    return {
        **receipt,
        "transport": "localhost-ssh",
        "nonemptySocketPath": True,
        "remoteProxiesQuiescent": True,
    }


@contextmanager
def _header_proxy(
    root: Path, daemon: Path, container: str, required: str
) -> Iterator[tuple[Path, dict[str, int]]]:
    stop = threading.Event()
    counts = {"acceptedFinite": 0, "acceptedFollow": 0, "deniedFinite": 0, "deniedFollow": 0}

    class Handler(http.server.BaseHTTPRequestHandler):
        timeout = 2

        def log_message(self, format: str, *args: object) -> None:
            del format, args

        def do_HEAD(self) -> None:
            self.do_GET()

        def do_GET(self) -> None:
            self.close_connection = True
            route = urlsplit(self.path)
            path = re.sub(r"^/v[0-9]+\.[0-9]+/", "/", route.path)
            allowed = {
                "/_ping",
                "/version",
                f"/containers/{container}/json",
                f"/containers/{container}/logs",
            }
            if (
                path not in allowed
                or self.headers.get("Transfer-Encoding")
                or self.headers.get("Content-Length", "0") != "0"
            ):
                self.send_error(400, "request outside log witness scope")
                return
            if path.endswith("/logs"):
                follow = parse_qs(route.query).get("follow", ["0"])[0] in {"1", "true"}
                accepted = self.headers.get_all("X-Developer-Scope") == [required]
                counts[
                    ("accepted" if accepted else "denied") + ("Follow" if follow else "Finite")
                ] += 1
                if not accepted:
                    payload = json.dumps({"message": required}).encode()
                    self.send_response(403)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
            with socket.socket(socket.AF_UNIX) as upstream:
                upstream.settimeout(2)
                upstream.connect(str(daemon))
                connection = http.client.HTTPConnection("docker", timeout=2)
                connection.sock = upstream
                headers = dict(self.headers.items())
                for key in tuple(headers):
                    if key.lower() == "connection":
                        del headers[key]
                headers["Connection"] = "close"
                try:
                    connection.request(self.command, self.path, headers=headers)
                    deadline = time.monotonic() + 60
                    while not stop.is_set() and time.monotonic() < deadline:
                        readable, _, _ = select.select((self.connection, upstream), (), (), 0.1)
                        if self.connection in readable:
                            return
                        if upstream in readable:
                            data = upstream.recv(65536)
                            if not data:
                                return
                            self.connection.sendall(data)
                finally:
                    connection.close()

    class Server(socketserver.UnixStreamServer):
        requests_seen = 0

        def verify_request(self, request: object, client_address: object) -> bool:
            self.requests_seen += 1
            return self.requests_seen <= 64

        def handle_error(self, request: object, client_address: object) -> None:
            pass

    path = root / "header-proxy.sock"
    with Server(str(path), Handler) as server, ThreadPoolExecutor(max_workers=1) as executor:
        path.chmod(0o600)
        serving = executor.submit(server.serve_forever, 0.05)
        try:
            yield path, counts
        finally:
            stop.set()
            server.shutdown()
            serving.result(timeout=5)


def verify_configured_log_headers(
    identity: InstanceIdentity, project: ComposeProject
) -> dict[str, object]:
    selected = project.log_invocations(services=("backend",), tail=0, follow=False)
    if len(selected) != 1:
        raise ComposeError("header log witness requires one owned backend")
    base = selected[0]
    endpoint = _command(
        base, ("context", "inspect", "--format", "{{.Endpoints.docker.Host}}")
    ).strip()
    if not endpoint.startswith("unix://"):
        raise ComposeError("header log witness requires an admitted local Unix daemon")
    daemon = Path(endpoint.removeprefix("unix://")).resolve(strict=True)
    if not daemon.is_socket():
        raise ComposeError("header log witness daemon socket is unavailable")
    image = _command(base, ("inspect", "--format", "{{.Image}}", base.argv[-1])).strip()
    if re.fullmatch(r"sha256:[0-9a-f]{64}", image) is None:
        raise ComposeError("header log witness image identity is unavailable")
    for source in ("DOCKER_CONFIG", "HOME"):
        nonce = secrets.token_hex(16)
        owned: list[str] = []
        primary: BaseException | None = None
        try:
            container = _create(base, image, "ci-coordinator-header-" + nonce, nonce, nonce, owned)
            with tempfile.TemporaryDirectory(prefix="ci-log-headers-") as temporary:
                root = Path(temporary).resolve()
                required = secrets.token_hex(24)
                with _header_proxy(root, daemon, container, required) as (proxy, counts):
                    _verify_header_route(
                        identity, base, container, nonce, root, proxy, source, required
                    )
                    if not (
                        counts["acceptedFinite"] >= 2
                        and counts["acceptedFollow"] >= 1
                        and counts["deniedFinite"] == 1
                        and counts["deniedFollow"] == 1
                    ):
                        raise ComposeError("header log witness request evidence is incomplete")
        except BaseException as error:
            primary = error
            raise
        finally:
            for container in tuple(owned):
                try:
                    _remove(base, container, nonce, owned)
                except ComposeError:
                    if primary is None:
                        raise
                    primary.add_note("Header log witness fixture cleanup is also unproven.")
    return {
        "state": "passed",
        "configSources": ["DOCKER_CONFIG", "HOME"],
        "cliPositiveControl": True,
        "finiteChannels": ["stdout", "stderr"],
        "followChannels": ["stdout", "stderr"],
        "missingHeaderRejectedFiniteAndFollow": True,
        "providerFailureSanitized": True,
    }


def _verify_header_route(
    identity: InstanceIdentity,
    base: ProviderInvocation,
    container: str,
    token: str,
    root: Path,
    proxy: Path,
    source: str,
    required: str,
) -> None:
    home = root / "home"
    fallback = home / ".docker"
    fallback.mkdir(parents=True)
    _private_text(
        fallback / "config.json", json.dumps({"HttpHeaders": {"X-Developer-Scope": "unselected"}})
    )
    configuration = fallback if source == "HOME" else root / "explicit"
    configuration.mkdir(exist_ok=True)
    path = configuration / "config.json"
    _private_text(path, json.dumps({"HttpHeaders": {"X-Developer-Scope": required}}))
    environment = dict(base.environment)
    for key in ("DOCKER_CONFIG", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"):
        environment.pop(key, None)
    environment.update({"DOCKER_HOST": "unix://" + str(proxy), "HOME": str(home)})
    if source == "DOCKER_CONFIG":
        environment["DOCKER_CONFIG"] = str(configuration)
    admitted = ProviderInvocation(base.argv, base.cwd, environment)
    output = root / "output"
    _verify_follow(identity, admitted, container, token, output, signal_base=base)
    expected = (*_tokens(token, "initial"), *_tokens(token, "live"))
    control = spawn(
        "docker",
        ("logs", "--tail", "4", container),
        cwd=base.cwd,
        env=environment,
        max_buffer=65536,
        timeout_seconds=15,
    )
    if (
        control.status != 0
        or control.error is not None
        or control.failure_kind is not None
        or sorted((control.stdout + control.stderr).encode().splitlines()) != sorted(expected)
    ):
        raise ComposeError("header log witness Docker CLI positive control failed")
    if sorted(_capture(identity, admitted, container, tail=4, path=output).splitlines()) != sorted(
        expected
    ):
        raise ComposeError("header log witness finite application channels differ")
    _private_text(path, "{}")
    for follow in (False, True):
        deadline = time.monotonic() + 15

        def expired(end: float = deadline) -> bool:
            return time.monotonic() >= end

        try:
            with output.open("wb") as handle:
                stream_logs(
                    (_logs(admitted, container, tail=4, follow=follow),),
                    identity=identity,
                    stdout_fd=handle.fileno(),
                    stop_requested=expired,
                )
        except ComposeError as error:
            if (
                error.reason != Reason.PROVIDER_UNAVAILABLE
                or _read_output(output)
                or required in str(error)
                or time.monotonic() >= deadline
            ):
                raise ComposeError("header log witness denial was not sanitized") from None
        else:
            raise ComposeError("header log witness accepted a missing required header")


def _fixture_command(command: str, args: Sequence[str], *, cwd: Path) -> None:
    result = spawn(command, args, cwd=cwd, max_buffer=65536, timeout_seconds=10)
    if result.status != 0 or result.error is not None or result.failure_kind is not None:
        raise ComposeError("SSH log witness preparation failed")


def _private_text(path: Path, value: str, *, executable: bool = False) -> None:
    path.write_text(value)
    path.chmod(0o700 if executable else 0o600)


@contextmanager
def _ssh_fixture_directory() -> Iterator[Path]:
    try:
        home = Path(pwd.getpwuid(os.getuid()).pw_dir).resolve(strict=True)
        metadata = home.stat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid not in {0, os.getuid()}
            or metadata.st_mode & 0o022
        ):
            raise ComposeError("SSH log witness passwd home ownership or modes are inadmissible")
    except (KeyError, OSError):
        raise ComposeError("SSH log witness passwd home is unavailable") from None
    with tempfile.TemporaryDirectory(prefix=".ci-coordinator-log-ssh-", dir=home) as temporary:
        yield Path(temporary).resolve()


def _ssh_event_codes(payload: bytes) -> list[str]:
    patterns = {
        "account-locked": b"not allowed because account is locked",
        "strict-modes-rejected": b"Authentication refused: bad ownership or modes",
        "key-file-unavailable": b"Could not open user",
        "public-key-accepted": b"Accepted publickey for",
        "public-key-rejected": b"Failed publickey for",
        "forced-session-started": b"Starting session: forced-command",
        "privsep-directory-unavailable": b"Missing privilege separation directory",
        "host-key-unavailable": b"no hostkeys available",
        "negotiation-rejected": b"Unable to negotiate with",
        "configuration-invalid": b"Bad configuration option",
    }
    return [code for code, pattern in patterns.items() if pattern in payload]


def _ssh_diagnostics(root: Path) -> dict[str, object]:
    events: list[str] = []
    log_state = "unavailable"
    try:
        with (root / "sshd.log").open("rb") as handle:
            payload = handle.read(_MAX_OUTPUT_BYTES + 1)
        log_state = "truncated" if len(payload) > _MAX_OUTPUT_BYTES else "available"
        events = _ssh_event_codes(payload[:_MAX_OUTPUT_BYTES])
    except OSError:
        pass
    stages = {"entered": 0, "rejected": 0, "exec-attempted": 0, "unrecognized": 0}
    stage_state = "available"
    try:
        markers = tuple(islice((root / "ssh-stages").iterdir(), 257))
        if len(markers) > 256:
            stage_state = "truncated"
        for marker in markers[:256]:
            if not marker.name.isdecimal() or not stat.S_ISREG(marker.lstat().st_mode):
                stage_state = "invalid"
                continue
            with marker.open("rb") as handle:
                value = handle.read(32)
            stage = {
                b"entered\n": "entered",
                b"rejected\n": "rejected",
                b"exec-attempted\n": "exec-attempted",
            }.get(value, "unrecognized")
            stages[stage] += 1
    except OSError:
        stage_state = "unavailable"
    return {
        "serverLog": log_state,
        "serverEvents": events,
        "stageInventory": stage_state,
        "forcedCommandStages": stages,
    }


def _annotate_ssh_failure(error: BaseException, projection: Mapping[str, object]) -> None:
    diagnostic = "sshFixture=" + json.dumps(dict(projection), separators=(",", ":"))
    if isinstance(error, ComposeError):
        error.args = (str(error) + "; " + diagnostic,)
    else:
        error.add_note(diagnostic)


def _ssh_files(
    root: Path, *, port: int, daemon_socket: Path, docker: str, ssh: str, keygen: str
) -> tuple[Path, dict[str, str]]:
    for name in ("host-key", "client-key"):
        _fixture_command(
            keygen, ("-q", "-t", "ed25519", "-N", "", "-f", str(root / name)), cwd=root
        )
    socket_path = root / "selected-daemon.sock"
    socket_path.symlink_to(daemon_socket)
    proxies = root / "proxies"
    proxies.mkdir(mode=0o700)
    stages = root / "ssh-stages"
    stages.mkdir(mode=0o700)
    stage_path = shlex.quote(str(stages)) + '/"$$"'
    _private_text(root / "sshd.log", "")
    remote = "docker --host unix://" + str(socket_path) + " system dial-stdio"
    forced = root / "proxy-command"
    _private_text(
        forced,
        "#!/bin/sh\nset -eu\numask 077\n"
        + "printf 'entered\\n' > "
        + stage_path
        + "\n"
        + 'if [ "${SSH_ORIGINAL_COMMAND-}" != '
        + shlex.quote(remote)
        + " ]; then\n  printf 'rejected\\n' > "
        + stage_path
        + "\n  exit 64\nfi\n"
        + 'printf "%s\\n" "$$" > '
        + shlex.quote(str(proxies))
        + '/"$$"\n'
        + "printf 'exec-attempted\\n' > "
        + stage_path
        + "\n"
        + "exec "
        + shlex.join((docker, "--host=unix://" + str(socket_path), "system", "dial-stdio"))
        + "\n",
        executable=True,
    )
    authorized = root / "authorized_keys"
    _private_text(authorized, (root / "client-key.pub").read_text())
    known_hosts = root / "known_hosts"
    _private_text(known_hosts, f"[127.0.0.1]:{port} " + (root / "host-key.pub").read_text())
    user = pwd.getpwuid(os.getuid()).pw_name
    server_config = root / "sshd_config"
    _private_text(
        server_config,
        "\n".join(
            (
                f"Port {port}",
                "ListenAddress 127.0.0.1",
                f"HostKey {root / 'host-key'}",
                f"PidFile {root / 'sshd.pid'}",
                f"AuthorizedKeysFile {authorized}",
                f"AllowUsers {user}",
                "StrictModes yes",
                "PasswordAuthentication no",
                "KbdInteractiveAuthentication no",
                "PubkeyAuthentication yes",
                "UsePAM no",
                "PermitRootLogin no",
                "PermitUserEnvironment no",
                "PermitUserRC no",
                "AllowTcpForwarding no",
                "X11Forwarding no",
                "PermitTTY no",
                f"ForceCommand {forced}",
                "LogLevel VERBOSE",
                "",
            )
        ),
    )
    client_config = root / "ssh_config"
    _private_text(
        client_config,
        "\n".join(
            (
                "Host *",
                f"  IdentityFile {root / 'client-key'}",
                f"  UserKnownHostsFile {known_hosts}",
                "  GlobalKnownHostsFile /dev/null",
                "  StrictHostKeyChecking yes",
                "  IdentitiesOnly yes",
                "  BatchMode yes",
                "  PasswordAuthentication no",
                "  ControlMaster no",
                "  ControlPath none",
                "  RequestTTY no",
                "  ConnectTimeout 5",
                "",
            )
        ),
    )
    binaries = root / "bin"
    binaries.mkdir(mode=0o700)
    _private_text(
        binaries / "ssh",
        "#!/bin/sh\nexec " + shlex.join((ssh, "-F", str(client_config))) + ' "$@"\n',
        executable=True,
    )
    config = root / "docker"
    config.mkdir(mode=0o700)
    return server_config, {
        "HOME": str(root),
        "DOCKER_CONFIG": str(config),
        "PATH": str(binaries)
        + os.pathsep
        + str(Path(docker).parent)
        + os.pathsep
        + "/usr/bin:/bin",
        "DOCKER_HOST": f"ssh://{user}@127.0.0.1:{port}{socket_path}",
    }


def _wait_for_proxy_exit(root: Path, *, required: bool) -> None:
    deadline = time.monotonic() + 10
    while True:
        markers = tuple((root / "proxies").iterdir())
        if required and not markers:
            raise ComposeError("SSH log witness did not observe an admitted remote proxy")
        if len(markers) > 256 or any(not path.name.isdecimal() for path in markers):
            raise ComposeError("SSH log witness proxy inventory is invalid")
        if not any(Path(f"/proc/{path.name}").exists() for path in markers):
            return
        if time.monotonic() >= deadline:
            raise ComposeError("SSH log witness remote proxies did not quiesce")
        threading.Event().wait(0.05)


@contextmanager
def _localhost_ssh(base: ProviderInvocation) -> Iterator[Mapping[str, str]]:
    binaries = tuple(shutil.which(name) for name in ("docker", "ssh", "ssh-keygen"))
    sshd = Path("/usr/sbin/sshd")
    if any(value is None for value in binaries) or not sshd.is_file():
        raise ComposeError("SSH log witness requires openssh-server and openssh-client")
    docker, ssh, keygen = (str(value) for value in binaries)
    endpoint = _command(
        base, ("context", "inspect", "--format", "{{.Endpoints.docker.Host}}")
    ).strip()
    if not endpoint.startswith("unix://"):
        raise ComposeError("SSH log witness requires an admitted local Unix daemon")
    daemon_socket = Path(endpoint.removeprefix("unix://")).resolve(strict=True)
    if not daemon_socket.is_socket():
        raise ComposeError("SSH log witness daemon socket is unavailable")
    with _ssh_fixture_directory() as root:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        config, environment = _ssh_files(
            root, port=port, daemon_socket=daemon_socket, docker=docker, ssh=ssh, keygen=keygen
        )
        _select_ssh_context(base, environment)
        stop = threading.Event()
        primary: BaseException | None = None

        def stop_server() -> bool:
            try:
                return stop.is_set() or (root / "sshd.log").stat().st_size > _MAX_OUTPUT_BYTES
            except OSError:
                return True

        with ThreadPoolExecutor(max_workers=1) as executor:
            server = executor.submit(
                run_interactive,
                str(sshd),
                ("-D", "-E", str(root / "sshd.log"), "-f", str(config)),
                cwd=root,
                env={"PATH": "/usr/bin:/bin", "HOME": str(root)},
                stop_requested=stop_server,
                timeout_seconds=240,
                stderr=subprocess.DEVNULL,
            )
            try:
                deadline = time.monotonic() + 10
                while True:
                    if server.done() or time.monotonic() >= deadline:
                        raise ComposeError("SSH log witness listener did not become available")
                    try:
                        with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                            break
                    except OSError:
                        stop.wait(0.05)
                yield environment
            except BaseException as error:
                primary = error
                raise
            finally:
                try:
                    _wait_for_proxy_exit(root, required=primary is None)
                except ComposeError as error:
                    if primary is None:
                        primary = error
                    else:
                        primary.add_note("SSH log witness proxy cleanup is also unproven.")
                stop.set()
                result = server.result()
                if (
                    not result.process_group_quiescent
                    or result.escalated
                    or result.failure_kind != "cancelled"
                    or not result.cancellation_signal_sent
                ):
                    if primary is None:
                        primary = ComposeError("SSH log witness server cleanup is unproven")
                    else:
                        primary.add_note("SSH log witness server cleanup is also unproven.")
                projection = _ssh_diagnostics(root)
                if primary is None and projection["serverLog"] != "available":
                    primary = ComposeError("SSH log witness bounded diagnostics are unavailable")
                if primary is not None and not isinstance(primary, GeneratorExit):
                    _annotate_ssh_failure(primary, projection)
                    raise primary


def _select_ssh_context(base: ProviderInvocation, environment: dict[str, str]) -> None:
    source = ProviderInvocation(base.argv, base.cwd, environment)
    _command(
        source,
        (
            "context",
            "create",
            "owned-log-ssh",
            "--docker",
            "host=" + environment["DOCKER_HOST"],
        ),
    )
    environment["DOCKER_CONTEXT"] = "owned-log-ssh"
    environment.pop("DOCKER_HOST")
    if _command(source, ("context", "show")).strip() != "owned-log-ssh":
        raise ComposeError("SSH log witness CLI did not select the isolated context")


def _command(base: ProviderInvocation, arguments: Sequence[str]) -> str:
    result = spawn(
        "docker",
        arguments,
        cwd=base.cwd,
        env=base.environment,
        max_buffer=_MAX_OUTPUT_BYTES,
        timeout_seconds=15,
    )
    if result.status != 0 or result.error is not None or result.failure_kind is not None:
        operation = arguments[0] if arguments else "unknown"
        if operation not in {"context", "inspect", "create", "start", "exec", "rm"}:
            operation = "unknown"
        subcommand = "none"
        if operation == "context":
            subcommand = arguments[1] if len(arguments) > 1 else "unknown"
            if subcommand not in {"inspect", "create", "show"}:
                subcommand = "unknown"
        status = "none" if result.status is None else str(result.status)
        failure = result.failure_kind or "none"
        process_error = str(result.error is not None).lower()
        raise ComposeError(
            "log witness provider operation failed "
            f"(provider=docker operation={operation} subcommand={subcommand} "
            f"status={status} failureKind={failure} processError={process_error})"
        )
    return result.stdout


def _create(
    base: ProviderInvocation,
    image: str,
    name: str,
    owner: str,
    token: str,
    owned: list[str],
) -> str:
    container = _command(
        base,
        (
            "create",
            "--name",
            name,
            "--label",
            f"{_OWNER_LABEL}={owner}",
            "--network",
            "none",
            "--read-only",
            "--user",
            "65532:65532",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--pids-limit",
            "16",
            "--memory",
            "64m",
            "--entrypoint",
            "python",
            image,
            "-u",
            "-c",
            _PROGRAM,
            token,
        ),
    ).strip()
    if re.fullmatch(r"[0-9a-f]{64}", container) is None:
        raise ComposeError("log witness container identity is unavailable")
    owned.append(container)
    _admit(base, container, owner)
    _command(base, ("start", container))
    return container


def _admit(base: ProviderInvocation, container: str, owner: str) -> None:
    observed = _command(
        base,
        (
            "inspect",
            "--format",
            '{{index .Config.Labels "' + _OWNER_LABEL + '"}}|{{.Config.Tty}}',
            container,
        ),
    )
    if observed.strip() != owner + "|false":
        raise ComposeError("log witness fixture ownership or non-TTY identity changed")


def _remove(base: ProviderInvocation, container: str, owner: str, owned: list[str]) -> None:
    _admit(base, container, owner)
    _command(base, ("rm", "--force", container))
    owned.remove(container)


def _logs(
    base: ProviderInvocation, container: str, *, tail: int, follow: bool
) -> ProviderInvocation:
    return ProviderInvocation(
        ("docker", "logs", "--tail", str(tail), *(("--follow",) if follow else ()), container),
        base.cwd,
        base.environment,
    )


def _tokens(token: str, phase: str) -> tuple[bytes, bytes]:
    return (f"{token}-stdout-{phase}".encode(), f"{token}-stderr-{phase}".encode())


def _read_output(path: Path) -> bytes:
    with path.open("rb") as handle:
        output = handle.read(_MAX_OUTPUT_BYTES + 1)
    if len(output) > _MAX_OUTPUT_BYTES:
        raise ComposeError("log witness output exceeded its bound")
    return output


def _capture(
    identity: InstanceIdentity, base: ProviderInvocation, container: str, *, tail: int, path: Path
) -> bytes:
    with path.open("wb") as output:
        stream_logs(
            (_logs(base, container, tail=tail, follow=False),),
            identity=identity,
            stdout_fd=output.fileno(),
        )
    return _read_output(path)


def _verify_follow(
    identity: InstanceIdentity,
    base: ProviderInvocation,
    container: str,
    token: str,
    path: Path,
    *,
    signal_base: ProviderInvocation | None = None,
) -> None:
    deadline = time.monotonic() + 45
    emitted = False
    idle_until: float | None = None

    def observed_both_channels() -> bool:
        nonlocal emitted, idle_until
        if time.monotonic() >= deadline:
            raise ComposeError("follow log channel observation timed out")
        observed = _read_output(path)
        if idle_until is None and all(value in observed for value in _tokens(token, "initial")):
            idle_until = time.monotonic() + LOG_REQUEST_TIMEOUT_SECONDS + 1
        if not emitted and idle_until is not None and time.monotonic() >= idle_until:
            _command(
                signal_base or base,
                ("exec", container, "python", "-c", "import os,signal; os.kill(1, signal.SIGUSR1)"),
            )
            emitted = True
        return all(value in observed for value in _tokens(token, "live"))

    with path.open("wb") as output:
        stream_logs(
            (_logs(base, container, tail=4, follow=True),),
            identity=identity,
            stop_requested=observed_both_channels,
            stdout_fd=output.fileno(),
        )
    if not emitted or not all(value in _read_output(path) for value in _tokens(token, "live")):
        raise ComposeError("follow logs did not preserve both live application channels")


def _verify_replacement(
    identity: InstanceIdentity,
    base: ProviderInvocation,
    image: str,
    first: str,
    peer: str,
    name: str,
    nonce: str,
    owned: list[str],
    path: Path,
) -> None:
    deadline = time.monotonic() + 45
    replaced = False

    def replace_after_both_streams_started() -> bool:
        nonlocal replaced
        if time.monotonic() >= deadline:
            raise ComposeError("log replacement observation timed out")
        expected = (*_tokens(nonce + "-first", "initial"), *_tokens(nonce + "-peer", "initial"))
        if not replaced and all(value in _read_output(path) for value in expected):
            _remove(base, first, nonce, owned)
            _create(base, image, name, nonce, nonce + "-replacement", owned)
            replaced = True
        return False

    try:
        with path.open("wb") as output:
            stream_logs(
                tuple(_logs(base, container, tail=4, follow=True) for container in (first, peer)),
                identity=identity,
                stop_requested=replace_after_both_streams_started,
                stdout_fd=output.fileno(),
            )
    except ComposeError as error:
        if error.reason != Reason.STALE_OBSERVATION:
            raise
    else:
        raise ComposeError("container replacement did not end the old log observation")
    if not replaced or (nonce + "-replacement").encode() in _read_output(path):
        raise ComposeError("log observation followed an unadmitted replacement")

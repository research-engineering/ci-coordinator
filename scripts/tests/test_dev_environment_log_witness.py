from __future__ import annotations

import json
import os
import pwd
import stat
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from scripts.bounded_process import CommandResult
from scripts.dev_environment import log_witness
from scripts.dev_environment.compose import ComposeError, ComposeProject, ProviderInvocation
from scripts.dev_environment.diagnostics import Reason
from scripts.dev_environment.identity import derive_instance_identity
from scripts.dev_environment.log_transport import LOG_REQUEST_TIMEOUT_SECONDS


def test_ssh_fixture_uses_passwd_home_with_private_modes_and_ignores_environment_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "passwd-home"
    home.mkdir(mode=0o700)
    monkeypatch.setattr(pwd, "getpwuid", lambda _uid: SimpleNamespace(pw_dir=str(home)))
    monkeypatch.setenv("HOME", str(tmp_path / "unselected-home"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "unselected-tmp"))
    with log_witness._ssh_fixture_directory() as directory:
        assert directory.parent == home
        assert stat.S_IMODE(directory.stat().st_mode) == 0o700
        assert directory.stat().st_uid == os.getuid()
    assert not directory.exists()
    assert stat.S_IMODE(home.stat().st_mode) == 0o700


@pytest.mark.parametrize("mode", [0o720, 0o702, 0o777])
def test_ssh_fixture_rejects_a_writable_passwd_home_without_changing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: int
) -> None:
    home = tmp_path / "passwd-home"
    home.mkdir()
    home.chmod(mode)
    monkeypatch.setattr(pwd, "getpwuid", lambda _uid: SimpleNamespace(pw_dir=str(home)))
    with (
        pytest.raises(ComposeError, match="passwd home ownership or modes"),
        log_witness._ssh_fixture_directory(),
    ):
        pytest.fail("inadmissible home must not allocate a fixture")
    assert stat.S_IMODE(home.stat().st_mode) == mode
    assert tuple(home.iterdir()) == ()


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"User private-user not allowed because account is locked\n", ["account-locked"]),
        (
            b"Authentication refused: bad ownership or modes for directory /secret-path\n",
            ["strict-modes-rejected"],
        ),
        (
            b"Could not open user 'private-user' authorized keys '/secret': Permission denied\n",
            ["key-file-unavailable"],
        ),
        (
            b"Accepted publickey for private-user from 127.0.0.1 port 1234 "
            b"ssh2: ED25519 SHA256:private-fingerprint\n",
            ["public-key-accepted"],
        ),
        (
            b"Starting session: forced-command (config) '/private-path' for private-user\n",
            ["forced-session-started"],
        ),
        (b"Authorization: secret-header\n-----BEGIN PRIVATE KEY-----\nsecret-output", []),
    ],
)
def test_ssh_diagnostics_project_only_closed_server_event_codes(
    tmp_path: Path, payload: bytes, expected: list[str]
) -> None:
    (tmp_path / "sshd.log").write_bytes(payload)
    (tmp_path / "ssh-stages").mkdir()
    assert log_witness._ssh_diagnostics(tmp_path) == {
        "serverLog": "available",
        "serverEvents": expected,
        "stageInventory": "available",
        "forcedCommandStages": {
            "entered": 0,
            "rejected": 0,
            "exec-attempted": 0,
            "unrecognized": 0,
        },
    }


def test_ssh_diagnostic_bounds_and_missing_files_preserve_the_provider_failure(
    tmp_path: Path,
) -> None:
    (tmp_path / "sshd.log").write_bytes(b"x" * 65536 + b"Accepted publickey for secret\n")
    stages = tmp_path / "ssh-stages"
    stages.mkdir()
    for number in range(257):
        (stages / str(number)).write_text("exec-attempted\n")
    projection = log_witness._ssh_diagnostics(tmp_path)
    assert projection["serverLog"] == "truncated" and projection["serverEvents"] == []
    assert projection["stageInventory"] == "truncated"
    assert projection["forcedCommandStages"] == {
        "entered": 0,
        "rejected": 0,
        "exec-attempted": 256,
        "unrecognized": 0,
    }
    missing = log_witness._ssh_diagnostics(tmp_path / "absent")
    assert missing["serverLog"] == missing["stageInventory"] == "unavailable"
    error = ComposeError(
        "provider=docker operation=inspect status=1", reason=Reason.PROVIDER_UNAVAILABLE
    )
    error.add_note("existing cleanup failure")
    log_witness._annotate_ssh_failure(error, missing)
    assert str(error).startswith("provider=docker operation=inspect status=1; sshFixture=")
    assert error.reason == Reason.PROVIDER_UNAVAILABLE
    assert error.__notes__ == ["existing cleanup failure"]


@pytest.mark.parametrize("header_failure", [False, True])
def test_application_receipt_requires_the_native_header_witness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, header_failure: bool
) -> None:
    identity = derive_instance_identity(
        tmp_path, state_home=tmp_path.parent / (tmp_path.name + "-state")
    )
    base = ProviderInvocation(("docker", "logs", "a" * 64), tmp_path, {})
    project = cast("ComposeProject", SimpleNamespace(log_invocations=lambda **_options: (base,)))
    monkeypatch.setattr(log_witness, "_verify_application_logs", lambda *_args: {"state": "passed"})

    def headers(*_args: object) -> dict[str, object]:
        if header_failure:
            raise ComposeError("header witness failed")
        return {"state": "passed", "missingHeaderRejectedFiniteAndFollow": True}

    monkeypatch.setattr(log_witness, "verify_configured_log_headers", headers)
    if header_failure:
        with pytest.raises(ComposeError, match="header witness failed"):
            log_witness.verify_application_logs(identity, project)
    else:
        assert log_witness.verify_application_logs(identity, project) == {
            "state": "passed",
            "configuredHeaders": {"state": "passed", "missingHeaderRejectedFiniteAndFollow": True},
        }


@pytest.mark.parametrize("source", ["DOCKER_CONFIG", "HOME"])
@pytest.mark.parametrize("denial", ["proper", "leaked", "success", "wrong-reason", "timeout"])
def test_native_header_oracle_requires_sanitized_rejection_for_finite_and_follow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    denial: str,
) -> None:
    identity = derive_instance_identity(
        tmp_path, state_home=tmp_path.parent / (tmp_path.name + "-state")
    )
    base = ProviderInvocation(
        ("docker", "logs", "a" * 64),
        tmp_path,
        {"DOCKER_CONFIG": "original", "DOCKER_CONTEXT": "original"},
    )
    observed: list[bool] = []
    clock = [0.0]
    expected = b"token-stdout-initial\ntoken-stderr-initial\ntoken-stdout-live\ntoken-stderr-live\n"

    def config(admitted: ProviderInvocation) -> Path:
        environment = admitted.environment
        assert "DOCKER_CONTEXT" not in environment
        assert environment["DOCKER_HOST"] == "unix://" + str(tmp_path / "proxy.sock")
        if source == "HOME":
            assert "DOCKER_CONFIG" not in environment
            return Path(environment["HOME"]) / ".docker/config.json"
        assert environment["DOCKER_CONFIG"] == str(tmp_path / "explicit")
        assert (
            json.loads((Path(environment["HOME"]) / ".docker/config.json").read_text())[
                "HttpHeaders"
            ]["X-Developer-Scope"]
            == "unselected"
        )
        return Path(environment["DOCKER_CONFIG"]) / "config.json"

    def follow(
        _identity: object, admitted: ProviderInvocation, *_args: object, **options: object
    ) -> None:
        assert options["signal_base"] is base
        assert json.loads(config(admitted).read_text()) == {
            "HttpHeaders": {"X-Developer-Scope": "synthetic-private-value"}
        }

    def stream(invocations: Sequence[ProviderInvocation], **options: object) -> None:
        assert json.loads(config(invocations[0]).read_text()) == {}
        assert cast("Callable[[], bool]", options["stop_requested"])() is False
        observed.append("--follow" in invocations[0].argv)
        if denial == "success":
            return
        if denial == "leaked":
            os.write(cast("int", options["stdout_fd"]), b"synthetic-private-value")
        if denial == "timeout":
            clock[0] += 20
        reason = (
            Reason.STALE_OBSERVATION if denial == "wrong-reason" else Reason.PROVIDER_UNAVAILABLE
        )
        raise ComposeError("provider denied request", reason=reason)

    monkeypatch.setattr(log_witness, "_verify_follow", follow)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        log_witness, "spawn", lambda *_args, **_kwargs: CommandResult(0, expected.decode(), "")
    )
    monkeypatch.setattr(log_witness, "_capture", lambda *_args, **_kwargs: expected)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(log_witness, "stream_logs", stream)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(log_witness, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    arguments = (
        identity,
        base,
        "a" * 64,
        "token",
        tmp_path,
        tmp_path / "proxy.sock",
        source,
        "synthetic-private-value",
    )
    if denial == "proper":
        log_witness._verify_header_route(*arguments)
        assert observed == [False, True]
    else:
        with pytest.raises(ComposeError, match="header log witness"):
            log_witness._verify_header_route(*arguments)


@pytest.mark.parametrize(
    ("result", "status", "failure", "process_error"),
    [
        (CommandResult(1, "secret-output", "secret-stderr"), "1", "none", "false"),
        (
            CommandResult(None, "secret-output", "secret-stderr", "secret-error", "spawn"),
            "none",
            "spawn",
            "true",
        ),
        (
            CommandResult(0, "secret-output", "secret-stderr", failure_kind="timeout"),
            "0",
            "timeout",
            "false",
        ),
        (CommandResult(0, "secret-output", "secret-stderr", "secret-error"), "0", "none", "true"),
        (
            CommandResult(-15, "secret-output", "secret-stderr", signal="secret-signal"),
            "-15",
            "none",
            "false",
        ),
    ],
)
def test_provider_failure_identifies_the_call_without_exposing_provider_data(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: CommandResult,
    status: str,
    failure: str,
    process_error: str,
) -> None:
    base = ProviderInvocation(("docker", "logs"), tmp_path, {"SECRET": "secret-environment"})
    arguments = ("context", "create", "secret-context", "--docker", "host=secret-host")

    def spawn(command: str, args: Sequence[str], **options: object) -> CommandResult:
        assert command == "docker" and args == arguments
        assert options == {
            "cwd": tmp_path,
            "env": base.environment,
            "max_buffer": 65536,
            "timeout_seconds": 15,
        }
        return result

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(log_witness, "spawn", spawn)
    with pytest.raises(ComposeError) as captured:
        log_witness._command(base, arguments)
    assert captured.value.reason == Reason.PROVIDER_UNAVAILABLE
    assert str(captured.value) == (
        "log witness provider operation failed "
        "(provider=docker operation=context subcommand=create "
        f"status={status} failureKind={failure} processError={process_error})"
    )


@pytest.mark.parametrize(
    ("arguments", "operation", "subcommand"),
    [
        ((), "unknown", "none"),
        (("secret-operation",), "unknown", "none"),
        (("context", "secret-subcommand"), "context", "unknown"),
        (("context",), "context", "unknown"),
        (("inspect", "secret-container"), "inspect", "none"),
        (("exec", "secret-container", "secret-command"), "exec", "none"),
    ],
)
def test_provider_failure_projects_only_admitted_command_names(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: tuple[str, ...],
    operation: str,
    subcommand: str,
) -> None:
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(log_witness, "spawn", lambda *_args, **_kwargs: CommandResult(1, "", ""))
    with pytest.raises(ComposeError) as captured:
        log_witness._command(ProviderInvocation(("docker", "logs"), tmp_path, {}), arguments)
    assert str(captured.value) == (
        "log witness provider operation failed "
        f"(provider=docker operation={operation} subcommand={subcommand} "
        "status=1 failureKind=none processError=false)"
    )


@pytest.mark.parametrize("selected", [False, True])
def test_ssh_fixture_admits_cli_context_selection_without_a_conflicting_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selected: bool
) -> None:
    environment = {
        "DOCKER_HOST": "ssh://private-host/private-socket",
        "DOCKER_CONFIG": "private-config",
    }
    calls: list[tuple[str, ...]] = []

    def command(base: ProviderInvocation, arguments: Sequence[str]) -> str:
        calls.append(tuple(arguments))
        if arguments[:2] == ("context", "create"):
            assert arguments == (
                "context",
                "create",
                "owned-log-ssh",
                "--docker",
                "host=ssh://private-host/private-socket",
            )
            return "owned-log-ssh\n"
        assert arguments == ("context", "show")
        assert base.environment["DOCKER_CONTEXT"] == "owned-log-ssh"
        assert "DOCKER_HOST" not in base.environment
        return "owned-log-ssh\n" if selected else "secret-unexpected-context\n"

    monkeypatch.setattr(log_witness, "_command", command)
    base = ProviderInvocation(("docker", "logs"), tmp_path, {"DOCKER_HOST": "original-host"})
    if selected:
        log_witness._select_ssh_context(base, environment)
    else:
        with pytest.raises(
            ComposeError, match=r"^SSH log witness CLI did not select the isolated context$"
        ):
            log_witness._select_ssh_context(base, environment)
    assert len(calls) == 2
    assert environment == {"DOCKER_CONTEXT": "owned-log-ssh", "DOCKER_CONFIG": "private-config"}
    assert base.environment == {"DOCKER_HOST": "original-host"}


@pytest.mark.parametrize("stderr_observed", [False, True])
def test_follow_witness_requires_both_live_channels_after_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stderr_observed: bool
) -> None:
    identity = derive_instance_identity(
        tmp_path, state_home=tmp_path.parent / (tmp_path.name + "-state")
    )
    base = ProviderInvocation(("docker", "logs", "--tail", "4", "a" * 64), tmp_path, {})
    calls: list[tuple[str, ...]] = []
    clock = [0.0]

    def command(_base: ProviderInvocation, arguments: Sequence[str]) -> str:
        calls.append(tuple(arguments))
        return ""

    def stream(_invocations: Sequence[ProviderInvocation], **options: object) -> None:
        output = cast("int", options["stdout_fd"])
        stop = cast("Callable[[], bool]", options["stop_requested"])
        os.write(output, b"token-stdout-initial\ntoken-stderr-initial\n")
        assert stop() is False
        assert calls == []
        clock[0] = LOG_REQUEST_TIMEOUT_SECONDS + 1
        assert stop() is False
        os.write(output, b"token-stdout-live\n")
        if stderr_observed:
            os.write(output, b"token-stderr-live\n")
        assert stop() is stderr_observed

    monkeypatch.setattr(log_witness, "_command", command)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(log_witness, "stream_logs", stream)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(log_witness, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    if stderr_observed:
        log_witness._verify_follow(identity, base, "a" * 64, "token", tmp_path / "output")
    else:
        with pytest.raises(ComposeError, match="both live application channels"):
            log_witness._verify_follow(identity, base, "a" * 64, "token", tmp_path / "output")
    assert len(calls) == 1 and calls[0][:3] == ("exec", "a" * 64, "python")


@pytest.mark.parametrize("leaked_replacement", [False, True])
def test_replacement_witness_rejects_automatic_follow_of_the_new_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, leaked_replacement: bool
) -> None:
    identity = derive_instance_identity(
        tmp_path, state_home=tmp_path.parent / (tmp_path.name + "-state")
    )
    base = ProviderInvocation(("docker", "logs", "--tail", "4", "a" * 64), tmp_path, {})
    effects: list[str] = []

    def stream(invocations: Sequence[ProviderInvocation], **options: object) -> None:
        assert {item.argv[-1] for item in invocations} == {"a" * 64, "b" * 64}
        output = cast("int", options["stdout_fd"])
        os.write(output, b"nonce-first-stdout-initial\nnonce-first-stderr-initial\n")
        os.write(output, b"nonce-peer-stdout-initial\nnonce-peer-stderr-initial\n")
        assert cast("Callable[[], bool]", options["stop_requested"])() is False
        if leaked_replacement:
            os.write(output, b"nonce-replacement-stdout-initial\n")
        raise ComposeError("selected stream ended", reason=Reason.STALE_OBSERVATION)

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(log_witness, "stream_logs", stream)
    monkeypatch.setattr(log_witness, "_remove", lambda *_args: effects.append("remove"))
    monkeypatch.setattr(log_witness, "_create", lambda *_args: effects.append("create"))
    owned: list[str] = []
    arguments = (
        identity,
        base,
        "image",
        "a" * 64,
        "b" * 64,
        "fixture-name",
        "nonce",
        owned,
        tmp_path / "output",
    )
    if leaked_replacement:
        with pytest.raises(ComposeError, match="unadmitted replacement"):
            log_witness._verify_replacement(*arguments)
    else:
        log_witness._verify_replacement(*arguments)
    assert effects == ["remove", "create"]


def test_log_fixture_cleanup_never_removes_a_foreign_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, ...]] = []

    def command(_base: ProviderInvocation, arguments: Sequence[str]) -> str:
        calls.append(tuple(arguments))
        return "foreign|false\n"

    monkeypatch.setattr(log_witness, "_command", command)
    base = ProviderInvocation(("docker", "logs", "--tail", "0", "a" * 64), tmp_path, {})
    with pytest.raises(ComposeError, match="ownership"):
        log_witness._remove(base, "a" * 64, "owner", ["a" * 64])
    assert len(calls) == 1 and calls[0][0] == "inspect"


@pytest.mark.parametrize("command_kind", ["exact", "missing-path", "wrong-path", "extra-command"])
def test_ssh_fixture_requires_the_cli_selected_socket_and_isolates_client_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command_kind: str,
) -> None:
    daemon = tmp_path / "daemon.sock"
    docker = tmp_path / "fixture-docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    docker.chmod(0o700)
    calls: list[tuple[str, ...]] = []

    def keygen(_command: str, args: Sequence[str], *, cwd: Path) -> None:
        calls.append(tuple(args))
        assert cwd == tmp_path and args[:4] == ("-q", "-t", "ed25519", "-N")
        Path(args[-1] + ".pub").write_text("ssh-ed25519 public-fixture-key\n")

    monkeypatch.setattr(log_witness, "_fixture_command", keygen)
    server, environment = log_witness._ssh_files(
        tmp_path,
        port=43210,
        daemon_socket=daemon,
        docker=str(docker),
        ssh="/usr/bin/ssh",
        keygen="/usr/bin/ssh-keygen",
    )
    assert len(calls) == 2
    assert environment["DOCKER_CONFIG"] == str(tmp_path / "docker")
    assert environment["HOME"] == str(tmp_path)
    assert environment["DOCKER_HOST"].endswith(":43210" + str(tmp_path / "selected-daemon.sock"))
    assert (tmp_path / "selected-daemon.sock").readlink() == daemon
    proxy = (tmp_path / "proxy-command").read_text()
    assert '"${SSH_ORIGINAL_COMMAND-}"' in proxy
    assert (
        "docker --host unix://" + str(tmp_path / "selected-daemon.sock") + " system dial-stdio"
        in proxy
    )
    assert '"$$"' in proxy and "/proxies/" in proxy
    config = server.read_text()
    assert "ListenAddress 127.0.0.1\n" in config and "PasswordAuthentication no\n" in config
    assert "StrictModes yes\n" in config and "UsePAM no\n" in config
    assert "LogLevel VERBOSE\n" in config
    assert stat.S_IMODE((tmp_path / "sshd.log").stat().st_mode) == 0o600
    assert f"ForceCommand {tmp_path / 'proxy-command'}\n" in config
    client = (tmp_path / "ssh_config").read_text()
    assert "StrictHostKeyChecking yes\n" in client and "ControlPath none\n" in client
    assert "-F " + str(tmp_path / "ssh_config") in (tmp_path / "bin/ssh").read_text()
    socket_path = str(tmp_path / "selected-daemon.sock")
    original = {
        "exact": f"docker --host unix://{socket_path} system dial-stdio",
        "missing-path": "docker system dial-stdio",
        "wrong-path": f"docker --host unix://{socket_path}-other system dial-stdio",
        "extra-command": f"docker --host unix://{socket_path} system dial-stdio; printf extra",
    }[command_kind]
    result = subprocess.run(
        (str(tmp_path / "proxy-command"),),
        env={"SSH_ORIGINAL_COMMAND": original},
        capture_output=True,
        text=True,
        check=False,
        timeout=5,
    )
    markers = tuple((tmp_path / "proxies").iterdir())
    stages = tuple((tmp_path / "ssh-stages").iterdir())
    assert len(stages) == 1 and stages[0].name.isdecimal()
    assert stat.S_IMODE(stages[0].stat().st_mode) == 0o600
    assert result.stderr == ""
    if command_kind == "exact":
        assert result.returncode == 0
        assert result.stdout.splitlines() == [
            "--host=unix://" + socket_path,
            "system",
            "dial-stdio",
        ]
        assert len(markers) == 1 and markers[0].name.isdecimal()
        assert markers[0].read_text() == markers[0].name + "\n"
        assert stages[0].read_text() == "exec-attempted\n"
    else:
        assert result.returncode == 64 and result.stdout == ""
        assert markers == ()
        assert stages[0].read_text() == "rejected\n"


def test_native_ssh_receipt_cannot_be_vacuous(tmp_path: Path) -> None:
    (tmp_path / "proxies").mkdir()
    with pytest.raises(ComposeError, match="did not observe"):
        log_witness._wait_for_proxy_exit(tmp_path, required=True)


@pytest.mark.parametrize("alive", [False, True])
def test_native_ssh_receipt_requires_recorded_remote_proxies_to_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alive: bool,
) -> None:
    proxies = tmp_path / "proxies"
    proxies.mkdir()
    (proxies / "12345").write_text("12345\n")
    moments = iter((0.0, 11.0))
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(log_witness, "time", SimpleNamespace(monotonic=lambda: next(moments)))
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(log_witness, "Path", lambda value: SimpleNamespace(exists=lambda: alive))
    if alive:
        with pytest.raises(ComposeError, match="did not quiesce"):
            log_witness._wait_for_proxy_exit(tmp_path, required=True)
    else:
        log_witness._wait_for_proxy_exit(tmp_path, required=True)

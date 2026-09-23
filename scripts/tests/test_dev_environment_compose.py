from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from scripts.dev_environment.compose import (
    _FILE_PRESENCE_COMMAND,
    CommandResult,
    ComposeError,
    ComposeProject,
    ProviderCommandFailed,
    ServiceAbsent,
    ServiceStatus,
)
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity
from scripts.dev_environment.secrets import ensure_instance_state


class FakeRunner:
    def __init__(
        self,
        identity: InstanceIdentity,
        *,
        foreign: bool = False,
        allocated: bool = False,
        status_output: str | None = None,
        interrupt_stream: bool = False,
        compose_version: str = "5.3.0",
        port_host: str = "127.0.0.1",
        runtime_valid: bool = True,
        failing_operation: str | None = None,
    ) -> None:
        self.identity = identity
        self.foreign = foreign
        self.allocated = allocated
        self.status_output = status_output
        self.interrupt_stream = interrupt_stream
        self.compose_version = compose_version
        self.port_host = port_host
        self.runtime_valid = runtime_valid
        self.failing_operation = failing_operation
        self.calls: list[tuple[tuple[str, ...], Path, bool, Mapping[str, str]]] = []

    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        capture: bool = True,
        env: Mapping[str, str],
        timeout_seconds: float | None,
    ) -> CommandResult:
        del timeout_seconds
        args = tuple(argv)
        self.calls.append((args, cwd, capture, env))
        if self.failing_operation is not None and self.failing_operation in args:
            return CommandResult(17)
        if not capture and self.interrupt_stream:
            raise KeyboardInterrupt
        if args == ("docker", "compose", "version", "--short"):
            return CommandResult(0, f"{self.compose_version}\n")
        if "--filter" in args and "--format" in args and args[0] == "docker":
            if self.foreign:
                return CommandResult(0, "label=foreign\n")
            if self.allocated:
                return CommandResult(0, f"label={self.identity.root_digest}\n")
            return CommandResult(0)
        if args[-3:] == ("port", "backend", "3000"):
            return CommandResult(0, f"{self.port_host}:49151\n")
        if args[-3:] == ("port", "frontend", "5173"):
            return CommandResult(0, f"{self.port_host}:49152\n")
        if args[-3:] == ("port", "postgres", "5432"):
            return CommandResult(0, f"{self.port_host}:49153\n")
        if args[-3:] in {
            ("ps", "--quiet", "backend"),
            ("ps", "--quiet", "frontend"),
        }:
            return CommandResult(0, "a" * 64 + "\n")
        if args[:3] == ("docker", "exec", "a" * 64) and args[-1] == "/proc/1/status":
            capability = "0000000000000000" if self.runtime_valid else "0000000000000001"
            return CommandResult(
                0,
                "Uid:\t65532\t65532\t65532\t65532\n"
                "Gid:\t65532\t65532\t65532\t65532\n"
                f"CapEff:\t{capability}\n"
                "NoNewPrivs:\t1\n",
            )
        if args[:3] == ("docker", "exec", "a" * 64) and "--format=%u:%g:%a:%F" in args:
            return CommandResult(0, "65532:65532:400:regular file\n")
        if args[:4] == ("docker", "inspect", "--format", "{{.State.StartedAt}}"):
            return CommandResult(0, "2026-07-18T12:34:56.123456789Z\n")
        if args[-3:] == ("ps", "--format", "json"):
            default = json.dumps(
                [
                    {
                        "ExitCode": 0,
                        "Health": "healthy",
                        "Service": "backend",
                        "State": "running",
                    }
                ]
            )
            return CommandResult(0, default if self.status_output is None else self.status_output)
        if args[-5:] == ("ps", "--all", "--no-trunc", "--format", "json"):
            default = json.dumps(
                [
                    {
                        "ExitCode": 1,
                        "Health": "",
                        "Service": "migrate",
                        "State": "exited",
                    }
                ]
            )
            return CommandResult(0, default if self.status_output is None else self.status_output)
        if args[:2] == ("docker", "compose") and "--file" in args:
            operation = args[args.index("--file") + 2 :]
            if operation in {
                ("config",),
                ("config", "--quiet"),
                ("stop", "backend"),
                ("down", "--remove-orphans"),
                ("down", "--volumes", "--remove-orphans"),
                ("logs", "--follow", "--tail", "200"),
                ("up", "--detach", "--build", "--wait", "--wait-timeout", "240"),
                ("watch", "--no-up"),
            }:
                return CommandResult(0)
        raise AssertionError(f"unexpected command: {args!r}")


@pytest.fixture
def prepared(tmp_path: Path) -> tuple[InstanceIdentity, dict[str, str]]:
    identity = derive_instance_identity(
        tmp_path,
        state_home=tmp_path.parent / f"{tmp_path.name}-user-state",
    )
    return identity, dict(ensure_instance_state(identity))


def test_up_uses_exact_project_and_discovers_dynamic_ports(
    prepared: tuple[InstanceIdentity, dict[str, str]],
) -> None:
    identity, environment = prepared
    runner = FakeRunner(identity)
    project = ComposeProject(identity, environment, runner=runner)

    endpoints = project.up()

    assert endpoints.api == "http://127.0.0.1:49151"
    assert endpoints.ui == "http://127.0.0.1:49152"
    assert endpoints.postgres == "postgresql://127.0.0.1:49153"
    compose_calls = [
        call[0] for call in runner.calls if call[0][:3] == ("docker", "compose", "--project-name")
    ]
    assert compose_calls
    assert all(
        call[2:6]
        == (
            "--project-name",
            identity.project_name,
            "--env-file",
            str(identity.environment_path),
        )
        for call in compose_calls
    )
    assert any("--wait" in call and "--build" in call for call in compose_calls)
    operations = [call[call.index("--file") + 2 :] for call in compose_calls]
    assert operations[:3] == [
        ("config", "--quiet"),
        ("stop", "backend"),
        ("up", "--detach", "--build", "--wait", "--wait-timeout", "240"),
    ]


@pytest.mark.parametrize("failed_phase", ["config", "stop"])
def test_up_does_not_cross_a_failed_admission_or_runtime_stop(
    prepared: tuple[InstanceIdentity, dict[str, str]], failed_phase: str
) -> None:
    identity, environment = prepared
    runner = FakeRunner(identity, failing_operation=failed_phase)
    project = ComposeProject(identity, environment, runner=runner)

    with pytest.raises(ComposeError, match=f"Docker Compose {failed_phase} failed with status 17"):
        project.up()

    assert not any("up" in call[0] for call in runner.calls)
    if failed_phase == "config":
        assert not any("stop" in call[0] for call in runner.calls)
    assert not any("down" in call[0] or "--volumes" in call[0] for call in runner.calls)


def test_provider_failure_names_only_the_admitted_operation(
    prepared: tuple[InstanceIdentity, dict[str, str]],
) -> None:
    identity, environment = prepared
    project = ComposeProject(
        identity,
        environment,
        runner=FakeRunner(identity, failing_operation="up"),
    )

    with pytest.raises(ComposeError, match=r"^Docker Compose up failed with status 17$"):
        project.up()


def test_compose_commands_reject_ambient_managed_environment(
    prepared: tuple[InstanceIdentity, dict[str, str]],
) -> None:
    identity, environment = prepared
    runner = FakeRunner(identity)
    project = ComposeProject(
        identity,
        environment,
        runner=runner,
        provider_environment={
            "CI_COORDINATOR_DEV_API_URL": "http://ambient.invalid",
            "COMPOSE_PROJECT_NAME": "foreign",
            "DOCKER_CONTEXT": "desktop-linux",
            "HOME": "/home/developer",
            "PATH": "/usr/bin",
        },
    )

    project.validate()

    assert runner.calls
    assert all(
        call[3]
        == {
            "DOCKER_CONTEXT": "desktop-linux",
            "HOME": "/home/developer",
            "PATH": "/usr/bin",
        }
        for call in runner.calls
    )


@pytest.mark.parametrize("compose_version", ["2.21.9", "2.23.0", "2.39.3", "invalid"])
def test_up_rejects_unsupported_or_malformed_compose_before_mutation(
    prepared: tuple[InstanceIdentity, dict[str, str]], compose_version: str
) -> None:
    identity, environment = prepared
    runner = FakeRunner(identity, compose_version=compose_version)
    project = ComposeProject(identity, environment, runner=runner)

    with pytest.raises(ComposeError, match="Docker Compose"):
        project.up()

    assert not any("up" in call[0] or "stop" in call[0] for call in runner.calls)


def test_endpoints_reject_non_loopback_provider_output(
    prepared: tuple[InstanceIdentity, dict[str, str]],
) -> None:
    identity, environment = prepared
    project = ComposeProject(
        identity,
        environment,
        runner=FakeRunner(identity, port_host="0.0.0.0"),  # noqa: S104
    )

    with pytest.raises(ComposeError, match="beyond loopback"):
        project.endpoints()


@pytest.mark.parametrize(
    ("operation", "expected_tail"),
    [
        ("down", ("down", "--remove-orphans")),
        ("reset", ("down", "--volumes", "--remove-orphans")),
    ],
)
def test_cleanup_is_scoped_to_the_derived_project(
    prepared: tuple[InstanceIdentity, dict[str, str]],
    operation: str,
    expected_tail: tuple[str, ...],
) -> None:
    identity, environment = prepared
    runner = FakeRunner(identity)
    project = ComposeProject(identity, environment, runner=runner)

    getattr(project, operation)()

    cleanup = runner.calls[-1][0]
    assert cleanup[-len(expected_tail) :] == expected_tail
    assert cleanup[cleanup.index("--project-name") + 1] == identity.project_name


def test_watch_streams_only_the_running_owned_project(
    prepared: tuple[InstanceIdentity, dict[str, str]],
) -> None:
    identity, environment = prepared
    runner = FakeRunner(identity)
    project = ComposeProject(identity, environment, runner=runner)

    project.watch()

    watch_call = runner.calls[-1]
    assert watch_call[0][-2:] == ("watch", "--no-up")
    assert watch_call[0][watch_call[0].index("--project-name") + 1] == identity.project_name
    assert watch_call[2] is False


@pytest.mark.parametrize("operation", ["logs", "watch"])
def test_user_interrupt_is_a_clean_stream_stop(
    prepared: tuple[InstanceIdentity, dict[str, str]], operation: str
) -> None:
    identity, environment = prepared
    project = ComposeProject(
        identity,
        environment,
        runner=FakeRunner(identity, interrupt_stream=True),
    )

    getattr(project, operation)()


def test_foreign_resource_blocks_compose_before_mutation(
    prepared: tuple[InstanceIdentity, dict[str, str]],
) -> None:
    identity, environment = prepared
    runner = FakeRunner(identity, foreign=True)
    project = ComposeProject(identity, environment, runner=runner)

    with pytest.raises(ComposeError, match="foreign container"):
        project.up()

    assert not any("up" in call[0] or "stop" in call[0] for call in runner.calls)


@pytest.mark.parametrize("operation", ["endpoints", "logs", "watch"])
def test_foreign_resource_blocks_observation_before_provider_access(
    prepared: tuple[InstanceIdentity, dict[str, str]], operation: str
) -> None:
    identity, environment = prepared
    runner = FakeRunner(identity, foreign=True)
    project = ComposeProject(identity, environment, runner=runner)

    with pytest.raises(ComposeError, match="foreign container"):
        getattr(project, operation)()

    assert runner.calls
    assert all("--filter" in call[0] for call in runner.calls)


def test_ci_allocation_guard_rejects_an_existing_owned_project(
    prepared: tuple[InstanceIdentity, dict[str, str]],
) -> None:
    identity, environment = prepared
    project = ComposeProject(
        identity,
        environment,
        runner=FakeRunner(identity, allocated=True),
    )

    with pytest.raises(ComposeError, match="already allocated"):
        project.assert_unallocated()


def test_status_json_is_structured(
    prepared: tuple[InstanceIdentity, dict[str, str]],
) -> None:
    identity, environment = prepared
    project = ComposeProject(identity, environment, runner=FakeRunner(identity))

    assert project.status() == (ServiceStatus("backend", "running", "healthy", 0),)


def test_diagnostic_status_includes_stopped_services(
    prepared: tuple[InstanceIdentity, dict[str, str]],
) -> None:
    identity, environment = prepared
    project = ComposeProject(identity, environment, runner=FakeRunner(identity))

    assert project.diagnostic_status() == (ServiceStatus("migrate", "exited", "", 1),)


def test_diagnostic_identity_matches_quiet_identity_under_compose_truncation(
    prepared: tuple[InstanceIdentity, dict[str, str]],
) -> None:
    identity, environment = prepared
    fallback = FakeRunner(identity)

    def runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        capture: bool = True,
        env: Mapping[str, str],
        timeout_seconds: float | None,
    ) -> CommandResult:
        # Both pinned Compose formatters truncate JSON ID() unless no-trunc is set;
        # ps --quiet returns the full ID. Model that boundary, not identical fixtures.
        if tuple(argv[:2]) == ("docker", "compose") and "ps" in argv and "--all" in argv:
            container_id = "a" * 64 if "--no-trunc" in argv else "a" * 12
            return CommandResult(
                0,
                json.dumps(
                    [
                        {
                            "Service": "backend",
                            "State": "running",
                            "Health": "healthy",
                            "ExitCode": 0,
                            "ID": container_id,
                        }
                    ]
                ),
            )
        return fallback(argv, cwd=cwd, capture=capture, env=env, timeout_seconds=timeout_seconds)

    project = ComposeProject(identity, environment, runner=runner)
    (backend,) = project.diagnostic_status()
    runtime = project.service_runtime_identity("backend")

    assert backend == ServiceStatus("backend", "running", "healthy", 0, runtime.container_id)
    assert backend.container_id == "a" * 64


def test_runtime_boundary_admits_exact_pid_and_tmpfs_secret_identity(
    prepared: tuple[InstanceIdentity, dict[str, str]],
) -> None:
    identity, environment = prepared
    project = ComposeProject(identity, environment, runner=FakeRunner(identity))

    project.assert_runtime_boundary(
        "backend",
        expected_uid=65_532,
        protected_path="/run/ci-coordinator-secrets/runtime-dsn",
    )


def test_runtime_boundary_rejects_residual_capability(
    prepared: tuple[InstanceIdentity, dict[str, str]],
) -> None:
    identity, environment = prepared
    project = ComposeProject(
        identity,
        environment,
        runner=FakeRunner(identity, runtime_valid=False),
    )

    with pytest.raises(ComposeError, match="privilege boundary"):
        project.assert_runtime_boundary(
            "backend",
            expected_uid=65_532,
            protected_path="/run/ci-coordinator-secrets/runtime-dsn",
        )


def test_service_runtime_identity_uses_container_and_start_timestamp(
    prepared: tuple[InstanceIdentity, dict[str, str]],
) -> None:
    identity, environment = prepared
    project = ComposeProject(identity, environment, runner=FakeRunner(identity))

    runtime = project.service_runtime_identity("backend")

    assert runtime.container_id == "a" * 64
    assert runtime.started_at == "2026-07-18T12:34:56.123456789Z"


@pytest.mark.parametrize(
    ("output", "shape"),
    [
        ("\n", "empty"),
        ("a" * 64 + "\n" + "b" * 64 + "\n", "multiple"),
        ("a" * 11, "noncanonical"),
        ("a" * 65, "noncanonical"),
        ("provider-private-output", "noncanonical"),
    ],
)
def test_invalid_runtime_identity_reports_only_bounded_observation_shape(
    prepared: tuple[InstanceIdentity, dict[str, str]], output: str, shape: str
) -> None:
    identity, environment = prepared
    calls: list[tuple[str, ...]] = []

    def runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        capture: bool = True,
        env: Mapping[str, str],
        timeout_seconds: float | None,
    ) -> CommandResult:
        del cwd, capture, env, timeout_seconds
        calls.append(tuple(argv))
        return CommandResult(0, output)

    project = ComposeProject(identity, environment, runner=runner)
    with pytest.raises(ComposeError) as error:
        project.service_runtime_identity("backend")

    assert str(error.value) == (
        f"Compose returned an invalid container identity (service=backend, shape={shape})"
    )
    assert isinstance(error.value, ServiceAbsent) == (shape == "empty")
    assert len(calls) == 1
    assert calls[0][-3:] == ("ps", "--quiet", "backend")


def test_failed_empty_ps_is_not_a_pending_service(
    prepared: tuple[InstanceIdentity, dict[str, str]],
) -> None:
    identity, environment = prepared
    runner = FakeRunner(identity, failing_operation="ps")
    project = ComposeProject(identity, environment, runner=runner)
    with pytest.raises(ComposeError, match="ps failed with status 17") as caught:
        project.service_runtime_identity("frontend")
    assert not isinstance(caught.value, ServiceAbsent)
    assert len(runner.calls) == 1


@pytest.mark.parametrize(
    ("status", "output", "expected"),
    [
        (0, "present\n", b"content"),
        (0, "absent\n", None),
        (1, "absent\n", "failed"),
        (17, "present\n", "failed"),
        (0, "", "malformed"),
        (0, "absent", "malformed"),
    ],
)
def test_file_observation_requires_success_and_an_exact_presence_marker(
    prepared: tuple[InstanceIdentity, dict[str, str]],
    status: int,
    output: str,
    expected: bytes | str | None,
) -> None:
    identity, environment = prepared
    calls: list[tuple[str, ...]] = []

    def runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        capture: bool = True,
        env: Mapping[str, str],
        timeout_seconds: float | None,
    ) -> CommandResult:
        del cwd, capture, env
        assert timeout_seconds is not None and 0 < timeout_seconds <= 30
        args = tuple(argv)
        calls.append(args)
        if args[-3:] == ("ps", "--quiet", "frontend"):
            return CommandResult(0, "a" * 64)
        if args[3:5] == ("sh", "-c"):
            assert args[6:] == ("file-presence", "/workspace/source name", "/workspace")
            return CommandResult(status, output)
        assert args[3:] == ("cat", "--", "/workspace/source name")
        return CommandResult(0, "content")

    project = ComposeProject(identity, environment, runner=runner)
    if isinstance(expected, str):
        with pytest.raises(ProviderCommandFailed if expected == "failed" else ComposeError):
            project.service_file("frontend", "/workspace/source name")
    else:
        assert project.service_file("frontend", "/workspace/source name") == expected
    assert len(calls) == (3 if expected == b"content" else 2)


@pytest.mark.parametrize("kind", ["file", "missing", "directory", "missing-parent", "broken-link"])
def test_presence_probe_distinguishes_absence_from_invalid_entries(
    tmp_path: Path, kind: str
) -> None:
    path = tmp_path / "source $name"
    if kind == "file":
        path.write_text("content")
    elif kind == "directory":
        path.mkdir()
    elif kind == "missing-parent":
        path = path / "child"
    elif kind == "broken-link":
        path.symlink_to(tmp_path / "missing-target")
    result = subprocess.run(
        ["sh", "-c", _FILE_PRESENCE_COMMAND, "file-presence", str(path), str(path.parent)],
        capture_output=True,
        timeout=5,
        check=False,
    )
    expected = (
        (0, b"present\n") if kind == "file" else (0, b"absent\n") if kind == "missing" else (2, b"")
    )
    assert (result.returncode, result.stdout) == expected
    assert result.stderr == b""


def test_absent_container_is_not_an_absent_file(
    prepared: tuple[InstanceIdentity, dict[str, str]],
) -> None:
    identity, environment = prepared
    calls: list[tuple[str, ...]] = []

    def runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        capture: bool = True,
        env: Mapping[str, str],
        timeout_seconds: float | None,
    ) -> CommandResult:
        del cwd, capture, env, timeout_seconds
        calls.append(tuple(argv))
        return CommandResult(0, "\n")

    project = ComposeProject(identity, environment, runner=runner)
    with pytest.raises(ServiceAbsent):
        project.service_file("frontend", "/workspace/frontend/src/probe")
    assert len(calls) == 1 and calls[0][-3:] == ("ps", "--quiet", "frontend")


@pytest.mark.parametrize(
    "status_output",
    [
        json.dumps(
            [
                {
                    "ExitCode": 0,
                    "Health": "healthy",
                    "Service": "postgres",
                    "State": "running",
                },
                {"ExitCode": 0, "Health": "", "Service": "backend", "State": "running"},
            ]
        ),
        "\n".join(
            [
                json.dumps(
                    {
                        "ExitCode": 0,
                        "Health": "healthy",
                        "Service": "postgres",
                        "State": "running",
                    }
                ),
                json.dumps(
                    {
                        "ExitCode": 0,
                        "Health": "",
                        "Service": "backend",
                        "State": "running",
                    }
                ),
            ]
        ),
    ],
)
def test_status_accepts_array_and_ndjson_provider_shapes(
    prepared: tuple[InstanceIdentity, dict[str, str]], status_output: str
) -> None:
    identity, environment = prepared
    project = ComposeProject(
        identity,
        environment,
        runner=FakeRunner(identity, status_output=status_output),
    )

    assert project.status() == (
        ServiceStatus("backend", "running", "", 0),
        ServiceStatus("postgres", "running", "healthy", 0),
    )


@pytest.mark.parametrize(
    "status_output",
    [
        "not-json",
        json.dumps(["not-an-object"]),
        json.dumps(
            [
                {"ExitCode": 0, "Health": "", "Service": "backend", "State": "running"},
                {"ExitCode": 0, "Health": "", "Service": "backend", "State": "running"},
            ]
        ),
        json.dumps(
            [
                {
                    "ExitCode": 0,
                    "Health": "",
                    "Service": f"service-{index}",
                    "State": "running",
                }
                for index in range(17)
            ]
        ),
        json.dumps([{"ExitCode": True, "Health": "", "Service": "backend", "State": "running"}]),
    ],
)
def test_status_rejects_malformed_or_unbounded_provider_output(
    prepared: tuple[InstanceIdentity, dict[str, str]], status_output: str
) -> None:
    identity, environment = prepared
    project = ComposeProject(
        identity,
        environment,
        runner=FakeRunner(identity, status_output=status_output),
    )

    with pytest.raises(ComposeError):
        project.status()


def test_command_double_rejects_unmodelled_commands(
    prepared: tuple[InstanceIdentity, dict[str, str]],
) -> None:
    identity, _environment = prepared
    runner = FakeRunner(identity)

    with pytest.raises(AssertionError, match="unexpected command"):
        runner(
            ("docker", "unexpected"),
            cwd=identity.repo_root,
            env={},
            timeout_seconds=1,
        )

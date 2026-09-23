from __future__ import annotations

import io
import json
import os
import sys
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import cast

import pytest
from scripts.bounded_process import InteractiveResult, spawn
from scripts.dev_environment import cli, doctor, logs, opening
from scripts.dev_environment.compose import (
    CommandResult,
    ComposeError,
    ComposeProject,
    ProviderInvocation,
)
from scripts.dev_environment.diagnostics import Reason
from scripts.dev_environment.environment import EnvironmentError, dependency_lease
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity


class ObserverRunner:
    def __init__(
        self,
        identity: InstanceIdentity,
        *,
        foreign: bool = False,
        replacing: bool = False,
    ) -> None:
        self.identity = identity
        self.foreign = foreign
        self.replacing = replacing
        self.calls: list[tuple[str, ...]] = []
        self.identities = 0

    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        capture: bool = True,
        env: Mapping[str, str],
        timeout_seconds: float | None,
    ) -> CommandResult:
        args = tuple(argv)
        self.calls.append(args)
        if args[-5:] == ("ps", "--all", "--no-trunc", "--format", "json"):
            return CommandResult(
                0,
                json.dumps(
                    [
                        {
                            "ID": "a" * 64,
                            "Service": "backend",
                            "State": "running",
                            "Health": "healthy",
                            "ExitCode": 0,
                        },
                        {
                            "ID": "b" * 64,
                            "Service": "migrate",
                            "State": "exited",
                            "Health": "",
                            "ExitCode": 0,
                        },
                    ]
                ),
            )
        if args[:3] == ("docker", "inspect", "--format"):
            root = "f" * 64 if self.foreign else self.identity.root_digest
            return CommandResult(0, f"{self.identity.project_name}|{root}|2026-09-08T12:00:00Z\n")
        if args[-3:] == ("ps", "--quiet", "frontend"):
            self.identities += 1
            return CommandResult(
                0, ("b" if self.replacing and self.identities > 1 else "a") * 64 + "\n"
            )
        if args[-3:] == ("port", "frontend", "5173"):
            return CommandResult(0, "127.0.0.1:41002\n")
        return CommandResult(0)


@pytest.fixture
def identity(tmp_path: Path) -> InstanceIdentity:
    return derive_instance_identity(tmp_path, state_home=tmp_path.parent / f"{tmp_path.name}-state")


def project_for(identity: InstanceIdentity, runner: ObserverRunner) -> ComposeProject:
    return ComposeProject(
        identity,
        {"CI_COORDINATOR_DEV_ROOT_DIGEST": identity.root_digest},
        runner=runner,
    )


def test_logs_use_owned_snapshot_ids_and_only_follow_running_services(
    identity: InstanceIdentity,
) -> None:
    runner = ObserverRunner(identity)
    invocations = project_for(identity, runner).log_invocations(tail=17)
    assert {item.argv for item in invocations} == {
        ("docker", "logs", "--tail", "17", "--follow", "a" * 64),
        ("docker", "logs", "--tail", "17", "b" * 64),
    }
    assert sum(call[:3] == ("docker", "inspect", "--format") for call in runner.calls) == 2


def test_logs_reject_foreign_container_labels_before_streaming(
    identity: InstanceIdentity,
) -> None:
    runner = ObserverRunner(identity, foreign=True)
    with pytest.raises(ComposeError) as caught:
        project_for(identity, runner).log_invocations()
    assert caught.value.reason == Reason.FOREIGN_STATE
    assert all(call[:2] != ("docker", "logs") for call in runner.calls)


@pytest.mark.parametrize("selection,tail", [(("foreign",), 10), ((), -1), ((), 10001)])
def test_log_selection_is_admitted_before_any_docker_query(
    identity: InstanceIdentity, selection: tuple[str, ...], tail: int
) -> None:
    runner = ObserverRunner(identity)
    with pytest.raises(ComposeError) as caught:
        project_for(identity, runner).log_invocations(services=selection, tail=tail)
    assert caught.value.reason == Reason.INVALID_ARGUMENT
    assert runner.calls == []


def test_open_rejects_endpoint_when_container_changes(
    identity: InstanceIdentity,
) -> None:
    runner = ObserverRunner(identity, replacing=True)
    with pytest.raises(ComposeError) as caught:
        project_for(identity, runner).admitted_ui_endpoint()
    assert caught.value.reason == Reason.STALE_OBSERVATION


def test_headless_open_prints_the_admitted_endpoint(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = project_for(identity, ObserverRunner(identity))
    monkeypatch.setattr(cli, "_project", lambda *_args, **_kwargs: project)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(cli, "open_loopback_ui", lambda _url: False)
    out, error = io.StringIO(), io.StringIO()
    assert (
        cli.run(
            ["open"],
            repo_root=identity.repo_root,
            state_home=identity.state_home,
            stdout=out,
            stderr=error,
        )
        == 0
    )
    assert json.loads(out.getvalue()) == {
        "projectName": identity.project_name,
        "state": "manual",
        "endpoints": {"ui": "http://127.0.0.1:41002"},
    }
    assert error.getvalue() == ""


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:1234",
        "http://example.test:1234",
        "http://user:password@127.0.0.1:1234",
        "http://127.0.0.1:1234/?token=secret",
        "file:///etc/passwd",
    ],
)
def test_opener_never_receives_unadmitted_urls(url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    def reject(*_args: object, **_kwargs: object) -> None:
        pytest.fail("unadmitted URL reached OS opener")

    monkeypatch.setattr("scripts.dev_environment.opening.subprocess.run", reject)
    with pytest.raises(ComposeError):
        opening.open_loopback_ui(url)


def test_ending_follow_stream_cancels_other_owned_streams(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    other_started, other_stopped = threading.Event(), threading.Event()

    def provider(command: str, args: Sequence[str], **kwargs: object) -> InteractiveResult:
        assert command == sys.executable
        assert tuple(args[:2]) == ("-m", "scripts.dev_environment.logs")
        descriptors = cast("tuple[int, ...]", kwargs["inherited_fds"])
        assert len(descriptors) == 1 and os.fstat(descriptors[0]).st_ino > 0
        assert kwargs["timeout_seconds"] is None
        with pytest.raises(EnvironmentError), dependency_lease(identity, exclusive=True):
            pytest.fail("a log worker did not retain environment ownership")
        if args[-1] == "a" * 64:
            assert other_started.wait(2)
            return InteractiveResult(0, True)
        other_started.set()
        stop = cast("Callable[[], bool]", kwargs["stop_requested"])
        deadline = threading.Event()
        for _ in range(200):
            if stop():
                other_stopped.set()
                return InteractiveResult(0, True)
            deadline.wait(0.01)
        pytest.fail("peer stream did not receive bounded stop")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(logs, "run_interactive", provider)
    invocations = [
        ProviderInvocation(
            ("docker", "logs", "--tail", "200", "--follow", value * 64), identity.repo_root, {}
        )
        for value in ("a", "b")
    ]
    with pytest.raises(ComposeError) as caught:
        logs.stream_logs(invocations, identity=identity)
    assert caught.value.reason == Reason.STALE_OBSERVATION
    assert other_stopped.is_set()


def test_log_environment_preparation_blocks_python_workers_before_spawn(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        logs, "run_interactive", lambda *_args, **_kwargs: pytest.fail("worker started")
    )
    invocation = ProviderInvocation(
        ("docker", "logs", "--tail", "0", "a" * 64), identity.repo_root, {}
    )
    with dependency_lease(identity, exclusive=True), pytest.raises(ComposeError) as caught:
        logs.stream_logs((invocation,), identity=identity)
    assert caught.value.reason == Reason.PREPARATION_IN_PROGRESS


def test_log_worker_uses_finite_deadline_and_releases_its_environment_after_completion(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    def provider(_command: str, _args: Sequence[str], **options: object) -> InteractiveResult:
        assert options["timeout_seconds"] == 30
        assert options["inherited_fds"]
        with pytest.raises(EnvironmentError), dependency_lease(identity, exclusive=True):
            pytest.fail("finite log reader did not hold environment ownership")
        return InteractiveResult(0, True)

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(logs, "run_interactive", provider)
    invocation = ProviderInvocation(
        ("docker", "logs", "--tail", "0", "a" * 64), identity.repo_root, {}
    )
    logs.stream_logs((invocation,), identity=identity)
    with dependency_lease(identity, exclusive=True):
        pass


@pytest.mark.parametrize("follow", [False, True])
def test_log_reader_keeps_both_application_channels(
    follow: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    def read(container: str, **options: object) -> None:
        assert container == "a" * 64
        assert options["follow"] is follow and options["tail"] == 17
        assert options["environment"] is os.environ and options["cwd"] == Path.cwd()
        cast("io.BytesIO", options["output"]).write(b"application-out\napplication-err\n")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(logs, "read_logs", read)
    output = io.BytesIO()
    logs._read_logs("a" * 64, tail=17, follow=follow, output=output)
    assert output.getvalue() == b"application-out\napplication-err\n"


def test_sdk_provider_failure_has_no_raw_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def unavailable(*_args: object, **_kwargs: object) -> None:
        raise OSError("https://private-user:private-password@provider.invalid")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(logs, "read_logs", unavailable)
    assert logs.main(["--tail", "1", "a" * 64]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


def test_doctor_imports_without_site_packages_or_backend_environment() -> None:
    root = Path(__file__).resolve().parents[2]
    result = spawn(
        sys.executable,
        (
            "-S",
            "-c",
            "import sys; import scripts.dev_environment.doctor; "
            "assert 'cryptography' not in sys.modules; "
            "assert 'ci_coordinator' not in sys.modules",
        ),
        cwd=root,
        max_buffer=65536,
        timeout_seconds=10,
    )
    assert result.status == 0, result.stderr


def test_doctor_reports_missing_dependencies_without_installing(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    (identity.repo_root / "mise.toml").write_text(
        '[tools]\npython="' + ".".join(str(item) for item in sys.version_info[:3]) + '"\n'
    )
    monkeypatch.setattr(ComposeProject, "provider_preflight", lambda _self: None)
    out = io.StringIO()
    assert (
        doctor.run([], repo_root=identity.repo_root, state_home=identity.state_home, stdout=out)
        == 2
    )
    checks = {item["name"]: item for item in json.loads(out.getvalue())["checks"]}
    assert checks["backend_dependencies"]["diagnostic"]["reason"] == "dependencies_missing"
    assert checks["instance"]["diagnostic"]["reason"] == "missing_state"
    assert not (identity.repo_root / "backend/.venv").exists()
    assert not identity.state_directory.exists()

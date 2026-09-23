from __future__ import annotations

import hashlib
import json
import shutil
import threading
import tomllib
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from ruamel.yaml import YAML
from scripts.bounded_process import CommandResult, InteractiveResult, spawn
from scripts.dev_environment import watch_session, watch_witness
from scripts.dev_environment.compose import (
    CommandResult as ComposeCommandResult,
)
from scripts.dev_environment.compose import (
    ComposeError,
    ComposeProject,
    LocalEndpoints,
    ProviderInvocation,
    ServiceAbsent,
    ServiceRuntimeIdentity,
    ServiceStatus,
)
from scripts.dev_environment.diagnostics import Reason
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity
from scripts.dev_environment.lifecycle import (
    OperationBlocked,
    instance_operation_lock,
    operation_paths,
)
from scripts.dev_environment.private_files import atomic_write_private_text
from scripts.dev_environment.watch_witness import verify_source_watch

from ci_coordinator.runtime_settings import (
    DisabledRuntimeSettings,
    RuntimeSettingsRejection,
    admit_runtime_settings,
)


class FakeProcess:
    def __init__(self, *, exited: bool = False) -> None:
        self.exited = exited
        self.stopped = False

    def poll(self) -> int | None:
        return 1 if self.exited else None

    def stop(self) -> None:
        self.stopped = True


class FakeProject:
    def __init__(
        self,
        repo_root: Path,
        *,
        runtime_delay: int = 0,
        transient_failures: int = 0,
    ) -> None:
        self.repo_root = repo_root
        self.runtime_delay = runtime_delay
        self.runtime_observations = 0
        self.transient_failures = transient_failures
        self.source_snapshot: bytes | None = None
        self.pending_observations = 0
        self.runtime_revision = 0

    def watch_invocation(self) -> ProviderInvocation:
        return ProviderInvocation(("docker", "compose", "watch"), self.repo_root, {"PATH": "/bin"})

    def endpoints(self) -> LocalEndpoints:
        return LocalEndpoints("http://127.0.0.1:3000", "http://127.0.0.1:5173", "127.0.0.1:5432")

    def diagnostic_status(self) -> tuple[ServiceStatus, ...]:
        return tuple(
            ServiceStatus(service, "running", "healthy", 0)
            for service in ("backend", "postgres", "frontend")
        )

    def assert_runtime_boundary(
        self, service: str, *, expected_uid: int, protected_path: str | None
    ) -> None:
        assert service == "backend" and expected_uid == 65_532
        assert protected_path == "/run/ci-coordinator-secrets/runtime-dsn"

    @contextmanager
    def observation_budget(self, seconds: float = 30) -> Iterator[None]:
        assert 0 < seconds <= 30
        yield

    def service_runtime_identity(self, service: str) -> ServiceRuntimeIdentity:
        if service == "frontend":
            return ServiceRuntimeIdentity(container_id="f" * 64, started_at="frontend-start")
        assert service == "backend"
        self.runtime_observations += 1
        source = self.repo_root / "backend/src/ci-coordinator-watch-probe.txt"
        snapshot = source.read_bytes() if source.exists() else None
        if snapshot != self.source_snapshot:
            self.pending_observations += 1
            if self.pending_observations > self.runtime_delay:
                self.source_snapshot = snapshot
                self.runtime_revision += 1
                self.pending_observations = 0
        return ServiceRuntimeIdentity(
            container_id="a" * 64,
            started_at=f"2026-07-18T12:34:{50 + self.runtime_revision}Z",
        )

    def service_file(self, service: str, path: str) -> bytes | None:
        assert service in {"backend", "frontend"}
        if self.transient_failures:
            self.transient_failures -= 1
            raise ServiceAbsent("service absent during restart")
        source = self.repo_root / service / "src" / Path(path).name
        return source.read_bytes() if source.exists() else None


def test_watch_witness_proves_reversible_sync_and_backend_restart(
    tmp_path: Path,
) -> None:
    for service in ("backend", "frontend"):
        (tmp_path / service / "src").mkdir(parents=True)
    process = FakeProcess()

    verify_source_watch(
        FakeProject(tmp_path),
        process_factory=lambda _invocation: process,
        wait=lambda _seconds: None,
    )

    assert process.stopped is True
    assert not tuple(tmp_path.rglob("*watch-probe*"))


def test_watch_witness_tolerates_bounded_restart_unavailability(
    tmp_path: Path,
) -> None:
    for service in ("backend", "frontend"):
        (tmp_path / service / "src").mkdir(parents=True)
    process = FakeProcess()

    verify_source_watch(
        FakeProject(tmp_path, transient_failures=1),
        process_factory=lambda _invocation: process,
        wait=lambda _seconds: None,
    )

    assert process.stopped is True


def test_watch_witness_waits_for_delayed_runtime_restart(tmp_path: Path) -> None:
    for service in ("backend", "frontend"):
        (tmp_path / service / "src").mkdir(parents=True)
    process = FakeProcess()
    project = FakeProject(tmp_path, runtime_delay=2)

    verify_source_watch(
        project,
        process_factory=lambda _invocation: process,
        wait=lambda _seconds: None,
    )

    assert project.runtime_revision == 3
    assert project.runtime_observations > 6
    assert process.stopped is True


@pytest.mark.parametrize("recreate", [False, True])
def test_runtime_effect_returns_the_successful_identity_without_an_unbounded_resample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recreate: bool
) -> None:
    project = FakeProject(tmp_path)
    previous = ServiceRuntimeIdentity("a" * 64, "old")
    installed = ServiceRuntimeIdentity(("b" if recreate else "a") * 64, "new")
    now = [0.0]
    effects: list[int] = []
    reads: list[str] = []

    def runtime(service: str) -> ServiceRuntimeIdentity:
        reads.append(service)
        if len(reads) == 1:
            raise ServiceAbsent(
                "Compose returned an invalid container identity (service=backend, shape=empty)"
            )
        assert len(reads) == 2, "an additional sample can hit the next watch batch's removal"
        return installed

    def effect() -> bool:
        effects.append(len(effects))
        return len(effects) != 2

    monkeypatch.setattr(project, "service_runtime_identity", runtime)
    monkeypatch.setattr("scripts.dev_environment.watch_witness.time.monotonic", lambda: now[0])
    monkeypatch.setattr(
        "scripts.dev_environment.watch_witness.time.sleep",
        lambda delay: now.__setitem__(0, now[0] + delay),
    )
    result = watch_witness._await_runtime_effect(
        project,
        effect,
        before={"backend": previous},
        process=FakeProcess(),
        recreate=recreate,
        timeout_seconds=1,
    )
    assert result == {"backend": installed}
    assert len(effects) == 3 and reads == ["backend", "backend"]
    assert now[0] == pytest.approx(0.4)


@pytest.mark.parametrize("failure", ["absent", "no-restart", "no-recreate", "wrong-recreate"])
def test_runtime_effect_keeps_the_deadline_and_restart_recreate_distinction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    project = FakeProject(tmp_path)
    previous = ServiceRuntimeIdentity("a" * 64, "old")
    now = [0.0]
    reads: list[str] = []

    def runtime(service: str) -> ServiceRuntimeIdentity:
        reads.append(service)
        if failure == "absent":
            raise ServiceAbsent("service absent during restart")
        if failure == "wrong-recreate":
            return ServiceRuntimeIdentity("b" * 64, "new")
        return previous

    monkeypatch.setattr(project, "service_runtime_identity", runtime)
    monkeypatch.setattr("scripts.dev_environment.watch_witness.time.monotonic", lambda: now[0])
    monkeypatch.setattr(
        "scripts.dev_environment.watch_witness.time.sleep", lambda _: now.__setitem__(0, 1.0)
    )
    message = "unexpectedly recreated" if failure == "wrong-recreate" else "deadline exceeded"
    with pytest.raises(watch_witness.WatchError, match=message):
        watch_witness._await_runtime_effect(
            project,
            lambda: True,
            before={"backend": previous},
            process=FakeProcess(),
            recreate=failure == "no-recreate",
            timeout_seconds=1,
        )
    assert reads == ["backend"]
    assert now[0] == (0 if failure == "wrong-recreate" else 1)


def test_recreation_effect_cannot_combine_identities_from_different_rounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = FakeProject(tmp_path)
    before = {
        service: ServiceRuntimeIdentity(service, "old") for service in ("backend", "frontend")
    }
    installed = {service: ServiceRuntimeIdentity(service + "-new", "new") for service in before}
    rounds = [0]

    def effect() -> bool:
        rounds[0] += 1
        return True

    def runtime(service: str) -> ServiceRuntimeIdentity:
        if rounds[0] == 1 and service == "frontend":
            raise ServiceAbsent("service absent during recreation")
        if rounds[0] == 2 and service == "backend":
            return before[service]
        assert rounds[0] <= 3
        return installed[service]

    monkeypatch.setattr(project, "service_runtime_identity", runtime)
    monkeypatch.setattr("scripts.dev_environment.watch_witness.time.sleep", lambda _: None)
    result = watch_witness._await_runtime_effect(
        project, effect, before=before, process=FakeProcess(), recreate=True
    )
    assert result == installed and rounds[0] == 3


@pytest.mark.parametrize(
    "outcome", ["gap", "command-gap", "missing", "malformed", "multiple", "provider"]
)
def test_rebuild_baseline_observes_typed_absence_through_the_compose_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    source = tmp_path / "repository"
    source.mkdir()
    identity = derive_instance_identity(source, state_home=tmp_path / "state")
    now = [0.0]
    rounds = [0]
    calls: list[tuple[str, ...]] = []
    budgets: list[float] = []

    def runner(
        argv: Sequence[str],
        *,
        cwd: Path,
        capture: bool = True,
        env: Mapping[str, str],
        timeout_seconds: float | None,
    ) -> ComposeCommandResult:
        del cwd, capture, env
        assert timeout_seconds is not None and 0 < timeout_seconds <= 30 - now[0]
        budgets.append(timeout_seconds)
        calls.append(tuple(argv))
        if tuple(argv[-3:]) == ("ps", "--quiet", "backend"):
            rounds[0] += 1
            return ComposeCommandResult(0, ("a" if rounds[0] == 1 else "b") * 64)
        if tuple(argv[-3:]) == ("ps", "--quiet", "frontend"):
            if outcome in {"gap", "command-gap"} and rounds[0] > 1:
                return ComposeCommandResult(0, "f" * 64)
            return ComposeCommandResult(
                17 if outcome in {"provider", "command-gap"} else 0,
                "private-invalid-output"
                if outcome == "malformed"
                else ("c" * 64 + "\n" + "d" * 64)
                if outcome == "multiple"
                else "\n",
            )
        assert tuple(argv[:4]) == ("docker", "inspect", "--format", "{{.State.StartedAt}}")
        return ComposeCommandResult(0, "2026-09-13T15:17:51Z")

    project = ComposeProject(
        identity,
        {"CI_COORDINATOR_DEV_ROOT_DIGEST": identity.root_digest},
        runner=runner,
        provider_environment={},
    )
    monkeypatch.setattr("scripts.dev_environment.watch_witness.time.monotonic", lambda: now[0])
    monkeypatch.setattr(
        "scripts.dev_environment.watch_witness.time.sleep",
        lambda _: now.__setitem__(0, 30 if outcome in {"missing", "provider"} else 1),
    )
    if outcome in {"gap", "command-gap"}:
        result = watch_witness._await_rebuild_baseline(project, FakeProcess())
        assert {service: value.container_id for service, value in result.items()} == {
            "backend": "b" * 64,
            "frontend": "f" * 64,
        }
        assert rounds[0] == 2 and now[0] == 1
        assert budgets == [30, 30, 30, 29, 29, 29, 29]
    elif outcome in {"missing", "provider"}:
        with pytest.raises(watch_witness.WatchError, match="deadline exceeded"):
            watch_witness._await_rebuild_baseline(project, FakeProcess())
        assert rounds[0] == 1 and now[0] == 30
    else:
        with pytest.raises(ComposeError) as caught:
            watch_witness._await_rebuild_baseline(project, FakeProcess())
        assert not isinstance(caught.value, ServiceAbsent)
        assert "private-invalid-output" not in str(caught.value)
        assert rounds[0] == 1 and now[0] == 0
    assert len(calls) == (7 if outcome in {"gap", "command-gap"} else 3)


@pytest.mark.parametrize("owner", ["effect", "projection", "restart", "baseline", "image"])
@pytest.mark.parametrize(
    "reason",
    [
        Reason.INVALID_PROVIDER_RESPONSE,
        Reason.FOREIGN_STATE,
        Reason.PROVIDER_UNAVAILABLE,
        Reason.PROVIDER_TIMEOUT,
    ],
)
def test_watch_transition_never_retries_a_non_absence_compose_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner: str, reason: Reason
) -> None:
    project = FakeProject(tmp_path)
    failure = ComposeError("bounded provider failure", reason=reason)
    previous = ServiceRuntimeIdentity("a" * 64, "old")
    calls: list[str] = []

    def runtime(service: str) -> ServiceRuntimeIdentity:
        calls.append(service)
        raise failure

    def content(service: str, _path: str) -> bytes | None:
        calls.append(service)
        raise failure

    monkeypatch.setattr(project, "service_runtime_identity", runtime)
    monkeypatch.setattr(project, "service_file", content)
    monkeypatch.setattr(watch_witness, "_health_status", lambda _: "alive")
    monkeypatch.setattr(watch_witness, "_http", lambda *_: (200, b"", ""))
    monkeypatch.setattr(
        "scripts.dev_environment.watch_witness.time.sleep",
        lambda _: pytest.fail("provider error retried"),
    )
    with pytest.raises(ComposeError) as caught:
        if owner == "effect":
            watch_witness._await_runtime_effect(
                project, lambda: True, before={"backend": previous}, process=FakeProcess()
            )
        elif owner == "projection":
            watch_witness._await_projection(
                project,
                (("backend", tmp_path / "probe", "/probe"),),
                None,
                process=FakeProcess(),
                wait=lambda _: pytest.fail("provider error retried"),
            )
        elif owner == "restart":
            watch_witness._await_runtime_transition(
                project,
                previous,
                process=FakeProcess(),
                wait=lambda _: pytest.fail("provider error retried"),
            )
        elif owner == "baseline":
            watch_witness._await_rebuild_baseline(project, FakeProcess())
        else:
            watch_witness._await_image_transition(
                project,
                FakeProcess(),
                before={"backend": previous, "frontend": previous},
                tokens=None,
            )
    assert caught.value is failure and calls == ["backend"]


def test_absent_service_does_not_acknowledge_probe_deletion(tmp_path: Path) -> None:
    project = FakeProject(tmp_path, transient_failures=1)
    probes = (("backend", tmp_path / "probe", "/probe"),)
    assert watch_witness._projected(project, probes, None) is False
    assert watch_witness._projected(project, probes, None) is True


def test_build_context_waits_for_phase_entry_and_recreation_gaps_before_accepting_effects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = "ci-coordinator-build-context-probe.txt"
    ignore = tmp_path / ".dockerignore"
    ignore.write_bytes(b"# original\n")
    for service in ("backend", "frontend"):
        (tmp_path / service / "src").mkdir(parents=True)
    gaps: list[str] = []
    restored = [False]
    now = [0.0]

    class BuildContextProject(FakeProject):
        def service_runtime_identity(self, service: str) -> ServiceRuntimeIdentity:
            excluded = name.encode() in ignore.read_bytes()
            if excluded:
                restored[0] = True
            phase = "excluded" if excluded else "restored" if restored[0] else "baseline"
            if service == "frontend" and phase not in gaps:
                gaps.append(phase)
                if phase == "baseline":
                    assert not tuple(tmp_path.rglob(name)), "mutation preceded baseline admission"
                raise ServiceAbsent("service absent during recreation")
            marker = "a" if phase == "baseline" else "b" if phase == "excluded" else "c"
            source_present = (tmp_path / service / "src" / name).exists()
            return ServiceRuntimeIdentity(
                (marker if service == "backend" else {"a": "d", "b": "e", "c": "f"}[marker]) * 64,
                "new" if service == "backend" and source_present else "old",
            )

        def service_file(self, service: str, path: str) -> bytes | None:
            self.service_runtime_identity(service)
            if name.encode() in ignore.read_bytes():
                return None
            source = tmp_path / service / "src" / Path(path).name
            return source.read_bytes() if source.exists() else None

    monkeypatch.setattr("scripts.dev_environment.watch_witness.time.monotonic", lambda: now[0])
    monkeypatch.setattr(
        "scripts.dev_environment.watch_witness.time.sleep",
        lambda delay: now.__setitem__(0, now[0] + delay),
    )
    watch_witness._verify_build_context_effect(BuildContextProject(tmp_path), FakeProcess())
    assert gaps == ["baseline", "excluded", "restored"]
    assert now[0] == pytest.approx(0.6)
    assert ignore.read_bytes() == b"# original\n"
    assert not tuple(tmp_path.rglob(name))


@pytest.mark.parametrize("delayed_restoration", [2, 4], ids=["vite-config", "transitive-config"])
def test_frontend_feedback_awaits_restoration_epoch_before_the_next_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, delayed_restoration: int
) -> None:
    frontend = tmp_path / "frontend"
    (frontend / "src/api/workbench").mkdir(parents=True)
    config = frontend / "vite.config.ts"
    limits = frontend / "src/api/workbench/limits.ts"
    document = frontend / "index.html"
    config.write_text("    server: {\n")
    limits.write_text("export const limit = 20;\n")
    document.write_text("CI Coordinator")
    originals = {path: path.read_bytes() for path in (config, limits, document)}
    project = FakeProject(tmp_path)
    phase, applied, delayed_reads, rendered = 0, 0, 0, False
    now = [0.0]

    def response(_endpoint: str, path: str) -> tuple[int, bytes, str]:
        nonlocal phase
        configured = b"X-Coordinator-Watch" in config.read_bytes()
        limited = b" = 19;" in limits.read_bytes()
        if configured:
            phase = 1
        elif phase == 1:
            phase = 2
        if limited:
            assert applied == 2 or phase == 3, "transitive edit overtook config restoration"
            phase = 3
        elif phase == 3:
            phase = 4
        return (
            (404 if limited else 401) if path != "/" else 200,
            document.read_bytes(),
            "probe" if configured else "",
        )

    def runtime(service: str) -> ServiceRuntimeIdentity:
        nonlocal applied, delayed_reads
        assert service == "frontend"
        if phase == delayed_restoration and applied != phase and delayed_reads == 0:
            delayed_reads += 1
        else:
            applied = phase
        return ServiceRuntimeIdentity("a" * 64, str(applied))

    def render(*_args: object, **_kwargs: object) -> CommandResult:
        nonlocal rendered
        assert phase == applied == 4, "HMR phase overtook transitive config restoration"
        rendered = True
        return CommandResult(0, "", "")

    monkeypatch.setattr(project, "service_runtime_identity", runtime)
    monkeypatch.setattr(watch_witness, "_http", response)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(watch_witness, "spawn", render)
    monkeypatch.setattr(watch_witness, "_require_render_result", lambda _result: None)
    monkeypatch.setattr(
        "scripts.dev_environment.watch_witness.secrets.token_hex", lambda _length: "probe"
    )
    monkeypatch.setattr("scripts.dev_environment.watch_witness.time.monotonic", lambda: now[0])
    monkeypatch.setattr(
        "scripts.dev_environment.watch_witness.time.sleep",
        lambda delay: now.__setitem__(0, now[0] + delay),
    )

    watch_witness._verify_frontend_effects(project, FakeProcess())

    assert rendered and delayed_reads == 1
    assert all(path.read_bytes() == content for path, content in originals.items())


@pytest.mark.parametrize("exit_before_observation", [False, True])
def test_effect_receipt_cannot_outlive_the_watch_process(exit_before_observation: bool) -> None:
    process = FakeProcess(exited=exit_before_observation)
    calls: list[bool] = []

    def effect() -> ServiceRuntimeIdentity:
        calls.append(True)
        process.exited = True
        return ServiceRuntimeIdentity("a" * 64, "new")

    with pytest.raises(watch_witness.WatchError, match="exited before witness completion"):
        watch_witness._await_effect(effect, process=process)
    assert calls == ([] if exit_before_observation else [True])


def test_watch_phase_retains_the_stop_evidence_and_provider_output_shape() -> None:
    process = InteractiveResult(130, False, "cancelled", cancellation_signal_sent=True)
    cancellation = watch_session.CancelResult("blocked", "process_stop_unproven", "owned-nonce")
    stopped = watch_witness.WatchError(
        "clean stop is unproven", process=process, cancellation=cancellation
    )
    with (
        pytest.raises(watch_witness.WatchError) as caught,
        watch_witness._watch_phase("source-watch"),
    ):
        raise stopped
    assert caught.value.__cause__ is stopped
    assert caught.value.process is process and caught.value.cancellation is cancellation

    provider = ComposeError(
        "Compose returned an invalid container identity (service=frontend, shape=empty)"
    )
    with (
        pytest.raises(watch_witness.WatchError) as caught,
        watch_witness._watch_phase("frontend-compiler"),
    ):
        raise provider
    assert caught.value.__cause__ is provider
    assert str(caught.value) == f"frontend-compiler: {provider}"


def test_watch_witness_cleans_host_probes_when_provider_exits(tmp_path: Path) -> None:
    for service in ("backend", "frontend"):
        (tmp_path / service / "src").mkdir(parents=True)
    process = FakeProcess(exited=True)

    with pytest.raises(RuntimeError, match="exited before witness completion"):
        verify_source_watch(
            FakeProject(tmp_path),
            process_factory=lambda _invocation: process,
            wait=lambda _seconds: None,
        )

    assert process.stopped is True
    assert not tuple(tmp_path.rglob("*watch-probe*"))


def test_watch_witness_rejects_preexisting_probe_without_mutation(
    tmp_path: Path,
) -> None:
    for service in ("backend", "frontend"):
        (tmp_path / service / "src").mkdir(parents=True)
    probe = tmp_path / "backend/src/ci-coordinator-watch-probe.txt"
    probe.write_text("owned by caller\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="already allocated"):
        verify_source_watch(FakeProject(tmp_path))

    assert probe.read_text(encoding="utf-8") == "owned by caller\n"


def test_sync_without_a_runtime_restart_cannot_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for service in ("backend", "frontend"):
        (tmp_path / service / "src").mkdir(parents=True)
    process = FakeProcess()
    now = [0.0]
    monkeypatch.setattr("scripts.dev_environment.watch_witness.time.monotonic", lambda: now[0])

    def wait(seconds: float) -> None:
        now[0] += seconds

    with pytest.raises(watch_witness.WatchError, match="did not restart"):
        verify_source_watch(
            FakeProject(tmp_path, runtime_delay=100_000),
            process_factory=lambda _: process,
            wait=wait,
        )
    assert process.stopped
    assert not tuple(tmp_path.rglob("*watch-probe*"))


def test_startup_barrier_retries_a_missed_first_write_without_assuming_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for service in ("backend", "frontend"):
        (tmp_path / service / "src").mkdir(parents=True)
    now = [0.0]
    monkeypatch.setattr("scripts.dev_environment.watch_witness.time.monotonic", lambda: now[0])

    class LateWatchProject(FakeProject):
        def service_file(self, service: str, path: str) -> bytes | None:
            observed = super().service_file(service, path)
            return None if observed == b"barrier-1\n" else observed

    def wait(seconds: float) -> None:
        now[0] += seconds

    project = LateWatchProject(tmp_path)
    verify_source_watch(project, process_factory=lambda _: FakeProcess(), wait=wait)
    assert project.runtime_revision == 3
    assert now[0] >= 2


@pytest.mark.parametrize("changed_service", ["backend", "frontend"])
def test_recreated_container_is_not_a_source_restart(tmp_path: Path, changed_service: str) -> None:
    for service in ("backend", "frontend"):
        (tmp_path / service / "src").mkdir(parents=True)

    class RecreatedProject(FakeProject):
        def service_runtime_identity(self, service: str) -> ServiceRuntimeIdentity:
            current = super().service_runtime_identity(service)
            if service == changed_service and self.runtime_revision:
                return ServiceRuntimeIdentity(container_id="b" * 64, started_at=current.started_at)
            return current

    message = "unexpectedly recreated" if changed_service == "backend" else "unexpectedly restarted"
    with pytest.raises(watch_witness.WatchError, match=message):
        verify_source_watch(RecreatedProject(tmp_path), process_factory=lambda _: FakeProcess())


def test_feedback_effects_share_the_acknowledged_watcher(tmp_path: Path) -> None:
    for service in ("backend", "frontend"):
        (tmp_path / service / "src").mkdir(parents=True)
    process = FakeProcess()
    observed: list[object] = []

    def effect(active: watch_witness.WatchProcess) -> None:
        assert not process.stopped
        observed.append(active)

    verify_source_watch(
        FakeProject(tmp_path), process_factory=lambda _: process, effect_verifier=effect
    )
    assert observed == [process]
    assert process.stopped


def test_feedback_input_is_restored_on_failure(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.write_bytes(b"original")
    with (
        pytest.raises(ValueError, match="failed"),
        watch_witness._replaced(source, b"candidate"),
    ):
        raise ValueError("failed")
    assert source.read_bytes() == b"original"


def test_feedback_restore_does_not_overwrite_an_unowned_edit(tmp_path: Path) -> None:
    source = tmp_path / "input"
    source.write_bytes(b"original")
    with (
        pytest.raises(watch_witness.WatchError, match="outside the witness"),
        watch_witness._replaced(source, b"candidate"),
    ):
        source.write_bytes(b"concurrent")
    assert source.read_bytes() == b"concurrent"


@pytest.mark.parametrize(
    ("result", "admitted"),
    [
        (InteractiveResult(0, True), True),
        (InteractiveResult(0, True, "cancelled", cancellation_signal_sent=True), True),
        (InteractiveResult(130, True, "cancelled", cancellation_signal_sent=True), True),
        (InteractiveResult(130, True), False),
        (InteractiveResult(130, True, "cancelled"), False),
        (InteractiveResult(130, False, "cancelled", cancellation_signal_sent=True), False),
        (InteractiveResult(0, True, "cancelled", escalated=True), False),
        (InteractiveResult(0, False, "cancelled"), False),
        (InteractiveResult(0, True, "timeout"), False),
        (InteractiveResult(-15, True, "cancelled", cancellation_signal_sent=True), False),
    ],
)
def test_feedback_client_admits_only_observed_clean_physical_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: InteractiveResult,
    admitted: bool,
) -> None:
    entered = threading.Event()
    cancellation_published = threading.Event()
    observation: dict[str, object] = {}

    def observe_write(path: Path, text: str) -> None:
        atomic_write_private_text(path, text)
        if path == operation_paths(identity).stop:
            cancellation_published.set()

    def run(command: str, args: Sequence[str], **kwargs: object) -> InteractiveResult:
        observation.update(command=command, args=args, **kwargs)
        entered.set()
        # A controlled result follows the real nonce-bound cancellation write;
        # entering the fake does not establish that the caller requested stop.
        assert cancellation_published.wait(timeout=2)
        requested = kwargs["stop_requested"]
        assert callable(requested) and requested()
        return result

    monkeypatch.setattr("scripts.dev_environment.watch_session.run_interactive", run)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(watch_session, "atomic_write_private_text", observe_write)
    source = tmp_path / "source"
    source.mkdir()
    identity = derive_instance_identity(source, state_home=tmp_path / "state")
    process = watch_witness._SubprocessWatch(FakeProject(source), identity)
    try:
        assert entered.wait(timeout=2)
        if admitted:
            process.stop()
            assert process.poll() == result.returncode
        else:
            with pytest.raises(watch_witness.WatchError, match="clean stop") as caught:
                process.stop()
            assert caught.value.process is result
            assert caught.value.cancellation is not None
            assert caught.value.cancellation.nonce == process._session.nonce
            assert operation_paths(identity).session.exists()
            with pytest.raises(OperationBlocked), instance_operation_lock(identity):
                pytest.fail("unsafe watch stop admitted another mutation")
        assert observation["command"] == "docker"
        assert observation["args"] == ("compose", "watch")
        assert observation["cwd"] == source
        assert observation["env"] == {"PATH": "/bin"}
        assert observation["timeout_seconds"] == 1_800
        assert observation["graceful_seconds"] == 10
        assert observation["kill_seconds"] == 5
        requested = observation["stop_requested"]
        assert callable(requested)
        descriptors = observation["inherited_fds"]
        assert isinstance(descriptors, tuple) and len(descriptors) == 1
    finally:
        process._resources.close()
        process._executor.shutdown(wait=False, cancel_futures=True)


@pytest.mark.parametrize(
    ("result", "primary", "last_phase", "secondary"),
    [
        (
            CommandResult(
                1,
                "hmr_phase=updated-text\nhmr_failed=updated-text\n"
                "hmr_phase=browser-close\nhmr_failed=browser-close\n"
                "hmr_phase=remove-style\nhmr_ok=remove-style\n",
                "secret-browser-url",
            ),
            "updated-text",
            "remove-style",
            "browser-close",
        ),
        (
            CommandResult(
                None,
                "hmr_ok=initial-text\nhmr_phase=ws-connected\n",
                "secret-timeout-output",
                failure_kind="timeout",
            ),
            "ws-connected",
            "ws-connected",
            "none",
        ),
        (
            CommandResult(
                1,
                "hmr_phase=secret-source\nhmr_failed=secret-token\n"
                "hmr_ok=secret-path\nsecret-stdout\n",
                "secret-stderr",
                error="secret-provider-error",
            ),
            "bootstrap",
            "bootstrap",
            "none",
        ),
    ],
)
def test_render_failure_retains_only_closed_check_codes(
    result: CommandResult, primary: str, last_phase: str, secondary: str
) -> None:
    with pytest.raises(watch_witness.WatchError) as caught:
        watch_witness._require_render_result(result)
    message = str(caught.value)
    assert f"check={primary};" in message
    assert f"last_phase={last_phase};" in message
    assert f"secondary_failures={secondary};" in message
    assert "secret" not in message
    assert "updated-text=0" in message
    if result.timed_out:
        assert "process=timeout" in message
        assert "initial-text=1" in message


@pytest.mark.parametrize(
    ("present", "closed", "changed", "connected"),
    [(0, 0, 0, 0), (1, 1, 0, 1), (1, 0, 1, 2)],
)
def test_render_transport_distinguishes_the_failed_readiness_operands(
    present: int, closed: int, changed: int, connected: int
) -> None:
    output = (
        "hmr_phase=ws-connected\nhmr_failed=ws-connected\n"
        f"hmr_transport=present:{present}\nhmr_transport=closed:{closed}\n"
        f"hmr_transport=changed:{changed}\nhmr_transport=connected:{connected}\n"
        "hmr_transport=handshake-status:101\nhmr_transport=http-client-status:200\n"
        "hmr_transport=cdp-connected:1\n"
        "hmr_phase=browser-close\nhmr_ok=browser-close\n"
    )
    with pytest.raises(watch_witness.WatchError) as caught:
        watch_witness._require_render_result(CommandResult(1, output, ""))
    message = str(caught.value)
    assert "check=ws-connected;" in message
    transport = set(message.split("; transport=", 1)[1].split(","))
    for name, value in (("present", present), ("closed", closed), ("changed", changed)):
        assert f"{name}={value}" in transport
    assert f"connected={connected}" in transport
    assert "handshake-status=101" in transport
    assert "http-client-status=200" in transport
    assert "cdp-connected=1" in transport
    assert "attempts=unknown" in transport
    assert len(message) < 2_048


@pytest.mark.parametrize("invalid_count", ["17", "-1", "1.0", "1:secret", "\u0661", "999999"])
def test_render_transport_rejects_unbounded_or_noninteger_diagnostics(invalid_count: str) -> None:
    output = (
        "hmr_phase=ws-connected\n"
        "hmr_transport=attempts:2\n"
        f"hmr_transport=attempts:{invalid_count}\n"
        "hmr_transport=present:2\n"
        "hmr_transport=handshake-status:600\n"
        "hmr_transport=secret-url:1\n"
    )
    with pytest.raises(watch_witness.WatchError) as caught:
        watch_witness._require_render_result(CommandResult(1, output, "secret-stderr"))
    message = str(caught.value)
    assert "attempts=2," in message
    assert "present=unknown," in message
    assert "handshake-status=unknown," in message
    assert "secret" not in message


@pytest.mark.parametrize(
    "missing_check",
    [
        "ws-connected",
        "module-ready",
        "prime-module",
        "prime-receipt",
        "primed-text",
        "document-retained",
        "remove-style",
    ],
)
def test_render_success_marker_cannot_replace_a_missing_check(missing_check: str) -> None:
    evidence = "".join(
        f"hmr_ok={code}\n" for code in watch_witness._RENDER_PHASES if code != missing_check
    )
    evidence += "hmr_transport=connected:1\nhmr_transport=cdp-connected:1\n"
    evidence += "hmr_transport=prime-status:200\nhmr_transport=prime-dom:2\n"
    with pytest.raises(watch_witness.WatchError, match="incomplete-evidence"):
        watch_witness._require_render_result(
            CommandResult(0, evidence + "rendered_hmr_and_style\n", "")
        )


def test_render_complete_checks_still_require_a_clean_process_exit() -> None:
    evidence = "".join(f"hmr_ok={code}\n" for code in watch_witness._RENDER_PHASES)
    evidence += "rendered_hmr_and_style\n"
    watch_witness._require_render_result(CommandResult(0, evidence, ""))
    with pytest.raises(watch_witness.WatchError, match="process=timeout"):
        watch_witness._require_render_result(CommandResult(0, evidence, "", failure_kind="timeout"))


@pytest.mark.parametrize(
    ("name", "valid", "invalid"),
    [
        ("prime-status", "503", "600"),
        ("prime-requests", "16", "17"),
        ("prime-fetch-truncated", "1", "2"),
        ("prime-dom", "4", "5"),
        ("prime-accept", "1", "https://private.invalid/?token=secret"),
        ("pre-pin-page-error", "1", "RuntimeError: secret"),
        ("pre-pin-request-failed", "1", "-1"),
        ("pre-pin-console-error", "2", "2\u0661"),
        ("pre-pin-vite-error", "1", "1.0"),
    ],
)
def test_render_priming_diagnostics_reject_payloads_and_preserve_failure(
    name: str, valid: str, invalid: str
) -> None:
    output = (
        "hmr_failed=primed-text\n"
        f"hmr_transport={name}:{valid}\n"
        f"hmr_transport={name}:{invalid}\n"
        "hmr_transport=private-url:1\n"
    )
    with pytest.raises(watch_witness.WatchError) as caught:
        watch_witness._require_render_result(CommandResult(1, output, "secret exception"))
    message = str(caught.value)
    assert "check=primed-text;" in message
    assert f"{name}={valid}," in message
    assert "secret" not in message and "private" not in message


def test_render_projection_requires_native_initialization_for_the_owned_module(
    tmp_path: Path,
) -> None:
    assertions = r"""
import assert from 'node:assert/strict';
const initialization =
  'import { createHotContext as __vite__createHotContext } from "/@vite/client";' +
  'import.meta.hot = __vite__createHotContext("/src/ci-coordinator-hmr-probe.js");';
const raw = `import './ci-coordinator-hmr-probe.css';
if (import.meta.hot) {
  import.meta.hot.accept();
  window.watchHmrAcceptReady = "created";
}
document.querySelector('#watch-value').textContent = "created";`;
const transformed = initialization + raw.replace(
  "'./ci-coordinator-hmr-probe.css'", '"/src/ci-coordinator-hmr-probe.css"');
assert.equal(isPreparedHmrProjection(transformed), true);
assert.equal(isPreparedHmrProjection(
  transformed + '\n//# sourceMappingURL=data:application/json;base64,e30='), true);
for (const [name, source] of [
  ['raw-200-with-created-and-hot-accept', raw],
  ['hot-import-without-initialization', initialization.split(';')[0] + ';' + raw],
  ['css-initialization-with-created', initialization.replace('.js', '.css') + raw],
  ['another-module-initialization',
    transformed.replace('/src/ci-coordinator-hmr-probe.js', '/src/other.js')],
  ['another-client-initialization', transformed.replace('/@vite/client', '/unowned-client')],
  ['initialization-comment', '/*' + initialization + '*/' + raw],
  ['initialization-format-drift', transformed.replace(';import.meta.hot', ';\nimport.meta.hot')],
  ['different-marker-with-created-comment',
    transformed.replaceAll('"created"', '"updated"') + '\n// created'],
]) assert.equal(isPreparedHmrProjection(source), false, name);
console.log('projection_admission_checked');
"""
    result = spawn(
        "node",
        ["--input-type=module", "-e", watch_witness._RENDER_DOCUMENT_LIFETIME + assertions],
        cwd=tmp_path,
        max_buffer=65_536,
        timeout_seconds=5,
    )
    assert result.status == 0, result.stderr
    assert result.failure_kind is None
    assert result.stdout.strip() == "projection_admission_checked"


def test_render_dom_diagnostics_are_bounded_and_do_not_change_the_predicate(tmp_path: Path) -> None:
    assertions = r"""
import assert from 'node:assert/strict';
let text;
const expected = 'primed-owned-value';
globalThis.document = {querySelector: () => text === null ? null : {textContent: text}};
globalThis.window = {};
let messages = [];
console.debug = message => messages.push(message);
for (const [observed, accepted, category, acceptCategory] of [
  ['created', 'created', 1, 1],
  [expected, expected, 2, 2],
  ['updated', expected, 3, 2],
  [null, 'private-value', 4, 4],
  ['https://private.invalid/?token=secret', undefined, 4, 4],
]) {
  text = observed;
  window.watchHmrAcceptReady = accepted;
  assert.equal(observePrimedText(expected), observed === expected);
  assert.deepEqual(parsePrimingState(messages.at(-1)), [category, acceptCategory]);
}
for (const invalid of [
  '[watch-hmr-state] 5:1', '[watch-hmr-state] -1:2',
  '[watch-hmr-state] 1:2\n', '[watch-hmr-state] 1:2 private-value',
  '[watch-hmr-state] 1:https://private.invalid/?token=secret',
]) assert.equal(parsePrimingState(invalid), null);
window.watchHmrDiagnosticState = undefined;
messages = [];
for (let index = 0; index < 40; index += 1) {
  text = index % 2 ? 'created' : 'private-value';
  assert.equal(observePrimedText(expected), false);
}
assert.equal(messages.length, 16);
text = expected;
assert.equal(observePrimedText(expected), true);
assert.equal(messages.length, 16);
assert.ok(messages.every(message => parsePrimingState(message) !== null));
assert.ok(messages.every(message => !message.includes('private') && !message.includes(expected)));
console.log('priming_diagnostics_checked');
"""
    result = spawn(
        "node",
        ["--input-type=module", "-e", watch_witness._RENDER_DOCUMENT_LIFETIME + assertions],
        cwd=tmp_path,
        max_buffer=65_536,
        timeout_seconds=5,
    )
    assert result.status == 0, result.stderr
    assert result.failure_kind is None
    assert result.stdout.strip() == "priming_diagnostics_checked"


@pytest.mark.parametrize(
    "scenario",
    [
        "positive",
        "late",
        "expired",
        "microtask-expired",
        "hung",
        "cleanup-hung",
        "cleanup-shared",
        "cleanup-late",
        "cleanup-once",
        "late-rejection",
        "action-rejection",
        "missing-clear",
    ],
)
def test_render_budget_preserves_deadlines_and_cleanup_reserve(
    tmp_path: Path, scenario: str
) -> None:
    assertions = r"""
import assert from 'node:assert/strict';
const scenario = process.argv[1];
let now = 0;
const budget = createRenderBudget(70, 10, () => now);
const { setTimeout: nativeTimeout, clearTimeout: nativeClear } = await import('node:timers');
const pending = new Set();
let created = 0;
globalThis.setTimeout = (callback, milliseconds) => {
  const handle = nativeTimeout(() => { pending.delete(handle); callback(); }, milliseconds);
  pending.add(handle);
  created += 1;
  return handle;
};
globalThis.clearTimeout = handle => { pending.delete(handle); nativeClear(handle); };
if (scenario === 'positive' || scenario === 'missing-clear') {
  assert.equal(await budget.run(() => { now = 69; return 42; }), 42);
  budget.cleanup();
  assert.equal(budget.remaining(), 10);
  assert.equal(await budget.run(() => { now = 78; return 7; }), 7);
} else if (scenario === 'late') {
  await assert.rejects(budget.run(() => { now = 70; return 42; }));
  budget.cleanup();
  assert.equal(budget.remaining(), 10);
  assert.equal(await budget.run(() => 7), 7);
} else if (scenario === 'expired' || scenario === 'microtask-expired') {
  let invoked = false;
  if (scenario === 'expired') now = 70;
  const operation = budget.run(() => { invoked = true; });
  now = 70;
  await assert.rejects(operation);
  assert.equal(invoked, false);
  assert.equal(created, scenario === 'expired' ? 0 : 1);
} else if (scenario === 'hung' || scenario === 'cleanup-hung') {
  if (scenario === 'cleanup-hung') budget.cleanup();
  await assert.rejects(budget.run(() => new Promise(() => {})));
} else if (scenario === 'cleanup-shared') {
  now = 70;
  budget.cleanup();
  await budget.run(() => { now = 79; });
  assert.equal(budget.remaining(), 1);
  await assert.rejects(budget.run(() => { now = 80; }));
  let invoked = false;
  await assert.rejects(budget.run(() => { invoked = true; }));
  assert.equal(invoked, false);
} else if (scenario === 'cleanup-late') {
  now = 75;
  budget.cleanup();
  assert.equal(budget.remaining(), 5);
  await assert.rejects(budget.run(() => { now = 80; }));
} else if (scenario === 'cleanup-once') {
  budget.cleanup();
  assert.throws(() => budget.cleanup());
} else if (scenario === 'action-rejection') {
  const expected = new Error('owned action error');
  await assert.rejects(budget.run(() => { throw expected; }), error => error === expected);
} else {
  let reject;
  await assert.rejects(budget.run(() => new Promise((_, fail) => { reject = fail; })));
  reject(new Error('late rejected operation'));
  await new Promise(resolve => setImmediate(resolve));
}
assert.equal(pending.size, 0, 'pending phase timers');
console.log('render_budget_checked');
"""
    source = watch_witness._RENDER_BUDGET
    if scenario == "missing-clear":
        assert source.count("clearTimeout(timer);") == 1
        source = source.replace("clearTimeout(timer);", "void timer;", 1)
    result = spawn(
        "node",
        ["--input-type=module", "-e", source + assertions, scenario],
        cwd=tmp_path,
        max_buffer=65_536,
        timeout_seconds=5,
    )
    assert result.failure_kind is None
    if scenario == "missing-clear":
        assert result.status == 1
        assert "pending phase timers" in result.stderr
        assert "render_budget_checked" not in result.stdout
    else:
        assert result.status == 0, result.stderr
        assert result.stdout.strip() == "render_budget_checked"


@pytest.mark.parametrize("value", ["90001", "-1", "1.0", "999999", "secret", "\u0661"])
def test_render_elapsed_diagnostics_are_bounded_and_non_authoritative(value: str) -> None:
    output = (
        "hmr_failed=updated-text\nhmr_ms=updated-text:42\n"
        f"hmr_ms=updated-text:{value}\nhmr_ms=private:1\n"
        "hmr_transport=post-update:2\nhmr_transport=module-status:200\n"
    )
    with pytest.raises(watch_witness.WatchError) as caught:
        watch_witness._require_render_result(CommandResult(1, output, "secret-provider-output"))
    assert "elapsed_ms=updated-text=42" in str(caught.value)
    assert "post-update=2" in str(caught.value) and "module-status=200" in str(caught.value)
    assert "secret" not in str(caught.value) and "private" not in str(caught.value)


@pytest.mark.parametrize("failed", [False, True])
def test_material_phase_timing_preserves_stdout_and_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], failed: bool
) -> None:
    clock = iter([1_000_000, 43_000_000])
    monkeypatch.setattr(
        "scripts.dev_environment.watch_witness.time.monotonic_ns", lambda: next(clock)
    )
    error = RuntimeError("secret-message")
    observed_failure = False
    try:
        with watch_witness._watch_phase("secret-phase"):
            if failed:
                raise error
    except RuntimeError as caught:
        assert caught is error
        observed_failure = True
    assert observed_failure == failed
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {
        "code": "material_phase_timing",
        "phase": "other",
        "outcome": "failed" if failed else "passed",
        "elapsedMs": 42,
    }


@pytest.mark.parametrize(
    "scenario",
    [
        "buffered-reload",
        "buffered-reload-wildcard",
        "same-document-duplicate",
        "unannounced-navigation",
        "unrelated-html-reload",
        "pin-raced-navigation",
        "navigation-after-pin",
        "socket-created-after-pin",
        "socket-closed-after-pin",
        "reload-after-pin",
    ],
)
def test_render_document_lifetime_rejects_unowned_transitions(
    tmp_path: Path, scenario: str
) -> None:
    assertions = r"""
import assert from 'node:assert/strict';
const lifetime = createDocumentBoundHmr();
const socket = () => ({closed: false, isClosed() { return this.closed; }});
const first = socket();
lifetime.navigate();
const firstReceive = lifetime.socketCreated(first);
firstReceive({type: 'connected'});
assert.equal(lifetime.ready(), false);
assert.throws(() => lifetime.pin(lifetime.document, true));
lifetime.prime(lifetime.document, true);
const scenario = process.argv[1];
if (scenario === 'same-document-duplicate') {
  lifetime.socketCreated(socket())({type: 'connected'});
}
if (scenario === 'unannounced-navigation') {
  lifetime.navigate();
}
const reloadPath = scenario === 'unrelated-html-reload' ? '/other.html' :
  scenario === 'buffered-reload-wildcard' ? '*' : '/index.html';
firstReceive({type: 'full-reload', path: reloadPath});
assert.equal(lifetime.ready(), false);
assert.throws(() => lifetime.pin(lifetime.document, true));
lifetime.navigate();
const second = socket();
const secondReceive = lifetime.socketCreated(second);
secondReceive({type: 'connected'});
const invalidBootstrap =
  ['same-document-duplicate', 'unannounced-navigation', 'unrelated-html-reload'];
if (invalidBootstrap.includes(scenario)) {
  assert.equal(lifetime.ready(), false);
  assert.throws(() => lifetime.pin(lifetime.document, true));
} else {
  assert.equal(first.isClosed(), false);
  assert.equal(lifetime.ready(), true);
  const candidate = lifetime.document;
  if (scenario === 'pin-raced-navigation') {
    secondReceive({type: 'full-reload', path: '/index.html'});
    lifetime.navigate();
    lifetime.socketCreated(socket())({type: 'connected'});
    assert.equal(lifetime.ready(), true);
    assert.throws(() => lifetime.pin(candidate, true));
  } else {
    lifetime.pin(candidate, true);
    assert.equal(lifetime.retained(), true);
    if (scenario.startsWith('buffered-reload')) {
      firstReceive({type: 'full-reload', path: '/index.html'});
      firstReceive({type: 'connected'});
      assert.equal(lifetime.retained(), true);
      assert.equal(lifetime.facts().epoch, 2);
      assert.equal(lifetime.facts()['baseline-epoch'], 2);
    } else {
      if (scenario === 'navigation-after-pin') lifetime.navigate();
      if (scenario === 'socket-created-after-pin') lifetime.socketCreated(socket());
      if (scenario === 'socket-closed-after-pin') second.closed = true;
      if (scenario === 'reload-after-pin') {
        secondReceive({type: 'full-reload', path: '/index.html'});
      }
      assert.equal(lifetime.retained(), false);
    }
  }
}
console.log('document_lifetime_checked');
"""
    result = spawn(
        "node",
        [
            "--input-type=module",
            "-e",
            watch_witness._RENDER_DOCUMENT_LIFETIME + assertions,
            scenario,
        ],
        cwd=tmp_path,
        max_buffer=65_536,
        timeout_seconds=5,
    )
    assert result.status == 0, result.stderr
    assert result.failure_kind is None
    assert result.stdout.strip() == "document_lifetime_checked"


@pytest.mark.parametrize(
    "scenario",
    [
        "zero-reload",
        "buffered-after-prime",
        "buffered-after-update",
        "buffered-after-render",
        "reload-before-prime",
        "update-before-registration",
        "update-before-connected",
        "wrong-update-path",
        "wrong-accepted-path",
        "wrong-update-kind",
        "wrong-update-shape",
        "wrong-update-socket",
        "wrong-reload-socket",
        "stale-document-update",
        "primed-without-receipt",
        "receipt-without-render",
        "prime-raced-navigation",
        "pin-raced-reload",
        "prime-twice",
        "socket-closed-before-pin",
    ],
)
def test_render_priming_requires_a_current_document_effect_branch(
    tmp_path: Path, scenario: str
) -> None:
    assertions = r"""
import assert from 'node:assert/strict';
const scenario = process.argv[1];
const lifetime = createDocumentBoundHmr();
const path = '/src/ci-coordinator-hmr-probe.js';
const update = overrides => ({type: 'update', updates: [
  {type: 'js-update', path, acceptedPath: path, ...overrides},
]});
const socket = () => ({closed: false, isClosed() { return this.closed; }});
const first = socket();
lifetime.navigate();
const origin = lifetime.document;
const receive = lifetime.socketCreated(first);
const rejectPin = (document = lifetime.document, rendered = true) =>
  assert.throws(() => lifetime.pin(document, rendered));
const reload = () => receive({type: 'full-reload', path: '/index.html'});
const nextDocument = () => {
  lifetime.navigate();
  const nextReceive = lifetime.socketCreated(socket());
  nextReceive({type: 'connected'});
  return nextReceive;
};
if (scenario === 'update-before-connected') {
  receive(update());
  assert.throws(() => lifetime.prime(origin, true));
}
receive({type: 'connected'});
assert.equal(lifetime.connected(), true);
assert.equal(lifetime.ready(), false);
rejectPin();
if (scenario === 'update-before-registration') {
  receive(update());
  assert.throws(() => lifetime.prime(origin, false));
  assert.equal(lifetime.facts()['prime-epoch'], 0);
}
if (scenario === 'reload-before-prime' || scenario === 'prime-raced-navigation') {
  reload();
  assert.throws(() => lifetime.prime(origin, true));
  const nextReceive = nextDocument();
  assert.throws(() => lifetime.prime(origin, true));
  lifetime.prime(lifetime.document, true);
  assert.equal(lifetime.facts()['prime-reload'], 0);
  receive(update());
  assert.equal(lifetime.facts()['prime-update'], 0);
  assert.equal(lifetime.ready(), false);
  rejectPin();
  nextReceive(update());
  assert.equal(lifetime.ready(), true);
  lifetime.pin(lifetime.document, true);
  assert.equal(lifetime.retained(), true);
} else {
  lifetime.prime(origin, true);
  assert.equal(lifetime.facts()['prime-epoch'], 1);
  assert.equal(lifetime.facts()['prime-update'], 0);
  assert.equal(lifetime.ready(), false);
  rejectPin();
  if (scenario.startsWith('buffered-') || scenario === 'stale-document-update') {
    if (scenario !== 'buffered-after-prime') {
      receive(update());
      assert.equal(lifetime.ready(), true);
    }
    const renderedDocument = lifetime.document;
    reload();
    assert.equal(lifetime.ready(), false);
    rejectPin();
    receive(update());
    assert.equal(lifetime.ready(), false);
    nextDocument();
    assert.equal(lifetime.facts()['prime-update'], 0);
    assert.equal(lifetime.facts()['prime-reload'], 1);
    receive(update());
    assert.equal(lifetime.facts()['prime-update'], 0);
    assert.equal(lifetime.ready(), true);
    if (scenario === 'buffered-after-render') rejectPin(renderedDocument);
    rejectPin(lifetime.document, false);
    lifetime.pin(lifetime.document, true);
    assert.equal(lifetime.retained(), true);
  } else if (scenario.startsWith('wrong-') || scenario === 'primed-without-receipt') {
    if (scenario === 'wrong-update-path') receive(update({path: '/src/other.js'}));
    if (scenario === 'wrong-accepted-path') receive(update({acceptedPath: '/src/other.js'}));
    if (scenario === 'wrong-update-kind') receive(update({type: 'css-update'}));
    if (scenario === 'wrong-update-shape') receive({type: 'update', updates: {path}});
    if (scenario === 'wrong-update-socket') lifetime.socketCreated(socket())(update());
    if (scenario === 'wrong-reload-socket') {
      lifetime.socketCreated(socket())({type: 'full-reload', path: '/index.html'});
      nextDocument();
      assert.equal(lifetime.facts()['lifetime-invalid'], 1);
    }
    assert.equal(lifetime.facts()['prime-update'], 0);
    assert.equal(lifetime.ready(), false);
    rejectPin();
  } else {
    if (scenario === 'prime-twice') assert.throws(() => lifetime.prime(origin, true));
    receive(update());
    assert.equal(lifetime.ready(), true);
    if (scenario === 'receipt-without-render') {
      rejectPin(origin, false);
    } else if (scenario === 'pin-raced-reload') {
      reload();
      assert.equal(lifetime.ready(), false);
      rejectPin(origin);
    } else if (scenario === 'socket-closed-before-pin') {
      first.closed = true;
      assert.equal(lifetime.ready(), false);
      rejectPin(origin);
    } else {
      lifetime.pin(origin, true);
      assert.equal(lifetime.retained(), true);
      assert.equal(lifetime.facts()['prime-update'], 1);
      assert.equal(lifetime.facts()['prime-reload'], 0);
      assert.throws(() => lifetime.prime(origin, true));
    }
  }
}
console.log('priming_lifetime_checked');
"""
    result = spawn(
        "node",
        [
            "--input-type=module",
            "-e",
            watch_witness._RENDER_DOCUMENT_LIFETIME + assertions,
            scenario,
        ],
        cwd=tmp_path,
        max_buffer=65_536,
        timeout_seconds=5,
    )
    assert result.status == 0, result.stderr
    assert result.failure_kind is None
    assert result.stdout.strip() == "priming_lifetime_checked"


@pytest.mark.parametrize("separate_runtime", [False, True])
def test_feedback_browser_preflight_precedes_writes_and_uses_the_explicit_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, separate_runtime: bool
) -> None:
    source = tmp_path / "source"
    (source / "frontend/src").mkdir(parents=True)
    runtime = tmp_path / "runtime" if separate_runtime else source
    package = runtime / "frontend/node_modules/@playwright/test/package.json"
    package.parent.mkdir(parents=True)
    package.write_text("{}", encoding="utf-8")
    events: list[str] = []
    active = FakeProcess()

    def preflight(command: str, args: Sequence[str], **kwargs: object) -> CommandResult:
        assert command == "node" and args[:2] == ["--input-type=module", "-e"]
        assert kwargs["cwd"] == runtime / "frontend"
        assert kwargs["timeout_seconds"] == 30
        events.append("preflight")
        return CommandResult(0, "browser_runtime_available\n", "")

    def source_watch(
        project: watch_witness.WatchProject,
        *,
        identity: InstanceIdentity | None = None,
        effect_verifier: Callable[[watch_witness.WatchProcess], None],
    ) -> None:
        assert project.repo_root == source
        events.append("source_watch")
        effect_verifier(active)
        active.stop()
        events.append("watch_stopped")

    def frontend_effects(
        project: watch_witness.FeedbackProject,
        process: watch_witness.WatchProcess,
        *,
        browser_runtime_root: Path | None = None,
    ) -> None:
        assert project.repo_root == source and process is active
        assert browser_runtime_root == runtime
        events.append("frontend")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(watch_witness, "spawn", preflight)
    monkeypatch.setattr(watch_witness, "verify_source_watch", source_watch)
    monkeypatch.setattr(watch_witness, "_verify_frontend_effects", frontend_effects)
    monkeypatch.setattr(
        watch_witness, "_verify_backend_effects", lambda *_args: events.append("backend")
    )
    project = FakeProject(source)
    initial_status = project.diagnostic_status

    def observe_health() -> tuple[ServiceStatus, ...]:
        assert active.stopped
        events.append("health_observed")
        return initial_status()

    monkeypatch.setattr(project, "diagnostic_status", observe_health)
    watch_witness.verify_feedback_effects(
        project, browser_runtime_root=runtime if separate_runtime else None
    )
    assert events == [
        "preflight",
        "source_watch",
        "frontend",
        "backend",
        "watch_stopped",
        "health_observed",
    ]


@pytest.mark.parametrize("failure", ["relative", "missing", "symlink", "dependencies", "browser"])
def test_feedback_missing_runtime_admission_never_starts_source_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    source = tmp_path / "source"
    (source / "frontend/src").mkdir(parents=True)
    runtime = tmp_path / "runtime"
    if failure == "relative":
        runtime = Path("relative")
    elif failure == "symlink":
        runtime.symlink_to(source, target_is_directory=True)
    elif failure in {"dependencies", "browser"}:
        (runtime / "frontend").mkdir(parents=True)
    if failure == "browser":
        package = runtime / "frontend/node_modules/@playwright/test/package.json"
        package.parent.mkdir(parents=True)
        package.write_text("{}", encoding="utf-8")
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        watch_witness, "spawn", lambda *_args, **_kwargs: CommandResult(1, "", "unavailable")
    )
    monkeypatch.setattr(
        watch_witness,
        "verify_source_watch",
        lambda *_args, **_kwargs: pytest.fail("unprepared feedback began source mutation"),
    )
    with pytest.raises(watch_witness.WatchError):
        watch_witness.verify_feedback_effects(FakeProject(source), browser_runtime_root=runtime)
    assert list((source / "frontend/src").iterdir()) == []


def test_feedback_restoration_waits_for_docker_health_and_ignores_completed_one_shots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = FakeProject(tmp_path)
    observations: list[str] = []

    def status() -> tuple[ServiceStatus, ...]:
        health = "starting" if len(observations) < 2 else "healthy"
        observations.append(health)
        return (
            ServiceStatus("backend", "running", health, 0),
            ServiceStatus("postgres", "running", "healthy", 0),
            ServiceStatus("frontend", "running", "healthy", 0),
            ServiceStatus("database-provision", "exited", "", 0),
            ServiceStatus("migrate", "exited", "", 0),
            ServiceStatus("database-access", "exited", "", 0),
        )

    monkeypatch.setattr(project, "diagnostic_status", status)
    monkeypatch.setattr("scripts.dev_environment.watch_witness.time.sleep", lambda _: None)
    watch_witness._await_ordinary_health(project)
    assert observations == ["starting", "starting", "healthy"]


@pytest.mark.parametrize("service", ["backend", "postgres", "frontend"])
@pytest.mark.parametrize(
    ("state", "health"),
    [("running", "starting"), ("running", "unhealthy"), ("exited", "healthy"), ("missing", "")],
)
def test_feedback_restoration_cannot_admit_a_nonready_core_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: str,
    state: str,
    health: str,
) -> None:
    project = FakeProject(tmp_path)
    statuses = tuple(item for item in project.diagnostic_status() if item.service != service)
    if state != "missing":
        statuses = (*statuses, ServiceStatus(service, state, health, 0))
    now = [0.0]

    def wait(seconds: float) -> None:
        now[0] += seconds

    monkeypatch.setattr(project, "diagnostic_status", lambda: statuses)
    monkeypatch.setattr("scripts.dev_environment.watch_witness.time.monotonic", lambda: now[0])
    monkeypatch.setattr("scripts.dev_environment.watch_witness.time.sleep", wait)
    with pytest.raises(watch_witness.WatchError, match="did not become healthy") as caught:
        watch_witness._await_ordinary_health(project, timeout_seconds=1)
    assert f"{service}={state}" in str(caught.value)
    assert now[0] == 1


def test_feedback_restoration_propagates_lost_ownership_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = FakeProject(tmp_path)

    def foreign() -> tuple[ServiceStatus, ...]:
        raise ComposeError("foreign instance")

    monkeypatch.setattr(project, "diagnostic_status", foreign)
    monkeypatch.setattr(
        "scripts.dev_environment.watch_witness.time.sleep",
        lambda _: pytest.fail("ownership was retried"),
    )
    with pytest.raises(ComposeError, match="foreign instance"):
        watch_witness._await_ordinary_health(project)


def test_material_ledger_accounts_for_each_declared_watch_input_once() -> None:
    root = Path(__file__).resolve().parents[2]
    model = YAML(typ="safe").load(root / "compose.yaml")
    classes = watch_witness.MATERIAL_INPUT_CLASSES
    assert len({item.name for item in classes}) == len(classes)
    inputs = [path for item in classes for path in item.inputs]
    assert len(inputs) == len(set(inputs))
    for service in ("backend", "frontend"):
        for rule in model["services"][service]["develop"]["watch"]:
            path = rule["path"].removeprefix("./")
            owners = [item for item in classes if path in item.inputs]
            assert len(owners) == 1, (service, path)
            assert owners[0].mechanism == rule["action"]
    assert next(item for item in classes if item.name == "compose-model").mechanism == (
        "stop/validate/up"
    )


@pytest.mark.parametrize("service", ["backend", "frontend"])
def test_dependency_fixture_keeps_the_frozen_graph_coherent_and_restores_exact_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, service: str
) -> None:
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / "source"
    names = (
        (
            "backend/pyproject.toml",
            "backend/uv.lock",
            "backend/src/ci_coordinator/observability/health.py",
        )
        if service == "backend"
        else (
            "frontend/package.json",
            "frontend/vite.config.ts",
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
            "patches/minimatch@5.1.9.patch",
        )
    )
    for name in names:
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, source / name)
    originals = {name: (source / name).read_bytes() for name in names}
    observations: list[bool] = []

    def inspect_fixture(_observed: object, **_kwargs: object) -> ServiceRuntimeIdentity:
        # This adapter admits fixture coherence only. Real installation,
        # container replacement and runtime consumption belong to native CI.
        if service == "backend":
            manifest = tomllib.loads((source / "backend/pyproject.toml").read_text())
            lock = tomllib.loads((source / "backend/uv.lock").read_text())
            original_lock = tomllib.loads(originals["backend/uv.lock"].decode())
            assert [item for item in lock["package"] if item["name"] == "debugpy"] == [
                item for item in original_lock["package"] if item["name"] == "debugpy"
            ]
            package = next(
                item for item in lock["package"] if item["name"] == "ci-coordinator-backend"
            )
            selected = "debugpy==1.8.22" in manifest["project"]["dependencies"]
            dependencies = {item["name"] for item in package["dependencies"]}
            requirements = {item["name"]: item for item in package["metadata"]["requires-dist"]}
            assert ("debugpy" in dependencies) == selected == ("debugpy" in requirements)
            if selected:
                assert requirements["debugpy"]["specifier"] == "==1.8.22"
                assert (
                    next(item for item in lock["package"] if item["name"] == "debugpy")["version"]
                    == "1.8.22"
                )
            assert manifest["dependency-groups"]["debug"] == ["debugpy==1.8.22"]
        else:
            manifest = json.loads((source / "frontend/package.json").read_bytes())
            lock_documents = list(YAML(typ="safe").load_all(source / "pnpm-lock.yaml"))
            assert len(lock_documents) == 2
            original_documents = list(YAML(typ="safe").load_all(originals["pnpm-lock.yaml"]))
            assert lock_documents[0] == original_documents[0]
            original_prefix, _ = watch_witness._frontend_lock_document(originals["pnpm-lock.yaml"])
            assert (source / "pnpm-lock.yaml").read_bytes().startswith(original_prefix)
            lock = lock_documents[1]
            workspace = YAML(typ="safe").load(source / "pnpm-workspace.yaml")
            alias = "coordinator-watch-dependency"
            selected = alias in manifest["dependencies"]
            patch_path = workspace["patchedDependencies"]["minimatch@5.1.9"]
            patch = (source / patch_path).read_bytes().replace(b"\r\n", b"\n")
            digest = hashlib.sha256(patch).hexdigest()
            assert lock["patchedDependencies"]["minimatch@5.1.9"] == digest
            snapshot = f"minimatch@5.1.9(patch_hash={digest})"
            assert snapshot in lock["snapshots"]
            assert lock["snapshots"][snapshot]["dependencies"]["brace-expansion"] == "5.0.12"
            if selected:
                assert manifest["dependencies"][alias] == "npm:minimatch@5.1.9"
                assert lock["importers"]["frontend"]["dependencies"][alias] == {
                    "specifier": "npm:minimatch@5.1.9",
                    "version": snapshot,
                }
                assert b"@@ -18,2 +18,3 @@" in patch
                assert b"+minimatch.coordinatorWatchProbe = () => '" in patch
                assert (
                    b"coordinatorWatchProbe()" in (source / "frontend/vite.config.ts").read_bytes()
                )
        observations.append(selected)
        return ServiceRuntimeIdentity("a" * 64, "initial")

    def inspect_transition(
        _project: watch_witness.WatchProject,
        observed: Callable[[], bool],
        *,
        before: Mapping[str, ServiceRuntimeIdentity],
        **kwargs: object,
    ) -> dict[str, ServiceRuntimeIdentity]:
        inspect_fixture(observed, **kwargs)
        return {service: ServiceRuntimeIdentity("b" * 64, "installed") for service in before}

    monkeypatch.setattr(watch_witness, "_await_effect", inspect_fixture)
    monkeypatch.setattr(watch_witness, "_await_runtime_effect", inspect_transition)
    verify = (
        watch_witness._verify_backend_dependency_effect
        if service == "backend"
        else watch_witness._verify_frontend_dependency_effect
    )
    verify(FakeProject(source), FakeProcess())
    assert True in observations and observations[-1] is False
    assert all((source / name).read_bytes() == content for name, content in originals.items())
    assert not (source / "patches/ci-coordinator-watch-minimatch.patch").exists()


def test_backend_dependency_probe_requires_current_version_and_recreated_restoration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = Path(__file__).resolve().parents[2]
    names = (
        "backend/pyproject.toml",
        "backend/uv.lock",
        "backend/src/ci_coordinator/observability/health.py",
    )
    originals = {name: (repository / name).read_bytes() for name in names}
    for name, content in originals.items():
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    probe_status = "absent"
    effect_calls = 0
    transitions: list[dict[str, ServiceRuntimeIdentity]] = []

    def health_status(_project: object) -> str:
        return probe_status

    def await_effect(observed: Callable[[], object], **_kwargs: object) -> object:
        nonlocal probe_status, effect_calls
        probe_status = "absent" if effect_calls == 0 else "alive"
        result = observed()
        if effect_calls == 0:
            assert isinstance(result, ServiceRuntimeIdentity)
        else:
            assert result is True
        effect_calls += 1
        return result

    def await_runtime_effect(
        _project: object,
        observed: Callable[[], bool],
        *,
        before: Mapping[str, ServiceRuntimeIdentity],
        recreate: bool = False,
        **_kwargs: object,
    ) -> dict[str, ServiceRuntimeIdentity]:
        nonlocal probe_status
        assert recreate is True
        assert set(before) == {"backend"}
        if transitions:
            assert before == transitions[-1]
        phase = len(transitions)
        accepted = ("1.8.22", "absent")[phase]
        # Expected runtime observations are independent of the mutated TOML.
        # A stale installed version or a no-op dependency mutation must fail.
        for status in ("absent", "1.8.21", "1.8.22"):
            probe_status = status
            assert observed() is (status == accepted)
        result = {"backend": ServiceRuntimeIdentity(("b" if phase == 0 else "c") * 64, "new")}
        assert result["backend"].container_id != before["backend"].container_id
        transitions.append(result)
        return result

    monkeypatch.setattr(watch_witness, "_health_status", health_status)
    monkeypatch.setattr(watch_witness, "_await_effect", await_effect)
    monkeypatch.setattr(watch_witness, "_await_runtime_effect", await_runtime_effect)
    watch_witness._verify_backend_dependency_effect(FakeProject(tmp_path), FakeProcess())
    assert effect_calls == 2 and len(transitions) == 2
    assert all((tmp_path / name).read_bytes() == content for name, content in originals.items())


_PNPM_BOOTSTRAP = (
    b"---\nlockfileVersion: '9.0'\nimporters:\n  .:\n"
    b"    configDependencies: {}\n    packageManagerDependencies:\n"
    b"      pnpm: {specifier: 12.5.1, version: 12.5.1}\n"
    b"packages: {}\nsnapshots: {}\n"
)
_PNPM_PROJECT = (
    b"lockfileVersion: '9.0'\nimporters:\n  .: {}\n  frontend:\n"
    b"    dependencies:\n      example: {specifier: 1.0.0, version: 1.0.0}\n"
    b"packages:\n  example@1.0.0: {resolution: {integrity: sha512-example}}\n"
    b"snapshots:\n  example@1.0.0: {}\n"
)


def test_frontend_lock_document_preserves_bootstrap_bytes_and_exact_restoration(
    tmp_path: Path,
) -> None:
    original = _PNPM_BOOTSTRAP + b"---\n" + _PNPM_PROJECT
    prefix, project = watch_witness._frontend_lock_document(original)
    assert prefix == _PNPM_BOOTSTRAP + b"---\n"
    assert project == YAML(typ="safe").load(_PNPM_PROJECT)
    project["settings"] = {"autoInstallPeers": True}
    candidate = prefix + watch_witness._yaml_bytes(project)
    assert watch_witness._frontend_lock_document(candidate)[0] == prefix
    lock = tmp_path / "pnpm-lock.yaml"
    lock.write_bytes(original)
    with (
        pytest.raises(RuntimeError, match="fixture failure"),
        watch_witness._replaced(lock, candidate),
    ):
        assert lock.read_bytes() == candidate
        raise RuntimeError("fixture failure")
    assert lock.read_bytes() == original


@pytest.mark.parametrize(
    "source",
    [
        _PNPM_PROJECT,
        _PNPM_BOOTSTRAP,
        _PNPM_BOOTSTRAP + b"---\n" + _PNPM_PROJECT + b"---\n{}\n",
        b"---\n" + _PNPM_PROJECT + _PNPM_BOOTSTRAP,
        _PNPM_BOOTSTRAP + b"---\n" + _PNPM_PROJECT.replace(b"frontend:", b"other:"),
        _PNPM_BOOTSTRAP + b"---\n" + _PNPM_PROJECT + b"lockfileVersion: '9.0'\n",
        _PNPM_BOOTSTRAP + b"---\n" + _PNPM_PROJECT + b"aliases: [&shared {}, *shared]\n",
        _PNPM_BOOTSTRAP + b"---\n" + _PNPM_PROJECT + b"deep: " + b"[" * 129 + b"]" * 129,
        b"\xff" + _PNPM_BOOTSTRAP + b"---\n" + _PNPM_PROJECT,
        b"x" * (16 * 1024 * 1024 + 1),
    ],
    ids=[
        "project-only",
        "bootstrap-only",
        "extra-document",
        "reversed-documents",
        "missing-frontend",
        "duplicate-key",
        "aliases",
        "excessive-depth",
        "invalid-utf8",
        "excessive-bytes",
    ],
)
def test_frontend_lock_document_rejects_missing_ambiguous_or_unbounded_planes(
    source: bytes,
) -> None:
    with pytest.raises(watch_witness.WatchError):
        watch_witness._frontend_lock_document(source)


def _disabled_settings_input() -> dict[str, str]:
    return {
        "CI_COORDINATOR_RUNTIME_MODE": "disabled",
        "CI_COORDINATOR_BIND_HOST": "127.0.0.1",
        "CI_COORDINATOR_BIND_PORT": "3000",
        "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": "30",
    }


@pytest.mark.parametrize(
    "name", ["CI_COORDINATOR_IMAGE_WITNESS", "CI_COORDINATOR_ENTRYPOINT_WITNESS"]
)
def test_image_fixture_cannot_use_the_closed_runtime_settings_namespace(name: str) -> None:
    source = _disabled_settings_input()
    assert isinstance(admit_runtime_settings(source), DisabledRuntimeSettings)
    assert admit_runtime_settings({**source, name: "fixture"}) == RuntimeSettingsRejection(
        "invalid_setting_value", name
    )


@pytest.mark.parametrize("failed_phase", [None, "install", "restoration"])
def test_image_fixture_preserves_admission_and_restores_every_owned_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_phase: str | None
) -> None:
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / "source"
    names = (
        "docker/development/backend.Dockerfile",
        "frontend/Dockerfile.dev",
        "docker/development/secret-entrypoint.sh",
        "backend/src/ci_coordinator/observability/health.py",
        "frontend/vite.config.ts",
    )
    for name in names:
        (source / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(root / name, source / name)
    originals = {name: (source / name).read_bytes() for name in names}
    phases: list[str] = []
    installed = {
        service: ServiceRuntimeIdentity(service + "-installed", "installed")
        for service in ("backend", "frontend")
    }
    reads: list[str] = []

    class InitialIdentityProject(FakeProject):
        def service_runtime_identity(self, service: str) -> ServiceRuntimeIdentity:
            assert service not in reads, "installation must return its observed identity"
            reads.append(service)
            return super().service_runtime_identity(service)

    def transition(
        project: watch_witness.FeedbackProject,
        process: watch_witness.WatchProcess,
        *,
        before: Mapping[str, ServiceRuntimeIdentity],
        tokens: tuple[str, str] | None,
    ) -> dict[str, ServiceRuntimeIdentity]:
        assert project.repo_root == source and process.poll() is None
        assert set(before) == {"backend", "frontend"}
        phase = "restoration" if tokens is None else "install"
        phases.append(phase)
        if tokens is not None:
            image_env = (source / names[0]).read_text().splitlines()[-1].removeprefix("ENV ")
            image_name, image_value = image_env.split("=", 1)
            assert image_value == tokens[0]
            assert (source / names[1]).read_text().splitlines()[-1] == f"ENV {image_env}"
            entrypoint = (source / names[2]).read_text()
            added_export = next(
                line
                for line in entrypoint.splitlines()
                if line.startswith("export ") and tokens[1] in line
            ).removeprefix("export ")
            entrypoint_name, entrypoint_value = added_export.split("=", 1)
            assert entrypoint_value == tokens[1]
            baseline = _disabled_settings_input()
            assert admit_runtime_settings(
                {
                    **baseline,
                    image_name: image_value,
                    entrypoint_name: entrypoint_value,
                }
            ) == admit_runtime_settings(baseline)
            health = (source / names[3]).read_text()
            assert f'os.environ.get("{image_name}", "")' in health
            assert f'os.environ.get("{entrypoint_name}", "")' in health
            assert f'process.env["{image_name}"]' in (source / names[4]).read_text()
            assert (
                entrypoint.replace(f"export {added_export}\n\n", "").encode() == originals[names[2]]
            )
        else:
            assert before == installed
            assert all(
                (source / name).read_bytes() == content for name, content in originals.items()
            )
        if failed_phase == phase:
            raise watch_witness.WatchError(f"{phase} failed")
        return installed

    monkeypatch.setattr(watch_witness, "_await_image_transition", transition)
    if failed_phase is None:
        watch_witness._verify_image_and_entrypoint_effects(
            InitialIdentityProject(source), FakeProcess()
        )
    else:
        with pytest.raises(watch_witness.WatchError, match=f"{failed_phase} failed"):
            watch_witness._verify_image_and_entrypoint_effects(
                InitialIdentityProject(source), FakeProcess()
            )
    assert phases == (["install"] if failed_phase == "install" else ["install", "restoration"])
    assert all((source / name).read_bytes() == content for name, content in originals.items())


@pytest.mark.parametrize(
    ("restoring", "failed_check"),
    [
        (False, None),
        (True, None),
        (False, "health_image"),
        (False, "entrypoint"),
        (False, "frontend_header"),
        (False, "recreated_backend"),
        (False, "recreated_frontend"),
        (False, "uid_secret_boundary"),
        (False, "backend_absent"),
        (True, "health_restored"),
        (True, "frontend_header"),
        (True, "recreated_backend"),
        (True, "recreated_frontend"),
        (True, "uid_secret_boundary"),
    ],
)
def test_image_transition_reports_each_required_effect_without_disclosing_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, restoring: bool, failed_check: str | None
) -> None:
    project = FakeProject(tmp_path)
    before = {
        service: ServiceRuntimeIdentity(service, "old") for service in ("backend", "frontend")
    }
    image_token, entrypoint_token = "private-image-marker", "private-entrypoint-marker"
    calls: list[str] = []
    now = [0.0]

    def health(_project: watch_witness.FeedbackProject) -> str:
        calls.append("health")
        if failed_check == "backend_absent":
            raise ServiceAbsent("service absent during recreation")
        if restoring:
            return "unexpected" if failed_check == "health_restored" else "alive"
        image = "unexpected" if failed_check == "health_image" else image_token
        entrypoint = "unexpected" if failed_check == "entrypoint" else entrypoint_token
        return f"{image}:{entrypoint}"

    def http(_endpoint: str, _path: str) -> tuple[int, bytes, str]:
        calls.append("frontend_header")
        return (
            200,
            b"",
            "unexpected"
            if failed_check == "frontend_header"
            else ("" if restoring else image_token),
        )

    def runtime(service: str) -> ServiceRuntimeIdentity:
        calls.append(f"recreated_{service}")
        return (
            before[service]
            if failed_check == f"recreated_{service}"
            else ServiceRuntimeIdentity(f"{service}-new", "new")
        )

    def boundary(service: str, *, expected_uid: int, protected_path: str | None) -> None:
        assert service == "backend" and expected_uid == 65_532
        assert protected_path == "/run/ci-coordinator-secrets/runtime-dsn"
        calls.append("uid_secret_boundary")
        if failed_check == "uid_secret_boundary":
            raise ComposeError("private provider diagnostic")

    monkeypatch.setattr(watch_witness, "_health_status", health)
    monkeypatch.setattr(watch_witness, "_http", http)
    monkeypatch.setattr(project, "service_runtime_identity", runtime)
    monkeypatch.setattr(project, "assert_runtime_boundary", boundary)
    monkeypatch.setattr("scripts.dev_environment.watch_witness.time.monotonic", lambda: now[0])
    monkeypatch.setattr(
        "scripts.dev_environment.watch_witness.time.sleep", lambda _: now.__setitem__(0, 300.0)
    )
    tokens = None if restoring else (image_token, entrypoint_token)
    if failed_check is None:
        result = watch_witness._await_image_transition(
            project, FakeProcess(), before=before, tokens=tokens
        )
        assert result == {
            service: ServiceRuntimeIdentity(f"{service}-new", "new") for service in before
        }
        assert calls.count("recreated_backend") == calls.count("recreated_frontend") == 1
        assert "uid_secret_boundary" in calls and now[0] == 0
    else:
        with pytest.raises(watch_witness.WatchError) as caught:
            watch_witness._await_image_transition(
                project, FakeProcess(), before=before, tokens=tokens
            )
        message = str(caught.value)
        assert message.startswith("restoration:" if restoring else "install:")
        vector = json.loads(message.split("; observed=", 1)[1])
        if failed_check == "backend_absent":
            assert vector["health_image"] is None and vector["entrypoint"] is None
        else:
            assert vector[failed_check] is False
        assert not any(
            value in message for value in (image_token, entrypoint_token, "private provider")
        )
        assert now[0] == (0 if failed_check == "uid_secret_boundary" else 300)
    assert {"health", "frontend_header", "recreated_backend", "recreated_frontend"} <= set(calls)


class ModelProject(FakeProject):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.model = YAML(typ="safe").load(root / "compose.yaml")
        self.revision = 0
        self.reconcile_attempts = 0
        self.reconciliations: list[dict[str, object]] = []

    def validate(self) -> None:
        model = YAML(typ="safe").load(self.repo_root / "compose.yaml")
        if "coordinator-invalid-model-field" in model["services"]["frontend"]:
            raise ComposeError("invalid Compose model")

    def watch_invocation(self) -> ProviderInvocation:
        return ProviderInvocation(
            (
                "docker",
                "compose",
                "--file",
                str(self.repo_root / "compose.yaml"),
                "watch",
                "--no-up",
            ),
            self.repo_root,
            {"PATH": "/bin"},
        )

    def service_runtime_identity(self, service: str) -> ServiceRuntimeIdentity:
        return ServiceRuntimeIdentity(
            f"{service}-{self.revision}" if service == "frontend" else "backend",
            f"started-{self.revision}" if service == "frontend" else "started",
        )

    def reconcile(self) -> LocalEndpoints:
        self.reconcile_attempts += 1
        self.validate()
        self.model = YAML(typ="safe").load(self.repo_root / "compose.yaml")
        self.reconciliations.append(self.model)
        self.revision += 1
        published = self.model["services"]["frontend"]["ports"][0].split(":")[1]
        return LocalEndpoints(
            "http://127.0.0.1:3000", f"http://127.0.0.1:{published or '5173'}", "127.0.0.1:5432"
        )


@pytest.mark.parametrize("bad_effect", [False, True])
def test_model_fixture_restores_inputs_and_ordinary_runtime_after_validated_transition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_effect: bool
) -> None:
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / "source"
    (source / "frontend").mkdir(parents=True)
    for name in ("compose.yaml", "frontend/vite.config.ts"):
        shutil.copyfile(root / name, source / name)
    originals = {
        name: (source / name).read_bytes() for name in ("compose.yaml", "frontend/vite.config.ts")
    }
    project = ModelProject(source)
    preserved: list[int] = []

    def provider(command: str, args: Sequence[str], **kwargs: object) -> CommandResult:
        assert command == "docker" and kwargs["cwd"] == source
        if args[-2:] == ["config", "--quiet"]:
            candidate = YAML(typ="safe").load(source / "compose.yaml")
            assert candidate["services"]["frontend"]["coordinator-invalid-model-field"] is True
            return CommandResult(
                1,
                "",
                "services.frontend Additional property "
                "coordinator-invalid-model-field is not allowed",
            )
        assert args[0] == "inspect"
        return CommandResult(
            0, json.dumps(project.model["services"]["frontend"]["healthcheck"]["test"]), ""
        )

    def response(endpoint: str, path: str) -> tuple[int, bytes, str]:
        assert urlsplit(endpoint).hostname == "127.0.0.1" and path == "/"
        token = project.model["services"]["frontend"]["environment"].get(
            "CI_COORDINATOR_MODEL_WITNESS", ""
        )
        return 200, b"CI Coordinator", "wrong" if token and bad_effect else token

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(watch_witness, "spawn", provider)
    monkeypatch.setattr(watch_witness, "_http", response)
    if bad_effect:
        with pytest.raises(watch_witness.WatchError, match="environment and port"):
            watch_witness._verify_compose_model_effect(
                project,
                reconcile=project.reconcile,
                preservation_check=lambda: preserved.append(project.revision),
            )
    else:
        watch_witness._verify_compose_model_effect(
            project,
            reconcile=project.reconcile,
            preservation_check=lambda: preserved.append(project.revision),
        )
    assert len(project.reconciliations) == 2
    assert project.reconcile_attempts == 3
    assert preserved[0] == 0 and preserved[-1] == 2
    assert all((source / name).read_bytes() == content for name, content in originals.items())
    assert (
        "CI_COORDINATOR_MODEL_WITNESS" not in project.model["services"]["frontend"]["environment"]
    )


@pytest.mark.parametrize(
    "result",
    [
        CommandResult(0, "", ""),
        CommandResult(1, "", "Docker unavailable"),
        CommandResult(
            None, "", "coordinator-invalid-model-field not allowed", failure_kind="timeout"
        ),
    ],
)
def test_invalid_model_cannot_pass_on_generic_provider_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: CommandResult
) -> None:
    class BindingProject(FakeProject):
        def watch_invocation(self) -> ProviderInvocation:
            return ProviderInvocation(("docker", "compose", "watch", "--no-up"), self.repo_root, {})

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(watch_witness, "spawn", lambda *_args, **_kwargs: result)
    with pytest.raises(watch_witness.WatchError, match="schema rejection"):
        watch_witness._require_invalid_model(BindingProject(tmp_path))


@pytest.mark.parametrize(
    ("failure", "restore_fails"),
    [
        ("invalid-schema", False),
        ("invalid-reconcile", False),
        ("install-validate", False),
        ("install-reconcile", False),
        ("install-reconcile", True),
        ("install-health", True),
        ("restoration-reconcile", True),
    ],
)
def test_model_phase_diagnostics_preserve_primary_and_restoration_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, restore_fails: bool
) -> None:
    root = Path(__file__).resolve().parents[2]
    source = tmp_path / "source"
    (source / "frontend").mkdir(parents=True)
    names = ("compose.yaml", "frontend/vite.config.ts")
    for name in names:
        shutil.copyfile(root / name, source / name)
    originals = {name: (source / name).read_bytes() for name in names}
    project = ModelProject(source)
    primary = (
        ValueError("private parser input")
        if failure == "invalid-reconcile"
        else ComposeError("primary status 1; services=frontend=running/unhealthy/exit=0")
    )
    restoration = ComposeError("restoration status 1; services=backend=exited/none/exit=1")
    calls: list[str] = []
    original_validate, original_reconcile = project.validate, project.reconcile

    def candidate_frontend() -> dict[str, object]:
        model = YAML(typ="safe").load(source / "compose.yaml")
        return dict(model["services"]["frontend"])

    def installing() -> bool:
        return "CI_COORDINATOR_MODEL_WITNESS" in str(candidate_frontend()["environment"])

    def validate() -> None:
        if installing() and failure == "install-validate":
            raise primary
        original_validate()

    def invalid(_project: watch_witness.FeedbackProject) -> None:
        if failure == "invalid-schema":
            raise primary

    def reconcile() -> LocalEndpoints:
        if "coordinator-invalid-model-field" in candidate_frontend():
            calls.append("invalid-reconcile")
            if failure == "invalid-reconcile":
                raise primary
        elif installing():
            calls.append("install-reconcile")
            if failure == "install-reconcile":
                raise primary
        else:
            calls.append("restoration-reconcile")
            assert all(
                (source / name).read_bytes() == content for name, content in originals.items()
            )
            if restore_fails:
                raise restoration
        return original_reconcile()

    def health(_project: watch_witness.FeedbackProject) -> None:
        if installing() and failure == "install-health":
            raise primary

    def response(_endpoint: str, _path: str) -> tuple[int, bytes, str]:
        environment = project.model["services"]["frontend"]["environment"]
        return 200, b"", environment.get("CI_COORDINATOR_MODEL_WITNESS", "")

    monkeypatch.setattr(project, "validate", validate)
    monkeypatch.setattr(watch_witness, "_require_invalid_model", invalid)
    monkeypatch.setattr(watch_witness, "_await_ordinary_health", health)
    monkeypatch.setattr(watch_witness, "_http", response)
    monkeypatch.setattr(watch_witness, "_require_healthcheck_model", lambda *_args: None)
    with pytest.raises(watch_witness.ModelFeedbackError) as caught:
        watch_witness._verify_compose_model_effect(
            project, reconcile=reconcile, preservation_check=lambda: None
        )
    error = caught.value
    assert error.phase == failure
    assert error.primary_error is (restoration if failure == "restoration-reconcile" else primary)
    assert error.__cause__ is error.primary_error
    assert f"compose-model/{failure}:" in str(error)
    if restore_fails and failure != "restoration-reconcile":
        assert error.restoration_error is not None
        assert error.restoration_error.phase == "restoration-reconcile"
        assert error.restoration_error.primary_error is restoration
        assert "restoration_failure=compose-model/restoration-reconcile:" in str(error)
    else:
        assert error.restoration_error is None
    assert "private parser input" not in str(error)
    if failure.startswith("invalid-") or failure == "install-validate":
        assert "install-reconcile" not in calls and "restoration-reconcile" not in calls
    else:
        assert calls == ["invalid-reconcile", "install-reconcile", "restoration-reconcile"]
    assert all((source / name).read_bytes() == content for name, content in originals.items())


def test_material_cohort_cannot_skip_any_effect_or_preservation_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    identity = derive_instance_identity(source, state_home=tmp_path / "state")
    events: list[str] = []

    class Project(FakeProject):
        def validate(self) -> None:
            pass

    project = Project(source)

    def feedback(
        _project: watch_witness.FeedbackProject,
        *,
        effect_verifier: Callable[[watch_witness.WatchProcess], None],
        **kwargs: object,
    ) -> None:
        assert kwargs["identity"] is identity
        events.append("source")
        effect_verifier(FakeProcess())
        events.append("stopped")

    monkeypatch.setattr(watch_witness, "verify_feedback_effects", feedback)
    for name in (
        "_verify_frontend_compiler_effect",
        "_verify_backend_dependency_effect",
        "_verify_frontend_dependency_effect",
        "_verify_image_and_entrypoint_effects",
        "_verify_build_context_effect",
    ):

        def verify(*_args: object, _name: str = name, **_kwargs: object) -> None:
            events.append(_name)

        monkeypatch.setattr(watch_witness, name, verify)

    def model(
        _project: watch_witness.MaterialFeedbackProject,
        *,
        reconcile: Callable[[], LocalEndpoints],
        preservation_check: Callable[[], None],
    ) -> None:
        assert events[-2:] == ["stopped", "preserved"]
        events.append("model")
        reconcile()
        preservation_check()

    monkeypatch.setattr(watch_witness, "_verify_compose_model_effect", model)
    result = watch_witness.verify_material_feedback(
        project,
        identity=identity,
        browser_runtime_root=tmp_path,
        preservation_check=lambda: events.append("preserved"),
        reconcile=project.endpoints,
    )
    assert result == tuple(item.name for item in watch_witness.MATERIAL_INPUT_CLASSES)
    assert events.count("source") == 1 and events.count("stopped") == 1
    assert len([event for event in events if event.startswith("_verify_")]) == 5
    assert events[-2:] == ["model", "preserved"]

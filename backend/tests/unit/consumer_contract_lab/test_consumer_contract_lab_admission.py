from __future__ import annotations

import contextlib
import copy
import json
import os
import selectors
import signal
import struct
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import replace
from pathlib import Path
from types import FrameType, ModuleType
from typing import Any, Literal, cast

import pytest
from scripts.dev_environment.environment import ManagedProcessBorrow

from ci_coordinator.consumer_contract_lab import bootstrap, git_source, process
from ci_coordinator.consumer_contract_lab.codec import (
    ConsumerLabAdmissionError,
    parse_consumer_lab_profile,
    parse_scenario_corpus,
)
from ci_coordinator.consumer_contract_lab.model import (
    ChangeStatus,
    ConsumerLabProfile,
    ConsumerLabScenario,
    ConsumerRepository,
    EventName,
    ExpectedMode,
    ExpectedOutcome,
    FileBinding,
    GitSourceSnapshot,
    ManifestEntry,
    ScenarioChange,
    ScenarioCorpus,
    SourceKind,
)
from ci_coordinator.consumer_contract_lab.process import LifetimeScope, run_bounded
from ci_coordinator.kernel import canonical_json

_COMMIT = "a" * 40
_DIGEST = "b" * 64
_REPOSITORY = ConsumerRepository(1, 2, "example-org", "consumer", "master")
_POLICY = FileBinding("dynamic-ci-policy.v1.json", _DIGEST)
_CORPUS = FileBinding("local-lab-scenarios.v1.json", "c" * 64)
_CHANGE = ScenarioChange("src/service.py", "modified", None)
_OUTCOME = ExpectedOutcome("selected", ("test",))
_SCENARIO = ConsumerLabScenario("source-change", "pull_request", (_CHANGE,), _OUTCOME)


@pytest.mark.parametrize(
    "owner,cut",
    [
        pytest.param(owner, cut, id=f"{name}-{cut}")
        for name, owner in (("bootstrap", bootstrap), ("verified-image", process))
        for cut in (
            "positive",
            "accept-term",
            "construct",
            "register",
            "register-term",
            "register-close",
            "close",
            "exit-error",
            "exit-term",
            "caught-work-term",
            "caught-close-term",
            "unrelated-exit-term",
        )
    ]
    + [
        pytest.param("image-entry", cut, id=f"image-entry-{cut}")
        for cut in ("positive", "accept-term")
    ],
)
def test_native_capture_acceptance_setup_and_close_keep_owned_drain(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    managed_consumer_lifetime: tuple[LifetimeScope, int, int],
    owner: ModuleType | str,
    cut: str,
) -> None:
    outer, _, _ = managed_consumer_lifetime
    ready = tmp_path / "capture-ready"
    positive = cut in {"positive", "close", "caught-close-term"}
    source = f"from pathlib import Path\nimport time\nPath({str(ready)!r}).touch()\n" + (
        "print('accepted')\n" if positive else "time.sleep(60)\n"
    )
    image_source = tmp_path / "image"
    package = image_source / "ci_coordinator"
    if owner == "image-entry":
        lab = package / "consumer_contract_lab"
        lab.mkdir(parents=True)
        (package / "__init__.py").write_text("")
        (lab / "__init__.py").write_text("")
        for module in (bootstrap, process):
            assert module.__file__ is not None
            origin = Path(module.__file__)
            (lab / origin.name).write_bytes(origin.read_bytes())
        (lab / "cli.py").write_text(
            "def main(arguments):\n"
            + "\n".join("    " + line for line in source.splitlines())
            + "\n    import os\n"
            + "    assert 'COVERAGE_PROCESS_CONFIG' not in os.environ\n"
            + "    assert 'COVERAGE_PROCESS_START' not in os.environ\n"
            + "\n    return 7\n"
        )
    children: list[subprocess.Popen[bytes]] = []
    cancellation = bootstrap.LifetimeCancelled(signal.SIGTERM)
    first = cancellation if "term" in cut and cut != "exit-term" else ValueError("setup cut")
    secondary = RuntimeError("secondary close cut")
    original_popen = subprocess.Popen
    original_selector = selectors.DefaultSelector
    signal_times: list[float] = []
    exit_cuts: list[BaseException] = []
    recoveries: list[str] = []
    work_injected = False
    previous_profile = sys.getprofile()

    def signal_now() -> None:
        signal_times.append(time.monotonic())
        os.kill(os.getpid(), signal.SIGTERM)
        first_received = received.signal_at
        os.kill(os.getpid(), signal.SIGTERM)
        assert received.signal_at == first_received

    def exit_cut(
        frame: FrameType,
        event: Literal["call", "return", "c_call", "c_return", "c_exception"],
        argument: object,
    ) -> None:
        if previous_profile is not None:
            previous_profile(frame, event, argument)
        if (
            not exit_cuts
            and cut in {"exit-error", "exit-term"}
            and event == "call"
            and frame.f_code is contextlib._GeneratorContextManager.__exit__.__code__
            and frame.f_locals.get("value") is first
        ):
            assert ready.exists() and len(children) == 1
            exit_cuts.append(first)
            if cut == "exit-term":
                signal_now()

    @contextmanager
    def unrelated_exit() -> Iterator[None]:
        try:
            yield
        finally:
            signal_now()

    def work_cut() -> None:
        nonlocal work_injected
        if work_injected:
            return
        work_injected = True
        if cut == "caught-work-term":
            try:
                raise ValueError("caught while work is still running")
            except ValueError:
                signal_now()
                assert received.stop_requested()
                recoveries.append(cut)
        if cut == "unrelated-exit-term":
            handled = ValueError("unrelated context exit")
            try:
                with unrelated_exit():
                    raise handled
            except ValueError as error:
                assert error is handled and received.stop_requested()
                recoveries.append(cut)
        if cut == "register-term":
            signal_now()
            pytest.fail("ordinary work did not interrupt")
        if cut in {"register", "register-close", "exit-error", "exit-term"}:
            raise first

    def accepted(*args: Any, **kwargs: Any) -> subprocess.Popen[bytes]:
        child = original_popen(*args, **kwargs)
        children.append(child)
        deadline = time.monotonic() + 5
        while not ready.exists():
            assert child.poll() is None or ready.exists()
            assert time.monotonic() < deadline, "real child did not reach the acceptance barrier"
            time.sleep(0.01)
        if cut == "accept-term":
            signal_now()
        return child

    def selector() -> selectors.BaseSelector:
        if cut == "construct":
            raise first
        instance = original_selector()
        register, close = instance.register, instance.close

        def register_cut(*args: Any, **kwargs: Any) -> selectors.SelectorKey:
            work_cut()
            return register(*args, **kwargs)

        def close_cut() -> None:
            close()
            if cut == "caught-close-term":
                signal_now()
            if cut in {"register-close", "close"}:
                raise secondary

        monkeypatch.setattr(instance, "register", register_cut)
        monkeypatch.setattr(instance, "close", close_cut)
        return instance

    monkeypatch.setattr(subprocess, "Popen", accepted)
    monkeypatch.setattr(selectors, "DefaultSelector", selector)
    monkeypatch.setattr(bootstrap, "LifetimeCancelled", lambda _number=0: cancellation)
    try:
        with bootstrap.lifetime_invocation(("-c", source), timeout_seconds=10) as invocation:
            lifetime_owner = bootstrap if owner == "image-entry" else cast(ModuleType, owner)
            received = lifetime_owner._decode_lifetime(invocation.arguments[-1])
            previous = signal.signal(signal.SIGTERM, received.signal)
            try:
                with bootstrap.borrowed_lifetime(received):
                    pending_before = bootstrap._DEFERRED_CANCELLATION.get()
                    mode_before = bootstrap._INTERRUPTIBLE.get()

                    def invoke() -> tuple[bytes, int | None]:
                        if owner == "image-entry":
                            with monkeypatch.context() as image_environment:
                                image_environment.delenv("COVERAGE_PROCESS_CONFIG", raising=False)
                                image_environment.delenv("COVERAGE_PROCESS_START", raising=False)
                                status = bootstrap._run_exact_image(
                                    source_root=image_source,
                                    package_root=package,
                                    cache_root=tmp_path / "cache",
                                    coordinator_root=tmp_path,
                                    coordinator_commit=_COMMIT,
                                    target_root=tmp_path,
                                    profile="fixture.json",
                                    output="-",
                                )
                            return b"", status
                        if owner is bootstrap:
                            stdout, _stderr, status = bootstrap._capture_bounded(
                                (sys.executable, "-c", source),
                                cwd=tmp_path,
                                env=dict(os.environ),
                                stdout_limit=4096,
                            )
                            return stdout, status
                        result = process.run_bounded(
                            sys.executable,
                            ("-c", source),
                            cwd=tmp_path,
                            env=dict(os.environ),
                            max_output_bytes=4096,
                            timeout_seconds=5,
                        )
                        return result.stdout, result.status

                    sys.setprofile(exit_cut)
                    try:
                        if cut == "positive":
                            assert invoke() == (
                                (b"", 7) if owner == "image-entry" else (b"accepted\n", 0)
                            )
                        elif cut == "caught-close-term":
                            try:
                                raise ValueError("ambient already-handled error")
                            except ValueError:
                                with pytest.raises(type(cancellation)) as caught_cancel:
                                    invoke()
                                assert caught_cancel.value is cancellation
                        else:
                            expected = secondary if cut == "close" else first
                            with pytest.raises(type(expected)) as caught:
                                invoke()
                            assert caught.value is expected
                    finally:
                        sys.setprofile(previous_profile)
                    assert exit_cuts == ([first] if cut in {"exit-error", "exit-term"} else [])
                    assert recoveries == (
                        [cut] if cut in {"caught-work-term", "unrelated-exit-term"} else []
                    )
                    assert bootstrap._DEFERRED_CANCELLATION.get() is pending_before
                    assert bootstrap._INTERRUPTIBLE.get() is mode_before
                    assert sys.getprofile() is previous_profile
                    if signal_times:
                        observed = received.signal_at
                        assert observed is not None and signal_times[0] <= observed
                        assert received.cancellation_deadline == observed + received.grace
                    assert received.inherited_fds == outer.inherited_fds
            finally:
                signal.signal(signal.SIGTERM, previous)
            assert bootstrap.current_lifetime() is outer
        assert len(children) == 1
        child = children[0]
        assert child.poll() is not None, "accepted child was not reaped"
        with pytest.raises(ProcessLookupError):
            os.killpg(child.pid, 0)
        assert all(
            stream.closed
            for stream in (child.stdin, child.stdout, child.stderr)
            if stream is not None
        )
    finally:
        sys.setprofile(previous_profile)
        for child in children:
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            child.wait(timeout=2)


@pytest.mark.parametrize("owner", [bootstrap, process], ids=["bootstrap", "verified-image"])
@pytest.mark.parametrize(
    "operand",
    [
        "version",
        "deadline",
        "grace",
        "direction",
        "pipe-kind",
        "lease-fd",
        "lease-access",
        "device",
        "inode",
        "duplicate-fd",
        "unknown-field",
        "duplicate-field",
    ],
)
def test_lifetime_decoders_reject_each_operand_after_a_valid_positive(
    managed_consumer_lifetime: tuple[LifetimeScope, int, int],
    owner: ModuleType,
    operand: str,
) -> None:
    scope, _, descriptor = managed_consumer_lifetime
    reader, writer = os.pipe()
    readonly: int | None = None
    try:
        os.set_blocking(reader, False)
        os.set_blocking(writer, False)
        identity = os.fstat(descriptor)
        payload = {
            "version": 1,
            "deadline": time.monotonic() + 10,
            "stopFd": reader,
            "stopGrace": 1,
            "leases": [{"fd": descriptor, "device": identity.st_dev, "inode": identity.st_ino}],
        }
        admitted = owner._decode_lifetime(json.dumps(payload))
        assert admitted.inherited_fds == (descriptor,)
        assert admitted.reader == reader
        assert not admitted.stop_requested()
        invalid = copy.deepcopy(payload)
        leases = cast(list[dict[str, int]], invalid["leases"])
        if operand == "version":
            invalid["version"] = 2
        elif operand == "deadline":
            invalid["deadline"] = float("nan")
        elif operand == "grace":
            invalid["stopGrace"] = 2
        elif operand == "direction":
            invalid["stopFd"] = writer
        elif operand == "pipe-kind":
            invalid["stopFd"] = descriptor
        elif operand == "lease-fd":
            leases[0]["fd"] = reader
        elif operand == "lease-access":
            readonly = os.open(cast(ManagedProcessBorrow, scope)._leases[-1].lock, os.O_RDONLY)
            leases[0]["fd"] = readonly
        elif operand in {"device", "inode"}:
            leases[0][operand] += 1
        elif operand == "duplicate-fd":
            leases.append(leases[0].copy())
        elif operand == "unknown-field":
            invalid["unexpected"] = 1
        encoded = json.dumps(invalid)
        if operand == "duplicate-field":
            encoded = '{"version":1,' + encoded[1:]
        with pytest.raises(ValueError):
            owner._decode_lifetime(encoded)
        assert descriptor in scope.inherited_fds
        os.fstat(descriptor)
    finally:
        os.close(reader)
        os.close(writer)
        if readonly is not None:
            os.close(readonly)


@pytest.mark.parametrize("owner", [bootstrap, process], ids=["bootstrap", "verified-image"])
@pytest.mark.parametrize("cut", ["positive", "partial", "eof", "expired", "repeat"])
def test_first_stop_frame_is_not_a_renewable_or_multicast_grant(
    managed_consumer_lifetime: tuple[LifetimeScope, int, int],
    owner: ModuleType,
    cut: str,
) -> None:
    _, _, descriptor = managed_consumer_lifetime
    reader, writer = os.pipe()
    try:
        os.set_blocking(reader, False)
        identity = os.fstat(descriptor)
        admitted = owner._decode_lifetime(
            json.dumps(
                {
                    "version": 1,
                    "deadline": time.monotonic() + 10,
                    "stopFd": reader,
                    "stopGrace": 1,
                    "leases": [
                        {"fd": descriptor, "device": identity.st_dev, "inode": identity.st_ino}
                    ],
                }
            )
        )
        assert not admitted.stop_requested()
        force = time.monotonic() + (0.8 if cut != "expired" else -1)
        if cut == "eof":
            os.close(writer)
            writer = -1
        else:
            os.write(writer, b"CF" if cut == "partial" else struct.pack("!4sd", b"CF1:", force))
        assert admitted.stop_requested()
        if cut in {"partial", "eof"}:
            assert admitted.cancellation_deadline == 0
        else:
            assert admitted.cancellation_deadline == force
            schedule = owner._StopSchedule(admitted, None, admitted.deadline)
            first = schedule.bounds()
            assert first[0] <= first[1] <= max(force, time.monotonic())
            assert schedule.bounds() == first
            if cut == "repeat":
                os.write(writer, struct.pack("!4sd", b"CF1:", time.monotonic() + 100))
                assert admitted.cancellation_deadline == 0
                assert admitted.first_force == force
                assert schedule.bounds()[1] <= first[1]
    finally:
        os.close(reader)
        if writer >= 0:
            os.close(writer)


@pytest.mark.parametrize("owner", ["bootstrap", "process"])
@pytest.mark.parametrize(
    "mode,code",
    [
        ("return", 7),
        ("exit", 7),
        ("exception", 1),
        ("exit-zero-stop", 143),
    ],
)
def test_native_lifetime_entry_preserves_primary_outcome_and_restores_scope(
    managed_consumer_lifetime: tuple[LifetimeScope, int, int],
    tmp_path: Path,
    owner: str,
    mode: str,
    code: int,
) -> None:
    source_root = Path(__file__).resolve().parents[4] / "backend/src"
    program = f"""
import json,os,signal,sys
from ci_coordinator.consumer_contract_lab import {owner} as owner
original=sys.argv
payload=json.loads(sys.argv[-1])
descriptors=[payload['stopFd'],*[row['fd'] for row in payload['leases']]]
def main():
    assert sys.argv[1]=={mode!r}
    assert owner.current_lifetime() is not None
    print('body',flush=True)
    if {mode!r}=='exception': raise ValueError('primary-probe-error')
    if {mode!r}=='exit': raise SystemExit(7)
    if {mode!r}=='exit-zero-stop':
        try: os.kill(os.getpid(),signal.SIGTERM)
        except owner.LifetimeCancelled: pass
        raise SystemExit(0)
    return 7
try:
    status=owner.managed_lifetime_entrypoint(main)
finally:
    closed=[]
    for fd in descriptors:
        try: os.fstat(fd); closed.append(False)
        except OSError: closed.append(True)
    print(json.dumps({{'restored':sys.argv is original,'empty':owner.current_lifetime() is None,
                      'closed':closed}}),flush=True)
raise SystemExit(status)
"""
    with process.lifetime_invocation(("-c", program, mode), timeout_seconds=10) as invocation:
        result = run_bounded(
            sys.executable,
            invocation.arguments,
            cwd=tmp_path,
            max_output_bytes=65_536,
            timeout_seconds=10,
            env={"PYTHONPATH": str(source_root)},
            inherited_fds=invocation.inherited_fds,
            cancellation_fd=invocation.cancellation_fd,
            execution_deadline=invocation.deadline,
        )
    assert result.status == code and result.error is None, result
    lines = result.stdout.decode().splitlines()
    assert lines[0] == "body"
    terminal = json.loads(lines[1])
    assert terminal["restored"] is True and terminal["empty"] is True
    assert terminal["closed"] and all(value is True for value in terminal["closed"])
    assert (b"primary-probe-error" in result.stderr) is (mode == "exception")
    assert result.process_group_quiescent is True


@pytest.mark.parametrize("managed", [False, True], ids=["public", "managed"])
def test_bootstrap_receiver_admission_is_conditional_on_managed_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    managed_consumer_lifetime: tuple[LifetimeScope, int, int],
    managed: bool,
) -> None:
    scope, _, _ = managed_consumer_lifetime
    actual_scope = bootstrap.current_lifetime
    assert actual_scope() is scope
    identities = [(fd, os.fstat(fd)) for fd in scope.inherited_fds]
    coordinator = tmp_path / "coordinator"
    source = coordinator / "backend/src"
    package = source / "ci_coordinator"
    receiver = package / "consumer_contract_lab/process.py"
    receiver.parent.mkdir(parents=True)
    capable = (
        "LAB_LIFETIME_PROTOCOL: int = 1\n"
        "def managed_lifetime_entrypoint(main):\n"
        "    return main()\n"
    )
    receiver.write_text(capable, encoding="ascii")
    target = tmp_path / "target"
    cache = tmp_path / "cache"
    target.mkdir()
    cache.mkdir()
    output = tmp_path / "receipt.json"
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []
    admissions: list[Path] = []
    waits: list[float] = []

    class ImageProcess:
        def wait(self, *, timeout: float) -> int:
            waits.append(timeout)
            return 7

    image = ImageProcess()
    require_receiver = bootstrap._require_lifetime_receiver

    def admit(root: Path) -> None:
        admissions.append(root)
        require_receiver(root)

    def spawn(command: tuple[str, ...], **kwargs: object) -> ImageProcess:
        calls.append((command, kwargs))
        return image

    def wait_managed(child: ImageProcess, schedule: bootstrap._StopSchedule) -> int:
        assert child is image and schedule.scope is scope
        return 7

    def invoke() -> int:
        return bootstrap._run_exact_image(
            source_root=source,
            package_root=package,
            cache_root=cache,
            coordinator_root=coordinator,
            coordinator_commit=_COMMIT,
            target_root=target,
            profile="profile.json",
            output=str(output),
        )

    launcher = (
        "import sys;source=sys.argv.pop(1);sys.path.insert(0,source);"
        "from ci_coordinator.consumer_contract_lab.process import managed_lifetime_entrypoint;"
        "raise SystemExit(managed_lifetime_entrypoint(lambda: "
        "__import__('ci_coordinator.consumer_contract_lab.cli',fromlist=['main']).main(sys.argv[1:])))"
        if managed
        else (
            "import sys;source=sys.argv.pop(1);sys.path.insert(0,source);"
            "from ci_coordinator.consumer_contract_lab.cli import main;"
            "raise SystemExit(main(sys.argv[1:]))"
        )
    )
    expected = (
        sys.executable,
        "-I",
        "-B",
        "-X",
        f"pycache_prefix={cache}",
        "-c",
        launcher,
        str(source),
        "--coordinator-root",
        str(coordinator),
        "--coordinator-commit",
        _COMMIT,
        "--coordinator-package-root",
        str(package),
        "--target-root",
        str(target),
        "--profile",
        "profile.json",
        "--output",
        str(output),
    )
    with monkeypatch.context() as patch:
        # Model dispatch only; the real enclosing ContextVar and lease owners remain intact.
        patch.setattr(subprocess, "Popen", spawn)
        patch.setattr(bootstrap, "_wait_managed", wait_managed)
        patch.setattr(bootstrap, "_require_lifetime_receiver", admit)
        patch.setattr(bootstrap, "current_lifetime", lambda: scope if managed else None)
        assert invoke() == 7
        assert len(calls) == 1
        command, options = calls[0]
        if managed:
            assert command[:-2] == expected
            assert command[-2] == "--managed-lab-lifetime"
            reader = json.loads(command[-1])["stopFd"]
            assert options == {
                "cwd": target,
                "start_new_session": True,
                "pass_fds": (*scope.inherited_fds, reader),
            }
        else:
            assert calls == [(expected, {"cwd": target, "start_new_session": True})]
        assert admissions == ([package] if managed else [])
        assert waits == ([] if managed else [180])
        calls.clear()
        admissions.clear()
        waits.clear()
        receiver.write_text(
            capable.replace("managed_lifetime_entrypoint", "public_entrypoint"),
            encoding="ascii",
        )
        if managed:
            with pytest.raises(
                bootstrap.ConsumerLabBootstrapError,
                match=r"\Ahistorical image has no managed lifetime receiver\Z",
            ):
                invoke()
            assert calls == [] and waits == [] and admissions == [package]
        else:
            assert invoke() == 7
            assert calls == [(expected, {"cwd": target, "start_new_session": True})]
            assert waits == [180] and admissions == []
        assert actual_scope() is scope
    assert bootstrap.current_lifetime() is scope
    assert not output.exists()
    assert [(fd, os.fstat(fd)) for fd in scope.inherited_fds] == identities


@pytest.mark.parametrize("explicit", [False, True], ids=["sys-argv", "explicit-argv"])
@pytest.mark.parametrize("outcome", ["return", "exit", "exception"])
def test_public_bootstrap_entry_preserves_argv_terminal_and_enclosing_scope(
    monkeypatch: pytest.MonkeyPatch,
    managed_consumer_lifetime: tuple[LifetimeScope, int, int],
    capsys: pytest.CaptureFixture[str],
    explicit: bool,
    outcome: str,
) -> None:
    scope, _, _ = managed_consumer_lifetime
    identities = [(fd, os.fstat(fd)) for fd in scope.inherited_fds]
    arguments = (
        "--coordinator-root",
        "/coordinator",
        "--target-root",
        "/target",
        "--profile",
        "profile.json",
        "--output",
        "/receipt.json",
    )
    original = ["caller", "host-argument"] if explicit else ["consumer-lab", *arguments]
    terminal = {"return": None, "exit": SystemExit(7), "exception": ValueError("public-primary")}[
        outcome
    ]
    calls: list[tuple[str, ...]] = []

    def body() -> int:
        calls.append(tuple(sys.argv))
        assert vars(bootstrap._parser().parse_args()) == {
            "coordinator_root": "/coordinator",
            "target_root": "/target",
            "profile": "profile.json",
            "output": "/receipt.json",
        }
        assert bootstrap.current_lifetime() is scope
        if terminal is not None:
            raise terminal
        return 7

    with monkeypatch.context() as patch:
        patch.setattr(sys, "argv", original)
        patch.setattr(bootstrap, "_main", body)
        if terminal is None:
            assert bootstrap.main(arguments if explicit else None) == 7
        else:
            with pytest.raises(type(terminal)) as raised:
                bootstrap.main(arguments if explicit else None)
            assert raised.value is terminal
        assert sys.argv is original
        assert calls == [(original[0], *arguments)]
    assert bootstrap.current_lifetime() is scope
    assert [(fd, os.fstat(fd)) for fd in scope.inherited_fds] == identities
    assert capsys.readouterr() == ("", "")


_PROFILE = ConsumerLabProfile(
    "native-consumer",
    _COMMIT,
    _REPOSITORY,
    ".github/workflows/full-check.yml",
    ("pull_request",),
    ".ci-coordinator",
    _POLICY,
    _CORPUS,
    (),
)
_SCENARIO_CORPUS = ScenarioCorpus("native-consumer", (_SCENARIO,))

type _Factory = Callable[[], object]
type _JsonPath = tuple[str | int, ...]


def _invalid_model_cases() -> tuple[tuple[str, _Factory], ...]:
    many_bindings = tuple(FileBinding(f"inputs/{index}.json", _DIGEST) for index in range(33))
    many_changes = (_CHANGE,) * 1_001
    many_scenarios = (_SCENARIO,) * 129
    many_jobs = tuple(f"job-{index}" for index in range(65))
    return (
        ("binding-path", lambda: FileBinding("../escape", _DIGEST)),
        ("binding-digest", lambda: FileBinding("input.json", "B" * 64)),
        ("installation-type", lambda: replace(_REPOSITORY, installation_id=True)),
        ("repository-id-bound", lambda: replace(_REPOSITORY, repository_id=0)),
        ("owner-shape", lambda: replace(_REPOSITORY, owner="-invalid")),
        ("repository-shape", lambda: replace(_REPOSITORY, name="")),
        ("default-branch-text", lambda: replace(_REPOSITORY, default_branch="\x00")),
        ("default-branch-git", lambda: replace(_REPOSITORY, default_branch="HEAD")),
        ("profile-id", lambda: replace(_PROFILE, profile_id="INVALID")),
        ("coordinator-commit", lambda: replace(_PROFILE, expected_coordinator_commit="A" * 40)),
        (
            "repository-type",
            lambda: replace(_PROFILE, repository=cast(ConsumerRepository, object())),
        ),
        ("workflow-path", lambda: replace(_PROFILE, workflow_path="full-check.yml")),
        (
            "event-surface-type",
            lambda: replace(
                _PROFILE,
                event_surface=cast(tuple[EventName, ...], ["pull_request"]),
            ),
        ),
        ("event-surface-empty", lambda: replace(_PROFILE, event_surface=())),
        (
            "event-surface-duplicate",
            lambda: replace(_PROFILE, event_surface=("pull_request", "pull_request")),
        ),
        (
            "event-surface-value",
            lambda: replace(
                _PROFILE,
                event_surface=(cast(EventName, "workflow_dispatch"),),
            ),
        ),
        (
            "artifacts-directory",
            lambda: replace(_PROFILE, target_artifacts_directory="../artifacts"),
        ),
        (
            "dynamic-policy-type",
            lambda: replace(_PROFILE, dynamic_policy=cast(FileBinding, object())),
        ),
        (
            "scenario-corpus-type",
            lambda: replace(_PROFILE, scenario_corpus=cast(FileBinding, object())),
        ),
        (
            "target-bindings-type",
            lambda: replace(
                _PROFILE,
                target_bindings=cast(tuple[FileBinding, ...], []),
            ),
        ),
        ("target-bindings-bound", lambda: replace(_PROFILE, target_bindings=many_bindings)),
        (
            "target-bindings-canonical",
            lambda: replace(
                _PROFILE,
                target_bindings=(
                    FileBinding("inputs/z.json", _DIGEST),
                    FileBinding("inputs/a.json", _DIGEST),
                ),
            ),
        ),
        ("target-bindings-duplicate", lambda: replace(_PROFILE, target_bindings=(_POLICY,))),
        ("change-path", lambda: replace(_CHANGE, path="../service.py")),
        (
            "change-status",
            lambda: replace(_CHANGE, status=cast(ChangeStatus, "deleted")),
        ),
        ("previous-path", lambda: replace(_CHANGE, previous_path="../old.py")),
        (
            "renamed-previous-path",
            lambda: ScenarioChange("src/service.py", "renamed", None),
        ),
        (
            "modified-previous-path",
            lambda: replace(_CHANGE, previous_path="src/old.py"),
        ),
        (
            "outcome-mode",
            lambda: replace(_OUTCOME, mode=cast(ExpectedMode, "unknown")),
        ),
        (
            "selected-jobs-type",
            lambda: replace(_OUTCOME, selected_jobs=cast(tuple[str, ...], ["test"])),
        ),
        ("selected-jobs-bound", lambda: replace(_OUTCOME, selected_jobs=many_jobs)),
        ("selected-job-shape", lambda: replace(_OUTCOME, selected_jobs=("invalid job",))),
        ("selected-job-order", lambda: replace(_OUTCOME, selected_jobs=("test", "lint"))),
        ("selected-mode-empty", lambda: replace(_OUTCOME, selected_jobs=())),
        ("fallback-mode-nonempty", lambda: ExpectedOutcome("fallback", ("test",))),
        ("scenario-id", lambda: replace(_SCENARIO, scenario_id="INVALID")),
        (
            "scenario-event",
            lambda: replace(_SCENARIO, event_name=cast(EventName, "workflow_dispatch")),
        ),
        (
            "scenario-changes-type",
            lambda: replace(
                _SCENARIO,
                changes=cast(tuple[ScenarioChange, ...], []),
            ),
        ),
        ("scenario-changes-bound", lambda: replace(_SCENARIO, changes=many_changes)),
        (
            "scenario-change-type",
            lambda: replace(
                _SCENARIO,
                changes=(cast(ScenarioChange, object()),),
            ),
        ),
        ("scenario-changes-duplicate", lambda: replace(_SCENARIO, changes=(_CHANGE, _CHANGE))),
        (
            "scenario-outcome-type",
            lambda: replace(
                _SCENARIO,
                expected_outcome=cast(ExpectedOutcome, object()),
            ),
        ),
        ("corpus-id", lambda: replace(_SCENARIO_CORPUS, profile_id="INVALID")),
        (
            "corpus-scenarios-type",
            lambda: replace(
                _SCENARIO_CORPUS,
                scenarios=cast(tuple[ConsumerLabScenario, ...], []),
            ),
        ),
        ("corpus-scenarios-empty", lambda: replace(_SCENARIO_CORPUS, scenarios=())),
        ("corpus-scenarios-bound", lambda: replace(_SCENARIO_CORPUS, scenarios=many_scenarios)),
        (
            "corpus-scenario-type",
            lambda: replace(
                _SCENARIO_CORPUS,
                scenarios=(cast(ConsumerLabScenario, object()),),
            ),
        ),
        (
            "corpus-scenarios-duplicate",
            lambda: replace(_SCENARIO_CORPUS, scenarios=(_SCENARIO, _SCENARIO)),
        ),
        ("snapshot-head", lambda: GitSourceSnapshot("A" * 40, (), "commit")),
        (
            "snapshot-paths-type",
            lambda: replace(
                GitSourceSnapshot(_COMMIT, (), "commit"),
                dirty_paths=cast(tuple[str, ...], []),
            ),
        ),
        (
            "snapshot-paths-canonical",
            lambda: GitSourceSnapshot(_COMMIT, ("b.py", "a.py"), "worktree"),
        ),
        ("snapshot-paths-safe", lambda: GitSourceSnapshot(_COMMIT, ("../a.py",), "worktree")),
        (
            "snapshot-kind",
            lambda: GitSourceSnapshot(_COMMIT, (), cast(SourceKind, "unknown")),
        ),
        ("snapshot-commit-dirty", lambda: GitSourceSnapshot(_COMMIT, ("a.py",), "commit")),
        ("snapshot-worktree-clean", lambda: GitSourceSnapshot(_COMMIT, (), "worktree")),
        ("manifest-path", lambda: ManifestEntry("../input", _DIGEST, 1)),
        ("manifest-digest", lambda: ManifestEntry("input", "B" * 64, 1)),
        ("manifest-size-type", lambda: ManifestEntry("input", _DIGEST, True)),
        ("manifest-size-negative", lambda: ManifestEntry("input", _DIGEST, -1)),
    )


_INVALID_MODEL_CASES = _invalid_model_cases()


@pytest.mark.parametrize(
    "factory",
    [factory for _, factory in _INVALID_MODEL_CASES],
    ids=[case_id for case_id, _ in _INVALID_MODEL_CASES],
)
def test_consumer_contract_models_reject_values_outside_the_finite_language(
    factory: _Factory,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        factory()


@pytest.mark.parametrize(
    ("kind", "path", "value"),
    [
        ("profile", ("schemaVersion",), "unsupported"),
        ("profile", ("repository", "installationId"), "1"),
        ("profile", ("eventSurface",), "pull_request"),
        ("profile", ("eventSurface", 0), 1),
        ("corpus", ("schemaVersion",), "unsupported"),
        ("corpus", ("scenarios",), {}),
        ("corpus", ("scenarios", 0, "eventName"), "workflow_dispatch"),
        ("corpus", ("scenarios", 0, "expectedOutcome", "mode"), "unknown"),
        ("corpus", ("scenarios", 0, "changes", 0, "status"), "deleted"),
    ],
)
def test_consumer_contract_codecs_reject_structural_and_algebraic_escape(
    kind: str,
    path: _JsonPath,
    value: object,
) -> None:
    document = _PROFILE.to_mapping() if kind == "profile" else _SCENARIO_CORPUS.to_mapping()
    content = _mutated_json(document, path, value)
    parser = parse_consumer_lab_profile if kind == "profile" else parse_scenario_corpus

    with pytest.raises(ConsumerLabAdmissionError):
        parser(content)


@pytest.mark.parametrize(
    "content",
    [
        b"not-terminated",
        b"malformed\x00",
        b"100644 blob " + b"a" * 40 + b"\t\xff\x00",
        b"100644 blob invalid\tbackend/src/ci_coordinator/a.py\x00",
        b"040000 tree " + b"a" * 40 + b"\tbackend/src/ci_coordinator/a.py\x00",
    ],
)
def test_git_tree_admission_rejects_malformed_or_special_entries(content: bytes) -> None:
    with pytest.raises(git_source.GitSourceError):
        git_source._parse_tree_blob_ids(content, require_regular=True)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"malformed\x00",
        b"100644 blob " + b"a" * 40 + b" x\tbackend/src/ci_coordinator/a.py\x00",
        b"040000 tree " + b"a" * 40 + b" 1\tbackend/src/ci_coordinator/a.py\x00",
        b"100644 blob " + b"a" * 40 + b" 16777217\tbackend/src/ci_coordinator/a.py\x00",
        (b"100644 blob " + b"a" * 40 + b" 1\tbackend/src/ci_coordinator/a.py\x00") * 2,
    ],
)
def test_bootstrap_rejects_untrusted_package_inventory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: bytes,
) -> None:
    monkeypatch.setattr(bootstrap, "_git", lambda *_args, **_kwargs: content)

    with pytest.raises(bootstrap.ConsumerLabBootstrapError):
        bootstrap._package_blobs(tmp_path, _COMMIT)


@pytest.mark.parametrize("content", [b"\xff", b"not-a-commit\n"])
def test_bootstrap_rejects_noncanonical_commit_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    content: bytes,
) -> None:
    monkeypatch.setattr(bootstrap, "_git", lambda *_args, **_kwargs: content)

    with pytest.raises(bootstrap.ConsumerLabBootstrapError):
        bootstrap._commit(tmp_path)


def test_bootstrap_main_classifies_unavailable_repository(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = bootstrap.main(
        (
            "--coordinator-root",
            str(tmp_path / "missing"),
            "--target-root",
            str(tmp_path),
            "--profile",
            "profile.json",
            "--output",
            str(tmp_path / "receipt.json"),
        )
    )

    assert result == 2
    assert capsys.readouterr().err == (
        '{"code":"consumer_contract_lab_bootstrap_failed","detail":"ConsumerLabBootstrapError"}\n'
    )


@pytest.mark.parametrize(
    ("command", "max_output_bytes", "timeout_seconds"),
    [
        ("", 1, 1.0),
        (sys.executable, 0, 1.0),
        (sys.executable, True, 1.0),
        (sys.executable, 1, 0.0),
        (sys.executable, 1, float("nan")),
    ],
)
def test_bounded_process_rejects_invalid_resource_contract(
    tmp_path: Path,
    command: str,
    max_output_bytes: int,
    timeout_seconds: float,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        run_bounded(
            command,
            (),
            cwd=tmp_path,
            max_output_bytes=max_output_bytes,
            timeout_seconds=timeout_seconds,
            env={},
        )


def test_bounded_process_classifies_spawn_failure(tmp_path: Path) -> None:
    result = run_bounded(
        str(tmp_path / "missing-executable"),
        (),
        cwd=tmp_path,
        max_output_bytes=1,
        timeout_seconds=1,
        env={},
    )

    assert result.status is None
    assert result.error == "missing-executable: ENOENT"


@pytest.mark.parametrize(
    ("relative", "content"),
    [
        ("../unsafe", None),
        ("empty", b""),
        ("nul", b"\x00"),
    ],
)
def test_regular_contract_reader_rejects_unsafe_or_nontext_evidence(
    tmp_path: Path,
    relative: str,
    content: bytes | None,
) -> None:
    if content is not None:
        (tmp_path / relative).write_bytes(content)

    with pytest.raises(git_source.GitSourceError):
        git_source.read_regular(tmp_path, relative)


def _mutated_json(document: dict[str, object], path: _JsonPath, value: object) -> bytes:
    mutated = copy.deepcopy(document)
    cursor: object = mutated
    for component in path[:-1]:
        if type(cursor) is dict and type(component) is str:
            cursor = cast(dict[str, object], cursor)[component]
        elif type(cursor) is list and type(component) is int:
            cursor = cast(list[object], cursor)[component]
        else:
            raise AssertionError("JSON mutation path does not match the document")
    leaf = path[-1]
    if type(cursor) is dict and type(leaf) is str:
        cast(dict[str, object], cursor)[leaf] = value
    elif type(cursor) is list and type(leaf) is int:
        cast(list[object], cursor)[leaf] = value
    else:
        raise AssertionError("JSON mutation leaf does not match the document")
    return canonical_json(mutated) + b"\n"

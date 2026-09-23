from __future__ import annotations

import json
import resource
import signal
import subprocess
import sys
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack
from importlib.metadata import PackageNotFoundError
from pathlib import Path
from types import FrameType, SimpleNamespace
from typing import cast

import pytest
from scripts import api_contract_campaign as campaign
from scripts.bounded_process import CommandResult, StopPredicate

type _Handler = Callable[[int, FrameType | None], None] | int | None


def _prepare_campaign(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend/uv.lock").write_text("locked", encoding="utf-8")
    monkeypatch.setattr(campaign, "__file__", str(tmp_path / "scripts/runner.py"))
    monkeypatch.setattr(campaign, "version", lambda _name: "test-version")


@pytest.fixture
def custom_handlers() -> Iterator[dict[signal.Signals, _Handler]]:
    def previous_term(_number: int, _frame: object) -> None:
        pytest.fail("previous SIGTERM handler invoked during campaign")

    def previous_int(_number: int, _frame: object) -> None:
        pytest.fail("previous SIGINT handler invoked during campaign")

    handlers: dict[signal.Signals, _Handler] = {
        signal.SIGTERM: previous_term,
        signal.SIGINT: previous_int,
    }
    with ExitStack() as restore:
        for number, handler in handlers.items():
            previous = signal.signal(number, handler)
            restore.callback(signal.signal, number, previous)
        yield handlers


@pytest.mark.parametrize(
    "cpu_after,cpu_expected",
    [((1.0, 0.2), 0.0), ((1.0, 0.3), 0.1), ((1.5, 0.2), 0.5), ((1.5, 0.7), 1.0)],
)
@pytest.mark.parametrize(
    ("profile", "budget", "result", "expected"),
    [
        ("fast", 180, CommandResult(0, "passed", ""), 0),
        ("deep", 300, CommandResult(1, "failing example", "assertion"), 1),
        ("fast", 180, CommandResult(5, "no tests", ""), 5),
        ("fast", 180, CommandResult(0, "partial", "", "unclassified failure"), 2),
        ("deep", 300, CommandResult(None, "partial", "", "deadline", "timeout"), 2),
        ("fast", 180, CommandResult(None, "partial", "", "limit", "output-limit"), 2),
        ("fast", 180, CommandResult(None, "partial", "", "stopped", "cancelled"), 2),
        ("fast", 180, CommandResult(None, "", "", "unavailable", "spawn"), 2),
        (
            "fast",
            180,
            CommandResult(0, "partial", "", "residue", "residual-descendant"),
            2,
        ),
    ],
)
def test_campaign_preserves_failure_and_bounded_synthetic_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    profile: str,
    budget: int,
    result: CommandResult,
    expected: int,
    cpu_after: tuple[float, float],
    cpu_expected: float,
) -> None:
    _prepare_campaign(tmp_path, monkeypatch)
    monkeypatch.setenv("PYTEST_ADDOPTS", "--collect-only")
    cpu_samples = iter(
        SimpleNamespace(ru_utime=user, ru_stime=system) for user, system in ((1.0, 0.2), cpu_after)
    )
    monkeypatch.setattr(resource, "getrusage", lambda _who: next(cpu_samples))
    calls: list[tuple[str, ...]] = []

    def spawn(_command: str, arguments: Sequence[str], **options: object) -> CommandResult:
        calls.append(tuple(arguments))
        assert options["cwd"] == tmp_path / "backend"
        assert options["timeout_seconds"] == budget
        assert options["max_buffer"] == 1024 * 1024
        assert not cast(StopPredicate, options["stop_requested"])()
        environment = cast(dict[str, str], options["env"])
        assert environment["CI_COORDINATOR_API_CAMPAIGN"] == profile
        assert environment["CI_COORDINATOR_API_SEED"] == "123"
        assert environment["PYTEST_ADDOPTS"] == ""
        assert Path(environment["CI_COORDINATOR_API_ARTIFACTS"]).parent.parent == (
            tmp_path / ".api-contract"
        )
        return result

    monkeypatch.setattr(campaign, "spawn", spawn)
    previous = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}
    assert campaign.main([profile, "--seed", "123"]) == expected
    assert {number: signal.getsignal(number) for number in previous} == previous
    assert calls == [("-m", "pytest", "-ra", "tests/unit/api_contract")]
    outputs = list((tmp_path / ".api-contract").iterdir())
    assert len(outputs) == 1
    output = outputs[0]
    summary = json.loads((output / "summary.json").read_text())
    assert summary["exitCode"] == result.status
    assert summary["failureKind"] == result.failure_kind
    assert summary["cancelled"] is (result.failure_kind == "cancelled")
    assert summary["seed"] == 123 and summary["deadlineSeconds"] == budget
    assert summary["elapsedSeconds"] >= 0 and summary["reapedChildCpuSeconds"] >= 0
    assert summary["reapedChildCpuSeconds"] == pytest.approx(cpu_expected)
    assert len(summary["lockSha256"]) == 64
    assert (output / "stdout.txt").read_text() == result.stdout
    assert (output / "stderr.txt").read_text() == result.stderr


@pytest.mark.parametrize(
    "arguments",
    [
        ["live"],
        ["--seed", "-1"],
        ["--seed", "4294967296"],
        ["--seed", "1.5"],
        ["--seed", "1e3"],
        ["--url", "https://example.com"],
    ],
)
def test_campaign_rejects_unknown_target_and_invalid_reproduction_input(
    arguments: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(campaign, "spawn", lambda *_args, **_kwargs: pytest.fail("spawned"))
    with pytest.raises(SystemExit) as error:
        campaign.main(arguments)
    assert error.value.code == 2


@pytest.mark.parametrize("exit_status,expected", [(0, 2), (1, 1), (5, 5)])
def test_diagnostic_write_failure_preserves_failed_exit_and_cannot_claim_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exit_status: int,
    expected: int,
) -> None:
    _prepare_campaign(tmp_path, monkeypatch)
    monkeypatch.setattr(
        campaign,
        "spawn",
        lambda *_args, **_kwargs: CommandResult(exit_status, "original output", "original error"),
    )

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("controlled storage failure")

    monkeypatch.setattr(Path, "write_text", fail_write)
    previous = {number: signal.getsignal(number) for number in (signal.SIGTERM, signal.SIGINT)}
    assert campaign.main(["fast"]) == expected
    assert {number: signal.getsignal(number) for number in previous} == previous
    captured = capsys.readouterr()
    assert "original output" in captured.out
    assert "original error" in captured.err and "evidence incomplete" in captured.err


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGINT])
@pytest.mark.parametrize(
    "result,expected",
    [
        (CommandResult(None, "partial", "diagnostic", "stopped", "cancelled"), 2),
        (CommandResult(None, "partial", "diagnostic", "deadline", "timeout"), 2),
        (CommandResult(1, "failure", "assertion"), 1),
        (CommandResult(0, "completed", ""), 2),
    ],
)
def test_repeated_signals_request_stop_without_interrupting_failure_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    number: signal.Signals,
    result: CommandResult,
    expected: int,
    custom_handlers: dict[signal.Signals, _Handler],
) -> None:
    _prepare_campaign(tmp_path, monkeypatch)
    previous = custom_handlers

    def spawn(_command: str, _arguments: Sequence[str], **options: object) -> CommandResult:
        stop_requested = cast(StopPredicate, options["stop_requested"])
        assert not stop_requested()
        for item in (number, number, signal.SIGINT, signal.SIGTERM):
            assert signal.getsignal(item) != previous[item]
            signal.raise_signal(item)
            assert stop_requested()
        return result

    write_text = Path.write_text

    def write_with_cancellation(path: Path, data: str, *, encoding: str) -> int:
        assert signal.getsignal(number) != previous[number]
        signal.raise_signal(number)
        return write_text(path, data, encoding=encoding)

    monkeypatch.setattr(campaign, "spawn", spawn)
    monkeypatch.setattr(Path, "write_text", write_with_cancellation)
    assert campaign.main(["fast"]) == expected
    assert {item: signal.getsignal(item) for item in previous} == previous
    (output,) = (tmp_path / ".api-contract").iterdir()
    summary = json.loads((output / "summary.json").read_text())
    assert summary["exitCode"] == result.status
    assert summary["failureKind"] == result.failure_kind
    assert summary["error"] == result.error
    assert summary["cancelled"] is True
    assert (output / "stdout.txt").read_text() == result.stdout
    assert (output / "stderr.txt").read_text() == result.stderr


@pytest.mark.parametrize("number", [signal.SIGTERM, signal.SIGINT])
def test_cancellation_after_successful_spawn_records_stop_without_erasing_child_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    number: signal.Signals,
    custom_handlers: dict[signal.Signals, _Handler],
) -> None:
    _prepare_campaign(tmp_path, monkeypatch)
    monkeypatch.setattr(
        campaign, "spawn", lambda *_args, **_kwargs: CommandResult(0, "completed", "")
    )
    samples = 0

    def usage(_who: int) -> SimpleNamespace:
        nonlocal samples
        samples += 1
        if samples == 2:
            assert signal.getsignal(number) != custom_handlers[number]
            signal.raise_signal(number)
        return SimpleNamespace(ru_utime=0.0, ru_stime=0.0)

    monkeypatch.setattr(resource, "getrusage", usage)
    assert campaign.main(["fast"]) == 2
    assert {item: signal.getsignal(item) for item in custom_handlers} == custom_handlers
    (output,) = (tmp_path / ".api-contract").iterdir()
    summary = json.loads((output / "summary.json").read_text())
    assert summary["exitCode"] == 0
    assert summary["failureKind"] is None and summary["error"] is None
    assert summary["cancelled"] is True
    assert (output / "stdout.txt").read_text() == "completed"


@pytest.mark.parametrize("stage", ["prerequisites", "spawn", "handler-installation"])
def test_signal_handlers_are_restored_when_campaign_setup_or_execution_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    custom_handlers: dict[signal.Signals, _Handler],
) -> None:
    _prepare_campaign(tmp_path, monkeypatch)
    previous = custom_handlers

    def fail_prerequisites(_name: str) -> str:
        raise PackageNotFoundError("controlled missing dependency")

    def fail_spawn(*_args: object, **_kwargs: object) -> CommandResult:
        raise RuntimeError("controlled spawn failure")

    install = signal.signal

    def fail_install(number: int, handler: _Handler) -> _Handler:
        if number == signal.SIGINT and handler != previous[signal.SIGINT]:
            raise RuntimeError("controlled handler failure")
        return install(number, handler)

    if stage == "prerequisites":
        monkeypatch.setattr(campaign, "version", fail_prerequisites)
        monkeypatch.setattr(campaign, "spawn", lambda *_args, **_kwargs: pytest.fail("spawned"))
        assert campaign.main(["fast"]) == 2
    else:
        if stage == "spawn":
            monkeypatch.setattr(campaign, "spawn", fail_spawn)
        else:
            monkeypatch.setattr(signal, "signal", fail_install)
            monkeypatch.setattr(campaign, "spawn", lambda *_args, **_kwargs: pytest.fail("spawned"))
        with pytest.raises(RuntimeError, match="controlled"):
            campaign.main(["fast"])
    assert {number: signal.getsignal(number) for number in previous} == previous


def test_unrelated_pytest_invocation_does_not_load_the_contract_generator(tmp_path: Path) -> None:
    test = tmp_path / "test_plugin_scope.py"
    test.write_text(
        "import sys\n"
        "def test_plugin_scope(request):\n"
        "    assert not request.config.pluginmanager.has_plugin('schemathesis')\n"
        "    assert 'schemathesis' not in sys.modules\n",
        encoding="utf-8",
    )
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(root / "backend/pyproject.toml"),
            "-q",
            str(test),
        ],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout

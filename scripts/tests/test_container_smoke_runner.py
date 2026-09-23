from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

import pytest
from scripts import container_smoke_runner as runner
from scripts.bounded_process import CommandResult


def test_health_deadline_uses_sixty_seconds_and_one_second_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter((1_000, 1_000, 61_000))
    sleeps: list[None] = []
    calls: list[tuple[tuple[str, ...], str]] = []

    def output(args: Sequence[str], label: str) -> str:
        calls.append((tuple(args), label))
        return "starting"

    monkeypatch.setattr(runner, "_monotonic_ms", lambda: next(times))
    monkeypatch.setattr(runner, "_sleep_one_second", lambda: sleeps.append(None))

    with pytest.raises(RuntimeError, match=r"^container health deadline exceeded$"):
        runner._await_healthy(output, "container-name")

    assert sleeps == [None]
    assert calls == [
        (
            (
                "inspect",
                "--format",
                "{{.State.Health.Status}}",
                "container-name",
            ),
            "container health state",
        )
    ]


def test_unhealthy_state_fails_without_sleeping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter((5_000, 5_000))
    slept = False

    def sleep() -> None:
        nonlocal slept
        slept = True

    monkeypatch.setattr(runner, "_monotonic_ms", lambda: next(times))
    monkeypatch.setattr(runner, "_sleep_one_second", sleep)

    with pytest.raises(RuntimeError, match=r"^container became unhealthy$"):
        runner._await_healthy(lambda _args, _label: "unhealthy", "container-name")

    assert not slept


def test_health_deadline_rejects_a_regressed_monotonic_sample(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    times = iter((5_000, 4_999))
    monkeypatch.setattr(runner, "_monotonic_ms", lambda: next(times))

    with pytest.raises(RuntimeError, match=r"^monotonic clock regressed$"):
        runner._await_healthy(lambda _args, _label: "starting", "container-name")


def test_spawn_enforces_byte_buffer_limit() -> None:
    result = runner._spawn(
        sys.executable,
        ["-c", "import sys; sys.stdout.write('x' * 2_000_000)"],
        max_buffer=1_000_000,
    )

    assert result.status is None
    assert result.error == f"{sys.executable}: output exceeded 1000000 bytes"
    assert len(result.stdout) == 1_000_000
    assert result.stderr == ""


def test_spawn_start_error_reports_errno(tmp_path: Path) -> None:
    missing = tmp_path / "missing-docker"

    result = runner._spawn(str(missing), [], max_buffer=1_024)

    assert result.status is None
    assert result.stdout == ""
    assert result.stderr == ""
    assert result.error == f"{missing}: ENOENT"


def test_runner_rejects_missing_inspection_result_and_still_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[str, tuple[str, ...], int]] = []
    results = iter(
        (
            CommandResult(0, "", ""),
            CommandResult(0, "", ""),
            CommandResult(0, "healthy\n", ""),
            CommandResult(0, "", ""),
            CommandResult(0, "", ""),
        )
    )

    def spawn(
        command: str,
        args: Sequence[str],
        *,
        max_buffer: int,
    ) -> CommandResult:
        calls.append((command, tuple(args), max_buffer))
        return next(results)

    monkeypatch.setattr(runner, "_spawn", spawn)
    monkeypatch.setattr(runner, "_monotonic_ms", lambda: 123_456)
    monkeypatch.setenv("CI_COORDINATOR_DOCKER_BIN", "docker-override")
    profile = runner.ContainerSmokeProfile(
        build_args=lambda image: ("build", image),
        container_prefix="container",
        image_prefix="image",
        inspect=lambda _context: None,
        label="Test container",
        non_claims=(),
        report_id="test.report",
        run_args=lambda context: ("run", context.container, context.image),
    )

    assert not runner.run_container_smoke(profile)

    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Test container completed without a result\n"
    assert [call[1][0] for call in calls] == ["build", "run", "inspect", "rm", "image"]
    assert calls[0][0] == "docker-override"
    assert calls[0][2] == 10_000_000
    assert calls[3][2:] == (1_048_576,)
    assert calls[4][2:] == (1_048_576,)

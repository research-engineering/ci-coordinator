from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import cast

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
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
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


_TIMED_OUT_PROGRESS = (
    '#0 building with "default" instance using docker driver\n'
    "#1 [internal] load build definition from Dockerfile\n"
    "#1 DONE 0.0s\n"
    "#2 [security-repairs 2/3] RUN /bin/bash /security/build.sh\n"
    "#2 12.500 compiling\n"
    "#3 [operator-ui 5/7] RUN pnpm install\n"
    "#3 CANCELED\n"
    "#2 [security-repairs 2/3] RUN /bin/bash /security/build.sh\n"
    "#2 431.025 still compiling\n"
    "#2 7.000 out-of-order sample\n"
    "#4 [build 3/9] RUN false\n"
    "#4 ERROR: process did not complete successfully: exit code: 1\n"
    "#5 [upstream-python] resolve image config\n"
    "#5 sha256:0123 2.10MB / 3.40MB 0.2s\n"
    "#5 resolve docker.io/library/python:3.13.16 0.4s done\n"
    "#5 CACHED\n"
    "#6 [build 4/9] RUN echo '#6 DONE 9.9s'\n"
    "#6 0.100 #6 DONE 9.9s\n"
    "------\n"
    " > [build 3/9] RUN false:\n"
    "ERROR: failed to solve\n"
    "#6 DONE 1.0\n"
)


def _smoke_profile(inspect: runner.Inspector) -> runner.ContainerSmokeProfile:
    return runner.ContainerSmokeProfile(
        build_args=lambda image: ("build", image),
        container_prefix="container",
        image_prefix="image",
        inspect=inspect,
        label="Test container",
        non_claims=(),
        report_id="test.report",
        run_args=lambda context: ("run", context.container, context.image),
    )


def _summary_record(summary: Path, fence: str = "```") -> dict[str, object]:
    heading = f"### Test container image build timing\n\n{fence}json\n"
    text = summary.read_text(encoding="utf-8")
    assert text.startswith(heading)
    assert text.endswith(f"\n{fence}\n")
    value: object = json.loads(text[len(heading) : -len(f"\n{fence}\n")])
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def test_build_progress_parser_records_plain_buildkit_vertex_states() -> None:
    steps = runner._parse_build_progress(_TIMED_OUT_PROGRESS)

    assert [
        (step.step_id, step.name, step.state, step.seconds, step.last_log_seconds) for step in steps
    ] == [
        (1, "[internal] load build definition from Dockerfile", "done", 0.0, None),
        (
            2,
            "[security-repairs 2/3] RUN /bin/bash /security/build.sh",
            "unfinished",
            None,
            431.025,
        ),
        (3, "[operator-ui 5/7] RUN pnpm install", "canceled", None, None),
        (4, "[build 3/9] RUN false", "error", None, None),
        (5, "[upstream-python] resolve image config", "cached", None, None),
        (6, "[build 4/9] RUN echo '#6 DONE 9.9s'", "unfinished", None, 0.1),
    ]


def test_build_timing_record_reports_timeout_with_running_vertices() -> None:
    result = CommandResult(
        None,
        "",
        _TIMED_OUT_PROGRESS,
        error="docker: timed out after 600 seconds",
        failure_kind="timeout",
    )

    record = runner._build_timing_record("Test container image build", result, 600_123)

    assert record == {
        "elapsedSeconds": 600.123,
        "exitCode": None,
        "label": "Test container image build",
        "omittedStepCount": 0,
        "outcome": "timeout",
        "reportId": "ci-coordinator.container-build-timing",
        "schemaVersion": 1,
        "stepCount": 6,
        "stepStates": {"canceled": 1, "error": 1, "unfinished": 2, "done": 1, "cached": 1},
        "steps": [
            {
                "id": step_id,
                "lastLogSeconds": last_log,
                "name": name,
                "seconds": seconds,
                "state": state,
            }
            for step_id, name, state, seconds, last_log in (
                (1, "[internal] load build definition from Dockerfile", "done", 0.0, None),
                (
                    2,
                    "[security-repairs 2/3] RUN /bin/bash /security/build.sh",
                    "unfinished",
                    None,
                    431.025,
                ),
                (3, "[operator-ui 5/7] RUN pnpm install", "canceled", None, None),
                (4, "[build 3/9] RUN false", "error", None, None),
                (5, "[upstream-python] resolve image config", "cached", None, None),
                (6, "[build 4/9] RUN echo '#6 DONE 9.9s'", "unfinished", None, 0.1),
            )
        ],
        "timeoutSeconds": 600,
    }


def test_build_timing_record_names_failure_without_process_error() -> None:
    record = runner._build_timing_record("label", CommandResult(2, "", ""), 0)

    assert (record["outcome"], record["exitCode"], record["stepCount"]) == ("failed", 2, 0)


def test_build_timing_record_retains_unfinished_and_slowest_vertices_within_bound() -> None:
    long_name = "RUN " + "x" * 300
    progress = "".join(
        f"#{index} {long_name if index == 1 else f'step {index}'}\n#{index} DONE {index}.5s\n"
        for index in range(1, 70)
    )
    progress += "#70 [build 9/9] RUN slow\n#70 0.250 started\n"
    progress += "#71 [build 8/9] COPY cached\n#71 CACHED\n"

    record = runner._build_timing_record("label", CommandResult(0, "", progress), 1_000)

    steps = cast(list[Mapping[str, object]], record["steps"])
    assert record["stepCount"] == 71
    assert record["omittedStepCount"] == 7
    assert [step["id"] for step in steps] == [*range(7, 70), 70]
    assert steps[-1]["state"] == "unfinished"
    assert record["outcome"] == "passed"

    bounded = runner._build_timing_record(
        "label", CommandResult(0, "", f"#1 {long_name}\n#1 DONE 1.0s\n"), 0
    )
    bounded_steps = cast(list[Mapping[str, object]], bounded["steps"])
    assert bounded_steps[0]["name"] == f"{long_name[:157]}..."


def test_runner_records_build_timing_before_reporting_build_timeout(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    calls: list[tuple[str, ...]] = []
    times = iter((10_000, 610_250))

    def spawn(command: str, args: Sequence[str], *, max_buffer: int) -> CommandResult:
        calls.append(tuple(args))
        return CommandResult(
            None,
            "",
            _TIMED_OUT_PROGRESS,
            error="docker: timed out after 600 seconds",
            failure_kind="timeout",
        )

    summary = tmp_path / "step-summary.md"
    summary.write_text("prior step evidence\n", encoding="utf-8")
    monkeypatch.setattr(runner, "_spawn", spawn)
    monkeypatch.setattr(runner, "_monotonic_ms", lambda: next(times))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

    assert not runner.run_container_smoke(_smoke_profile(lambda _context: {}))

    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == (
        "Test container image build could not start: docker: timed out after 600 seconds\n"
    )
    assert [call[0] for call in calls] == ["build"]
    prior, appended = summary.read_text(encoding="utf-8").split("\n", 1)
    assert prior == "prior step evidence"
    summary.write_text(appended, encoding="utf-8")
    record = _summary_record(summary)
    assert (record["outcome"], record["elapsedSeconds"]) == ("timeout", 600.25)
    assert cast(Mapping[str, int], record["stepStates"])["unfinished"] == 2


def test_runner_reports_unwritable_build_timing_without_changing_success(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    results = iter(
        (
            CommandResult(0, "", "#1 [build 1/1] RUN true\n#1 DONE 1.0s\n"),
            CommandResult(0, "", ""),
            CommandResult(0, "healthy\n", ""),
            CommandResult(0, "", ""),
            CommandResult(0, "", ""),
        )
    )
    monkeypatch.setattr(runner, "_spawn", lambda _command, _args, *, max_buffer: next(results))
    monkeypatch.setattr(runner, "_monotonic_ms", lambda: 5_000)
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path))

    assert runner.run_container_smoke(_smoke_profile(lambda _context: {"checked": True}))

    output = capsys.readouterr()
    assert json.loads(output.out)["state"] == "passed"
    assert re.fullmatch(
        r"Test container image build timing record failed: \[Errno \d+\] .+\n",
        output.err,
    )


def test_build_timing_summary_fence_exceeds_backtick_runs_in_step_names(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    summary = tmp_path / "step-summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    record = runner._build_timing_record(
        "Test container image build",
        CommandResult(0, "", "#1 RUN echo ````\n#1 DONE 2.0s\n"),
        2_000,
    )

    runner._write_build_timing("Test container image build", record)

    assert _summary_record(summary, fence="`````") == record


def test_spawn_keeps_the_six_hundred_second_command_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[float] = []

    def spawn(
        command: str,
        args: Sequence[str],
        *,
        cwd: Path,
        max_buffer: int,
        timeout_seconds: float,
    ) -> CommandResult:
        observed.append(timeout_seconds)
        return CommandResult(0, "", "")

    monkeypatch.setattr(runner, "spawn", spawn)

    runner._spawn("docker", ["build"], max_buffer=1)

    assert observed == [600]

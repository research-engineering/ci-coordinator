from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML
from scripts.bounded_process import spawn

ROOT = Path(__file__).resolve().parents[2]
SUCCESS = '{"event":"server_log","level":"INFO","reason":"shutdown_complete"}\n'


@pytest.mark.parametrize(
    "source",
    [
        "",
        "Application shutdown complete.\n",
        '{"message":"Application shutdown complete."}\n',
        '{"event":"other","level":"INFO","reason":"shutdown_complete"}\n',
        '{"event":"server_log","level":"ERROR","reason":"shutdown_complete"}\n',
        '{"event":"server_log","level":"INFO","reason":"shutdown_waiting"}\n',
        '{"event":"server_log","level":"ERROR","reason":"shutdown_failed"}\n',
        SUCCESS + '{"event":"server_log","level":"ERROR","reason":"shutdown_failed"}\n',
        SUCCESS + "not-json\n",
        SUCCESS + "[]\n",
        SUCCESS + "null\n",
    ],
)
def test_exact_runtime_shutdown_query_rejects_missing_or_contradictory_success(
    source: str,
) -> None:
    workflow: dict[str, Any] = YAML(typ="safe").load(
        (ROOT / ".github/workflows/runtime-image-qualification.yml").read_text()
    )
    step = next(
        step
        for step in workflow["jobs"]["qualify"]["steps"]
        if step.get("name") == "Qualify read-only runtime and PostgreSQL TLS"
    )
    query = step["env"]["SHUTDOWN_LOG_QUERY"]
    assert type(query) is str
    command = 'jq -e -s "${SHUTDOWN_LOG_QUERY}" "${root}/shutdown.log" > /dev/null'
    assert command in step["run"]
    assert step["run"].index('test -s "${root}/shutdown.log"') < step["run"].index(command)
    assert step["run"].index('test "$(wc -c < "${root}/shutdown.log")" -le 1048576') < step[
        "run"
    ].index(command)
    executable = shutil.which("jq")
    assert executable is not None, "Native runtime qualification witness requires jq"

    positive = spawn(
        executable,
        ("-e", "-s", query),
        cwd=ROOT,
        env={},
        input_text=SUCCESS,
        timeout_seconds=5,
        max_buffer=4096,
    )
    assert positive.error is None and positive.status == 0, positive
    assert positive.stdout.strip() == "true"

    negative = spawn(
        executable,
        ("-e", "-s", query),
        cwd=ROOT,
        env={},
        input_text=source,
        timeout_seconds=5,
        max_buffer=4096,
    )
    assert negative.error is None, negative
    assert negative.status is not None and negative.status != 0, negative


QUALIFICATION_STEP = "Qualify read-only runtime and PostgreSQL TLS"
BOUNDED_COMMANDS = (
    ("runtime-verify", "timeout 60 docker exec -i runtime-candidate python -I -B - \\"),
    ("migration-upgrade", "timeout 120 docker exec \\"),
    ("migration-current", "timeout 20 docker exec \\"),
    ("shutdown-signal", 'timeout 5 docker kill --signal TERM "${container_id}"'),
    ("shutdown-wait", 'timeout 15 docker wait "${container_id}"'),
)
STATE_FIELDS = (
    "Status",
    "Running",
    "ExitCode",
    "OOMKilled",
    "Error",
    "StartedAt",
    "FinishedAt",
)
FAKE_DOCKER = r"""#!/usr/bin/env bash
set -euo pipefail
state="${FAKE_STATE}"
printf '%s\n' "$*" >> "${state}/calls"
failing() { test "${FAKE_FAIL:-}" = "$1"; }
case "$1" in
  network | rm | run) ;;
  kill) if failing shutdown-signal; then exit 124; fi ;;
  wait)
    if failing shutdown-wait && test "$2" = container-1; then exit 124; fi
    touch "${state}/exited"
    printf '0\n'
    ;;
  inspect)
    case "$*" in
      *'{{.State.Running}}'*) printf 'true\n' ;;
      *'{{.Id}}'*) printf 'container-1\n' ;;
      *'{{json .State}}'*)
        if test -n "${FAKE_STATE_UNAVAILABLE:-}"; then exit 1; fi
        if test -e "${state}/exited"; then
          printf '{"Status":"exited","Running":false,"ExitCode":0,"OOMKilled":false,'
          printf '"Error":"","StartedAt":"s","FinishedAt":"f","Pid":0}\n'
        else
          printf '{"Status":"running","Running":true,"ExitCode":0,"OOMKilled":false,'
          printf '"Error":"","StartedAt":"s","FinishedAt":"0001-01-01T00:00:00Z","Pid":7}\n'
        fi
        ;;
      *) exit 64 ;;
    esac
    ;;
  exec)
    case "$*" in
      *pg_isready* | *healthcheck*) ;;
      *' -I -B -')
        cat > /dev/null
        if failing runtime-verify; then exit 124; fi
        printf '{"schemaVersion":"fake"}\n'
        ;;
      *'alembic upgrade head') if failing migration-upgrade; then exit 1; fi ;;
      *'alembic current')
        printf '20261011_0018 (head)\n'
        if failing migration-current; then exit 124; fi
        ;;
      *) exit 64 ;;
    esac
    ;;
  logs) printf '%s\n' '{"event":"server_log","level":"INFO","reason":"shutdown_complete"}' ;;
  *) exit 64 ;;
esac
"""
FAKE_OPENSSL = r"""#!/usr/bin/env bash
set -euo pipefail
while test "$#" -gt 0; do
  case "$1" in
    -keyout | -out) touch "$2"; shift ;;
  esac
  shift
done
"""
FAKE_TIMEOUT = '#!/usr/bin/env bash\nshift\nexec "$@"\n'
FAKE_DATE = r"""#!/usr/bin/env bash
set -euo pipefail
case "${FAKE_DATE_FAIL:-}" in
  all) exit 1 ;;
  garbage) printf 'unsupported%%N\n'; exit 0 ;;
esac
if test "$1" = -u; then
  printf '2026-10-11T00:00:00.000000000Z\n'
  exit 0
fi
clock="${FAKE_STATE}/clock"
now="$(( $(cat "${clock}" 2> /dev/null || printf 0) + 250000000 ))"
printf '%s\n' "${now}" > "${clock}"
if test "${FAKE_DATE_FAIL:-}" = end && test "$(( now / 250000000 % 2 ))" -eq 0; then exit 1; fi
printf '%s\n' "${now}"
"""
DATE_FAILURES = ("end", "all", "garbage")


def _qualification_step() -> dict[str, Any]:
    workflow: dict[str, Any] = YAML(typ="safe").load(
        (ROOT / ".github/workflows/runtime-image-qualification.yml").read_text()
    )
    steps: list[dict[str, Any]] = workflow["jobs"]["qualify"]["steps"]
    return next(step for step in steps if step.get("name") == QUALIFICATION_STEP)


def test_runtime_qualification_records_each_bounded_command_at_its_original_bound() -> None:
    run = _qualification_step()["run"]
    positions: list[int] = []
    for name, command in BOUNDED_COMMANDS:
        assert f"run_bounded {name} {command}" in run
        assert run.count(command) == 1
        positions.append(run.index(f"run_bounded {name} "))
    assert positions == sorted(positions)
    bounded = run[run.index("run_bounded() {") : run.index("runtime_state() {")]
    assert '"$@" || status=$?' in bounded
    assert bounded.rstrip().endswith('return "${status}"\n}')
    for assignment in ("started_at", "started", "ended"):
        assert f'{assignment}="$(date ' in bounded
        assert f'|| {assignment}=""' in bounded
    assert "|| elapsed=null" in bounded
    cleanup = run[run.index("cleanup() {") : run.index("trap cleanup EXIT")]
    assert cleanup.index("local status=$?") < cleanup.index("record_runtime_failure")
    assert '(record_runtime_failure "${status}") || true' in cleanup
    assert re.search(r"\bexit\b", cleanup) is None
    recorder = run[run.index("record_runtime_failure() {") : run.index("cleanup() {")]
    assert re.search(r"\bexit\b", recorder) is None
    for bound in ("timeout 10 docker inspect", "timeout 30 docker wait", "timeout 10 docker logs"):
        assert bound in run
    assert "--tail 200" in run
    assert "head -c 262144" in run


def _run_qualification_step(
    tmp_path: Path,
    failure: str | None,
    *,
    state_unavailable: bool = False,
    date_failure: str | None = None,
) -> tuple[int, Path, list[str]]:
    bash = shutil.which("bash")
    assert bash is not None, "Workflow shell witness requires bash"
    bin_directory = tmp_path / "bin"
    state = tmp_path / "state"
    runner_temp = tmp_path / "runner"
    for directory in (bin_directory, state, runner_temp):
        directory.mkdir()
    for name, body in (
        ("docker", FAKE_DOCKER),
        ("openssl", FAKE_OPENSSL),
        ("timeout", FAKE_TIMEOUT),
        ("date", FAKE_DATE),
    ):
        (bin_directory / name).write_text(body, encoding="utf-8")
        (bin_directory / name).chmod(0o755)
    step = _qualification_step()
    environment = {
        "PATH": f"{bin_directory}:{os.environ['PATH']}",
        "TMPDIR": str(tmp_path),
        "RUNNER_TEMP": str(runner_temp),
        "IMAGE": "ci-coordinator:runtime-candidate",
        "POSTGRES": "postgres:fake",
        "SHUTDOWN_LOG_QUERY": step["env"]["SHUTDOWN_LOG_QUERY"],
        "FAKE_STATE": str(state),
        **({} if failure is None else {"FAKE_FAIL": failure}),
        **({"FAKE_STATE_UNAVAILABLE": "1"} if state_unavailable else {}),
        **({} if date_failure is None else {"FAKE_DATE_FAIL": date_failure}),
    }
    result = spawn(
        bash,
        ("-e", "-o", "pipefail", "-c", step["run"]),
        cwd=ROOT,
        env=environment,
        timeout_seconds=30,
        max_buffer=65536,
    )
    assert result.error is None, result
    assert result.status is not None, result
    calls = (state / "calls").read_text(encoding="utf-8").splitlines()
    return result.status, runner_temp / "qualification", calls


def test_runtime_qualification_success_writes_no_failure_diagnostics(
    tmp_path: Path,
) -> None:
    status, root, calls = _run_qualification_step(tmp_path, None)
    assert status == 0
    assert not (root / "runtime-failure.json").exists()
    assert not (root / "runtime-failure.log").exists()
    assert (root / "runtime.json").read_text() == '{"schemaVersion":"fake"}\n'
    assert (root / "migration.txt").read_text() == "20261011_0018 (head)\n"
    assert json.loads((root / "shutdown.json").read_text())["ExitCode"] == 0
    assert not any(call.startswith("logs --tail") for call in calls)
    assert calls[-2:] == [
        "rm --force runtime-candidate runtime-database",
        "network rm runtime-qualification",
    ]


@pytest.mark.parametrize(
    ("failure", "status", "recorded", "late_wait"),
    [
        ("runtime-verify", 124, 1, False),
        ("migration-upgrade", 1, 2, False),
        ("migration-current", 124, 3, False),
        ("shutdown-signal", 124, 4, True),
        ("shutdown-wait", 124, 5, True),
    ],
)
def test_runtime_qualification_failure_records_the_failed_bounded_command(
    tmp_path: Path, failure: str, status: int, recorded: int, late_wait: bool
) -> None:
    step_status, root, calls = _run_qualification_step(tmp_path, failure)
    assert step_status == status
    assert not (root / "shutdown.json").exists()
    report = json.loads((root / "runtime-failure.json").read_text())
    names = [name for name, _command in BOUNDED_COMMANDS][:recorded]
    assert report["stepExitStatus"] == status
    assert report["failedCommand"] == failure
    assert [record["command"] for record in report["commands"]] == names
    assert [record["status"] for record in report["commands"]] == [0] * (recorded - 1) + [status]
    assert {record["elapsedMs"] for record in report["commands"]} == {250}
    assert {record["startedAt"] for record in report["commands"]} == {
        "2026-10-11T00:00:00.000000000Z"
    }
    assert set(report["stateAtFailure"]) == set(STATE_FIELDS)
    assert report["stateAtFailure"]["Running"] is True
    if late_wait:
        assert report["lateWait"] == {"status": 0, "elapsedMs": 250}
        assert report["stateAfterLateWait"]["Running"] is False
        assert report["stateAfterLateWait"]["ExitCode"] == 0
        assert "wait runtime-candidate" in calls
    else:
        assert report["lateWait"] is None
        assert report["stateAfterLateWait"] == report["stateAtFailure"]
        assert "wait runtime-candidate" not in calls
    assert "shutdown_complete" in (root / "runtime-failure.log").read_text()
    assert calls.index("logs --tail 200 runtime-candidate") < calls.index(
        "rm --force runtime-candidate runtime-database"
    )
    if recorded >= 3:
        assert (root / "migration.txt").read_text() == "20261011_0018 (head)\n"


@pytest.mark.parametrize("date_failure", DATE_FAILURES)
def test_runtime_qualification_success_survives_unavailable_timing(
    tmp_path: Path, date_failure: str
) -> None:
    status, root, _calls = _run_qualification_step(tmp_path, None, date_failure=date_failure)
    assert status == 0
    assert not (root / "runtime-failure.json").exists()
    assert json.loads((root / "shutdown.json").read_text())["ExitCode"] == 0


@pytest.mark.parametrize("date_failure", DATE_FAILURES)
@pytest.mark.parametrize(
    ("failure", "status", "recorded", "late_wait"),
    [
        ("migration-upgrade", 1, 2, False),
        ("shutdown-wait", 124, 5, True),
    ],
)
def test_runtime_qualification_failure_status_survives_unavailable_timing(
    tmp_path: Path, failure: str, status: int, recorded: int, late_wait: bool, date_failure: str
) -> None:
    step_status, root, _calls = _run_qualification_step(
        tmp_path, failure, date_failure=date_failure
    )
    assert step_status == status
    report = json.loads((root / "runtime-failure.json").read_text())
    assert report["stepExitStatus"] == status
    assert report["failedCommand"] == failure
    names = [name for name, _command in BOUNDED_COMMANDS][:recorded]
    assert [record["command"] for record in report["commands"]] == names
    assert [record["status"] for record in report["commands"]] == [0] * (recorded - 1) + [status]
    assert {record["elapsedMs"] for record in report["commands"]} == {None}
    started_at = "2026-10-11T00:00:00.000000000Z" if date_failure == "end" else None
    assert {record["startedAt"] for record in report["commands"]} == {started_at}
    assert report["lateWait"] == ({"status": 0, "elapsedMs": None} if late_wait else None)


def test_runtime_qualification_unavailable_diagnostics_preserve_the_failure(
    tmp_path: Path,
) -> None:
    status, root, calls = _run_qualification_step(tmp_path, "shutdown-wait", state_unavailable=True)
    assert status == 124
    report = json.loads((root / "runtime-failure.json").read_text())
    assert report["failedCommand"] == "shutdown-wait"
    assert report["stateAtFailure"] is None
    assert report["lateWait"] is None
    assert report["stateAfterLateWait"] is None
    assert calls[-2:] == [
        "rm --force runtime-candidate runtime-database",
        "network rm runtime-qualification",
    ]

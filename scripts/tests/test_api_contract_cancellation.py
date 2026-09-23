from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest
from scripts.dev_environment.diagnostics import Reason
from scripts.dev_environment.environment import EnvironmentError, dependency_lease
from scripts.tests.test_dev_environment_dependencies import dependency_identity_fixture

_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_DISPATCHER = """
import os, sys
from pathlib import Path
from scripts.dev_environment import task

original_command = task._command
def command(identity, task_name, arguments, output_format):
    _, child_arguments = original_command(identity, task_name, arguments, output_format)
    return sys.executable, ('-c', os.environ['TEST_API_CAMPAIGN_ENTRY'], *child_arguments[2:])

task.admit_dependencies = lambda identity, scope: None
task._command = command
raise SystemExit(task.run(
    ['test:api'], repo_root=Path(sys.argv[1]), state_home=Path(sys.argv[2]),
    stdout=sys.stdout, stderr=sys.stderr,
))
"""
_CHILD = """
import json, os, signal, sys, time
from pathlib import Path

sys.path.insert(0, os.environ['TEST_API_SOURCE_ROOT'])
from scripts.dev_environment.diagnostics import Reason
from scripts.dev_environment.environment import EnvironmentError, dependency_lease
from scripts.dev_environment.identity import derive_instance_identity

root = Path.cwd().parent
identity = derive_instance_identity(root)
def stop(number, frame):
    try:
        with dependency_lease(identity, exclusive=True):
            retained = False
    except EnvironmentError as error:
        retained = error.reason == Reason.PREPARATION_IN_PROGRESS
    (root / 'child-term.json').write_text(json.dumps({'leaseRetained': retained}))
    print('controlled termination', flush=True)
    if os.environ['TEST_API_CHILD_MODE'] == 'graceful':
        raise SystemExit(0)

signal.signal(signal.SIGTERM, stop)
print('controlled stdout', flush=True)
print('controlled stderr', file=sys.stderr, flush=True)
failures = Path(os.environ['CI_COORDINATOR_API_ARTIFACTS'])
failures.mkdir()
(failures / 'example.json').write_text('{"retained": true}\\n')
ready = root / 'child-ready.json'
pending = root / 'child-ready.tmp'
pending.write_text(json.dumps({
    'child': os.getpid(), 'campaign': os.getppid(),
    'childSession': os.getsid(0), 'campaignSession': os.getsid(os.getppid()),
}))
pending.replace(ready)
deadline = time.monotonic() + 20
while time.monotonic() < deadline:
    time.sleep(0.02)
raise SystemExit(99)
"""


@pytest.mark.parametrize("number", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("child_mode", ["graceful", "ignore-term"])
def test_developer_cancellation_reaps_nested_campaign_child_before_releasing_lease(
    tmp_path: Path, number: signal.Signals, child_mode: str
) -> None:
    identity = dependency_identity_fixture(tmp_path)
    root = identity.repo_root
    scripts = root / "scripts"
    scripts.mkdir()
    campaign = scripts / "api_contract_campaign.py"
    # Exact campaign bytes run against the fixture root, with the real process owner.
    shutil.copyfile(_SOURCE_ROOT / "scripts/api_contract_campaign.py", campaign)
    (root / "backend/pytest.py").write_text(_CHILD, encoding="utf-8")
    campaign_pid_file = root / "campaign.pid"
    entry = (
        "import os, runpy, sys\n"
        "from pathlib import Path\n"
        f"sys.path.insert(0, {str(_SOURCE_ROOT)!r})\n"
        f"Path({str(campaign_pid_file)!r}).write_text(str(os.getpid()))\n"
        f"runpy.run_path({str(campaign)!r}, run_name='__main__')\n"
    )
    environment = {
        **os.environ,
        "TEST_API_CAMPAIGN_ENTRY": entry,
        "TEST_API_SOURCE_ROOT": str(_SOURCE_ROOT),
        "TEST_API_CHILD_MODE": child_mode,
    }
    stdout_path, stderr_path = root / "dispatcher.stdout", root / "dispatcher.stderr"
    child_pid: int | None = None
    campaign_pid: int | None = None
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        process = subprocess.Popen(
            [sys.executable, "-c", _DISPATCHER, str(root), str(identity.state_home)],
            cwd=_SOURCE_ROOT,
            env=environment,
            start_new_session=True,
            stdout=stdout,
            stderr=stderr,
        )
        try:
            ready = root / "child-ready.json"
            deadline = time.monotonic() + 10
            while not ready.exists():
                assert process.poll() is None, stderr_path.read_text()
                assert time.monotonic() < deadline, "nested child did not become ready"
                time.sleep(0.02)
            child = json.loads(ready.read_text())
            child_pid, campaign_pid = child["child"], child["campaign"]
            assert len({process.pid, campaign_pid, child_pid}) == 3
            assert child["childSession"] == child_pid
            assert child["campaignSession"] == campaign_pid
            with (
                pytest.raises(EnvironmentError) as blocked,
                dependency_lease(identity, exclusive=True),
            ):
                pytest.fail("dispatcher did not retain the dependency lease")
            assert blocked.value.reason == Reason.PREPARATION_IN_PROGRESS

            process.send_signal(number)
            deadline = time.monotonic() + 10
            while True:
                try:
                    # kill(pid, 0) also observes a zombie: exit alone is not reap.
                    with (
                        dependency_lease(identity, exclusive=True),
                        pytest.raises(ProcessLookupError),
                    ):
                        os.kill(child_pid, 0)
                    break
                except EnvironmentError as error:
                    assert error.reason == Reason.PREPARATION_IN_PROGRESS
                assert time.monotonic() < deadline, "cancellation did not release the lease"
                time.sleep(0.01)
            assert process.wait(timeout=5) == 128 + number
            assert json.loads((root / "child-term.json").read_text()) == {"leaseRetained": True}
            with pytest.raises(ProcessLookupError):
                os.kill(campaign_pid, 0)
            (output,) = (root / ".api-contract").iterdir()
            summary = json.loads((output / "summary.json").read_text())
            assert summary["failureKind"] == "cancelled"
            assert summary["cancelled"] is True
            assert summary["exitCode"] is None
            assert "execution cancelled" in summary["error"]
            assert "controlled stdout" in (output / "stdout.txt").read_text()
            assert "controlled termination" in (output / "stdout.txt").read_text()
            assert (output / "stderr.txt").read_text() == "controlled stderr\n"
            assert (output / "failures/example.json").read_text() == '{"retained": true}\n'
        finally:
            if campaign_pid is None and campaign_pid_file.exists():
                campaign_pid = int(campaign_pid_file.read_text())
            if child_pid is None and (root / "child-ready.json").exists():
                child_pid = json.loads((root / "child-ready.json").read_text())["child"]
            for pid in (child_pid, campaign_pid, process.pid):
                if pid is not None:
                    with suppress(ProcessLookupError):
                        os.killpg(pid, signal.SIGKILL)
            process.wait(timeout=5)

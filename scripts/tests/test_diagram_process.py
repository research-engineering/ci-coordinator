from __future__ import annotations

import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager, nullcontext, suppress
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import patch

import pytest

_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_NODE_FIXTURE = _SOURCE_ROOT / "frontend/tools/diagrams-process-checks.mjs"
_WRAPPER_TIMEOUT = 15
_QUALIFIED_NODEIDS = frozenset(
    f"scripts/tests/test_diagram_process.py::{name}"
    for name in (
        "qualify_checked_push_closes_its_node_and_chromium_before_returning[SIGINT]",
        "qualify_checked_push_closes_its_node_and_chromium_before_returning[SIGTERM]",
        "qualify_checked_push_closes_its_node_and_chromium_before_returning[timeout]",
        "qualify_omitted_hook_handler_is_detected_and_harness_recovers_owned_processes",
        "qualify_checked_push_closes_pre_browser_git_before_returning[selection]",
        "qualify_checked_push_closes_pre_browser_git_before_returning[inventory]",
        "qualify_production_hook_accepts_valid_and_rejects_invalid_push",
        "qualify_production_hook_bypass_is_detected_by_the_rejection_oracle",
    )
)


class QualificationAdmission:
    def __init__(self) -> None:
        self.reports: list[tuple[str, str, str]] = []

    def pytest_collection_finish(self, session: pytest.Session) -> None:
        selected = [item.nodeid for item in session.items]
        if len(selected) != len(_QUALIFIED_NODEIDS) or set(selected) != _QUALIFIED_NODEIDS:
            raise pytest.UsageError(
                f"process qualification requires exactly {sorted(_QUALIFIED_NODEIDS)}; "
                f"collected {sorted(selected)}"
            )

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.reports.append((report.nodeid, report.when, report.outcome))

    def complete(self) -> bool:
        return sorted(self.reports) == sorted(
            (nodeid, phase, "passed")
            for nodeid in _QUALIFIED_NODEIDS
            for phase in ("setup", "call", "teardown")
        )


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    parent: int
    group: int
    state: str
    started: str


def process_snapshot() -> dict[int, ProcessIdentity]:
    result = subprocess.run(
        ["ps", "-axo", "pid=,ppid=,pgid=,stat=,lstart="],
        check=True,
        capture_output=True,
        text=True,
        timeout=3,
        env={**os.environ, "LC_ALL": "C"},
    )
    observed = {}
    for line in result.stdout.splitlines():
        pid, parent, group, state, started = line.split(maxsplit=4)
        row = ProcessIdentity(int(pid), int(parent), int(group), state, started)
        observed[row.pid] = row
    return observed


def executing(owned: dict[int, ProcessIdentity]) -> dict[int, ProcessIdentity]:
    observed = process_snapshot()
    return {
        pid: row
        for pid, original in owned.items()
        if (row := observed.get(pid)) is not None
        and row.started == original.started
        and not row.state.startswith("Z")
    }


def assert_quiescent(owned: dict[int, ProcessIdentity]) -> None:
    survivors = executing(owned)
    assert not survivors, f"owned processes still execute: {survivors}"


def read_receipt(directory: Path, name: str) -> dict[str, object] | None:
    path = directory / f"{name}.json"
    if not path.exists():
        return None
    try:
        value: object = json.loads(path.read_text())
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        raise ValueError("process receipt must be an object")
    receipt: dict[str, object] = {}
    for key, entry in value.items():
        if not isinstance(key, str):
            raise ValueError("process receipt keys must be strings")
        receipt[key] = entry
    return receipt


def require_pid(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 1:
        raise ValueError("process receipt contains an invalid PID")
    return value


def capture_owned(directory: Path, owned: dict[int, ProcessIdentity]) -> None:
    observed = process_snapshot()
    pids = set(owned)
    for name in ("hook", "node", "git"):
        receipt = read_receipt(directory, name)
        if receipt is not None:
            pids.add(require_pid(receipt["pid"]))
    ready = read_receipt(directory, "ready")
    if ready is not None:
        processes = ready["processes"]
        if not isinstance(processes, list):
            raise ValueError("browser process receipt must list its owned PIDs")
        pids.update(require_pid(pid) for pid in processes)
        pids.add(require_pid(ready["browser"]))
    for pid in pids:
        if pid in observed:
            owned.setdefault(pid, observed[pid])
    live = {
        pid for pid, row in observed.items() if pid in owned and row.started == owned[pid].started
    }
    while descendants := {pid for pid, row in observed.items() if row.parent in live} - live:
        live.update(descendants)
    for pid in live:
        owned.setdefault(pid, observed[pid])
    groups = {owned[pid].group for pid in pids if pid in owned and owned[pid].group == pid}
    for pid, row in observed.items():
        if row.group in groups:
            owned.setdefault(pid, row)


def cleanup_owned(
    wrapper: subprocess.Popen[bytes], directory: Path, owned: dict[int, ProcessIdentity]
) -> None:
    capture_owned(directory, owned)
    if wrapper.poll() is None:
        wrapper.terminate()
        try:
            wrapper.wait(timeout=8)
        except subprocess.TimeoutExpired:
            wrapper.kill()
            wrapper.wait(timeout=3)
    capture_owned(directory, owned)
    for process_signal in (signal.SIGTERM, signal.SIGKILL):
        for pid in executing(owned):
            with suppress(ProcessLookupError):
                os.kill(pid, process_signal)
        deadline = time.monotonic() + 3
        while executing(owned) and time.monotonic() < deadline:
            time.sleep(0.02)
    assert_quiescent(owned)


@pytest.fixture(scope="module")
def diagram_runtime() -> None:
    node = shutil.which("node")
    available = False
    if node is not None and (_SOURCE_ROOT / "frontend/node_modules/@playwright/test").is_dir():
        available = (
            subprocess.run(
                [
                    node,
                    "--input-type=module",
                    "-e",
                    "import { existsSync } from 'node:fs'; "
                    "import { chromium } from '@playwright/test'; "
                    "process.exit(existsSync(chromium.executablePath()) ? 0 : 1)",
                ],
                cwd=_SOURCE_ROOT / "frontend",
                capture_output=True,
                check=False,
                timeout=5,
            ).returncode
            == 0
        )
    if not available:
        pytest.fail(
            "diagram process qualification requires installed Node, Playwright and Chromium"
        )


@dataclass
class CheckedPush:
    wrapper: subprocess.Popen[bytes]
    directory: Path
    remote: Path
    owned: dict[int, ProcessIdentity]
    ready: dict[str, object]
    stderr: Path


@contextmanager
def running_push(
    temporary: Path, trigger: str, *, omit_hook_handler: bool = False
) -> Iterator[CheckedPush]:
    from scripts.bounded_git import run_git

    root = temporary / "checkout"
    remote = temporary / "remote.git"
    receipts = temporary / "receipts"
    root.mkdir()
    remote.mkdir()
    receipts.mkdir()
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_GLOBAL=os.devnull,
        PYTHONPATH=str(_SOURCE_ROOT),
    )
    with patch.dict(os.environ, environment, clear=True):
        run_git(root, ["init", "--quiet"])
        run_git(remote, ["init", "--bare", "--quiet"])
        (root / "README.md").write_text("# Disposable lifecycle qualification\n")
        run_git(root, ["add", "README.md"])
        run_git(
            root,
            [
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--quiet",
                "-m",
                "fixture",
            ],
        )
    command = [
        sys.executable,
        "-m",
        "scripts.tests.test_diagram_process",
        "--child",
        str(root),
        trigger,
        "omit" if omit_hook_handler else "installed",
    ]
    hooks = root / ".githooks"
    hooks.mkdir()
    hook = hooks / "pre-push"
    hook.write_text(f"#!/bin/sh\nexec {shlex.join([*command, '--hook'])}\n")
    hook.chmod(0o700)
    stderr = temporary / "wrapper.stderr"
    ready_name = "git" if trigger in {"selection", "inventory"} else "ready"
    with stderr.open("wb") as error_output:
        wrapper = subprocess.Popen(
            command,
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=error_output,
            start_new_session=True,
        )
        owned: dict[int, ProcessIdentity] = {}
        try:
            observed = process_snapshot()
            if wrapper.pid in observed:
                owned[wrapper.pid] = observed[wrapper.pid]
            deadline = time.monotonic() + 12
            ready = read_receipt(receipts, ready_name)
            while ready is None and wrapper.poll() is None and time.monotonic() < deadline:
                capture_owned(receipts, owned)
                time.sleep(0.02)
                ready = read_receipt(receipts, ready_name)
            capture_owned(receipts, owned)
            assert ready is not None, stderr.read_text()
            assert wrapper.poll() is None, stderr.read_text()
            yield CheckedPush(wrapper, receipts, remote, owned, ready, stderr)
        finally:
            cleanup_owned(wrapper, receipts, owned)


@pytest.mark.parametrize("trigger", ["SIGINT", "SIGTERM", "timeout"])
def qualify_checked_push_closes_its_node_and_chromium_before_returning(
    tmp_path: Path, diagram_runtime: None, trigger: str
) -> None:
    from scripts.bounded_git import run_git

    with running_push(tmp_path, trigger) as push:
        node = push.owned[require_pid(push.ready["node"])]
        browser = push.owned[require_pid(push.ready["browser"])]
        hook_receipt = read_receipt(push.directory, "hook")
        assert hook_receipt is not None
        hook = push.owned[require_pid(hook_receipt["pid"])]
        assert node.group == node.pid
        assert browser.group == browser.pid
        assert len({hook.group, node.group, browser.group}) == 3
        assert node.pid in executing(push.owned) and browser.pid in executing(push.owned)
        if trigger != "timeout":
            push.wrapper.send_signal(getattr(signal, trigger))
        returncode = push.wrapper.wait(timeout=_WRAPPER_TIMEOUT + 8)
        capture_owned(push.directory, push.owned)
        assert_quiescent(push.owned)
        expected = 1 if trigger == "timeout" else 128 + getattr(signal, trigger)
        assert returncode == expected, push.stderr.read_text()
        assert read_receipt(push.directory, "node-exit") == {"code": 128 + signal.SIGTERM}
        assert ("timeout" if trigger == "timeout" else "cancelled") in push.stderr.read_text()
        assert run_git(push.remote, ["show-ref"], check=False).status == 1


def qualify_omitted_hook_handler_is_detected_and_harness_recovers_owned_processes(
    tmp_path: Path, diagram_runtime: None
) -> None:
    with running_push(tmp_path, "SIGTERM", omit_hook_handler=True) as push:
        push.wrapper.send_signal(signal.SIGTERM)
        assert push.wrapper.wait(timeout=8) == 128 + signal.SIGTERM
        assert require_pid(push.ready["node"]) in executing(push.owned)
        assert require_pid(push.ready["browser"]) in executing(push.owned)
        with pytest.raises(AssertionError, match="owned processes still execute"):
            assert_quiescent(push.owned)
    assert_quiescent(push.owned)


@pytest.mark.parametrize("stage", ["selection", "inventory"])
def qualify_checked_push_closes_pre_browser_git_before_returning(
    tmp_path: Path, stage: str
) -> None:
    from scripts.bounded_git import run_git

    with running_push(tmp_path, stage) as push:
        child = push.owned[require_pid(push.ready["pid"])]
        hook_receipt = read_receipt(push.directory, "hook")
        assert hook_receipt is not None
        hook = push.owned[require_pid(hook_receipt["pid"])]
        arguments = push.ready["arguments"]
        assert isinstance(arguments, list) and arguments
        if arguments[:1] == ["--no-replace-objects"]:
            arguments = arguments[1:]
        assert arguments[0] == ("rev-parse" if stage == "selection" else "ls-files")
        assert push.ready["stage"] == stage
        assert child.group == child.pid and child.group != hook.group
        assert child.pid in executing(push.owned)
        assert read_receipt(push.directory, "node") is None
        assert read_receipt(push.directory, "ready") is None
        push.wrapper.send_signal(signal.SIGTERM)
        returncode = push.wrapper.wait(timeout=8)
        capture_owned(push.directory, push.owned)
        assert_quiescent(push.owned)
        assert returncode == 128 + signal.SIGTERM, push.stderr.read_text()
        assert "cancelled" in push.stderr.read_text()
        assert read_receipt(push.directory, "node") is None
        assert read_receipt(push.directory, "ready") is None
        assert run_git(push.remote, ["show-ref"], check=False).status == 1


@dataclass(frozen=True)
class ProductionHookRepository:
    root: Path
    remote: Path
    environment: dict[str, str]


@pytest.fixture
def production_hook_repository(tmp_path: Path) -> ProductionHookRepository:
    from scripts.bounded_git import run_git
    from scripts.diagram_contract import is_evaluator_path

    root = tmp_path / "checkout"
    remote = tmp_path / "remote.git"
    root.mkdir()
    remote.mkdir()
    run_git(root, ["init", "--quiet"])
    run_git(remote, ["init", "--bare", "--quiet"])
    inventory = run_git(
        _SOURCE_ROOT, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"]
    ).stdout.split("\0")
    for relative in sorted(
        {path for path in inventory if is_evaluator_path(path)} | {".gitignore"}
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_SOURCE_ROOT / relative, target)
    hook = root / ".githooks/pre-push"
    assert hook.read_bytes() == (_SOURCE_ROOT / ".githooks/pre-push").read_bytes()
    assert os.access(hook, os.X_OK)
    for relative in ("backend/.venv", "node_modules", "frontend/node_modules"):
        (root / relative).symlink_to(_SOURCE_ROOT / relative, target_is_directory=True)
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("GIT_") and key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    environment.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    return ProductionHookRepository(root, remote, environment)


def commit_production_fixture(root: Path, body: str) -> str:
    from scripts.bounded_git import run_git

    (root / "README.md").write_text(f"```mermaid\n{body}\n```\n")
    run_git(root, ["add", "--all"])
    run_git(
        root,
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "--quiet",
            "-m",
            "fixture",
        ],
    )
    return run_git(root, ["rev-parse", "HEAD"]).stdout.strip()


def production_push(
    repository: ProductionHookRepository, attempt: Path
) -> subprocess.CompletedProcess[str]:
    attempt.mkdir()
    error_path = attempt / "stderr"
    output_path = attempt / "stdout"
    command = [
        str(repository.root / "backend/.venv/bin/python"),
        "-m",
        "scripts.diagram_push",
        str(repository.remote),
        "HEAD:refs/heads/lifecycle",
    ]
    with error_path.open("wb") as error_output, output_path.open("wb") as output:
        process = subprocess.Popen(
            command,
            cwd=repository.root,
            env=repository.environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=error_output,
            start_new_session=True,
        )
        owned: dict[int, ProcessIdentity] = {}
        try:
            observed = process_snapshot()
            if process.pid in observed:
                owned[process.pid] = observed[process.pid]
            deadline = time.monotonic() + 40
            while process.poll() is None and time.monotonic() < deadline:
                capture_owned(attempt, owned)
                time.sleep(0.02)
            assert process.poll() is not None, error_path.read_text()
            returncode = process.wait(timeout=1)
            capture_owned(attempt, owned)
            assert_quiescent(owned)
            return subprocess.CompletedProcess(
                command, returncode, output_path.read_text(), error_path.read_text()
            )
        finally:
            cleanup_owned(process, attempt, owned)


def remote_tip(repository: ProductionHookRepository) -> str | None:
    from scripts.bounded_git import run_git

    result = run_git(
        repository.remote, ["rev-parse", "--verify", "refs/heads/lifecycle"], check=False
    )
    return result.stdout.strip() if result.status == 0 else None


def assert_invalid_push_rejected(
    result: subprocess.CompletedProcess[str],
    repository: ProductionHookRepository,
    previous: str | None,
) -> None:
    assert result.returncode != 0, "production hook admitted an invalid diagram"
    assert "README.md:" in result.stderr and "Parse error" in result.stderr, result.stderr
    assert "diagram validation failed" in result.stderr, result.stderr
    assert remote_tip(repository) == previous


def qualify_production_hook_accepts_valid_and_rejects_invalid_push(
    tmp_path: Path, production_hook_repository: ProductionHookRepository, diagram_runtime: None
) -> None:
    repository = production_hook_repository
    valid = commit_production_fixture(repository.root, "flowchart TD\nA[Ready] --> B[Done]")
    accepted = production_push(repository, tmp_path / "valid")
    assert accepted.returncode == 0, accepted.stderr
    assert f"Checking documentation diagrams at {valid}" in accepted.stderr
    assert remote_tip(repository) == valid
    invalid = commit_production_fixture(repository.root, "flowchart TD\nA[unclosed")
    rejected = production_push(repository, tmp_path / "invalid")
    assert f"Checking documentation diagrams at {invalid}" in rejected.stderr
    assert_invalid_push_rejected(rejected, repository, valid)


def qualify_production_hook_bypass_is_detected_by_the_rejection_oracle(
    tmp_path: Path, production_hook_repository: ProductionHookRepository
) -> None:
    repository = production_hook_repository
    invalid = commit_production_fixture(repository.root, "flowchart TD\nA[unclosed")
    (repository.root / ".githooks/pre-push").write_text("#!/bin/sh\nexit 0\n")
    accepted = production_push(repository, tmp_path / "bypassed")
    assert accepted.returncode == 0, accepted.stderr
    assert remote_tip(repository) == invalid
    with pytest.raises(AssertionError, match="production hook admitted an invalid diagram"):
        assert_invalid_push_rejected(accepted, repository, None)


def child_main(root: Path, trigger: str, omit_handler: str, hook: bool) -> int:
    from scripts import bounded_git, diagram_check, diagram_push, documentation_graph_filesystem
    from scripts.bounded_process import (
        CommandResult,
        DecodeErrors,
        InteractiveResult,
        StopPredicate,
        run_interactive,
        spawn,
    )
    from scripts.diagram_contract import (
        Diagram,
        DiagramManifest,
        digest_bytes,
        digest_json,
        load_profile,
    )
    from scripts.diagram_inventory import build_manifest
    from scripts.diagram_process import DiagramCancellation

    receipts = root.parent / "receipts"
    profile = load_profile(_SOURCE_ROOT)
    body = "flowchart TD\nA --> B\n"
    identity = {
        "path": "README.md",
        "line": 1,
        "endLine": 4,
        "bodySha256": digest_bytes(body.encode()),
    }
    diagram = Diagram.model_validate(
        {
            **identity,
            "id": digest_json(identity),
            "body": body,
            "semanticBody": body,
            "type": "flowchart",
        }
    )
    payload = {
        "schemaVersion": 1,
        "revision": None,
        "files": {"README.md": digest_bytes(f"```mermaid\n{body}```\n".encode())},
        "diagrams": [diagram.model_dump()],
        "profile": profile.model_dump(),
    }
    manifest = DiagramManifest.model_validate({**payload, "digest": digest_json(payload)})
    delayed = False

    def delayed_git_spawn(
        command: str,
        arguments: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        max_buffer: int,
        timeout_seconds: float,
        decode_errors: DecodeErrors = "replace",
        stop_requested: StopPredicate | None = None,
    ) -> CommandResult:
        nonlocal delayed
        git_arguments = tuple(arguments)
        if git_arguments[:1] == ("--no-replace-objects",):
            git_arguments = git_arguments[1:]
        target = (trigger == "selection" and git_arguments[:2] == ("rev-parse", "--verify")) or (
            trigger == "inventory" and git_arguments[:1] == ("ls-files",)
        )
        if target and not delayed:
            delayed = True
            arguments = [
                str(Path(__file__).resolve()),
                "--git-gate",
                str(receipts),
                trigger,
                command,
                *arguments,
            ]
            command = sys.executable
        return spawn(
            command,
            arguments,
            cwd=cwd,
            env=env,
            max_buffer=max_buffer,
            timeout_seconds=timeout_seconds,
            decode_errors=decode_errors,
            stop_requested=stop_requested,
        )

    def source_manifest(
        _root: Path,
        _revision: str | None = None,
        *,
        deadline: float | None = None,
        stop_requested: StopPredicate | None = None,
    ) -> DiagramManifest:
        return build_manifest(
            _SOURCE_ROOT, "HEAD", deadline=deadline, stop_requested=stop_requested
        )

    def renderer_spawn(
        command: str,
        arguments: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        input_text: str,
        timeout_seconds: float,
        max_buffer: int,
        stop_requested: StopPredicate | None,
    ) -> CommandResult:
        return spawn(
            command,
            ["--import", str(_NODE_FIXTURE), *arguments, "--artifacts", str(receipts)],
            cwd=_SOURCE_ROOT / "frontend",
            env=env,
            input_text=input_text,
            timeout_seconds=40,
            max_buffer=max_buffer,
            stop_requested=stop_requested,
        )

    def bounded_push(
        command: str,
        arguments: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: float,
        stop_requested: StopPredicate | None,
        graceful_seconds: float,
    ) -> InteractiveResult:
        return run_interactive(
            command,
            arguments,
            cwd=cwd,
            env=env,
            timeout_seconds=_WRAPPER_TIMEOUT if trigger == "timeout" else 40,
            stop_requested=stop_requested,
            graceful_seconds=graceful_seconds,
        )

    if hook:
        (receipts / "hook.json").write_text(json.dumps({"pid": os.getpid()}))
    sys.argv = (
        ["diagram-push", "--hook"]
        if hook
        else ["diagram-push", str(root.parent / "remote.git"), "HEAD:refs/heads/lifecycle"]
    )
    with ExitStack() as patches:
        patches.enter_context(patch.object(diagram_push, "REPO_ROOT", root))
        patches.enter_context(patch.object(diagram_push, "run_interactive", bounded_push))
        # Python package admission is outside this process-boundary witness.
        patches.enter_context(patch.object(diagram_check, "load_profile", return_value=profile))
        patches.enter_context(patch.object(diagram_check, "admit_python_packages"))
        if trigger == "inventory":
            patches.enter_context(patch.object(diagram_check, "build_manifest", source_manifest))
        else:
            patches.enter_context(
                patch.object(diagram_check, "build_manifest", return_value=manifest)
            )
        patches.enter_context(patch.object(diagram_check, "spawn", renderer_spawn))
        if hook and trigger == "selection":
            patches.enter_context(patch.object(bounded_git, "spawn", delayed_git_spawn))
        if hook and trigger == "inventory":
            patches.enter_context(
                patch.object(documentation_graph_filesystem, "spawn", delayed_git_spawn)
            )
        if hook and omit_handler == "omit":
            patches.enter_context(
                patch.object(
                    diagram_push,
                    "cancellation_signals",
                    lambda: nullcontext(DiagramCancellation()),
                )
            )
        return diagram_push.main()


if __name__ == "__main__":
    if len(sys.argv) > 5 and sys.argv[1] == "--git-gate":
        (Path(sys.argv[2]) / "git.json").write_text(
            json.dumps(
                {
                    "pid": os.getpid(),
                    "stage": sys.argv[3],
                    "command": sys.argv[4],
                    "arguments": sys.argv[5:],
                }
            )
        )
        time.sleep(25)
        raise SystemExit("delayed Git boundary reached its finite guard")
    if sys.argv[1:] == ["--qualify"]:
        sys.path.insert(0, str(_SOURCE_ROOT))
        admission = QualificationAdmission()
        status = pytest.main(
            [
                "-q",
                "--rootdir",
                str(_SOURCE_ROOT),
                "-o",
                "python_functions=qualify_*",
                str(Path(__file__).resolve()),
            ],
            plugins=[admission],
        )
        if status == 0 and not admission.complete():
            print(
                f"process qualification requires all {len(_QUALIFIED_NODEIDS)} cases "
                "to pass without skips",
                file=sys.stderr,
            )
            status = pytest.ExitCode.TESTS_FAILED
        raise SystemExit(status)
    if len(sys.argv) not in {5, 6} or sys.argv[1] != "--child":
        raise SystemExit("use pytest to run this process qualification")
    raise SystemExit(
        child_main(Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5:] == ["--hook"])
    )

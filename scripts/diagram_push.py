from __future__ import annotations

import os
import re
import shutil
import stat
import sys
from pathlib import Path

from scripts.bounded_git import run_git
from scripts.bounded_process import StopPredicate, run_interactive, spawn
from scripts.diagram_check import REPO_ROOT, check
from scripts.diagram_contract import is_diagram_input
from scripts.diagram_inventory import resolve_commit
from scripts.diagram_process import cancellation_signals

_OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
_MAX_PUSH_INPUT = 65_536


def selected_commits(
    root: Path, payload: str, *, stop_requested: StopPredicate | None = None
) -> tuple[str, ...]:
    if len(payload.encode("utf-8")) > _MAX_PUSH_INPUT or (payload and not payload.endswith("\n")):
        raise ValueError("pre-push input exceeds its bound or is truncated")
    selected: set[str] = set()
    for line in payload.splitlines():
        fields = line.split()
        if len(fields) != 4:
            raise ValueError("pre-push record must contain exactly four fields")
        _local_ref, local_oid, _remote_ref, remote_oid = fields
        if not _OID.fullmatch(local_oid) or not _OID.fullmatch(remote_oid):
            raise ValueError("pre-push record contains an invalid object identifier")
        if set(local_oid) == {"0"}:
            continue
        commit = resolve_commit(root, local_oid, stop_requested=stop_requested)
        remote = run_git(
            root,
            [
                "--no-replace-objects",
                "rev-parse",
                "--verify",
                "--end-of-options",
                f"{remote_oid}^{{commit}}",
            ],
            check=False,
            stop_requested=stop_requested,
        )
        if set(remote_oid) == {"0"} or remote.status != 0:
            selected.add(commit)
            continue
        changed = run_git(
            root,
            [
                "--no-replace-objects",
                "diff",
                "--name-only",
                "-z",
                "--no-renames",
                remote.stdout.strip(),
                commit,
                "--",
            ],
            stop_requested=stop_requested,
        ).stdout
        if changed and not changed.endswith("\0"):
            raise ValueError("pre-push changed-path inventory is truncated")
        if any(is_diagram_input(path) for path in changed.split("\0") if path):
            selected.add(commit)
    return tuple(sorted(selected))


def pre_push(root: Path, payload: str, *, stop_requested: StopPredicate | None = None) -> None:
    for revision in selected_commits(root, payload, stop_requested=stop_requested):
        print(f"Checking documentation diagrams at {revision}", file=sys.stderr)
        check(root, revision, stop_requested=stop_requested)


def admit_existing_hooks(
    root: Path,
    *,
    environment: dict[str, str] | None = None,
    stop_requested: StopPredicate | None = None,
) -> None:
    environment = dict(os.environ if environment is None else environment)
    git = shutil.which("git", path=environment.get("PATH", os.defpath))
    if git is None:
        raise ValueError("Git is unavailable")
    identity_args = [
        "--no-replace-objects",
        "rev-parse",
        "--path-format=absolute",
        "--show-toplevel",
        "--absolute-git-dir",
        "--git-common-dir",
    ]
    expected = run_git(root, identity_args, stop_requested=stop_requested).stdout.splitlines()
    actual = spawn(
        git,
        identity_args,
        cwd=root,
        env=environment,
        timeout_seconds=30,
        max_buffer=65_536,
        stop_requested=stop_requested,
    )
    observed = actual.stdout.splitlines()
    if (
        actual.error
        or actual.status != 0
        or len(expected) != 3
        or len(observed) != 3
        or Path(expected[0]).resolve() != root.resolve()
        or tuple(Path(path).resolve() for path in observed)
        != tuple(Path(path).resolve() for path in expected)
    ):
        raise ValueError("Git environment conflicts with the checker repository identity")
    configured = spawn(
        git,
        ["--no-replace-objects", "config", "--get", "core.hooksPath"],
        cwd=root,
        env=environment,
        timeout_seconds=30,
        max_buffer=65_536,
        stop_requested=stop_requested,
    )
    if configured.error or configured.status not in {0, 1}:
        raise ValueError("cannot determine the existing hooks configuration")
    if configured.status == 0:
        raise ValueError(
            "core.hooksPath is already configured; "
            "compose the diagram hook explicitly instead of replacing it"
        )
    hooks = spawn(
        git,
        [
            "--no-replace-objects",
            "rev-parse",
            "--path-format=absolute",
            "--git-path",
            "hooks/pre-push",
        ],
        cwd=root,
        env=environment,
        timeout_seconds=30,
        max_buffer=65_536,
        stop_requested=stop_requested,
    )
    if hooks.error or hooks.status != 0 or len(hooks.stdout.splitlines()) != 1:
        raise ValueError("cannot determine the existing pre-push hook path")
    existing = Path(hooks.stdout.strip())
    if existing.exists() or existing.is_symlink():
        raise ValueError("an existing pre-push hook would be bypassed; compose hooks explicitly")


def push(root: Path, arguments: list[str], *, stop_requested: StopPredicate | None = None) -> int:
    if any(argument.startswith("--no-ver") for argument in arguments):
        raise ValueError("the checked push command does not accept --no-verify")
    environment = dict(os.environ)
    admit_existing_hooks(root, environment=environment, stop_requested=stop_requested)
    hook = root / ".githooks/pre-push"
    if not stat.S_ISREG(hook.lstat().st_mode) or not os.access(hook, os.X_OK):
        raise ValueError("the diagram pre-push hook must be a regular executable file")
    executable = shutil.which("git", path=environment.get("PATH", os.defpath))
    if executable is None:
        raise ValueError("Git is unavailable")
    result = run_interactive(
        executable,
        ["--no-replace-objects", "-c", f"core.hooksPath={root / '.githooks'}", "push", *arguments],
        cwd=root,
        env=environment,
        timeout_seconds=900,
        stop_requested=stop_requested,
        graceful_seconds=5,
    )
    if result.failure_kind or not result.process_group_quiescent or result.returncode is None:
        raise ValueError(f"checked push did not complete: {result.failure_kind}")
    return result.returncode


def main() -> int:
    with cancellation_signals() as cancellation:
        try:
            if sys.argv[1:] == ["--hook"]:
                payload = sys.stdin.read(_MAX_PUSH_INPUT + 1)
                pre_push(REPO_ROOT, payload, stop_requested=cancellation.requested)
                status = 0
            else:
                arguments = sys.argv[1:]
                if arguments[:1] == ["--"]:
                    arguments = arguments[1:]
                status = push(REPO_ROOT, arguments, stop_requested=cancellation.requested)
        except (ValueError, OSError, RuntimeError) as error:
            print(f"diagram pre-push: {error}", file=sys.stderr)
            status = 1
        return cancellation.exit_code(status)


if __name__ == "__main__":
    raise SystemExit(main())

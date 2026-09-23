from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from scripts.bounded_git import capture_git_text
from scripts.mutation.detached_worktree_lifecycle import DetachedWorktreeLifecycle
from scripts.proofkit_branch_head_range import (
    BranchHeadReceipt,
    admit_exact_branch_head_range,
    assert_same_branch_head_receipt,
)
from scripts.quality_plan import (
    MAX_COMMAND_OUTPUT_BYTES,
    WitnessCommand,
    load_quality_plan,
    project_command_environment,
)

ReceiptReader = Callable[[Path, Mapping[str, str]], BranchHeadReceipt]
CommandExecutor = Callable[[WitnessCommand], None]
ReceiptWriter = Callable[[str], None]


def run_branch_head_quality(
    repository_root: Path,
    *,
    environment: Mapping[str, str],
    execute_command: CommandExecutor | None = None,
    read_receipt: ReceiptReader = admit_exact_branch_head_range,
    write_receipt: ReceiptWriter = print,
) -> BranchHeadReceipt:
    root = repository_root.resolve()
    lifecycle = DetachedWorktreeLifecycle(
        repo_root=root,
        temp_prefix="ci-branch-head-quality-",
    )
    failure: BaseException | None = None
    initial_receipt: BranchHeadReceipt | None = None
    try:
        lifecycle.install_signal_handlers()
        initial_head = _git_capture(root, ("rev-parse", "HEAD"))
        lifecycle.assert_running()
        initial_receipt = read_receipt(root, environment)
        lifecycle.assert_running()
        assert_tracked_tree_clean(root, "before branch-head quality")
        lifecycle.assert_running()
        executor = execute_command or _lifecycle_executor(lifecycle, environment)
        for command in load_quality_plan(root).branch_head_commands():
            executor(command)

        if lifecycle.received_signal is None:
            assert_tracked_tree_clean(root, "after branch-head quality")
            final_head = _git_capture(root, ("rev-parse", "HEAD"))
            if final_head != initial_head:
                raise RuntimeError(
                    f"branch-head quality changed HEAD from {initial_head} to {final_head}"
                )
            assert_same_branch_head_receipt(
                initial_receipt,
                read_receipt(root, environment),
            )
    except BaseException as error:
        failure = error
    finally:
        try:
            cleanup = lifecycle.cleanup()
        finally:
            lifecycle.dispose_signal_handlers()
        if cleanup.state != "passed" and failure is None:
            failure = RuntimeError("branch-head quality lifecycle cleanup failed")

    if lifecycle.received_signal is not None:
        lifecycle.rethrow_signal_if_needed()
        raise AssertionError("signal rethrow unexpectedly returned")
    if failure is not None:
        raise failure
    if initial_receipt is None:
        raise RuntimeError("branch-head quality did not complete")
    write_receipt(
        "branch-head quality passed for "
        f"{initial_receipt.base_commit}...{initial_receipt.head_commit}"
    )
    return initial_receipt


def assert_tracked_tree_clean(repository_root: Path, stage: str) -> None:
    status = _git_capture(
        repository_root,
        ("status", "--porcelain", "--untracked-files=all"),
    )
    if status:
        raise ValueError(f"{stage}: tracked worktree must equal HEAD\n{status}")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments:
        print("usage: python -m scripts.proofkit_branch_head_quality", file=sys.stderr)
        return 2
    try:
        root = Path(_git_capture(Path.cwd(), ("rev-parse", "--show-toplevel")))
        run_branch_head_quality(root, environment=os.environ)
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


def _lifecycle_executor(
    lifecycle: DetachedWorktreeLifecycle,
    source_environment: Mapping[str, str],
) -> CommandExecutor:
    def execute(command: WitnessCommand) -> None:
        result = lifecycle.run(
            command.argv[0],
            command.argv[1:],
            cwd=command.cwd,
            env=project_command_environment(command, source_environment),
            max_buffer=MAX_COMMAND_OUTPUT_BYTES,
            timeout_ms=command.timeout_ms,
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        if result.timed_out:
            raise RuntimeError(
                f"witness command {command.command_id} timed out after {command.timeout_ms} ms"
            )
        if result.error is not None:
            raise RuntimeError(f"witness command {command.command_id} failed: {result.error}")
        if result.status != 0:
            raise RuntimeError(
                f"witness command {command.command_id} failed with status {result.status}"
            )

    return execute


def _git_capture(repository_root: Path, arguments: Sequence[str]) -> str:
    return capture_git_text(repository_root, arguments, strip=True)


if __name__ == "__main__":
    raise SystemExit(main())

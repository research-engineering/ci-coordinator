from __future__ import annotations

import contextlib
import os
import resource
import signal
import stat
import subprocess
import tempfile
from pathlib import Path

import scanner


def run(
    argv: list[str], cwd: Path, environment: dict[str, str], limit: int, timeout: int
) -> tuple[int, bytes]:
    def set_file_bound() -> None:
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))

    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(  # noqa: S603 - fixed shell-free packaged tool arguments
            argv,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            preexec_fn=set_file_bound,
        )
        try:
            status = process.wait(timeout=timeout)
            try:
                os.killpg(process.pid, 0)
            except ProcessLookupError:
                pass
            else:
                raise scanner.ScanError(
                    "tool left a child process running", stage="process", code="residual_process"
                )
        except BaseException:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
            raise scanner.ScanError(
                "tool execution did not complete", stage="process", code="incomplete_process"
            ) from None
        output.seek(0)
        payload = output.read(limit + 1)
        if len(payload) > limit:
            raise scanner.ScanError(
                "tool output exceeded its bound", stage="process", code="output_limit"
            )
        return status, payload


def read(root: Path, relative: Path, maximum: int) -> bytes:
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ValueError("relative regular file required")
    parent = root
    for segment in relative.parts[:-1]:
        parent /= segment
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise ValueError("real parent directory required")
    path = root / relative
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
        raise ValueError("bounded regular file required")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        opened = os.fstat(descriptor)
        if opened != before:
            raise ValueError("file changed before read")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            payload = stream.read(maximum + 1)
        after = os.fstat(descriptor)

        def identity(item: os.stat_result) -> tuple[int, int, int, int, int]:
            return item.st_dev, item.st_ino, item.st_mode, item.st_size, item.st_mtime_ns

        if (
            len(payload) != before.st_size
            or identity(before) != identity(after)
            or identity(after) != identity(path.lstat())
        ):
            raise ValueError("file changed during read")
        return payload
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(scanner.main(run, read))

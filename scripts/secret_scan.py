from __future__ import annotations

import importlib.util
from pathlib import Path

from scripts.bounded_git import BoundedGitError, run_git
from scripts.bounded_process import spawn
from scripts.repository_paths import read_repository_regular_file

ACTION_ROOT = Path(__file__).resolve().parents[1] / ".github" / "actions" / "secret-scan"
_spec = importlib.util.spec_from_file_location("secret_scan_core", ACTION_ROOT / "scanner.py")
if _spec is None or _spec.loader is None:
    raise RuntimeError("packaged secret scanner unavailable")
scanner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scanner)


def run(
    argv: list[str], cwd: Path, environment: dict[str, str], limit: int, timeout: int
) -> tuple[int, bytes]:
    if Path(argv[0]).name == "git":
        try:
            git_result = run_git(
                cwd,
                argv[1:],
                check=False,
                decode_errors="surrogateescape",
                source_environment=environment,
                max_buffer=limit,
                timeout_seconds=timeout,
            )
        except BoundedGitError as error:
            raise scanner.ScanError(
                "bounded Git scope command failed", stage="process", code="bounded_git_failure"
            ) from error
        return git_result.status, (git_result.stdout + git_result.stderr).encode(
            "utf-8", "surrogateescape"
        )
    result = spawn(
        argv[0],
        argv[1:],
        cwd=cwd,
        env=environment,
        max_buffer=limit,
        timeout_seconds=timeout,
        decode_errors="surrogateescape",
    )
    if result.error is not None or result.status is None:
        raise scanner.ScanError(
            "bounded scanner process failed", stage="process", code="bounded_process_failure"
        )
    return result.status, (result.stdout + result.stderr).encode("utf-8", "surrogateescape")


def read(root: Path, relative: Path, maximum: int) -> bytes:
    return read_repository_regular_file(root, relative, "secret scan input", maximum_bytes=maximum)


if __name__ == "__main__":
    raise SystemExit(scanner.main(run, read, default_root=ACTION_ROOT.parents[2]))

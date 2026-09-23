"""Trusted launcher for an exact-commit consumer contract lab image."""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import selectors
import shutil
import signal
import subprocess
import sys
import tarfile
import time
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Final

_COORDINATOR_PACKAGE_PATH: Final = "backend/src/ci_coordinator"
_GIT_TIMEOUT_SECONDS: Final = 30
_LAB_TIMEOUT_SECONDS: Final = 180
_MAX_ARCHIVE_BYTES: Final = 20_971_520
_MAX_GIT_METADATA_BYTES: Final = 2_097_152
_MAX_GIT_STDERR_BYTES: Final = 65_536
_MAX_PACKAGE_BYTES: Final = 16_777_216
_MAX_PACKAGE_ENTRIES: Final = 1_024
_INTERNAL_LAUNCHER: Final = (
    "import sys;"
    "source=sys.argv.pop(1);"
    "sys.path.insert(0,source);"
    "from ci_coordinator.consumer_contract_lab.cli import main;"
    "raise SystemExit(main(sys.argv[1:]))"
)


class ConsumerLabBootstrapError(RuntimeError):
    """The trusted launcher cannot create or execute one exact source image."""


@dataclass(frozen=True, slots=True)
class _Blob:
    path: str
    object_id: str
    size: int


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    coordinator_root = Path(arguments.coordinator_root).expanduser().resolve()
    target_root = Path(arguments.target_root).expanduser().resolve()
    try:
        commit = _commit(coordinator_root)
        blobs = _package_blobs(coordinator_root, commit)
        archive = _archive(coordinator_root, commit)
        with TemporaryDirectory(prefix="ci-consumer-lab-source-") as temporary:
            image_root = (Path(temporary) / "source").resolve()
            _materialize(image_root, archive, blobs)
            source_root = image_root / "backend/src"
            package_root = source_root / "ci_coordinator"
            cache_root = (Path(temporary) / "pycache").resolve()
            cache_root.mkdir()
            return _run_exact_image(
                source_root=source_root,
                package_root=package_root,
                cache_root=cache_root,
                coordinator_root=coordinator_root,
                coordinator_commit=commit,
                target_root=target_root,
                profile=arguments.profile,
                output=arguments.output,
            )
    except ConsumerLabBootstrapError as error:
        return _reject(type(error).__name__)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ci-coordinator-consumer-lab")
    parser.add_argument("--coordinator-root", required=True)
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", required=True)
    return parser


def _commit(root: Path) -> str:
    if not root.is_dir():
        raise ConsumerLabBootstrapError("coordinator repository is unavailable")
    content = _git(root, ("rev-parse", "--verify", "HEAD^{commit}"), 128)
    try:
        commit = content.decode("ascii").strip()
    except UnicodeDecodeError as error:
        raise ConsumerLabBootstrapError("coordinator commit is not ASCII") from error
    if len(commit) != 40 or any(character not in "0123456789abcdef" for character in commit):
        raise ConsumerLabBootstrapError("coordinator commit is not a canonical SHA-1 object id")
    return commit


def _package_blobs(root: Path, commit: str) -> tuple[_Blob, ...]:
    content = _git(
        root,
        (
            "--literal-pathspecs",
            "ls-tree",
            "-lrz",
            "--full-tree",
            commit,
            "--",
            _COORDINATOR_PACKAGE_PATH,
        ),
        _MAX_GIT_METADATA_BYTES,
    )
    if not content or not content.endswith(b"\x00"):
        raise ConsumerLabBootstrapError("coordinator package tree is empty or malformed")
    blobs: list[_Blob] = []
    total_bytes = 0
    for item in content[:-1].split(b"\x00"):
        metadata, separator, raw_path = item.partition(b"\t")
        fields = metadata.split()
        if not separator or len(fields) != 4:
            raise ConsumerLabBootstrapError("coordinator package tree entry is malformed")
        mode, object_type, raw_object_id, raw_size = fields
        try:
            path = raw_path.decode("utf-8")
            object_id = raw_object_id.decode("ascii")
            size = int(raw_size)
        except (UnicodeDecodeError, ValueError) as error:
            raise ConsumerLabBootstrapError(
                "coordinator package tree entry identity is invalid"
            ) from error
        if (
            object_type != b"blob"
            or mode not in {b"100644", b"100755"}
            or not _is_package_path(path)
            or _is_python_cache_path(path)
            or len(object_id) != 40
            or any(character not in "0123456789abcdef" for character in object_id)
            or size < 0
        ):
            raise ConsumerLabBootstrapError("coordinator package tree contains an unsafe entry")
        total_bytes += size
        if total_bytes > _MAX_PACKAGE_BYTES:
            raise ConsumerLabBootstrapError("coordinator package exceeds its byte bound")
        blobs.append(_Blob(path, object_id, size))
        if len(blobs) > _MAX_PACKAGE_ENTRIES:
            raise ConsumerLabBootstrapError("coordinator package exceeds its entry bound")
    if not blobs or tuple(blob.path for blob in blobs) != tuple(
        sorted({blob.path for blob in blobs})
    ):
        raise ConsumerLabBootstrapError("coordinator package inventory is not canonical")
    return tuple(blobs)


def _archive(root: Path, commit: str) -> bytes:
    return _git(
        root,
        (
            "--literal-pathspecs",
            "archive",
            "--format=tar",
            commit,
            "--",
            _COORDINATOR_PACKAGE_PATH,
        ),
        _MAX_ARCHIVE_BYTES,
    )


def _materialize(image_root: Path, archive: bytes, blobs: tuple[_Blob, ...]) -> None:
    expected = {blob.path: blob for blob in blobs}
    observed: set[str] = set()
    image_root.mkdir()
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source:
            for member in source:
                path = member.name.rstrip("/")
                if member.isdir():
                    if not _is_package_directory(path):
                        raise ConsumerLabBootstrapError(
                            "coordinator archive contains an unsafe directory"
                        )
                    continue
                blob = expected.get(path)
                if (
                    blob is None
                    or path in observed
                    or not member.isfile()
                    or member.size != blob.size
                ):
                    raise ConsumerLabBootstrapError(
                        "coordinator archive inventory differs from its commit"
                    )
                stream = source.extractfile(member)
                if stream is None:
                    raise ConsumerLabBootstrapError("coordinator archive member is unavailable")
                content = stream.read(blob.size + 1)
                if len(content) != blob.size or _git_blob_id(content) != blob.object_id:
                    raise ConsumerLabBootstrapError(
                        "coordinator archive bytes differ from their commit"
                    )
                destination = image_root / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                with destination.open("xb") as output:
                    output.write(content)
                destination.chmod(0o400)
                observed.add(path)
    except (OSError, tarfile.TarError) as error:
        raise ConsumerLabBootstrapError("coordinator archive cannot be materialized") from error
    if observed != set(expected):
        raise ConsumerLabBootstrapError("coordinator archive is incomplete")


def _run_exact_image(
    *,
    source_root: Path,
    package_root: Path,
    cache_root: Path,
    coordinator_root: Path,
    coordinator_commit: str,
    target_root: Path,
    profile: str,
    output: str,
) -> int:
    command = (
        sys.executable,
        "-I",
        "-B",
        "-X",
        f"pycache_prefix={cache_root}",
        "-c",
        _INTERNAL_LAUNCHER,
        str(source_root),
        "--coordinator-root",
        str(coordinator_root),
        "--coordinator-commit",
        coordinator_commit,
        "--coordinator-package-root",
        str(package_root),
        "--target-root",
        str(target_root),
        "--profile",
        profile,
        "--output",
        output,
    )
    try:
        process = subprocess.Popen(  # noqa: S603
            command,
            cwd=target_root,
            start_new_session=True,
        )
        try:
            return process.wait(timeout=_LAB_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
            raise ConsumerLabBootstrapError("exact source image execution timed out") from error
    except OSError as error:
        raise ConsumerLabBootstrapError("exact source image cannot be executed") from error


def _git(root: Path, arguments: tuple[str, ...], max_output_bytes: int) -> bytes:
    executable = shutil.which("git")
    if executable is None:
        raise ConsumerLabBootstrapError("Git executable is unavailable")
    environment = {
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "HOME": str(root),
        "LANG": "C",
        "LC_ALL": "C",
    }
    stdout, _stderr, status = _capture_bounded(
        (executable, "-C", str(root), *arguments),
        cwd=root,
        env=environment,
        stdout_limit=max_output_bytes,
    )
    if status != 0:
        raise ConsumerLabBootstrapError("Git inspection rejected the coordinator source")
    return stdout


def _capture_bounded(
    command: tuple[str, ...],
    *,
    cwd: Path,
    env: dict[str, str],
    stdout_limit: int,
) -> tuple[bytes, bytes, int]:
    deadline = time.monotonic() + _GIT_TIMEOUT_SECONDS
    try:
        process = subprocess.Popen(  # noqa: S603
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        raise ConsumerLabBootstrapError("bounded Git inspection failed") from error
    if process.stdout is None or process.stderr is None:
        _kill_process_group(process)
        raise ConsumerLabBootstrapError("bounded Git inspection pipes are unavailable")

    stdout_descriptor = process.stdout.fileno()
    stderr_descriptor = process.stderr.fileno()
    output = {stdout_descriptor: bytearray(), stderr_descriptor: bytearray()}
    limits = {
        stdout_descriptor: stdout_limit,
        stderr_descriptor: _MAX_GIT_STDERR_BYTES,
    }
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    selector.register(process.stderr, selectors.EVENT_READ)
    completed = False
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ConsumerLabBootstrapError("bounded Git inspection timed out")
            events = selector.select(remaining)
            if not events:
                raise ConsumerLabBootstrapError("bounded Git inspection timed out")
            for key, _ in events:
                descriptor = key.fd
                try:
                    chunk = os.read(descriptor, 65_536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output[descriptor].extend(chunk)
                if (
                    len(output[descriptor]) > limits[descriptor]
                    or sum(map(len, output.values())) > stdout_limit + _MAX_GIT_STDERR_BYTES
                ):
                    raise ConsumerLabBootstrapError("bounded Git inspection exceeded output limits")
        status = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        completed = True
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ConsumerLabBootstrapError("bounded Git inspection failed") from error
    finally:
        selector.close()
        if not completed:
            _kill_process_group(process)
        process.stdout.close()
        process.stderr.close()
    return (
        bytes(output[stdout_descriptor]),
        bytes(output[stderr_descriptor]),
        status,
    )


def _kill_process_group(process: subprocess.Popen[bytes]) -> None:
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired as error:
        raise ConsumerLabBootstrapError("bounded Git process group cannot be reaped") from error


def _is_package_path(path: str) -> bool:
    return path.startswith(f"{_COORDINATOR_PACKAGE_PATH}/") and _is_safe_relative(path)


def _is_package_directory(path: str) -> bool:
    return (
        path
        in {
            "backend",
            "backend/src",
            _COORDINATOR_PACKAGE_PATH,
        }
        or path.startswith(f"{_COORDINATOR_PACKAGE_PATH}/")
    ) and _is_safe_relative(path)


def _is_safe_relative(path: str) -> bool:
    return (
        bool(path)
        and not path.startswith("/")
        and "\\" not in path
        and "\x00" not in path
        and all(part not in {"", ".", ".."} for part in path.split("/"))
        and PurePosixPath(path).as_posix() == path
    )


def _is_python_cache_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return "__pycache__" in parts or path.endswith((".pyc", ".pyo"))


def _git_blob_id(content: bytes) -> str:
    header = f"blob {len(content)}\0".encode("ascii")
    return hashlib.sha1(header + content, usedforsecurity=False).hexdigest()


def _reject(detail: str) -> int:
    print(
        f'{{"code":"consumer_contract_lab_bootstrap_failed","detail":"{detail}"}}',
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

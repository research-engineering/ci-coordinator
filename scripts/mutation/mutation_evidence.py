from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, Literal
from uuid import uuid4

from scripts.mutation.detached_worktree_lifecycle import DEFAULT_MAX_BUFFER_BYTES
from scripts.mutation.pytest_report import _MAXIMUM_REPORT_BYTES, PYTEST_REPORT_RELATIVE_PATH
from scripts.repository_paths import read_repository_regular_file

SUITE_NAME: Final = "python-managed-lifecycle"
MANIFEST_PATH: Final = "fixtures/conformance/v1/python-managed-lifecycle-mutants.v1.json"
MUTANT_IDS: Final = (
    "leaf-fd",
    "image-fd",
    "historical-gate",
    "stop-bound",
    "fresh-channel",
    "scope-restore",
    "cleanup-cut",
    "empty-only",
    "quiescence-fact",
)
EVIDENCE_ROOT: Final = Path(".ci-native/mutations") / SUITE_NAME
type Phase = Literal["baseline", "mutant"]


@dataclass(frozen=True, slots=True)
class EvidenceCapture:
    repository: Path
    source_revision: str
    manifest_digest: str
    invocation_id: str

    @property
    def relative_directory(self) -> Path:
        return EVIDENCE_ROOT / self.invocation_id


def allocate_capture(
    repository: Path, source_revision: str, manifest_digest: str
) -> EvidenceCapture:
    capture = EvidenceCapture(repository, source_revision, manifest_digest, uuid4().hex)
    if not _valid_capture_identity(capture):
        raise ValueError("invalid evidence invocation")
    descriptor = _phase_directory(repository, capture.relative_directory)
    os.close(descriptor)
    return capture


def _valid_capture_identity(capture: EvidenceCapture) -> bool:
    return all(
        isinstance(value, str) and re.fullmatch(pattern, value) is not None
        for value, pattern in (
            (capture.invocation_id, r"[0-9a-f]{32}"),
            (capture.source_revision, r"[0-9a-f]{40}"),
            (capture.manifest_digest, r"[0-9a-f]{64}"),
        )
    )


def capture_phase(
    capture: EvidenceCapture,
    *,
    worktree: Path,
    mutant_id: str,
    phase: Phase,
    source_file: str,
    source_digest: str,
    patch_digest: str,
    command: list[str],
    report_digest: str | None,
    stdout: str,
    stderr: str,
    exit_code: int | None,
) -> dict[str, object]:
    try:
        if (
            mutant_id not in MUTANT_IDS
            or phase not in {"baseline", "mutant"}
            or not _valid_capture_identity(capture)
            or any(
                not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in (capture.manifest_digest, source_digest, patch_digest, report_digest)
            )
        ):
            raise ValueError("invalid evidence identity")
        relative = PurePosixPath(source_file)
        if (
            not source_file
            or "\\" in source_file
            or "\0" in source_file
            or relative.is_absolute()
            or relative.as_posix() != source_file
            or any(part in {".", ".."} for part in relative.parts)
        ):
            raise ValueError("invalid evidence source path")
        source = read_repository_regular_file(
            worktree, Path(source_file), "mutation source", maximum_bytes=_MAXIMUM_REPORT_BYTES
        )
        if _digest(source) != source_digest:
            raise ValueError("mutation source changed before capture")
        xml = read_repository_regular_file(
            worktree,
            PYTEST_REPORT_RELATIVE_PATH,
            "mutation pytest evidence",
            maximum_bytes=_MAXIMUM_REPORT_BYTES,
        )
        if _digest(xml) != report_digest:
            raise ValueError("pytest report changed before capture")
        streams = {"stdout": stdout.encode("utf-8"), "stderr": stderr.encode("utf-8")}
        if sum(map(len, streams.values())) > DEFAULT_MAX_BUFFER_BYTES:
            raise ValueError("mutation evidence output exceeds its bound")
        relative_directory = capture.relative_directory / mutant_id / phase
        descriptor = _phase_directory(
            capture.repository,
            relative_directory,
            existing_parts=len(capture.relative_directory.parts),
        )
        try:
            files = [_write(descriptor, relative_directory, "pytest.xml", xml)]
            output: dict[str, object] = {}
            for name, content in streams.items():
                retained = exit_code != 0
                output[name] = {
                    "bytes": len(content),
                    "sha256": _digest(content),
                    "retained": retained,
                }
                if retained:
                    files.append(_write(descriptor, relative_directory, name + ".txt", content))
            record: dict[str, object] = {
                "schemaVersion": 1,
                "suite": SUITE_NAME,
                "invocationId": capture.invocation_id,
                "mutantId": mutant_id,
                "phase": phase,
                "sourceRevision": capture.source_revision,
                "manifestDigest": capture.manifest_digest,
                "patchDigest": patch_digest,
                "sourceFile": source_file,
                "sourceSha256": source_digest,
                "command": command,
                "exitCode": exit_code,
                "files": files,
                "output": output,
            }
            encoded = json.dumps(record, sort_keys=True, separators=(",", ":")).encode() + b"\n"
            receipt = _write(descriptor, relative_directory, "capture.json", encoded)
            return {"state": "captured", **record, "record": receipt}
        finally:
            os.close(descriptor)
    except (OSError, ValueError) as error:
        return {
            "state": "unqualified",
            "phase": phase,
            "invocationId": capture.invocation_id,
            "reason": "requested mutation evidence is unavailable or invalid",
            "errorType": type(error).__name__,
        }


def _phase_directory(root: Path, relative: Path, *, existing_parts: int = 0) -> int:
    descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for index, part in enumerate(relative.parts):
            if index >= existing_parts:
                try:
                    os.mkdir(part, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    if index == len(relative.parts) - 1:
                        raise
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _write(descriptor: int, directory: Path, name: str, content: bytes) -> dict[str, object]:
    child = os.open(
        name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=descriptor,
    )
    try:
        remaining = memoryview(content)
        while remaining:
            count = os.write(child, remaining)
            if count <= 0:
                raise OSError("mutation evidence write made no progress")
            remaining = remaining[count:]
    finally:
        os.close(child)
    return {
        "path": (directory / name).as_posix(),
        "bytes": len(content),
        "sha256": _digest(content),
    }


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

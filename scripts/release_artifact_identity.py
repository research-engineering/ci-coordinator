from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

MAX_GATE_RESPONSE_BYTES: Final = 1_048_576
RELEASE_IDENTITY_SCHEMA: Final = "ci-coordinator-release-identity/v1"

_GIT_SHA = re.compile(r"[0-9a-f]{40}")
_POSITIVE_INTEGER = re.compile(r"[1-9][0-9]*")


class ReleaseAdmissionError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GateExpectation:
    repository: str
    repository_id: int
    source_commit: str
    source_ref: str
    workflow_id: int
    workflow_name: str
    workflow_path: str


@dataclass(frozen=True, slots=True)
class GateRun:
    run_id: int
    run_attempt: int


@dataclass(frozen=True, slots=True)
class ReleaseCoordinates:
    repository: str
    repository_id: int
    source_commit: str
    source_ref: str
    gate_workflow_id: int
    gate_run_id: int
    gate_run_attempt: int
    release_event: str
    release_workflow_path: str
    release_run_id: int
    release_run_attempt: int

    def canonical_mapping(self) -> dict[str, object]:
        return {
            "gateRunAttempt": self.gate_run_attempt,
            "gateRunId": self.gate_run_id,
            "gateWorkflowId": self.gate_workflow_id,
            "releaseEvent": self.release_event,
            "releaseRunAttempt": self.release_run_attempt,
            "releaseRunId": self.release_run_id,
            "releaseWorkflowPath": self.release_workflow_path,
            "repository": self.repository,
            "repositoryId": self.repository_id,
            "schemaVersion": RELEASE_IDENTITY_SCHEMA,
            "sourceCommit": self.source_commit,
            "sourceRef": self.source_ref,
        }


def derive_release_identity(coordinates: ReleaseCoordinates) -> str:
    payload = json.dumps(
        coordinates.canonical_mapping(),
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def admit_gate_response(path: Path, expected: GateExpectation) -> GateRun:
    content = _read_gate_response(path)
    try:
        decoded = content.decode("utf-8", errors="strict")
        value = json.loads(
            decoded,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except (UnicodeError, json.JSONDecodeError, ReleaseAdmissionError) as error:
        raise ReleaseAdmissionError("Full Check response is not strict JSON") from error
    response = _object(value, "Full Check response")
    if _integer(response.get("total_count"), "Full Check total count") != 1:
        raise ReleaseAdmissionError("exactly one successful Full Check run is required")
    runs = response.get("workflow_runs")
    if not isinstance(runs, list) or len(runs) != 1:
        raise ReleaseAdmissionError("Full Check response must contain exactly one run")
    run = _object(runs[0], "Full Check run")
    _equal(run, "workflow_id", expected.workflow_id)
    _equal(run, "name", expected.workflow_name)
    _equal(run, "path", expected.workflow_path)
    _equal(run, "event", "workflow_dispatch")
    _equal(run, "status", "completed")
    _equal(run, "conclusion", "success")
    _equal(run, "head_branch", expected.source_ref.removeprefix("refs/heads/"))
    _equal(run, "head_sha", expected.source_commit)
    _admit_repository(run.get("repository"), expected, "repository")
    _admit_repository(run.get("head_repository"), expected, "head repository")
    return GateRun(
        run_id=_positive_api_integer(run.get("id"), "Full Check run id"),
        run_attempt=_positive_api_integer(
            run.get("run_attempt"),
            "Full Check run attempt",
        ),
    )


def _read_gate_response(path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ReleaseAdmissionError("Full Check response is unavailable") from error
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size <= 0
            or before.st_size > MAX_GATE_RESPONSE_BYTES
        ):
            raise ReleaseAdmissionError("Full Check response size is outside its bound")
        chunks: list[bytes] = []
        remaining = MAX_GATE_RESPONSE_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        after = os.fstat(descriptor)
    except OSError as error:
        raise ReleaseAdmissionError("Full Check response could not be read") from error
    finally:
        os.close(descriptor)
    content = b"".join(chunks)
    if len(content) > MAX_GATE_RESPONSE_BYTES:
        raise ReleaseAdmissionError("Full Check response size is outside its bound")
    stable_before = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    stable_after = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if stable_before != stable_after or len(content) != before.st_size:
        raise ReleaseAdmissionError("Full Check response changed while being read")
    return content


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate-response", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--repository-id", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-ref", required=True)
    parser.add_argument("--gate-workflow-id", required=True)
    parser.add_argument("--gate-workflow-name", required=True)
    parser.add_argument("--gate-workflow-path", required=True)
    parser.add_argument("--release-event", required=True)
    parser.add_argument("--release-workflow-path", required=True)
    parser.add_argument("--release-run-id", required=True)
    parser.add_argument("--release-run-attempt", required=True)
    arguments = parser.parse_args()

    source_commit = _git_sha(arguments.source_commit)
    repository_id = _positive_cli_integer(arguments.repository_id, "repository id")
    gate_workflow_id = _positive_cli_integer(
        arguments.gate_workflow_id,
        "Full Check workflow id",
    )
    expected = GateExpectation(
        repository=_bounded_text(arguments.repository, "repository", maximum=200),
        repository_id=repository_id,
        source_commit=source_commit,
        source_ref=_bounded_text(arguments.source_ref, "source ref", maximum=300),
        workflow_id=gate_workflow_id,
        workflow_name=_bounded_text(
            arguments.gate_workflow_name,
            "Full Check workflow name",
            maximum=100,
        ),
        workflow_path=_bounded_text(
            arguments.gate_workflow_path,
            "Full Check workflow path",
            maximum=300,
        ),
    )
    gate = admit_gate_response(arguments.gate_response, expected)
    coordinates = ReleaseCoordinates(
        repository=expected.repository,
        repository_id=expected.repository_id,
        source_commit=expected.source_commit,
        source_ref=expected.source_ref,
        gate_workflow_id=expected.workflow_id,
        gate_run_id=gate.run_id,
        gate_run_attempt=gate.run_attempt,
        release_event=_bounded_text(
            arguments.release_event,
            "release event",
            maximum=100,
        ),
        release_workflow_path=_bounded_text(
            arguments.release_workflow_path,
            "release workflow path",
            maximum=300,
        ),
        release_run_id=_positive_cli_integer(
            arguments.release_run_id,
            "release run id",
        ),
        release_run_attempt=_positive_cli_integer(
            arguments.release_run_attempt,
            "release run attempt",
        ),
    )
    print(f"release_identity={derive_release_identity(coordinates)}")
    print(f"source_commit={source_commit}")
    print(f"gate_run_id={gate.run_id}")
    print(f"gate_run_attempt={gate.run_attempt}")
    return 0


def _admit_repository(value: object, expected: GateExpectation, label: str) -> None:
    repository = _object(value, f"Full Check {label}")
    if _positive_api_integer(repository.get("id"), f"Full Check {label} id") != (
        expected.repository_id
    ):
        raise ReleaseAdmissionError(f"Full Check {label} id does not match")
    _equal(repository, "full_name", expected.repository)


def _object(value: object, label: str) -> dict[str, object]:
    if type(value) is not dict:
        raise ReleaseAdmissionError(f"{label} must be an object")
    return cast(dict[str, object], value)


def _equal(value: dict[str, object], key: str, expected: object) -> None:
    if value.get(key) != expected:
        raise ReleaseAdmissionError(f"Full Check {key} does not match")


def _integer(value: object, label: str) -> int:
    if type(value) is not int:
        raise ReleaseAdmissionError(f"{label} must be an integer")
    return value


def _positive_api_integer(value: object, label: str) -> int:
    integer = _integer(value, label)
    if integer <= 0:
        raise ReleaseAdmissionError(f"{label} must be positive")
    return integer


def _positive_cli_integer(value: str, label: str) -> int:
    if _POSITIVE_INTEGER.fullmatch(value) is None:
        raise ReleaseAdmissionError(f"{label} must be canonical positive decimal")
    return int(value)


def _git_sha(value: str) -> str:
    if _GIT_SHA.fullmatch(value) is None:
        raise ReleaseAdmissionError("source commit must be a lowercase 40-character Git SHA")
    return value


def _bounded_text(value: str, label: str, *, maximum: int) -> str:
    if not value or len(value.encode("utf-8")) > maximum or any(ord(item) < 32 for item in value):
        raise ReleaseAdmissionError(f"{label} is outside its text bound")
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseAdmissionError(f"duplicate object key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ReleaseAdmissionError(f"non-finite numeric constant: {value}")


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections.abc import Mapping
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

from scripts.bounded_process import CommandResult, spawn
from scripts.ci_business_witness.lifecycle import (
    EXECUTION_SECONDS,
    WitnessBudget,
    admit_docker_environment,
    local_docker_arguments,
)
from scripts.ci_business_witness.oracle import WitnessFailure, require

_MAXIMUM_BYTES = 4 * 1024 * 1024
_MAXIMUM_SERVICES = 4
_DRAIN_SECONDS = 40.0
_CLOSE_SECONDS = 5.0
_LABEL = "io.ci-coordinator.attached-log-probe"
_PROBE_INSPECT = (
    '{"Id":{{json .Id}},"Name":{{json .Name}},"Image":{{json .Image}},'
    '"Config":{"Labels":{{json .Config.Labels}},"Healthcheck":{{json .Config.Healthcheck}}},'
    '"HostConfig":{"LogConfig":{{json .HostConfig.LogConfig}},'
    '"NetworkMode":{{json .HostConfig.NetworkMode}},'
    '"ReadonlyRootfs":{{json .HostConfig.ReadonlyRootfs}}}}'
)
_PROBE_STDOUT = (
    b"early stdout\n\n-----BEGIN LOG CANARY-----\nfirst\nsecond\n-----END LOG CANARY-----\n"
    + b"x" * 17_000
    + b"\xffstdout without final newline"
)
_PROBE_STDERR = b"early stderr\n\n" + b"y" * 17_000 + b"\xfestderr without final newline"
_PROBE_PROGRAM = (
    "import os\n"
    "def emit(descriptor, data):\n"
    "    while data:\n"
    "        written = os.write(descriptor, data)\n"
    "        assert written > 0\n"
    "        data = data[written:]\n"
    + f"emit(1, {_PROBE_STDOUT!r})\n"
    + f"emit(2, {_PROBE_STDERR!r})\n"
)


@dataclass(frozen=True, slots=True)
class CapturedOutput:
    stdout: bytes
    stderr: bytes


@dataclass(frozen=True, slots=True)
class _Capture:
    container_id: str
    future: Future[CommandResult]


class AttachedLogs:
    def __init__(
        self,
        root: Path,
        environment: Mapping[str, str],
        budget: WitnessBudget,
        maximum_bytes: int = _MAXIMUM_BYTES,
    ) -> None:
        require(
            type(maximum_bytes) is int and 0 < maximum_bytes <= _MAXIMUM_BYTES,
            "attached-log-byte-bound",
        )
        admit_docker_environment(environment)
        self.root = root
        self.environment = dict(environment)
        self.budget = budget
        self.maximum_bytes = maximum_bytes
        self._stop = threading.Event()
        self._executor = ThreadPoolExecutor(max_workers=_MAXIMUM_SERVICES)
        self._captures: dict[str, _Capture] = {}
        self._finishing = False
        self._closing = False
        self._closed = False

    def start(self, service: str, exact_container_id: str) -> None:
        require(not self._finishing and not self._closing, "attached-log-lifecycle")
        require(
            re.fullmatch(r"[a-z][a-z0-9-]{0,63}", service) is not None
            and service not in self._captures
            and re.fullmatch(r"[0-9a-f]{64}", exact_container_id) is not None
            and exact_container_id not in {item.container_id for item in self._captures.values()}
            and len(self._captures) < _MAXIMUM_SERVICES,
            "attached-log-identity",
        )
        future = self._executor.submit(
            spawn,
            "docker",
            local_docker_arguments(("start", "--attach", exact_container_id)),
            cwd=self.root,
            env=self.environment,
            max_buffer=self.maximum_bytes,
            timeout_seconds=self.budget.timeout(EXECUTION_SECONDS),
            decode_errors="surrogateescape",
            stop_requested=self._stop.is_set,
        )
        self._captures[service] = _Capture(exact_container_id, future)

    def assert_running(self) -> None:
        require(
            bool(self._captures) and not self._finishing and not self._closing,
            "attached-log-lifecycle",
        )
        require(
            not any(item.future.done() for item in self._captures.values()),
            "attached-log-ended-before-stop",
        )

    def finish(self, exit_codes: Mapping[str, int]) -> dict[str, CapturedOutput]:
        require(not self._finishing and not self._closing, "attached-log-lifecycle")
        require(
            bool(self._captures)
            and set(exit_codes) == set(self._captures)
            and all(type(code) is int and 0 <= code <= 255 for code in exit_codes.values()),
            "attached-log-terminal-population",
        )
        self._finishing = True
        _done, pending = wait(
            [item.future for item in self._captures.values()],
            timeout=self.budget.timeout(_DRAIN_SECONDS),
        )
        require(not pending, "attached-log-drain-incomplete")
        captured = {}
        for service, item in self._captures.items():
            try:
                result = item.future.result()
            except Exception as error:
                raise WitnessFailure("attached-log-provider") from error
            captured[service] = _admit_output(result, exit_codes[service], self.maximum_bytes)
        return captured

    def close(self) -> None:
        if self._closed:
            return
        self._closing = True
        self._stop.set()
        _done, pending = wait(
            [item.future for item in self._captures.values()],
            timeout=max(0.0, min(_CLOSE_SECONDS, self.budget.remaining())),
        )
        self._executor.shutdown(wait=False, cancel_futures=True)
        require(not pending, "attached-log-cleanup-incomplete")
        self._closed = True


def _admit_output(result: CommandResult, expected_exit: int, maximum_bytes: int) -> CapturedOutput:
    require(result.failure_kind != "output-limit", "attached-log-output-limit")
    require(
        result.status == expected_exit
        and result.error is None
        and result.failure_kind is None
        and result.signal is None,
        "attached-log-provider",
    )
    try:
        stdout = result.stdout.encode("utf-8", errors="surrogateescape")
        stderr = result.stderr.encode("utf-8", errors="surrogateescape")
    except UnicodeError as error:
        raise WitnessFailure("attached-log-encoding") from error
    require(bool(stdout or stderr), "attached-log-empty")
    require(len(stdout) + len(stderr) <= maximum_bytes, "attached-log-output-limit")
    return CapturedOutput(stdout, stderr)


def _docker(
    arguments: tuple[str, ...],
    *,
    root: Path,
    environment: Mapping[str, str],
    budget: WitnessBudget,
    step_limit: float = 30,
) -> str:
    result = spawn(
        "docker",
        local_docker_arguments(arguments),
        cwd=root,
        env=environment,
        max_buffer=65_536,
        timeout_seconds=budget.timeout(step_limit),
    )
    require(
        result.status == 0 and result.error is None and result.failure_kind is None,
        "attached-log-probe-command",
    )
    return result.stdout


def _probe_exit(
    container_id: str,
    *,
    root: Path,
    environment: Mapping[str, str],
    budget: WitnessBudget,
) -> int:
    deadline = time.monotonic() + budget.timeout(30)
    while True:
        remaining = deadline - time.monotonic()
        require(remaining > 0, "attached-log-probe-state")
        state = json.loads(
            _docker(
                ("inspect", "--format", "{{json .State}}", container_id),
                root=root,
                environment=environment,
                budget=budget,
                step_limit=min(5.0, remaining),
            )
        )
        require(type(state) is dict, "attached-log-probe-state")
        if state.get("Status") == "exited":
            code = state.get("ExitCode")
            require(type(code) is int and 0 <= code <= 255, "attached-log-probe-exit")
            return int(code)
        require(
            state.get("Status") in {"created", "running"} and time.monotonic() < deadline,
            "attached-log-probe-state",
        )
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


def _probe_owned(
    metadata: object, *, container_id: str, name: str, image: str, project: str, mode: str
) -> bool:
    if not isinstance(metadata, dict) or not isinstance(metadata.get("Config"), dict):
        return False
    labels = metadata["Config"].get("Labels")
    return (
        isinstance(labels, dict)
        and metadata.get("Id") == container_id
        and metadata.get("Name") == f"/{name}"
        and metadata.get("Image") == image
        and labels.get(_LABEL) == mode
        and labels.get("com.docker.compose.project") == project
        and labels.get("io.ci-coordinator.business-witness") == project
    )


def qualify_attached_logs(
    *,
    root: Path,
    environment: Mapping[str, str],
    budget: WitnessBudget,
    image: str,
    project: str,
) -> dict[str, object]:
    admit_docker_environment(environment)
    environment = dict(environment)
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", image) is not None, "attached-log-probe-image")
    require(
        re.fullmatch(r"ci-business-[0-9a-f]{24}", project) is not None,
        "attached-log-probe-project",
    )
    for mode, limit in (("exact", _MAXIMUM_BYTES), ("overflow", 128)):
        name = f"{project}-logs-{mode}"
        container_id = _docker(
            (
                "create",
                "--name",
                name,
                "--label",
                f"com.docker.compose.project={project}",
                "--label",
                f"io.ci-coordinator.business-witness={project}",
                "--label",
                f"{_LABEL}={mode}",
                "--log-driver",
                "none",
                "--network",
                "none",
                "--read-only",
                "--no-healthcheck",
                "--ulimit",
                "core=0:0",
                "--entrypoint",
                "python",
                image,
                "-c",
                _PROBE_PROGRAM,
            ),
            root=root,
            environment=environment,
            budget=budget,
        ).strip()
        require(re.fullmatch(r"[0-9a-f]{64}", container_id) is not None, "attached-log-probe-id")
        logs = AttachedLogs(root, environment, budget, maximum_bytes=limit)
        primary: BaseException | None = None
        try:
            metadata = json.loads(
                _docker(
                    ("inspect", "--format", _PROBE_INSPECT, container_id),
                    root=root,
                    environment=environment,
                    budget=budget,
                )
            )
            require(
                _probe_owned(
                    metadata,
                    container_id=container_id,
                    name=name,
                    image=image,
                    project=project,
                    mode=mode,
                ),
                "attached-log-probe-owner",
            )
            require(
                metadata.get("HostConfig", {}).get("LogConfig", {}).get("Type") == "none"
                and metadata.get("HostConfig", {}).get("NetworkMode") == "none"
                and metadata.get("HostConfig", {}).get("ReadonlyRootfs") is True
                and metadata.get("Config", {}).get("Healthcheck", {}).get("Test") == ["NONE"],
                "attached-log-probe-policy",
            )
            logs.start(mode, container_id)
            code = _probe_exit(container_id, root=root, environment=environment, budget=budget)
            if mode == "exact":
                captured = logs.finish({mode: code})[mode]
                require(
                    code == 0 and captured == CapturedOutput(_PROBE_STDOUT, _PROBE_STDERR),
                    "attached-log-probe-byte-replay",
                )
            else:
                try:
                    logs.finish({mode: code})
                except WitnessFailure as error:
                    require(
                        str(error) == "attached-log-output-limit", "attached-log-probe-overflow"
                    )
                else:
                    raise WitnessFailure("attached-log-probe-overflow")
        except BaseException as error:
            primary = error
            raise
        finally:
            try:
                try:
                    logs.close()
                finally:
                    metadata = json.loads(
                        _docker(
                            ("inspect", "--format", _PROBE_INSPECT, container_id),
                            root=root,
                            environment=environment,
                            budget=budget,
                        )
                    )
                    require(
                        _probe_owned(
                            metadata,
                            container_id=container_id,
                            name=name,
                            image=image,
                            project=project,
                            mode=mode,
                        ),
                        "attached-log-probe-owner",
                    )
                    _docker(
                        ("rm", "--force", container_id),
                        root=root,
                        environment=environment,
                        budget=budget,
                    )
            except Exception:
                if primary is None:
                    raise
                primary.add_note("Attached-log probe cleanup is unproved.")
    return {
        "state": "passed",
        "stdoutBytes": len(_PROBE_STDOUT),
        "stderrBytes": len(_PROBE_STDERR),
        "stdoutSha256": hashlib.sha256(_PROBE_STDOUT).hexdigest(),
        "stderrSha256": hashlib.sha256(_PROBE_STDERR).hexdigest(),
        "overflowRejected": True,
    }

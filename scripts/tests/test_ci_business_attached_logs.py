from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from scripts.bounded_process import CommandResult
from scripts.ci_business_witness import attached_logs as attached
from scripts.ci_business_witness.lifecycle import LOCAL_DOCKER_HOST, WitnessBudget
from scripts.ci_business_witness.oracle import WitnessFailure


@pytest.mark.parametrize("exit_code", [0, 143])
def test_attach_preserves_bytes_and_matches_the_inspected_stop_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_code: int
) -> None:
    stdout, stderr = b"early\n\nline\n\xfftail", b"\xfeerror without newline"
    observed: dict[str, object] = {}

    def provider(*args: object, **kwargs: object) -> CommandResult:
        observed.update(arguments=args, **kwargs)
        return CommandResult(
            exit_code,
            stdout.decode("utf-8", "surrogateescape"),
            stderr.decode("utf-8", "surrogateescape"),
        )

    monkeypatch.setattr(attached, "spawn", provider)
    logs = attached.AttachedLogs(tmp_path, {"PATH": "/owned/bin"}, WitnessBudget())
    try:
        logs.start("backend", "a" * 64)
        assert logs.finish({"backend": exit_code}) == {
            "backend": attached.CapturedOutput(stdout, stderr)
        }
        assert observed["arguments"] == (
            "docker",
            ("--host", LOCAL_DOCKER_HOST, "start", "--attach", "a" * 64),
        )
        assert observed["decode_errors"] == "surrogateescape"
        assert observed["max_buffer"] == 4 * 1024 * 1024
    finally:
        logs.close()


@pytest.mark.parametrize(
    ("result", "expected_exit", "reason"),
    [
        (CommandResult(1, "log", ""), 0, "provider"),
        (CommandResult(0, "log", "", "transport error"), 0, "provider"),
        (CommandResult(None, "log", "", "timeout", "timeout"), 0, "provider"),
        (CommandResult(None, "log", "", "overflow", "output-limit"), 0, "output-limit"),
        (CommandResult(0, "log", "", failure_kind="residual-descendant"), 0, "provider"),
        (CommandResult(0, "log", "", signal="SIGTERM"), 0, "provider"),
        (CommandResult(0, "", ""), 0, "empty"),
        (CommandResult(0, "a" * 65, ""), 0, "output-limit"),
        (CommandResult(0, "a" * 32, "b" * 33), 0, "output-limit"),
        (CommandResult(0, "\ud800", ""), 0, "encoding"),
    ],
)
def test_transport_or_population_failure_never_becomes_log_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: CommandResult,
    expected_exit: int,
    reason: str,
) -> None:
    monkeypatch.setattr(attached, "spawn", lambda *args, **kwargs: result)
    logs = attached.AttachedLogs(tmp_path, {}, WitnessBudget(), maximum_bytes=64)
    try:
        logs.start("backend", "a" * 64)
        with pytest.raises(WitnessFailure, match=f"attached-log-{reason}"):
            logs.finish({"backend": expected_exit})
    finally:
        logs.close()


def test_all_started_services_and_exact_container_ids_remain_accounted_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(attached, "spawn", lambda *args, **kwargs: CommandResult(0, "log", ""))
    logs = attached.AttachedLogs(tmp_path, {}, WitnessBudget())
    try:
        logs.start("backend", "a" * 64)
        for service, container in (
            ("backend", "b" * 64),
            ("proxy", "a" * 64),
            ("bad/name", "c" * 64),
            ("proxy", "short"),
        ):
            with pytest.raises(WitnessFailure, match="attached-log-identity"):
                logs.start(service, container)
        for missing in ({}, {"foreign": 0}, {"backend": True}):
            with pytest.raises(WitnessFailure, match="terminal-population"):
                logs.finish(missing)
        for service, byte in (("proxy", "b"), ("postgres", "c"), ("keycloak", "d")):
            logs.start(service, byte * 64)
        with pytest.raises(WitnessFailure, match="attached-log-identity"):
            logs.start("fifth", "e" * 64)
        assert set(logs.finish(dict.fromkeys(("backend", "proxy", "postgres", "keycloak"), 0))) == {
            "backend",
            "proxy",
            "postgres",
            "keycloak",
        }
    finally:
        logs.close()
    with pytest.raises(WitnessFailure, match="attached-log-lifecycle"):
        logs.start("later", "f" * 64)


def test_provider_exception_cannot_become_a_completed_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failed(*args: object, **kwargs: object) -> CommandResult:
        raise OSError("capture transport failed")

    monkeypatch.setattr(attached, "spawn", failed)
    logs = attached.AttachedLogs(tmp_path, {}, WitnessBudget())
    try:
        logs.start("backend", "a" * 64)
        with pytest.raises(WitnessFailure, match="attached-log-provider"):
            logs.finish({"backend": 0})
    finally:
        logs.close()


def test_unexpected_completion_is_rejected_before_the_owner_stops_services(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    released, entered = threading.Event(), threading.Event()

    def provider(*args: object, **kwargs: object) -> CommandResult:
        entered.set()
        assert released.wait(2)
        return CommandResult(0, "terminal log", "")

    monkeypatch.setattr(attached, "spawn", provider)
    logs = attached.AttachedLogs(tmp_path, {}, WitnessBudget())
    try:
        logs.start("backend", "a" * 64)
        assert entered.wait(2)
        logs.assert_running()
        released.set()
        logs._captures["backend"].future.result(timeout=2)
        with pytest.raises(WitnessFailure, match="ended-before-stop"):
            logs.assert_running()
    finally:
        released.set()
        logs.close()


def test_close_requests_cancellation_and_joins_the_owned_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered, cancelled = threading.Event(), threading.Event()

    def provider(*args: object, **kwargs: object) -> CommandResult:
        stop = kwargs["stop_requested"]
        assert callable(stop)
        entered.set()
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if stop():
                cancelled.set()
                return CommandResult(None, "", "", "cancelled", "cancelled")
            cancelled.wait(0.01)
        raise AssertionError("capture cancellation was not delivered")

    monkeypatch.setattr(attached, "spawn", provider)
    logs = attached.AttachedLogs(tmp_path, {}, WitnessBudget())
    logs.start("backend", "a" * 64)
    assert entered.wait(2)
    logs.close()
    assert cancelled.is_set()
    logs.close()


def test_incomplete_drain_and_unjoined_cleanup_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(attached, "spawn", lambda *args, **kwargs: CommandResult(0, "log", ""))
    logs = attached.AttachedLogs(tmp_path, {}, WitnessBudget())
    logs.start("backend", "a" * 64)
    logs._captures["backend"].future.result(timeout=2)
    with monkeypatch.context() as patch:
        patch.setattr(attached, "wait", lambda futures, **kwargs: (set(), set(futures)))
        with pytest.raises(WitnessFailure, match="drain-incomplete"):
            logs.finish({"backend": 0})
        with pytest.raises(WitnessFailure, match="cleanup-incomplete"):
            logs.close()
    logs.close()


@pytest.mark.parametrize("limit", [False, 0, -1, 4 * 1024 * 1024 + 1])
def test_capture_bound_is_positive_and_cannot_exceed_the_owner_ceiling(
    tmp_path: Path, limit: int
) -> None:
    with pytest.raises(WitnessFailure, match="byte-bound"):
        attached.AttachedLogs(tmp_path, {}, WitnessBudget(), maximum_bytes=limit)


@pytest.mark.parametrize(
    "case",
    ["passed", "foreign-owner", "truncated", "overflow-survived", "disk-logs", "healthcheck"],
)
def test_native_qualification_keeps_exact_cleanup_ownership_and_overflow_oracle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    image, project = "sha256:" + "a" * 64, "ci-business-" + "b" * 24
    current: dict[str, str] = {}
    removed: list[str] = []
    observed_limits: list[int] = []

    def docker(arguments: tuple[str, ...], **kwargs: object) -> str:
        if arguments[0] == "create":
            mode = arguments[arguments.index("--name") + 1].rsplit("-", 1)[-1]
            current.update(mode=mode, id=("c" if mode == "exact" else "d") * 64)
            assert arguments[arguments.index("--log-driver") + 1] == "none"
            assert arguments[arguments.index("--network") + 1] == "none"
            assert "--no-healthcheck" in arguments
            assert arguments[-3:] == (image, "-c", attached._PROBE_PROGRAM)
            return current["id"]
        if arguments[0] == "inspect":
            projection = arguments[2]
            assert "{{json .}}" not in projection
            assert ".Args" not in projection and ".Config.Cmd" not in projection
            assert all(
                field in projection
                for field in (".Id", ".Name", ".Image", ".Config.Labels", ".HostConfig.LogConfig")
            )
            return json.dumps(
                {
                    "Id": current["id"],
                    "Name": f"/{project}-logs-{current['mode']}",
                    "Image": image,
                    "Config": {
                        "Labels": {
                            attached._LABEL: current["mode"],
                            "com.docker.compose.project": "foreign"
                            if case == "foreign-owner"
                            else project,
                            "io.ci-coordinator.business-witness": project,
                        },
                        "Healthcheck": {
                            "Test": ["CMD", "probe"] if case == "healthcheck" else ["NONE"]
                        },
                    },
                    "HostConfig": {
                        "LogConfig": {"Type": "json-file" if case == "disk-logs" else "none"},
                        "NetworkMode": "none",
                        "ReadonlyRootfs": True,
                    },
                }
            )
        assert arguments == ("rm", "--force", current["id"])
        removed.append(current["id"])
        return current["id"]

    class ProbeCapture:
        def __init__(self, *args: object, maximum_bytes: int, **kwargs: object) -> None:
            observed_limits.append(maximum_bytes)

        def start(self, service: str, container_id: str) -> None:
            assert service == current["mode"] and container_id == current["id"]

        def finish(self, exit_codes: dict[str, int]) -> dict[str, attached.CapturedOutput]:
            if current["mode"] == "overflow" and case != "overflow-survived":
                raise WitnessFailure("attached-log-output-limit")
            return {
                "exact": attached.CapturedOutput(
                    attached._PROBE_STDOUT[1:] if case == "truncated" else attached._PROBE_STDOUT,
                    attached._PROBE_STDERR,
                )
            }

        def close(self) -> None:
            pass

    monkeypatch.setattr(attached, "_docker", docker)
    monkeypatch.setattr(attached, "_probe_exit", lambda *args, **kwargs: 0)
    monkeypatch.setattr(attached, "AttachedLogs", ProbeCapture)
    if case != "passed":
        reason = {
            "foreign-owner": "probe-owner",
            "truncated": "byte-replay",
            "overflow-survived": "probe-overflow",
            "disk-logs": "probe-policy",
            "healthcheck": "probe-policy",
        }[case]
        with pytest.raises(WitnessFailure, match=reason):
            attached.qualify_attached_logs(
                root=tmp_path, environment={}, budget=WitnessBudget(), image=image, project=project
            )
        assert removed == (
            []
            if case == "foreign-owner"
            else ["c" * 64, "d" * 64]
            if case == "overflow-survived"
            else ["c" * 64]
        )
    else:
        result = attached.qualify_attached_logs(
            root=tmp_path, environment={}, budget=WitnessBudget(), image=image, project=project
        )
        assert result["state"] == "passed" and result["overflowRejected"] is True
        assert removed == ["c" * 64, "d" * 64]
        assert observed_limits == [4 * 1024 * 1024, 128]

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest
from anyio import BrokenWorkerProcess, CapacityLimiter, to_process
from config_control._document_admission_support import valid_policy_source
from config_epoch_support import CONFIG_SOURCE

from ci_coordinator.app.config_admission import PolicyAdmissionUnavailable
from ci_coordinator.config_control import ValidatedEpochDraft, admit_policy_document
from ci_coordinator.runtime.policy_admission import ProcessPolicyAdmission


def _record_progress_until_terminated(marker_path: str) -> None:
    with Path(marker_path).open("ab", buffering=0) as marker:
        marker.write(f"{os.getpid()}\n".encode())
        while True:
            marker.write(b".")
            time.sleep(0.005)


def _wait_for_progress(marker_path: Path) -> None:
    deadline = time.monotonic() + 5
    while not marker_path.exists() or marker_path.stat().st_size == 0:
        if time.monotonic() >= deadline:
            raise AssertionError("worker process did not publish progress")
        time.sleep(0.005)


@pytest.mark.parametrize("source", [b"{}", valid_policy_source()])
def test_process_admission_preserves_the_pure_result_across_the_worker_boundary(
    source: bytes,
) -> None:
    expected = admit_policy_document(source, "json")

    result = asyncio.run(ProcessPolicyAdmission()(source, "json"))

    assert result == expected
    assert isinstance(result, ValidatedEpochDraft) is (source != b"{}")


@pytest.mark.parametrize("token", (b"8.0", b"8e0"))
def test_real_worker_preserves_integral_sharding_values_and_source_identity(token: bytes) -> None:
    control_source = _dynamic_source(b"8")
    source = _dynamic_source(token)
    control = admit_policy_document(control_source, "json")
    assert isinstance(control, ValidatedEpochDraft)

    result = asyncio.run(ProcessPolicyAdmission()(source, "json"))

    assert isinstance(result, ValidatedEpochDraft)
    assert result.source_bytes == source != control.source_bytes
    assert result.source_hash != control.source_hash
    assert result.epoch_id != control.epoch_id
    assert result == replace(
        control, source_bytes=source, source_hash=result.source_hash, epoch_id=result.epoch_id
    )
    assert result == admit_policy_document(source, "json")


def _dynamic_source(token: bytes) -> bytes:
    dynamic = (
        b'{"planningEnabled":true,"policyVersion":"totality-v1","riskClasses":[], '
        b'"dependencyGraph":{"source":"configured","globalRiskPaths":[]},'
        b'"obligations":[{"obligationId":"quality","responsibility":{"paths":["src/**"]},'
        b'"requiredWitnessIds":["quality"],"defaultDepth":"standard","fullDepth":"full",'
        b'"omitAllowed":true}],"witnesses":[{"witnessId":"quality","executionProfileId":"linux",'
        b'"supportedDepths":["standard","full"]}],"executionProfiles":[{"profileId":"linux",'
        b'"runnerProfileId":"linux","permissionProfileId":"read","credentialProfileId":"none",'
        b'"fixtureProfileId":"none","capacityClassId":"hosted","shardingPolicy":{'
        b'"maxShards":' + token + b',"maxParallel":1,"maxItemsPerShard":100,'
        b'"setupSecondsPerShard":1}}]}'
    )
    assert CONFIG_SOURCE.count(b'"dynamicCi":null') == 1
    return CONFIG_SOURCE.replace(b'"dynamicCi":null', b'"dynamicCi":' + dynamic, 1)


def test_process_admission_uses_exact_cancellable_worker_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[object, tuple[object, ...], bool, CapacityLimiter | None]] = []

    async def run_sync(
        function: object,
        *args: object,
        cancellable: bool = False,
        limiter: CapacityLimiter | None = None,
    ) -> object:
        calls.append((function, args, cancellable, limiter))
        return admit_policy_document(b"{}", "json")

    monkeypatch.setattr(to_process, "run_sync", run_sync)

    result = asyncio.run(ProcessPolicyAdmission()(b"{}", "json"))

    assert result == admit_policy_document(b"{}", "json")
    assert len(calls) == 1
    function, args, cancellable, limiter = calls[0]
    assert (function, args, cancellable) == (
        admit_policy_document,
        (b"{}", "json"),
        True,
    )
    assert limiter is not None
    assert limiter.total_tokens == 1


def test_process_admission_rejects_concurrent_work_without_queueing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        async def run_sync(
            *_: object,
            cancellable: bool = False,
            limiter: CapacityLimiter | None = None,
        ) -> object:
            nonlocal calls
            assert cancellable is True
            assert limiter is not None
            assert limiter.total_tokens == 1
            calls += 1
            started.set()
            await release.wait()
            return admit_policy_document(b"{}", "json")

        monkeypatch.setattr(to_process, "run_sync", run_sync)
        first_admission = ProcessPolicyAdmission()
        second_admission = ProcessPolicyAdmission()
        first = asyncio.create_task(first_admission(b"{}", "json"))
        await started.wait()

        with pytest.raises(PolicyAdmissionUnavailable):
            await second_admission(b"{}", "json")

        assert calls == 1
        release.set()
        await first

    asyncio.run(scenario())


def test_process_admission_cancellation_releases_local_capacity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        started = asyncio.Event()
        calls = 0

        async def run_sync(*_: object, **__: object) -> object:
            nonlocal calls
            calls += 1
            if calls == 1:
                started.set()
                await asyncio.Future()
            return admit_policy_document(b"{}", "json")

        monkeypatch.setattr(to_process, "run_sync", run_sync)
        cancelled = asyncio.create_task(ProcessPolicyAdmission()(b"{}", "json"))
        await started.wait()
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled

        assert await ProcessPolicyAdmission()(b"{}", "json") == admit_policy_document(b"{}", "json")

    asyncio.run(scenario())


def test_installed_anyio_terminates_a_real_cancellable_worker(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        marker_path = tmp_path / "worker-progress"
        task = asyncio.create_task(
            to_process.run_sync(
                _record_progress_until_terminated,
                str(marker_path),
                cancellable=True,
                limiter=CapacityLimiter(1),
            )
        )
        await asyncio.to_thread(_wait_for_progress, marker_path)

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=5)

        stopped_size = marker_path.stat().st_size
        await asyncio.sleep(0.05)
        assert marker_path.stat().st_size == stopped_size
        assert await ProcessPolicyAdmission()(b"{}", "json") == admit_policy_document(b"{}", "json")

    asyncio.run(scenario())


@pytest.mark.parametrize("failure_type", [BrokenWorkerProcess, OSError])
def test_worker_failure_is_typed_unavailability(
    monkeypatch: pytest.MonkeyPatch,
    failure_type: type[Exception],
) -> None:
    async def run_sync(*_: object, **__: object) -> object:
        raise failure_type

    monkeypatch.setattr(to_process, "run_sync", run_sync)

    with pytest.raises(PolicyAdmissionUnavailable):
        asyncio.run(ProcessPolicyAdmission()(b"{}", "json"))

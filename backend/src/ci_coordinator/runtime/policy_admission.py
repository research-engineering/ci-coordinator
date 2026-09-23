"""Bounded process isolation for CPU-bound repository policy admission."""

from __future__ import annotations

from threading import Lock

from anyio import BrokenWorkerProcess, CapacityLimiter
from anyio import to_process as _to_process

from ci_coordinator.app.config_admission import PolicyAdmissionUnavailable
from ci_coordinator.config_control import (
    PolicyAdmissionResult,
    PolicySourceFormat,
    admit_policy_document,
)

_PROCESS_ADMISSION_GATE = Lock()


class ProcessPolicyAdmission:
    """Run at most one cancellable policy admission per backend process."""

    def __init__(self) -> None:
        self._worker_capacity = CapacityLimiter(1)

    async def __call__(
        self,
        raw_source: bytes,
        source_format: PolicySourceFormat,
    ) -> PolicyAdmissionResult:
        if not _PROCESS_ADMISSION_GATE.acquire(blocking=False):
            raise PolicyAdmissionUnavailable
        try:
            try:
                return await _to_process.run_sync(
                    admit_policy_document,
                    raw_source,
                    source_format,
                    cancellable=True,
                    limiter=self._worker_capacity,
                )
            except (BrokenWorkerProcess, OSError) as error:
                raise PolicyAdmissionUnavailable from error
        finally:
            _PROCESS_ADMISSION_GATE.release()

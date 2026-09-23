"""One no-queue, cancellable worker for dormant production-evidence replay."""

from threading import Lock

from anyio import BrokenWorkerProcess, CapacityLimiter, fail_after
from anyio import to_process as _to_process

from ci_coordinator.config_control import ValidatedEpochDraft
from ci_coordinator.production_admission.limits import PRODUCTION_STAGE_TIMEOUT_SECONDS
from ci_coordinator.production_admission.model import ProductionScopeGrant
from ci_coordinator.production_admission.ports import ProductionEvidenceAdmissionUnavailable
from ci_coordinator.production_admission.relation_admission import (
    StagedProductionEvidence,
    admit_staged_production_evidence,
)

_PROCESS_ADMISSION_GATE = Lock()


class ProcessProductionEvidenceAdmission:
    def __init__(self) -> None:
        self._worker_capacity = CapacityLimiter(1)

    async def __call__(
        self,
        content: bytes,
        scope_grant: ProductionScopeGrant,
        active_epoch: ValidatedEpochDraft,
        provider_paths: tuple[str, ...],
    ) -> StagedProductionEvidence:
        if not _PROCESS_ADMISSION_GATE.acquire(blocking=False):
            raise ProductionEvidenceAdmissionUnavailable
        try:
            with fail_after(PRODUCTION_STAGE_TIMEOUT_SECONDS):
                result = await _to_process.run_sync(
                    _admit,
                    content,
                    scope_grant,
                    active_epoch,
                    provider_paths,
                    cancellable=True,
                    limiter=self._worker_capacity,
                )
            if (
                type(result) is not StagedProductionEvidence
                or result.scope_grant != scope_grant
                or result.canonical_bytes != content
                or result.lookup.provider_paths != provider_paths
            ):
                raise ProductionEvidenceAdmissionUnavailable
            return result
        except (TimeoutError, BrokenWorkerProcess, OSError) as error:
            raise ProductionEvidenceAdmissionUnavailable from error
        finally:
            _PROCESS_ADMISSION_GATE.release()


def _admit(
    content: bytes,
    scope_grant: ProductionScopeGrant,
    active_epoch: ValidatedEpochDraft,
    provider_paths: tuple[str, ...],
) -> StagedProductionEvidence:
    try:
        return admit_staged_production_evidence(
            content,
            scope_grant=scope_grant,
            active_epoch=active_epoch,
            provider_paths=provider_paths,
        )
    except (TypeError, ValueError):
        raise ValueError("production evidence is invalid") from None

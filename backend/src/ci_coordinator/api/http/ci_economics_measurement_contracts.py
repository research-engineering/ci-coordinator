from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field

from ci_coordinator.api.http.ci_economics_contracts import (
    CiEconomicsAttemptIdentityResponse,
    CiEconomicsDurationResponse,
)
from ci_coordinator.api.http.ci_economics_source_contracts import (
    EconomicsProviderSourceResponse,
    provider_source_response,
)
from ci_coordinator.api.http.model_contracts import ResponseModel
from ci_coordinator.ci_economics.model import MEASUREMENT_DEFINITION_VERSION, PlannedRoute
from ci_coordinator.ci_economics.read_models import (
    IndependentAttemptSnapshot,
    RecordedAttemptEconomics,
)

type Digest = Annotated[str, Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")]


class EconomicsReconciliationSourceResponse(ResponseModel):
    source_kind: Literal["reconciliation"] = Field(alias="sourceKind")
    source_id: Digest
    attempt: CiEconomicsAttemptIdentityResponse
    contract_hash: Digest
    planned_route: PlannedRoute


class EconomicsMeasurementsResponse(ResponseModel):
    schema_version: Literal["ci-economics-measurements/v2"]
    ok: Literal[True]
    source: Annotated[
        EconomicsReconciliationSourceResponse | EconomicsProviderSourceResponse,
        Field(discriminator="source_kind"),
    ]
    recorded_at: datetime
    retain_until: datetime
    snapshot_digest: Digest
    definition_version: Literal["ci-economics-measurement/v1"]
    observation_set_hash: Digest
    queue: CiEconomicsDurationResponse
    runner_occupancy: CiEconomicsDurationResponse
    attempt_wall: CiEconomicsDurationResponse


def measurements_response(result: RecordedAttemptEconomics) -> EconomicsMeasurementsResponse:
    if type(result) is not RecordedAttemptEconomics:
        raise TypeError("measurement projection requires exact retained economics")
    snapshot = result.snapshot
    if isinstance(snapshot, IndependentAttemptSnapshot):
        source: EconomicsReconciliationSourceResponse | EconomicsProviderSourceResponse = (
            provider_source_response(snapshot.source)
        )
        digest = snapshot.evidence.snapshot_digest
    else:
        attempt = snapshot.attempt
        source = EconomicsReconciliationSourceResponse(
            source_kind="reconciliation",
            source_id=snapshot.subject_id,
            attempt=CiEconomicsAttemptIdentityResponse(
                installation_id=attempt.scope.installation_id,
                repository_id=attempt.scope.repository_id,
                workflow_run_id=attempt.workflow_run_id,
                run_attempt=attempt.run_attempt,
                head_sha=attempt.head_sha,
            ),
            contract_hash=snapshot.contract_hash,
            planned_route=snapshot.planned_route,
        )
        digest = snapshot.snapshot_digest
    measurements = result.measurements
    return EconomicsMeasurementsResponse(
        schema_version="ci-economics-measurements/v2",
        ok=True,
        source=source,
        recorded_at=snapshot.recorded_at,
        retain_until=snapshot.retain_until,
        snapshot_digest=digest,
        definition_version=MEASUREMENT_DEFINITION_VERSION,
        observation_set_hash=measurements.observation_set_hash,
        queue=CiEconomicsDurationResponse.model_validate(measurements.queue),
        runner_occupancy=CiEconomicsDurationResponse.model_validate(measurements.runner_occupancy),
        attempt_wall=CiEconomicsDurationResponse.model_validate(measurements.attempt_wall),
    )

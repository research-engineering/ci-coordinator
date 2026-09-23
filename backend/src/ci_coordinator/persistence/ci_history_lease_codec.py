from typing import Self

from pydantic import Field

from ci_coordinator.ci_economics.archive_statistics import ArchiveInstant
from ci_coordinator.ci_economics.observation_payload import ObservationDigest
from ci_coordinator.ci_economics.observation_scan import ObservationLease
from ci_coordinator.ci_economics.payload_model import EconomicsPayloadModel


class HistoryLeasePayload(EconomicsPayloadModel):
    worker_id: ObservationDigest = Field(alias="workerId")
    token: ObservationDigest = Field(repr=False)
    acquired_at: ArchiveInstant = Field(alias="acquiredAt")
    expires_at: ArchiveInstant = Field(alias="expiresAt")

    def to_lease(self) -> ObservationLease:
        return ObservationLease(self.worker_id, self.token, self.acquired_at, self.expires_at)

    @classmethod
    def from_lease(cls, lease: ObservationLease) -> Self:
        if type(lease) is not ObservationLease:
            raise TypeError("history lease encoding requires an exact lease")
        return cls(
            workerId=lease.worker_id,
            token=lease.token,
            acquiredAt=lease.acquired_at,
            expiresAt=lease.expires_at,
        )

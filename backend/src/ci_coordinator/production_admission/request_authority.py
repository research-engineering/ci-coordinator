"""A request-local prepared authority does not replace the final database admission."""

from dataclasses import dataclass
from typing import Protocol

from ci_coordinator.production_admission.authority import (
    AuthorizedProductionAdmission,
    ProductionAdmissionGrant,
)
from ci_coordinator.production_admission.current_evidence import CurrentProductionEvidence
from ci_coordinator.production_admission.model import (
    ProductionCandidateSubject,
    ProductionPlanSubject,
)


@dataclass(frozen=True, slots=True)
class PreparedProductionAuthority:
    grant: ProductionAdmissionGrant
    current: CurrentProductionEvidence | None

    def __post_init__(self) -> None:
        if type(self.grant) is not ProductionAdmissionGrant:
            raise TypeError("prepared authority requires a verified production receipt")
        if self.current is not None and (
            type(self.current) is not CurrentProductionEvidence
            or self.grant.scope_grant(self.current.candidate.scope) != self.current.scope_grant
        ):
            raise ValueError("prepared authority differs from its current evidence")

    def authorize(self, subject: ProductionPlanSubject) -> AuthorizedProductionAdmission | None:
        # A missing current witness is usable only by non-persistent lab stores.
        return self.grant.authorize(subject, current_evidence=self.current)


class ProductionRequestAuthority(Protocol):
    async def prepare(
        self, candidate: ProductionCandidateSubject
    ) -> PreparedProductionAuthority | None: ...

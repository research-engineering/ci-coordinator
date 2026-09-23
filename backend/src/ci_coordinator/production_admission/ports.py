"""Production-owned durable and current-evidence boundaries."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.production_admission.authority import (
    ProductionAdmissionGrant,
    ProductionIssuanceGuard,
)
from ci_coordinator.production_admission.codec import ProductionAdmissionRejection
from ci_coordinator.production_admission.current_evidence import CurrentActivationEvidence
from ci_coordinator.production_admission.cutover_commands import (
    ProductionCutoverCommand,
    ProductionCutoverResult,
)
from ci_coordinator.production_admission.cutover_drain import AdmittedProductionDrain
from ci_coordinator.production_admission.cutover_state import ProductionScopeState
from ci_coordinator.production_admission.evidence_lookup import ProductionEvidenceLookup
from ci_coordinator.production_admission.model import ProductionScopeGrant
from ci_coordinator.production_admission.relation_admission import StagedProductionEvidence
from ci_coordinator.reconciliation.contract import ReconciliationContract
from ci_coordinator.reconciliation.subject import ReconciliationSubject
from ci_coordinator.target_authority_producers.sources import ProviderAuthoritySources
from ci_coordinator.workflow_authority.evidence import WorkflowAuthorityEvidence

type ProductionReceiptVerifier = Callable[
    [bytes], ProductionAdmissionGrant | ProductionAdmissionRejection
]
type ProductionEvidenceAdmission = Callable[
    [bytes, ProductionScopeGrant, ValidatedEpochDraft, tuple[str, ...]],
    Awaitable[StagedProductionEvidence],
]
type ProductionDrainVerifier = Callable[[bytes], AdmittedProductionDrain | None]


class ProductionCutoverUnavailable(RuntimeError):
    """A durable cutover operation is unavailable or its commit outcome is unknown."""


class ProductionEvidenceAdmissionUnavailable(RuntimeError):
    """The bounded replay worker could not accept or complete this command."""


@dataclass(frozen=True, slots=True)
class RetainedProductionAuthority:
    state: ProductionScopeState
    envelope_canonical_json: bytes
    lookup: ProductionEvidenceLookup
    database_observed_at: datetime


type ProductionActivationObservation = Callable[
    [RetainedProductionAuthority, ProductionAdmissionGrant],
    Awaitable[CurrentActivationEvidence | None],
]


class ProductionAuthorityReader(Protocol):
    async def load_authority(
        self, scope: RepositoryScope, *, purpose: Literal["active", "staged"]
    ) -> RetainedProductionAuthority | None: ...


@dataclass(frozen=True, slots=True)
class CurrentProductionSources:
    workflows: tuple[WorkflowAuthorityEvidence, ...]
    provider: ProviderAuthoritySources


class CurrentProductionSourcesReader(Protocol):
    async def read(
        self, lookup: ProductionEvidenceLookup, *, revisions: tuple[str, ...]
    ) -> CurrentProductionSources | None: ...


class ProductionCutoverStore(ProductionAuthorityReader, Protocol):
    async def resolve(
        self, command: ProductionCutoverCommand
    ) -> ProductionCutoverResult | None: ...

    async def inspect(self, scope: RepositoryScope) -> ProductionScopeState | None: ...

    async def stage(
        self,
        command: ProductionCutoverCommand,
        *,
        grant: ProductionAdmissionGrant,
        evidence: StagedProductionEvidence,
    ) -> ProductionCutoverResult: ...

    async def load_evidence(self, scope: RepositoryScope, authority_id: str) -> bytes | None: ...

    async def begin(self, command: ProductionCutoverCommand) -> ProductionCutoverResult: ...

    async def activate(
        self,
        command: ProductionCutoverCommand,
        *,
        grant: ProductionAdmissionGrant,
        current: CurrentActivationEvidence,
        drain: AdmittedProductionDrain,
    ) -> ProductionCutoverResult: ...


class ProductionRegistrationPersistence(Protocol):
    async def register_selected(
        self,
        subject: ReconciliationSubject,
        contract: ReconciliationContract,
        guard: ProductionIssuanceGuard,
    ) -> bool: ...

    async def register_full_ci(
        self,
        subject: ReconciliationSubject,
        contract: ReconciliationContract,
    ) -> bool: ...

"""Read-time O1 capability passport composed from existing evidence owners.

The passport persists nothing and owns no readiness flag. Each fact is read from
its owner for this request; ``next_action`` and provider limits are derived from
those facts and are never stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol

from ci_coordinator.app.ci_economics import CiEconomicsReadForbidden
from ci_coordinator.app.ci_observation import CiObservationUseCase
from ci_coordinator.ci_economics.observation_ports import ObservationGapPage, ObservationStatus
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.identity_admission import JwksCacheState
from ci_coordinator.kernel import Clock
from ci_coordinator.observability import BackgroundHealth, BackgroundHealthState
from ci_coordinator.provider_inventory import (
    InstallationSummary,
    ProviderInstallationLookup,
    ProviderInventoryForbidden,
    ProviderInventoryUnavailable,
)
from ci_coordinator.provider_inventory.model import InventoryFailureReason
from ci_coordinator.workbench_read_models import WorkbenchAuthorizer

type CapabilityNextAction = Literal[
    "request_access",
    "repair_webhook",
    "refresh_evidence",
    "complete_configuration",
    "none",
]
type InstallationGrantStatus = Literal["known", "denied", "unavailable"]
type InstallationGrantReason = InventoryFailureReason | Literal["inventory_grant_absent"]
type ProviderLimitStatus = Literal["known", "unknown"]
type ProviderLimitReason = Literal["rate_limited", "no_retained_limit_observation"]
type ReconciliationStatus = Literal["known", "unknown", "denied", "unavailable"]
type ReconciliationReason = Literal[
    "observation_not_configured", "observation_paused", "access_denied", "store_unavailable"
]
type TrustKeyStatus = Literal["known", "expired", "unknown"]
type TrustKeyReason = Literal["cache_expired", "cache_absent", "cache_closed", "not_configured"]
type UnprojectedFactSource = Literal["history_delivery_inbox", "production_admission_authorities"]
type UnprojectedFactReason = Literal["receipt_clock_not_indexed", "not_projected"]

_INVENTORY_FAILURE_REASONS = frozenset(
    {
        "unavailable",
        "rate_limited",
        "not_found",
        "malformed_provider_response",
        "provider_binding_mismatch",
    }
)


@dataclass(frozen=True, slots=True)
class InstallationGrantFact:
    """Current App installation grant; denial is distinct from an empty grant set."""

    status: InstallationGrantStatus
    installation: InstallationSummary | None = None
    reason: InstallationGrantReason | None = None
    retry_after_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.status == "known":
            valid = type(self.installation) is InstallationSummary and self.reason is None
        elif self.status == "denied":
            valid = self.installation is None and self.reason == "inventory_grant_absent"
        elif self.status == "unavailable":
            valid = self.installation is None and self.reason in _INVENTORY_FAILURE_REASONS
        else:
            valid = False
        if not valid or (self.retry_after_seconds is not None and self.reason != "rate_limited"):
            raise ValueError("installation grant fact contradicts its status")


@dataclass(frozen=True, slots=True)
class ProviderLimitFact:
    """Only a rate limit observed by this read is known; absence of one is not capacity."""

    status: ProviderLimitStatus
    reason: ProviderLimitReason
    retry_after_seconds: int | None


@dataclass(frozen=True, slots=True)
class ReconciliationFact:
    status: ReconciliationStatus
    observation: ObservationStatus | None = None
    gaps_recorded: bool | None = None

    def __post_init__(self) -> None:
        observation = self.observation
        if self.status == "known":
            valid = (
                type(observation) is ObservationStatus
                and observation.snapshot is not None
                and type(self.gaps_recorded) is bool
            )
        elif self.status == "unknown":
            valid = (
                type(observation) is ObservationStatus
                and observation.snapshot is None
                and self.gaps_recorded is None
            )
        elif self.status in {"denied", "unavailable"}:
            valid = observation is None and self.gaps_recorded is None
        else:
            valid = False
        if not valid:
            raise ValueError("reconciliation fact contradicts its status")

    @property
    def enabled(self) -> bool | None:
        snapshot = None if self.observation is None else self.observation.snapshot
        return None if snapshot is None else snapshot.configuration.enabled

    @property
    def reason(self) -> ReconciliationReason | None:
        if self.status == "denied":
            return "access_denied"
        if self.status == "unavailable":
            return "store_unavailable"
        if self.status == "unknown":
            return "observation_not_configured"
        return None if self.enabled else "observation_paused"


@dataclass(frozen=True, slots=True)
class TrustKeyFact:
    """GitHub Actions OIDC key cache; ``None`` means this runtime has no JWKS provider."""

    cache: JwksCacheState | None

    @property
    def status(self) -> TrustKeyStatus:
        if self.cache is None:
            return "unknown"
        if self.cache.condition == "current":
            return "known"
        return "expired" if self.cache.condition == "expired" else "unknown"

    @property
    def reason(self) -> TrustKeyReason | None:
        if self.cache is None:
            return "not_configured"
        return {
            "current": None,
            "expired": "cache_expired",
            "absent": "cache_absent",
            "closed": "cache_closed",
        }[self.cache.condition]


@dataclass(frozen=True, slots=True)
class UnprojectedFact:
    """A fact that stays ``unknown`` because no bounded passive source exists."""

    source: UnprojectedFactSource
    reason: UnprojectedFactReason


# The history delivery inbox has no index that orders a repository's receipts by
# time, so the latest receipt cannot be read without scanning retained rows.
# Silence therefore stays unknown and is never reported as a broken webhook.
WEBHOOK_OBSERVATION = UnprojectedFact("history_delivery_inbox", "receipt_clock_not_indexed")
PRODUCTION_ADMISSION_KEYS = UnprojectedFact("production_admission_authorities", "not_projected")


@dataclass(frozen=True, slots=True)
class CapabilityPassport:
    scope: RepositoryScope
    observed_at: datetime
    installation_grant: InstallationGrantFact
    reconciliation: ReconciliationFact
    background_reconciliation: BackgroundHealthState
    actions_oidc_keys: TrustKeyFact

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("capability passport requires an exact repository scope")
        if not isinstance(self.observed_at, datetime) or self.observed_at.tzinfo is None:
            raise ValueError("capability passport observation time must be timezone-aware")
        installation = self.installation_grant.installation
        observation = self.reconciliation.observation
        if (
            installation is not None and installation.installation_id != self.scope.installation_id
        ) or (observation is not None and observation.scope != self.scope):
            raise ValueError("capability passport facts cross repository scope")

    @property
    def webhook_observation(self) -> UnprojectedFact:
        return WEBHOOK_OBSERVATION

    @property
    def production_admission_keys(self) -> UnprojectedFact:
        return PRODUCTION_ADMISSION_KEYS

    @property
    def provider_limits(self) -> ProviderLimitFact:
        grant = self.installation_grant
        if grant.reason == "rate_limited":
            return ProviderLimitFact("known", "rate_limited", grant.retry_after_seconds)
        return ProviderLimitFact("unknown", "no_retained_limit_observation", None)

    @property
    def next_action(self) -> CapabilityNextAction:
        """Closed operator action; ``repair_webhook`` is never derived from silence."""
        grant = self.installation_grant
        reconciliation = self.reconciliation
        if grant.status == "denied" or reconciliation.status == "denied":
            return "request_access"
        if grant.installation is not None and grant.installation.state != "active":
            return "request_access"
        if grant.status == "unavailable" or reconciliation.status == "unavailable":
            return "refresh_evidence"
        if reconciliation.status == "unknown" or reconciliation.enabled is False:
            return "complete_configuration"
        if reconciliation.gaps_recorded or self.actions_oidc_keys.reason in {
            "cache_expired",
            "cache_absent",
        }:
            return "refresh_evidence"
        return "none"


@dataclass(frozen=True, slots=True)
class CapabilityPassportForbidden:
    pass


type CapabilityPassportResult = CapabilityPassport | CapabilityPassportForbidden


class CapabilityPassportUseCase(Protocol):
    async def __call__(self, *, actor: str, scope: RepositoryScope) -> CapabilityPassportResult: ...


class BackgroundHealthSource(Protocol):
    def background_health(self) -> BackgroundHealth: ...


class ActionsOidcKeyCache(Protocol):
    def cache_state(self) -> JwksCacheState: ...


class CapabilityPassportService:
    """Compose owner reads; no owner is written, refreshed or probed by this service."""

    def __init__(
        self,
        *,
        authorizer: WorkbenchAuthorizer,
        installations: ProviderInstallationLookup,
        observation: CiObservationUseCase,
        background: BackgroundHealthSource,
        oidc_keys: ActionsOidcKeyCache | None,
        clock: Clock,
    ) -> None:
        self._authorizer = authorizer
        self._installations = installations
        self._observation = observation
        self._background = background
        self._oidc_keys = oidc_keys
        self._clock = clock

    async def __call__(self, *, actor: str, scope: RepositoryScope) -> CapabilityPassportResult:
        if type(scope) is not RepositoryScope:
            raise TypeError("capability passport requires an exact repository scope")
        if await self._authorizer.allows_scope(actor=actor, scope=scope) is not True:
            return CapabilityPassportForbidden()
        grant = await self._installation_grant(actor, scope.installation_id)
        reconciliation = await self._reconciliation(actor, scope)
        cache = None if self._oidc_keys is None else self._oidc_keys.cache_state()
        return CapabilityPassport(
            scope=scope,
            observed_at=self._clock.now(),
            installation_grant=grant,
            reconciliation=reconciliation,
            background_reconciliation=self._background.background_health().state,
            actions_oidc_keys=TrustKeyFact(cache),
        )

    async def _installation_grant(self, actor: str, installation_id: int) -> InstallationGrantFact:
        result = await self._installations.installation(
            actor=actor, installation_id=installation_id
        )
        if isinstance(result, ProviderInventoryForbidden):
            return InstallationGrantFact("denied", reason="inventory_grant_absent")
        if isinstance(result, ProviderInventoryUnavailable):
            return InstallationGrantFact(
                "unavailable",
                reason=result.reason,
                retry_after_seconds=result.retry_after_seconds,
            )
        if type(result) is InstallationSummary and result.installation_id == installation_id:
            return InstallationGrantFact("known", installation=result)
        return InstallationGrantFact("unavailable", reason="provider_binding_mismatch")

    async def _reconciliation(self, actor: str, scope: RepositoryScope) -> ReconciliationFact:
        status = await self._observation.status(actor=actor, scope=scope)
        if isinstance(status, CiEconomicsReadForbidden):
            return ReconciliationFact("denied")
        if type(status) is not ObservationStatus or status.scope != scope:
            return ReconciliationFact("unavailable")
        if status.snapshot is None:
            return ReconciliationFact("unknown", observation=status)
        gaps = await self._observation.gaps(actor=actor, scope=scope, after_cursor=None, limit=1)
        if isinstance(gaps, CiEconomicsReadForbidden):
            return ReconciliationFact("denied")
        if type(gaps) is not ObservationGapPage or gaps.scope != scope:
            return ReconciliationFact("unavailable")
        return ReconciliationFact("known", observation=status, gaps_recorded=bool(gaps.gaps))

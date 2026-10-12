from dataclasses import dataclass, field, replace
from datetime import timedelta

import pytest
from ci_economics.observation_factories import NOW, SCOPE, claimed_scan

from ci_coordinator.app.capability_passport import (
    CapabilityPassport,
    CapabilityPassportForbidden,
    CapabilityPassportService,
)
from ci_coordinator.app.ci_economics import CiEconomicsReadForbidden, CiEconomicsReadUnavailable
from ci_coordinator.app.ci_observation import (
    ObservationConfigurationResult,
    ObservationGapsResult,
    ObservationStatusResult,
    ObservationWorkflowsResult,
)
from ci_coordinator.ci_economics.observation_commands import ConfigureObservation
from ci_coordinator.ci_economics.observation_gaps import ObservationGap
from ci_coordinator.ci_economics.observation_ports import ObservationGapPage, ObservationStatus
from ci_coordinator.ci_economics.observation_progress import ObservationScanProgress
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.identity_admission import JwksCacheState
from ci_coordinator.kernel import FixedClock
from ci_coordinator.observability import BackgroundHealth, BackgroundHealthState
from ci_coordinator.provider_inventory import (
    InstallationLookupResult,
    InstallationPermission,
    InstallationSummary,
    ProviderInventoryForbidden,
    ProviderInventoryUnavailable,
)

ACTOR = "keycloak-human:v1:" + "a" * 64
_SNAPSHOT, _STATE, _ = claimed_scan()
CONFIGURED = ObservationStatus(
    SCOPE,
    _SNAPSHOT,
    (
        ObservationScanProgress(_STATE),
        ObservationScanProgress(replace(_STATE, lane="recent", lease=None)),
    ),
    0,
    None,
    NOW,
)
UNCONFIGURED = ObservationStatus(SCOPE, None, (), 0, None, NOW)
NO_GAPS = ObservationGapPage(SCOPE, (), None, NOW)


def _installation(installation_id: int = SCOPE.installation_id) -> InstallationSummary:
    return InstallationSummary(
        installation_id=installation_id,
        account_id=500 + installation_id,
        account_login="same-org",
        account_type="Organization",
        repository_selection="selected",
        state="active",
        permissions=(InstallationPermission("actions", "read"),),
        events=("workflow_run",),
    )


@dataclass
class _Boundary:
    allowed: bool = True
    grants: dict[int, InstallationLookupResult] = field(
        default_factory=lambda: {SCOPE.installation_id: _installation()}
    )
    status_result: ObservationStatusResult = CONFIGURED
    gaps_result: ObservationGapsResult = NO_GAPS
    cache: JwksCacheState | None = field(default_factory=lambda: JwksCacheState("current", 2, 10))
    calls: list[tuple[str, object]] = field(default_factory=list)

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        self.calls.append(("authorize", scope))
        return self.allowed

    async def installation(self, *, actor: str, installation_id: int) -> InstallationLookupResult:
        self.calls.append(("installation", installation_id))
        return self.grants[installation_id]

    async def status(self, *, actor: str, scope: RepositoryScope) -> ObservationStatusResult:
        self.calls.append(("status", scope))
        return self.status_result

    async def gaps(
        self, *, actor: str, scope: RepositoryScope, after_cursor: str | None, limit: int
    ) -> ObservationGapsResult:
        self.calls.append(("gaps", (scope, after_cursor, limit)))
        return self.gaps_result

    async def configure(self, command: ConfigureObservation) -> ObservationConfigurationResult:
        raise AssertionError("capability passport must not configure observation")

    async def workflows(
        self, *, actor: str, scope: RepositoryScope, page_number: int
    ) -> ObservationWorkflowsResult:
        raise AssertionError("capability passport must not read provider workflows")

    def background_health(self) -> BackgroundHealth:
        return BackgroundHealth(BackgroundHealthState.HEALTHY)

    def cache_state(self) -> JwksCacheState:
        assert self.cache is not None
        return self.cache

    async def passport(
        self, scope: RepositoryScope = SCOPE
    ) -> CapabilityPassport | CapabilityPassportForbidden:
        service = CapabilityPassportService(
            authorizer=self,
            installations=self,
            observation=self,
            background=self,
            oidc_keys=None if self.cache is None else self,
            clock=FixedClock(NOW + timedelta(minutes=1)),
        )
        return await service(actor=ACTOR, scope=scope)


async def _ready(boundary: _Boundary, scope: RepositoryScope = SCOPE) -> CapabilityPassport:
    passport = await boundary.passport(scope)
    assert isinstance(passport, CapabilityPassport)
    return passport


async def test_scope_denial_precedes_every_owner_read() -> None:
    # Kills: owner facts read before the exact repository scope is authorized.
    boundary = _Boundary(allowed=False)

    assert await boundary.passport() == CapabilityPassportForbidden()
    assert boundary.calls == [("authorize", SCOPE)]


async def test_grant_denial_is_not_an_empty_grant_set() -> None:
    # Kills: inventory denial projected as a known installation without permissions.
    denied = await _ready(_Boundary(grants={SCOPE.installation_id: ProviderInventoryForbidden()}))
    empty_installation = InstallationSummary(
        SCOPE.installation_id, 601, "same-org", "Organization", "selected", "active", (), ()
    )
    empty = await _ready(_Boundary(grants={SCOPE.installation_id: empty_installation}))

    assert (denied.installation_grant.status, denied.installation_grant.reason) == (
        "denied",
        "inventory_grant_absent",
    )
    assert denied.installation_grant.installation is None
    assert denied.next_action == "request_access"
    assert empty.installation_grant.status == "known"
    assert empty.installation_grant.installation == empty_installation
    assert empty.next_action == "none"


async def test_webhook_silence_stays_unknown_and_never_requests_repair() -> None:
    # Kills: a missing receipt clock derived as a broken webhook or a repair action.
    passport = await _ready(_Boundary())

    assert passport.webhook_observation.source == "history_delivery_inbox"
    assert passport.webhook_observation.reason == "receipt_clock_not_indexed"
    assert passport.next_action == "none"


async def test_unavailable_provider_keeps_stored_observation_evidence() -> None:
    # Kills: a provider failure erasing reconciliation facts or hiding the rate limit.
    boundary = _Boundary(
        grants={SCOPE.installation_id: ProviderInventoryUnavailable("rate_limited", 30)}
    )
    passport = await _ready(boundary)

    assert passport.installation_grant.status == "unavailable"
    assert passport.reconciliation.status == "known"
    assert passport.reconciliation.observation == CONFIGURED
    assert (passport.provider_limits.status, passport.provider_limits.retry_after_seconds) == (
        "known",
        30,
    )
    assert passport.next_action == "refresh_evidence"
    assert ("status", SCOPE) in boundary.calls


async def test_expired_or_absent_oidc_keys_are_never_known() -> None:
    # Kills: an expired key cache projected as current trust evidence.
    expired = await _ready(_Boundary(cache=JwksCacheState("expired", 2, 301)))
    absent = await _ready(_Boundary(cache=JwksCacheState("absent")))
    unconfigured = await _ready(_Boundary(cache=None))

    assert (expired.actions_oidc_keys.status, expired.next_action) == (
        "expired",
        "refresh_evidence",
    )
    assert (absent.actions_oidc_keys.status, absent.next_action) == ("unknown", "refresh_evidence")
    assert unconfigured.actions_oidc_keys.reason == "not_configured"
    assert unconfigured.next_action == "none"


async def test_identical_account_names_across_installations_are_isolated() -> None:
    # Kills: grant lookup keyed by mutable account name instead of the scoped installation.
    other = RepositoryScope(SCOPE.installation_id + 1, SCOPE.repository_id)
    boundary = _Boundary(
        grants={
            SCOPE.installation_id: _installation(),
            other.installation_id: ProviderInventoryForbidden(),
        },
        status_result=ObservationStatus(other, None, (), 0, None, NOW),
    )
    passport = await _ready(boundary, other)

    assert passport.installation_grant.status == "denied"
    assert [value for name, value in boundary.calls if name == "installation"] == [
        other.installation_id
    ]


@pytest.mark.parametrize(
    ("status_result", "gaps_result", "expected_status", "expected_action"),
    [
        (CiEconomicsReadForbidden(), NO_GAPS, "denied", "request_access"),
        (CiEconomicsReadUnavailable(), NO_GAPS, "unavailable", "refresh_evidence"),
        (UNCONFIGURED, NO_GAPS, "unknown", "complete_configuration"),
        (CONFIGURED, CiEconomicsReadUnavailable(), "unavailable", "refresh_evidence"),
    ],
)
async def test_reconciliation_outcomes_stay_distinguishable(
    status_result: ObservationStatusResult,
    gaps_result: ObservationGapsResult,
    expected_status: str,
    expected_action: str,
) -> None:
    # Kills: denied, unavailable and unconfigured observation collapsing into one state.
    passport = await _ready(_Boundary(status_result=status_result, gaps_result=gaps_result))

    assert passport.reconciliation.status == expected_status
    assert passport.next_action == expected_action


async def test_recorded_gaps_request_evidence_refresh() -> None:
    # Kills: a gap page ignored when deriving the next action.
    gap = ObservationGap(
        SCOPE, 3, "c" * 64, "recent", NOW, NOW - timedelta(seconds=1), NOW, "provider_truncated"
    )
    boundary = _Boundary(gaps_result=ObservationGapPage(SCOPE, (gap,), None, NOW))
    passport = await _ready(boundary)

    assert passport.reconciliation.gaps_recorded is True
    assert passport.next_action == "refresh_evidence"
    assert ("gaps", (SCOPE, None, 1)) in boundary.calls

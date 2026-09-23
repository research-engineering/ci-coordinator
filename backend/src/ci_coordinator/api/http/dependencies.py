"""Explicit HTTP dependencies for plan request authentication and execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fastapi import Request

from ci_coordinator.api.http.metrics_authentication import StaticMetricsBearerAuthenticator
from ci_coordinator.api.http.webhook_ingress import WebhookIngressUseCase
from ci_coordinator.app.analytics_configuration import PurposeSettingsUseCase
from ci_coordinator.app.ci_economics import CiEconomicsReadUseCase
from ci_coordinator.app.ci_economics_budgets import CiEconomicsBudgetUseCase
from ci_coordinator.app.ci_economics_sources import CiEconomicsSourceUseCase
from ci_coordinator.app.ci_history_administration import CiHistoryAdministrationUseCase
from ci_coordinator.app.ci_history_analytics import CiHistoryAnalyticsUseCase
from ci_coordinator.app.ci_history_read import CiHistoryReadUseCase
from ci_coordinator.app.ci_measurement_report_ingestion import MeasurementReportIngestionUseCase
from ci_coordinator.app.ci_measurement_reports import MeasurementReportReadUseCase
from ci_coordinator.app.ci_observation import CiObservationUseCase
from ci_coordinator.app.config_admission import ConfigAdmissionUseCase
from ci_coordinator.app.config_management import ConfigManagementUseCase
from ci_coordinator.app.config_queries import ConfigQueryUseCase
from ci_coordinator.app.dynamic_plan import DynamicPlanUseCase
from ci_coordinator.app.governance_baseline import GovernanceBaselineUseCase
from ci_coordinator.app.governance_comparison import GovernanceComparisonUseCase
from ci_coordinator.app.production_cutover import ProductionCutoverUseCase
from ci_coordinator.app.repository_attestation import RepositoryAttestationUseCase
from ci_coordinator.control_plane_identity import (
    BackChannelLogoutTokenVerifier,
    BrowserIdentityUseCase,
    ControlPlanePrincipal,
    ControlPlaneRoleAdmission,
)
from ci_coordinator.control_plane_identity.activity import IdentityActivityObserver
from ci_coordinator.control_plane_identity.activity_query import ActivityReadUseCase
from ci_coordinator.control_plane_identity.model import is_canonical_oidc_issuer
from ci_coordinator.governance_observation import GovernanceObservationUseCase
from ci_coordinator.identity_admission import TrustedActionsRun
from ci_coordinator.kernel import Clock
from ci_coordinator.observability import (
    RuntimeDiagnosticObserver,
    RuntimeMetrics,
    RuntimeReadinessUseCase,
    StructuredEventLogger,
)
from ci_coordinator.operator_controls import OperatorOverrideUseCase
from ci_coordinator.plan_issuance import PlanRequest
from ci_coordinator.provider_inventory import ProviderInventoryUseCase
from ci_coordinator.workbench_read_models import WorkbenchUseCase
from ci_coordinator.workflow_discovery import WorkflowDiscoveryUseCase


@dataclass(frozen=True, slots=True)
class InvalidCredential:
    """The request did not carry a valid authentication credential."""


@dataclass(frozen=True, slots=True)
class ForbiddenIdentity:
    """The verified credential did not satisfy the request identity policy."""


@dataclass(frozen=True, slots=True)
class AuthenticationDependencyUnavailable:
    """Authentication could not complete because a required dependency failed."""


type ActionsAuthenticationResult = (
    TrustedActionsRun | InvalidCredential | ForbiddenIdentity | AuthenticationDependencyUnavailable
)
type ControlPlaneAuthenticationResult = (
    ControlPlanePrincipal | InvalidCredential | AuthenticationDependencyUnavailable
)


class PlanRequestAuthenticator(Protocol):
    async def authenticate(
        self,
        authorization: str | None,
        plan_request: PlanRequest,
    ) -> ActionsAuthenticationResult: ...


class ActionsRunAuthenticator(Protocol):
    async def authenticate_run(
        self,
        authorization: str | None,
        *,
        repository: str,
        repository_id: int,
        ref: str,
        run_id: int,
        run_attempt: int,
        event_name: str,
        execution_sha: str,
    ) -> ActionsAuthenticationResult: ...


class ControlPlaneAuthenticator(Protocol):
    async def authenticate(self, request: Request) -> ControlPlaneAuthenticationResult: ...


class ControlPlaneMutationAdmission(Protocol):
    def admits(self, request: Request, principal: ControlPlanePrincipal) -> bool: ...


@dataclass(frozen=True, slots=True)
class PlanRouteDependencies:
    authenticator: PlanRequestAuthenticator
    use_case: DynamicPlanUseCase
    runtime_metrics: RuntimeMetrics | None = None


@dataclass(frozen=True, slots=True)
class GitHubWebhookRouteDependencies:
    webhook_ingress: WebhookIngressUseCase


@dataclass(frozen=True, slots=True)
class OperatorControlsRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    mutation_admission: ControlPlaneMutationAdmission
    override_use_case: OperatorOverrideUseCase


@dataclass(frozen=True, slots=True)
class ConfigManagementRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    mutation_admission: ControlPlaneMutationAdmission
    admission: ConfigAdmissionUseCase
    queries: ConfigQueryUseCase
    use_case: ConfigManagementUseCase


@dataclass(frozen=True, slots=True)
class ProductionCutoverRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    mutation_admission: ControlPlaneMutationAdmission
    use_case: ProductionCutoverUseCase


@dataclass(frozen=True, slots=True)
class ObservabilityRouteDependencies:
    readiness: RuntimeReadinessUseCase
    metrics: RuntimeMetrics
    request_logger: StructuredEventLogger | None = None
    diagnostics: RuntimeDiagnosticObserver | None = None
    metrics_authenticator: StaticMetricsBearerAuthenticator | None = None


@dataclass(frozen=True, slots=True)
class WorkbenchRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    use_case: WorkbenchUseCase


@dataclass(frozen=True, slots=True)
class ProviderInventoryRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    use_case: ProviderInventoryUseCase


@dataclass(frozen=True, slots=True)
class WorkflowDiscoveryRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    use_case: WorkflowDiscoveryUseCase


@dataclass(frozen=True, slots=True)
class GovernanceObservationRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    use_case: GovernanceObservationUseCase


@dataclass(frozen=True, slots=True)
class CiEconomicsRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    use_case: CiEconomicsReadUseCase


@dataclass(frozen=True, slots=True)
class CiEconomicsSourceRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    mutation_admission: ControlPlaneMutationAdmission
    use_case: CiEconomicsSourceUseCase


@dataclass(frozen=True, slots=True)
class MeasurementReportIngestionRouteDependencies:
    authenticator: ActionsRunAuthenticator
    use_case: MeasurementReportIngestionUseCase


@dataclass(frozen=True, slots=True)
class CiEconomicsBudgetRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    mutation_admission: ControlPlaneMutationAdmission
    use_case: CiEconomicsBudgetUseCase


@dataclass(frozen=True, slots=True)
class CiHistoryRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    mutation_admission: ControlPlaneMutationAdmission
    use_case: CiHistoryAdministrationUseCase


@dataclass(frozen=True, slots=True)
class CiHistoryReadRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    mutation_admission: ControlPlaneMutationAdmission
    use_case: CiHistoryReadUseCase


@dataclass(frozen=True, slots=True)
class CiHistoryAnalyticsRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    use_case: CiHistoryAnalyticsUseCase


@dataclass(frozen=True, slots=True)
class PurposeSettingsRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    mutation_admission: ControlPlaneMutationAdmission
    use_case: PurposeSettingsUseCase


@dataclass(frozen=True, slots=True)
class CiObservationRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    mutation_admission: ControlPlaneMutationAdmission
    use_case: CiObservationUseCase


@dataclass(frozen=True, slots=True)
class MeasurementReportReadRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    use_case: MeasurementReportReadUseCase


@dataclass(frozen=True, slots=True)
class ControlPlaneIdentityRouteDependencies:
    identity: BrowserIdentityUseCase
    back_channel_logout_tokens: BackChannelLogoutTokenVerifier
    mutation_admission: ControlPlaneMutationAdmission
    role_admission: ControlPlaneRoleAdmission
    issuer: str
    public_origin: str
    session_cookie_name: str
    transaction_cookie_name: str
    secure_cookies: bool
    activity: IdentityActivityObserver | None = None

    def __post_init__(self) -> None:
        if (
            not is_canonical_oidc_issuer(self.issuer)
            or type(self.public_origin) is not str
            or not self.public_origin
            or any(
                type(value) is not str
                or not value
                or any(character.isspace() for character in value)
                for value in (self.session_cookie_name, self.transaction_cookie_name)
            )
            or type(self.secure_cookies) is not bool
        ):
            raise ValueError("control-plane identity route settings are invalid")


@dataclass(frozen=True, slots=True)
class RepositoryAttestationRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    mutation_admission: ControlPlaneMutationAdmission
    service: RepositoryAttestationUseCase
    transaction_cookie_name: str
    secure_cookies: bool

    def __post_init__(self) -> None:
        if (
            type(self.transaction_cookie_name) is not str
            or not self.transaction_cookie_name
            or any(character.isspace() for character in self.transaction_cookie_name)
            or type(self.secure_cookies) is not bool
        ):
            raise ValueError("repository-attestation route settings are invalid")


@dataclass(frozen=True, slots=True)
class GovernanceBaselineRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    mutation_admission: ControlPlaneMutationAdmission
    use_case: GovernanceBaselineUseCase


@dataclass(frozen=True, slots=True)
class GovernanceComparisonRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    use_case: GovernanceComparisonUseCase


@dataclass(frozen=True, slots=True)
class ActivityRouteDependencies:
    authenticator: ControlPlaneAuthenticator
    role_admission: ControlPlaneRoleAdmission
    service: ActivityReadUseCase
    clock: Clock


@dataclass(frozen=True, slots=True)
class HttpRouteDependencies:
    plan: PlanRouteDependencies | None = None
    webhook: GitHubWebhookRouteDependencies | None = None
    operator_controls: OperatorControlsRouteDependencies | None = None
    config_management: ConfigManagementRouteDependencies | None = None
    production_cutover: ProductionCutoverRouteDependencies | None = None
    observability: ObservabilityRouteDependencies | None = None
    workbench: WorkbenchRouteDependencies | None = None
    provider_inventory: ProviderInventoryRouteDependencies | None = None
    workflow_discovery: WorkflowDiscoveryRouteDependencies | None = None
    governance_observation: GovernanceObservationRouteDependencies | None = None
    ci_economics: CiEconomicsRouteDependencies | None = None
    ci_economics_sources: CiEconomicsSourceRouteDependencies | None = None
    ci_economics_budgets: CiEconomicsBudgetRouteDependencies | None = None
    ci_observation: CiObservationRouteDependencies | None = None
    ci_history: CiHistoryRouteDependencies | None = None
    ci_history_read: CiHistoryReadRouteDependencies | None = None
    ci_history_analytics: CiHistoryAnalyticsRouteDependencies | None = None
    purpose_settings: PurposeSettingsRouteDependencies | None = None
    ci_measurement_reports: MeasurementReportReadRouteDependencies | None = None
    ci_measurement_report_ingestion: MeasurementReportIngestionRouteDependencies | None = None
    control_plane_identity: ControlPlaneIdentityRouteDependencies | None = None
    repository_attestation: RepositoryAttestationRouteDependencies | None = None
    governance_baseline: GovernanceBaselineRouteDependencies | None = None
    governance_comparison: GovernanceComparisonRouteDependencies | None = None
    activity: ActivityRouteDependencies | None = None

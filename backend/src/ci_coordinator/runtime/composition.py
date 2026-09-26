"""Concrete connected backend dependency composition."""

from __future__ import annotations

import asyncio
import secrets
from functools import partial

from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from fastapi import FastAPI
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    ActivityRouteDependencies,
    CiEconomicsBudgetRouteDependencies,
    CiEconomicsRouteDependencies,
    CiEconomicsSourceRouteDependencies,
    CiHistoryAnalyticsRouteDependencies,
    CiHistoryReadRouteDependencies,
    CiHistoryRouteDependencies,
    CiObservationRouteDependencies,
    ConfigManagementRouteDependencies,
    ControlPlaneIdentityRouteDependencies,
    GitHubWebhookRouteDependencies,
    GovernanceBaselineRouteDependencies,
    GovernanceComparisonRouteDependencies,
    GovernanceObservationRouteDependencies,
    HttpRouteDependencies,
    MeasurementReportIngestionRouteDependencies,
    MeasurementReportReadRouteDependencies,
    ObservabilityRouteDependencies,
    OperatorControlsRouteDependencies,
    PlanRouteDependencies,
    ProductionCutoverRouteDependencies,
    ProviderInventoryRouteDependencies,
    PurposeSettingsRouteDependencies,
    RepositoryAttestationRouteDependencies,
    WorkbenchRouteDependencies,
    WorkflowDiscoveryRouteDependencies,
)
from ci_coordinator.api.http.metrics_authentication import StaticMetricsBearerAuthenticator
from ci_coordinator.api.http.plan_authentication import RequestBoundActionsOidcAuthenticator
from ci_coordinator.app import (
    CapacityPlanningService,
    ConfigManagementService,
    DeterministicCandidatePlanner,
    DurablePlanningOverrideResolver,
    DurableReconciliationRegistrar,
    DynamicPlanService,
    ReconciliationRoundService,
    ShadowReconciliationProjector,
)
from ci_coordinator.app.analytics_configuration import PurposeSettingsService
from ci_coordinator.app.ci_economics import (
    CiEconomicsCollectionService,
    CiEconomicsReadService,
    CiEconomicsRetentionService,
)
from ci_coordinator.app.ci_economics_budgets import CiEconomicsBudgetService
from ci_coordinator.app.ci_economics_sources import CiEconomicsSourceService
from ci_coordinator.app.ci_history_administration import CiHistoryAdministrationService
from ci_coordinator.app.ci_history_analytics import CiHistoryAnalyticsService
from ci_coordinator.app.ci_history_collection import CiHistoryCollectionService
from ci_coordinator.app.ci_history_delivery import CiHistoryDeliveryService
from ci_coordinator.app.ci_history_read import CiHistoryReadService
from ci_coordinator.app.ci_measurement_report_ingestion import MeasurementReportIngestionService
from ci_coordinator.app.ci_measurement_reports import MeasurementReportReadService
from ci_coordinator.app.ci_observation import CiObservationService
from ci_coordinator.app.ci_observation_scanning import CiObservationScanningService
from ci_coordinator.app.config_admission import ConfigAdmissionService
from ci_coordinator.app.config_queries import ConfigQueryService
from ci_coordinator.app.governance_baseline import GovernanceBaselineService
from ci_coordinator.app.governance_comparison import GovernanceComparisonService
from ci_coordinator.app.planning_preparation import PlanningPreparationService
from ci_coordinator.app.production_cutover import ProductionCutoverService
from ci_coordinator.app.production_request_evidence import ProductionRequestAuthorityService
from ci_coordinator.app.repository_activation import RepositoryActivationAuthorityService
from ci_coordinator.app.repository_attestation import RepositoryAttestationService
from ci_coordinator.ci_economics import load_bundled_ci_economics_profile
from ci_coordinator.ci_economics.history_read_cursor import HistoryCursorCodec
from ci_coordinator.ci_economics.report_ingestion import measurement_report_audience
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import ControlPlaneActorAuthority
from ci_coordinator.control_plane_identity.activity_cursor import ActivityCursorCodec
from ci_coordinator.control_plane_identity.activity_query import (
    ActivityReadService,
    TrustedIssuerActivityAuthorization,
)
from ci_coordinator.github_ingestion import load_bundled_profile
from ci_coordinator.governance_observation import GovernanceObservationService
from ci_coordinator.integrations import GitHubActionsJwksProvider
from ci_coordinator.integrations.github import (
    GitHubAppTransportFactory,
    GitHubCapacityInputsProvider,
    GitHubGovernanceObservationReader,
    GitHubProviderInventory,
    GitHubReviewerPermissionReaderAdapter,
    GitHubReviewerProviderAdapter,
    GitHubRunnerSnapshotProvider,
    GitHubWorkflowSnapshotReader,
)
from ci_coordinator.integrations.github.ci_economics_provider import (
    GitHubCiEconomicsProvider,
)
from ci_coordinator.integrations.github.ci_economics_sources import GitHubCiEconomicsSources
from ci_coordinator.integrations.github.ci_history_provider import GitHubHistoryProvider
from ci_coordinator.integrations.github.ci_measurement_report_jobs import (
    GitHubMeasurementReportJobs,
)
from ci_coordinator.integrations.github.ci_observation_workflows import GitHubCiObservationWorkflows
from ci_coordinator.integrations.github.production_authority import (
    GitHubCurrentProductionSourcesReader,
)
from ci_coordinator.integrations.github.reconciliation_observer import (
    GitHubActionsReconciliationObserver,
    ReconciliationObserverLimits,
)
from ci_coordinator.integrations.github.repository_context import GitHubRepositoryContextProvider
from ci_coordinator.integrations.github.repository_membership import GitHubRepositoryAccess
from ci_coordinator.integrations.keycloak import KeycloakIntegration
from ci_coordinator.kernel import SystemClock, SystemMonotonicClock
from ci_coordinator.observability import (
    RuntimeDiagnosticObserver,
    RuntimeMetrics,
)
from ci_coordinator.operator_controls import ControlPlaneScopeAuthorizer, OperatorOverrideService
from ci_coordinator.persistence import (
    DatabaseReadinessProbe,
    PostgresCiEconomicsUnitOfWork,
    PostgresConfigEpochUnitOfWork,
    PostgresIngressIssuanceUnitOfWork,
    PostgresShadowReconciliationUnitOfWork,
    PostgresWebhookIngestionUnitOfWork,
    TransactionalCiEconomicsStore,
    TransactionalConfigEpochResolver,
    TransactionalConfigEpochStore,
    TransactionalGovernanceBaselineStore,
    TransactionalIssuanceStore,
    TransactionalReconciliationClaimSource,
    TransactionalReconciliationStore,
    TransactionalWebhookIngestionStore,
    bundled_alembic_config_path,
)
from ci_coordinator.persistence.activity_repository import PostgresActivityStore
from ci_coordinator.persistence.analytics_purpose_store import TransactionalPurposeSettingsStore
from ci_coordinator.persistence.ci_economics_budget_adapters import TransactionalBudgetPolicyStore
from ci_coordinator.persistence.ci_history_adapters import TransactionalHistoryStore
from ci_coordinator.persistence.ci_history_analytics import TransactionalHistoryAnalyticsStore
from ci_coordinator.persistence.ci_history_read_adapters import TransactionalHistoryReadStore
from ci_coordinator.persistence.ci_history_unit_of_work import (
    PostgresHistoryUnitOfWork,
    PostgresPurposeUnitOfWork,
)
from ci_coordinator.persistence.ci_observation_adapters import TransactionalObservationStore
from ci_coordinator.persistence.ci_observation_unit_of_work import PostgresObservationUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine, validate_postgres_url
from ci_coordinator.persistence.errors import PersistenceError
from ci_coordinator.persistence.governance_baseline_unit_of_work import (
    PostgresGovernanceBaselineUnitOfWork,
)
from ci_coordinator.persistence.operator_override_repository import (
    DurableOperatorOverrideStore,
    PostgresOperatorOverrideUnitOfWork,
)
from ci_coordinator.persistence.production_cutover_adapter import (
    TransactionalProductionCutoverStore,
)
from ci_coordinator.persistence.production_cutover_unit_of_work import (
    PostgresProductionCutoverUnitOfWork,
)
from ci_coordinator.persistence.production_registration_adapter import (
    TransactionalProductionRegistrationStore,
)
from ci_coordinator.persistence.proposal_review_adapter import TransactionalProposalReviewStore
from ci_coordinator.persistence.proposal_review_unit_of_work import (
    PostgresProposalReviewUnitOfWork,
)
from ci_coordinator.persistence.workbench_repository import PostgresWorkbenchRepository
from ci_coordinator.plan_issuance import SignedPlanIssuer, SignedPlanSigner
from ci_coordinator.production_admission import (
    ProductionAdmissionGrant,
    ProductionAdmissionRegistration,
    ProductionAdmissionRejection,
    admit_production_admission,
    read_production_admission_file,
)
from ci_coordinator.production_admission.cutover_drain import admit_production_drain
from ci_coordinator.production_admission.ports import (
    ProductionDrainVerifier,
    ProductionReceiptVerifier,
)
from ci_coordinator.provider_inventory import (
    ControlPlaneProviderInventoryAuthorizer,
    ProviderInventoryService,
)
from ci_coordinator.reconciliation import (
    DEFAULT_RECONCILIATION_CONVERGENCE_POLICY,
    ReconciliationScheduler,
)
from ci_coordinator.runtime.activity import ActivityCleanup, RuntimeActivityRepositoryAccess
from ci_coordinator.runtime.background_services import RuntimeBackgroundGroup
from ci_coordinator.runtime.control_plane_composition import (
    compose_control_plane_runtime_dependencies,
)
from ci_coordinator.runtime.cursor_key import derive_runtime_cursor_key
from ci_coordinator.runtime.event_logging import runtime_event_logger
from ci_coordinator.runtime.history_collection_worker import HistoryCollectionWorker
from ci_coordinator.runtime.history_cursor_key import derive_history_cursor_key
from ci_coordinator.runtime.history_detail_cleanup import (
    HISTORY_DETAIL_CLEANUP_TIMEOUT_SECONDS,
    HistoryDetailCleanup,
)
from ci_coordinator.runtime.maintenance_round import (
    BoundedMaintenanceOperation,
    RuntimeMaintenanceRound,
)
from ci_coordinator.runtime.planning_preparation import PlanningPreparationQueue
from ci_coordinator.runtime.policy_admission import ProcessPolicyAdmission
from ci_coordinator.runtime.production_evidence_admission import ProcessProductionEvidenceAdmission
from ci_coordinator.runtime.readiness import RuntimeReadiness
from ci_coordinator.runtime.reconciliation_service import PeriodicReconciliationService
from ci_coordinator.runtime.resources import RuntimeResources
from ci_coordinator.runtime.shutdown_budget import partition_shutdown_budget
from ci_coordinator.runtime.webhook_admission import ProcessWebhookAdmission
from ci_coordinator.runtime_settings import (
    ConnectedRuntimeSettings,
    EnforcingRuntimeSettings,
    NonEnforcingRuntimeSettings,
    load_bundled_build_identity,
)
from ci_coordinator.workbench_read_models import RepositoryWorkbenchService
from ci_coordinator.workflow_discovery import WorkflowDiscoveryService


class RuntimeDependencyConfigurationError(ValueError):
    """A redacted process-configuration failure known at composition time."""


class ProductionAdmissionConfigurationError(ValueError):
    """The configured production authority is absent, invalid, or stale."""


def compose_non_enforcing_dependencies(
    settings: NonEnforcingRuntimeSettings,
) -> tuple[FastAPI, RuntimeResources]:
    return _compose_connected_dependencies(settings, None, SystemClock())


def compose_enforcing_dependencies(
    settings: EnforcingRuntimeSettings,
) -> tuple[FastAPI, RuntimeResources]:
    clock = SystemClock()
    try:
        build_identity = load_bundled_build_identity()
        if not build_identity.production_eligible:
            raise ValueError("build is not production eligible")
        receipt_content = read_production_admission_file(settings.production_admission_receipt_path)
        scopes = tuple(
            sorted(
                (_scope(value) for value in settings.enforcement_scope_allowlist),
                key=lambda scope: (scope.installation_id, scope.repository_id),
            )
        )
        verifier = partial(
            admit_production_admission,
            public_key_pem=settings.production_admission_public_key_pem.encode("utf-8"),
            expected_key_id=settings.production_admission_key_id,
            expected_artifact_digest=settings.deployed_artifact_digest,
            expected_release_identity=build_identity.release_identity,
            expected_source_commit=build_identity.source_commit,
            expected_environment_id=settings.environment_id,
            expected_rollout_profile_id=settings.shadow_rollout_profile_id,
            expected_repository_scopes=scopes,
            minimum_remaining_seconds=(
                settings.request_timeout_seconds + settings.plan_ttl_seconds
            ),
            clock=clock,
        )
        receipt = verifier(receipt_content)
    except (OSError, TypeError, ValueError) as error:
        raise ProductionAdmissionConfigurationError(
            "production admission configuration is unavailable"
        ) from error
    if isinstance(receipt, ProductionAdmissionRejection):
        raise ProductionAdmissionConfigurationError(
            "production admission configuration is unavailable"
        )
    if not isinstance(receipt, ProductionAdmissionGrant):
        raise ProductionAdmissionConfigurationError(
            "production admission configuration is unavailable"
        )
    return _compose_connected_dependencies(
        settings,
        receipt,
        clock,
        production_verifier=verifier,
        drain_verifier=partial(
            admit_production_drain,
            public_key_pem=settings.production_admission_public_key_pem.encode("utf-8"),
            expected_key_id=settings.production_admission_key_id,
        ),
    )


def _compose_connected_dependencies(
    settings: ConnectedRuntimeSettings,
    enforcement_authority: ProductionAdmissionGrant | None,
    clock: SystemClock,
    *,
    production_verifier: ProductionReceiptVerifier | None = None,
    drain_verifier: ProductionDrainVerifier | None = None,
) -> tuple[FastAPI, RuntimeResources]:
    _require_synchronous_composition_context()
    if (enforcement_authority is None) != (production_verifier is None) or (
        production_verifier is None
    ) != (drain_verifier is None):
        raise ProductionAdmissionConfigurationError("production bootstrap and verifier must agree")
    runtime_metrics = RuntimeMetrics()
    runtime_worker_id = secrets.token_hex(32)
    structured_logger = runtime_event_logger()
    diagnostics = RuntimeDiagnosticObserver(structured_logger)
    shutdown = partition_shutdown_budget(settings.shutdown_timeout_seconds)
    engine: AsyncEngine | None = None
    github: GitHubAppTransportFactory | None = None
    jwks: GitHubActionsJwksProvider | None = None
    keycloak: KeycloakIntegration | None = None
    try:
        database_dsn = settings.database_dsn.reveal_for_composition()
        github_private_key = settings.github_private_key.reveal_for_composition()
        _validate_github_private_key(github_private_key)
        validate_postgres_url(database_dsn)
        signer = SignedPlanSigner(
            key_id=settings.plan_signing_key_id,
            private_key_pem=settings.plan_signing_private_key.reveal_for_composition().encode(
                "utf-8"
            ),
            ttl_seconds=settings.plan_ttl_seconds,
            clock=clock,
        )
        if isinstance(enforcement_authority, ProductionAdmissionGrant):
            asyncio.run(
                _register_production_admission(
                    database_dsn,
                    enforcement_authority.registration,
                )
            )
        engine = create_postgres_engine(
            database_dsn,
            pool_size=settings.database_pool_size,
            pool_timeout_seconds=settings.database_pool_timeout_seconds,
        )
        github = GitHubAppTransportFactory(
            app_id=settings.github_app_id,
            private_key_pem=github_private_key,
            clock=clock,
            outbound_proxy_url=settings.outbound_proxy_url,
            unavailable_observer=runtime_metrics,
        )
        jwks = GitHubActionsJwksProvider(
            SystemMonotonicClock(),
            outbound_proxy_url=settings.outbound_proxy_url,
        )

        def config_uow() -> PostgresConfigEpochUnitOfWork:
            return PostgresConfigEpochUnitOfWork(engine)

        def ingress_uow() -> PostgresIngressIssuanceUnitOfWork:
            return PostgresIngressIssuanceUnitOfWork(engine)

        def webhook_uow() -> PostgresWebhookIngestionUnitOfWork:
            return PostgresWebhookIngestionUnitOfWork(engine)

        def ci_economics_uow() -> PostgresCiEconomicsUnitOfWork:
            return PostgresCiEconomicsUnitOfWork(engine)

        def ci_observation_uow() -> PostgresObservationUnitOfWork:
            return PostgresObservationUnitOfWork(engine)

        def ci_history_uow() -> PostgresHistoryUnitOfWork:
            return PostgresHistoryUnitOfWork(engine)

        def purpose_uow() -> PostgresPurposeUnitOfWork:
            return PostgresPurposeUnitOfWork(engine)

        def governance_baseline_uow() -> PostgresGovernanceBaselineUnitOfWork:
            return PostgresGovernanceBaselineUnitOfWork(engine)

        def operator_uow() -> PostgresOperatorOverrideUnitOfWork:
            return PostgresOperatorOverrideUnitOfWork(engine)

        def proposal_review_uow() -> PostgresProposalReviewUnitOfWork:
            return PostgresProposalReviewUnitOfWork(engine)

        def shadow_uow() -> PostgresShadowReconciliationUnitOfWork:
            return PostgresShadowReconciliationUnitOfWork(engine)

        def production_uow() -> PostgresProductionCutoverUnitOfWork:
            return PostgresProductionCutoverUnitOfWork(engine)

        production_store = TransactionalProductionCutoverStore(production_uow)
        request_authority = (
            None
            if production_verifier is None
            else ProductionRequestAuthorityService(
                authorities=production_store,
                verifier=production_verifier,
                sources=GitHubCurrentProductionSourcesReader(github),
            )
        )

        convergence_policy = DEFAULT_RECONCILIATION_CONVERGENCE_POLICY
        ci_economics_profile = load_bundled_ci_economics_profile()
        reconciliation_store = TransactionalReconciliationStore(
            shadow_uow,
            clock,
            convergence_policy,
        )
        provider_observer = GitHubActionsReconciliationObserver(
            github,
            limits=ReconciliationObserverLimits(
                max_jobs=ci_economics_profile.maximum_jobs_per_attempt
            ),
        )
        reconciliation_round = ReconciliationRoundService(
            source=TransactionalReconciliationClaimSource(
                shadow_uow,
                worker_id=runtime_worker_id,
                convergence_policy=convergence_policy,
            ),
            persistence=reconciliation_store,
            poller=provider_observer,
            clock=clock,
            convergence_policy=convergence_policy,
            max_claims=settings.reconciliation_scan_limit,
            terminal_persistence=reconciliation_store,
            shadow_projector=ShadowReconciliationProjector(clock, runtime_metrics),
        )
        ci_economics_store = TransactionalCiEconomicsStore(ci_economics_uow)
        ci_economics_collection = CiEconomicsCollectionService(
            ci_economics_store,
            GitHubCiEconomicsProvider(provider_observer),
            ci_economics_profile,
            runtime_metrics=runtime_metrics,
            worker_id=runtime_worker_id,
        )
        ci_economics_retention = CiEconomicsRetentionService(
            ci_economics_store,
            ci_economics_profile,
        )
        repository_access = GitHubRepositoryAccess(github)
        ci_observation_store = TransactionalObservationStore(ci_observation_uow)
        ci_observation_scanning = CiObservationScanningService(
            store=ci_observation_store,
            provider=GitHubCiEconomicsSources(github),
            repository_access=repository_access,
            worker_id=runtime_worker_id,
            metrics=runtime_metrics,
            diagnostics=diagnostics,
        )
        ci_history_store = TransactionalHistoryStore(ci_history_uow)
        ci_history_collection = CiHistoryCollectionService(
            store=ci_history_store,
            discovery=GitHubCiEconomicsSources(github),
            attempts=GitHubHistoryProvider(github),
            repository_access=repository_access,
            worker_id=runtime_worker_id,
            metrics=runtime_metrics,
            diagnostics=diagnostics,
        )
        ci_history_delivery = CiHistoryDeliveryService(
            ci_history_store, runtime_metrics, diagnostics
        )
        activity_store = PostgresActivityStore(
            engine,
            ActivityCursorCodec(
                derive_runtime_cursor_key(
                    settings.plan_signing_private_key.reveal_for_composition(), purpose="activity"
                )
            ),
        )
        maintenance_round = RuntimeMaintenanceRound(
            reconciliation_round,
            (
                BoundedMaintenanceOperation(
                    "ci_economics_collection",
                    ci_economics_profile.collection_deadline_seconds,
                    ci_economics_collection,
                ),
                BoundedMaintenanceOperation(
                    "ci_economics_expiry",
                    ci_economics_profile.expiry_deadline_seconds,
                    ci_economics_retention.expire_evidence,
                ),
                BoundedMaintenanceOperation(
                    "ci_economics_tombstone_purge",
                    ci_economics_profile.purge_deadline_seconds,
                    ci_economics_retention.purge_tombstones,
                ),
                BoundedMaintenanceOperation(
                    "ci_economics_observation_cleanup",
                    ci_economics_profile.observation_cleanup_deadline_seconds,
                    ci_economics_retention.delete_expired_observations,
                ),
                BoundedMaintenanceOperation(
                    "ci_observation_discovery", 55, ci_observation_scanning
                ),
                BoundedMaintenanceOperation(
                    "ci_observation_gap_cleanup", 10, ci_observation_scanning.purge_gaps
                ),
                BoundedMaintenanceOperation("ci_history_delivery", 55, ci_history_delivery),
                BoundedMaintenanceOperation(
                    "ci_history_detail_cleanup",
                    HISTORY_DETAIL_CLEANUP_TIMEOUT_SECONDS,
                    HistoryDetailCleanup(ci_history_store.expire_details),
                ),
                BoundedMaintenanceOperation("activity_cleanup", 3, ActivityCleanup(activity_store)),
            ),
            metrics=runtime_metrics,
            clock=SystemMonotonicClock(),
            diagnostics=diagnostics,
        )
        background = RuntimeBackgroundGroup(
            primary=PeriodicReconciliationService(
                ReconciliationScheduler(maintenance_round),
                interval_seconds=settings.reconciliation_interval_seconds,
                startup_timeout_seconds=settings.reconciliation_startup_timeout_seconds,
                drain_timeout_seconds=shutdown.background_drain_seconds,
                metrics=runtime_metrics,
                diagnostics=diagnostics,
            ),
            additional=(
                HistoryCollectionWorker(
                    ci_history_collection.collect_next,
                    idle_seconds=settings.reconciliation_interval_seconds,
                    drain_seconds=shutdown.background_drain_seconds,
                    metrics=runtime_metrics,
                    diagnostics=diagnostics,
                ),
            ),
        )
        override_store = DurableOperatorOverrideStore(operator_uow)
        active_epochs = TransactionalConfigEpochResolver(config_uow)
        contexts = GitHubRepositoryContextProvider(
            github, observe=runtime_metrics.planning_preparation
        )
        preparation = PlanningPreparationQueue(
            PlanningPreparationService(active_epochs, contexts),
            contexts.close_prepared_contexts,
            runtime_metrics,
        )
        candidates = DeterministicCandidatePlanner(
            contexts,
            runtime_metrics,
            diagnostics,
        )
        dynamic_plans = DynamicPlanService(
            active_epochs=active_epochs,
            candidates=candidates,
            issuer=SignedPlanIssuer(
                store=TransactionalIssuanceStore(ingress_uow),
                signer=signer,
            ),
            reconciliation=DurableReconciliationRegistrar(
                reconciliation_store,
                settings.shadow_rollout_profile_id,
                production=TransactionalProductionRegistrationStore(
                    production_uow, convergence_policy
                ),
            ),
            overrides=DurablePlanningOverrideResolver(override_store, clock),
            capacity=CapacityPlanningService(
                snapshots=GitHubRunnerSnapshotProvider(github, clock=clock),
                inputs=GitHubCapacityInputsProvider(github),
                clock=clock,
                runtime_metrics=runtime_metrics,
                diagnostics=diagnostics,
            ),
            enforcement_authority=request_authority,
            runtime_metrics=runtime_metrics,
            diagnostics=diagnostics,
        )
        control_plane_authorizer = ControlPlaneScopeAuthorizer(
            actor_authority=ControlPlaneActorAuthority(),
            allowed_scopes=frozenset(
                _scope(value) for value in settings.control_plane_scope_allowlist
            ),
            scope_mode=settings.control_plane_scope_mode,
            repository_access=(
                repository_access if settings.control_plane_scope_mode == "app" else None
            ),
        )
        provider_inventory = GitHubProviderInventory(github)
        provider_inventory_authorizer = ControlPlaneProviderInventoryAuthorizer(
            inventory_installation_ids=(settings.control_plane_inventory_installation_allowlist),
            inventory_mode=settings.control_plane_inventory_mode,
            scope_mode=settings.control_plane_scope_mode,
            workbench_scopes=frozenset(
                _scope(value) for value in settings.control_plane_scope_allowlist
            ),
        )
        database_probe = DatabaseReadinessProbe(
            engine,
            bundled_alembic_config_path(),
            timeout_ms=settings.request_timeout_seconds * 1_000,
        )
        workbench_repository = PostgresWorkbenchRepository(engine)
        workflow_reader = GitHubWorkflowSnapshotReader(github)
        workflow_discovery = WorkflowDiscoveryService(
            authorizer=control_plane_authorizer,
            reader=workflow_reader,
        )
        proposal_review_store = TransactionalProposalReviewStore(proposal_review_uow)
        governance_baseline_store = TransactionalGovernanceBaselineStore(governance_baseline_uow)
        governance_reader = GitHubGovernanceObservationReader(github)
        control_plane = compose_control_plane_runtime_dependencies(
            settings=settings,
            engine=engine,
            clock=clock,
            activity=activity_store,
        )
        keycloak = control_plane.keycloak
        identity_settings = settings.control_plane_identity
        production_cutover_routes = None
        if identity_settings is None:
            identity_routes = None
            repository_attestation_routes = None
            config_management_routes = None
            governance_baseline_routes = None
            governance_comparison_routes = None
        else:
            if (
                control_plane.browser_identity is None
                or control_plane.identity_crypto is None
                or keycloak is None
            ):
                raise RuntimeError("control-plane identity composition is incomplete")
            identity_routes = ControlPlaneIdentityRouteDependencies(
                activity=activity_store,
                identity=control_plane.browser_identity,
                back_channel_logout_tokens=keycloak,
                mutation_admission=control_plane.mutation_admission,
                role_admission=control_plane.role_admission,
                issuer=identity_settings.issuer,
                public_origin=identity_settings.public_origin,
                session_cookie_name=identity_settings.session_cookie_name,
                transaction_cookie_name=identity_settings.transaction_cookie_name,
                secure_cookies=identity_settings.secure_cookies,
            )
            reviewer_provider = GitHubReviewerProviderAdapter(
                client_id=identity_settings.github_reviewer_client_id,
                client_secret=(
                    identity_settings.github_reviewer_client_secret.reveal_for_composition()
                ),
                redirect_uri=identity_settings.reviewer_callback_uri,
                transport_factory=github,
                outbound_proxy_url=settings.outbound_proxy_url,
            )
            reviewer_permission_reader = GitHubReviewerPermissionReaderAdapter(github)
            repository_attestation_routes = RepositoryAttestationRouteDependencies(
                authenticator=control_plane.authenticator,
                role_admission=control_plane.role_admission,
                mutation_admission=control_plane.mutation_admission,
                service=RepositoryAttestationService(
                    crypto=control_plane.identity_crypto,
                    provider=reviewer_provider,
                    discovery=workflow_discovery,
                    store=proposal_review_store,
                    clock=clock,
                    authority_profile_digest=identity_settings.profile_digest,
                ),
                transaction_cookie_name=identity_settings.reviewer_transaction_cookie_name,
                secure_cookies=identity_settings.secure_cookies,
            )
            config_store = TransactionalConfigEpochStore(config_uow)
            if (
                production_verifier is not None
                and drain_verifier is not None
                and request_authority is not None
            ):
                production_cutover_routes = ProductionCutoverRouteDependencies(
                    authenticator=control_plane.authenticator,
                    role_admission=control_plane.role_admission,
                    mutation_admission=control_plane.mutation_admission,
                    use_case=ProductionCutoverService(
                        authorizer=control_plane_authorizer,
                        store=production_store,
                        epochs=config_store,
                        verifier=production_verifier,
                        drain_verifier=drain_verifier,
                        evidence_admission=ProcessProductionEvidenceAdmission(),
                        observe_activation=request_authority.observe_activation,
                    ),
                )
            config_admission = ConfigAdmissionService(
                authorizer=control_plane_authorizer,
                policy_admission=ProcessPolicyAdmission(),
            )
            config_management_routes = ConfigManagementRouteDependencies(
                authenticator=control_plane.authenticator,
                role_admission=control_plane.role_admission,
                mutation_admission=control_plane.mutation_admission,
                admission=config_admission,
                queries=ConfigQueryService(
                    authorizer=control_plane_authorizer,
                    store=config_store,
                ),
                use_case=ConfigManagementService(
                    authorizer=control_plane_authorizer,
                    admission=config_admission,
                    store=config_store,
                    clock=clock,
                    activation_authorizer=RepositoryActivationAuthorityService(
                        store=proposal_review_store,
                        permission_reader=reviewer_permission_reader,
                        discovery=workflow_discovery,
                        authority_profile_digest=identity_settings.profile_digest,
                    ),
                    attested_activation_store=proposal_review_store,
                    runtime_metrics=runtime_metrics,
                ),
            )
            governance_observer = GovernanceObservationService(
                authorizer=control_plane_authorizer,
                reader=governance_reader,
                clock=clock,
            )
            governance_baseline_routes = GovernanceBaselineRouteDependencies(
                authenticator=control_plane.authenticator,
                role_admission=control_plane.role_admission,
                mutation_admission=control_plane.mutation_admission,
                use_case=GovernanceBaselineService(
                    authorizer=control_plane_authorizer,
                    observer=governance_observer,
                    store=governance_baseline_store,
                    clock=clock,
                ),
            )
            governance_comparison_routes = GovernanceComparisonRouteDependencies(
                authenticator=control_plane.authenticator,
                role_admission=control_plane.role_admission,
                use_case=GovernanceComparisonService(
                    authorizer=control_plane_authorizer,
                    observer=governance_observer,
                    store=governance_baseline_store,
                ),
            )

        def actions_authenticator(audience: str) -> RequestBoundActionsOidcAuthenticator:
            return RequestBoundActionsOidcAuthenticator(
                jwks_provider=jwks,
                clock=clock,
                audience=audience,
                allowed_workflow_refs=tuple(sorted(settings.oidc_allowed_workflow_refs)),
                allowed_job_workflow_refs=tuple(sorted(settings.oidc_allowed_job_workflow_refs)),
                allowed_workflow_paths=tuple(sorted(settings.oidc_allowed_workflow_paths)),
                allowed_job_workflow_paths=tuple(sorted(settings.oidc_allowed_job_workflow_paths)),
                runtime_metrics=runtime_metrics,
            )

        resources = RuntimeResources(
            engine,
            github,
            jwks,
            background,
            shutdown_timeout_seconds=shutdown.resource_cleanup_seconds,
            additional_resources=(preparation, *(() if keycloak is None else (keycloak,))),
        )
        app = create_app(
            HttpRouteDependencies(
                plan=PlanRouteDependencies(
                    authenticator=actions_authenticator(settings.oidc_audience),
                    use_case=dynamic_plans,
                    runtime_metrics=runtime_metrics,
                ),
                webhook=GitHubWebhookRouteDependencies(
                    webhook_ingress=ProcessWebhookAdmission(
                        secret=settings.webhook_secret.reveal_for_composition(),
                        profile=load_bundled_profile(),
                        ingestion_store=TransactionalWebhookIngestionStore(webhook_uow),
                        clock=clock,
                        offer_preparation=preparation.offer,
                    ),
                ),
                operator_controls=OperatorControlsRouteDependencies(
                    authenticator=control_plane.authenticator,
                    role_admission=control_plane.role_admission,
                    mutation_admission=control_plane.mutation_admission,
                    override_use_case=OperatorOverrideService(
                        authorizer=control_plane_authorizer,
                        store=override_store,
                        clock=clock,
                    ),
                ),
                config_management=config_management_routes,
                production_cutover=production_cutover_routes,
                observability=ObservabilityRouteDependencies(
                    readiness=RuntimeReadiness(
                        resources=resources,
                        database_probe=database_probe,
                        timeout_ms=settings.request_timeout_seconds * 1_000,
                        required_probes=(("oidc_jwks", jwks),),
                        metrics=runtime_metrics,
                        diagnostics=diagnostics,
                    ),
                    metrics=runtime_metrics,
                    request_logger=structured_logger,
                    diagnostics=diagnostics,
                    metrics_authenticator=StaticMetricsBearerAuthenticator(
                        settings.metrics_bearer_token.reveal_for_composition()
                    ),
                ),
                workbench=WorkbenchRouteDependencies(
                    authenticator=control_plane.authenticator,
                    role_admission=control_plane.role_admission,
                    use_case=RepositoryWorkbenchService(
                        authorizer=control_plane_authorizer,
                        repository=workbench_repository,
                        replay_probe=database_probe,
                    ),
                ),
                provider_inventory=ProviderInventoryRouteDependencies(
                    authenticator=control_plane.authenticator,
                    role_admission=control_plane.role_admission,
                    use_case=ProviderInventoryService(
                        authorizer=provider_inventory_authorizer,
                        installations=provider_inventory,
                        repositories=provider_inventory,
                        clock=clock,
                    ),
                ),
                workflow_discovery=WorkflowDiscoveryRouteDependencies(
                    authenticator=control_plane.authenticator,
                    role_admission=control_plane.role_admission,
                    use_case=workflow_discovery,
                ),
                governance_observation=GovernanceObservationRouteDependencies(
                    authenticator=control_plane.authenticator,
                    role_admission=control_plane.role_admission,
                    use_case=GovernanceObservationService(
                        authorizer=control_plane_authorizer,
                        reader=governance_reader,
                        clock=clock,
                    ),
                ),
                ci_economics=CiEconomicsRouteDependencies(
                    authenticator=control_plane.authenticator,
                    role_admission=control_plane.role_admission,
                    use_case=CiEconomicsReadService(
                        authorizer=control_plane_authorizer,
                        query=ci_economics_store,
                    ),
                ),
                ci_economics_sources=CiEconomicsSourceRouteDependencies(
                    authenticator=control_plane.authenticator,
                    role_admission=control_plane.role_admission,
                    mutation_admission=control_plane.mutation_admission,
                    use_case=CiEconomicsSourceService(
                        authorizer=control_plane_authorizer,
                        resolver=GitHubCiEconomicsSources(github),
                        registration=ci_economics_store,
                    ),
                ),
                ci_economics_budgets=CiEconomicsBudgetRouteDependencies(
                    authenticator=control_plane.authenticator,
                    role_admission=control_plane.role_admission,
                    mutation_admission=control_plane.mutation_admission,
                    use_case=CiEconomicsBudgetService(
                        authorizer=control_plane_authorizer,
                        store=TransactionalBudgetPolicyStore(ci_economics_uow),
                    ),
                ),
                ci_observation=CiObservationRouteDependencies(
                    authenticator=control_plane.authenticator,
                    role_admission=control_plane.role_admission,
                    mutation_admission=control_plane.mutation_admission,
                    use_case=CiObservationService(
                        authorizer=control_plane_authorizer,
                        configuration_store=ci_observation_store,
                        query=ci_observation_store,
                        workflow_catalog=GitHubCiObservationWorkflows(github),
                    ),
                ),
                activity=ActivityRouteDependencies(
                    authenticator=control_plane.authenticator,
                    role_admission=control_plane.role_admission,
                    service=ActivityReadService(
                        activity_store,
                        TrustedIssuerActivityAuthorization(
                            RuntimeActivityRepositoryAccess(control_plane_authorizer),
                            None if identity_settings is None else identity_settings.issuer,
                        ),
                        activity_store,
                    ),
                    clock=clock,
                ),
                ci_history=CiHistoryRouteDependencies(
                    authenticator=control_plane.authenticator,
                    role_admission=control_plane.role_admission,
                    mutation_admission=control_plane.mutation_admission,
                    use_case=CiHistoryAdministrationService(
                        authorizer=control_plane_authorizer, store=ci_history_store
                    ),
                ),
                ci_history_read=CiHistoryReadRouteDependencies(
                    authenticator=control_plane.authenticator,
                    role_admission=control_plane.role_admission,
                    mutation_admission=control_plane.mutation_admission,
                    use_case=CiHistoryReadService(
                        authorizer=control_plane_authorizer,
                        store=TransactionalHistoryReadStore(ci_history_uow),
                        cursors=HistoryCursorCodec(
                            derive_history_cursor_key(
                                settings.plan_signing_private_key.reveal_for_composition()
                            )
                        ),
                    ),
                ),
                ci_history_analytics=CiHistoryAnalyticsRouteDependencies(
                    authenticator=control_plane.authenticator,
                    role_admission=control_plane.role_admission,
                    use_case=CiHistoryAnalyticsService(
                        authorizer=control_plane_authorizer,
                        store=TransactionalHistoryAnalyticsStore(purpose_uow),
                    ),
                ),
                purpose_settings=PurposeSettingsRouteDependencies(
                    authenticator=control_plane.authenticator,
                    role_admission=control_plane.role_admission,
                    mutation_admission=control_plane.mutation_admission,
                    use_case=PurposeSettingsService(
                        authorizer=control_plane_authorizer,
                        store=TransactionalPurposeSettingsStore(purpose_uow),
                    ),
                ),
                ci_measurement_report_ingestion=MeasurementReportIngestionRouteDependencies(
                    authenticator=actions_authenticator(
                        measurement_report_audience(settings.oidc_audience)
                    ),
                    use_case=MeasurementReportIngestionService(
                        allowed_scopes=frozenset(
                            _scope(value) for value in settings.control_plane_scope_allowlist
                        ),
                        sources=GitHubCiEconomicsSources(github),
                        jobs=GitHubMeasurementReportJobs(github),
                        store=ci_economics_store,
                    ),
                ),
                ci_measurement_reports=MeasurementReportReadRouteDependencies(
                    authenticator=control_plane.authenticator,
                    role_admission=control_plane.role_admission,
                    use_case=MeasurementReportReadService(
                        authorizer=control_plane_authorizer, query=ci_economics_store
                    ),
                ),
                control_plane_identity=identity_routes,
                repository_attestation=repository_attestation_routes,
                governance_baseline=governance_baseline_routes,
                governance_comparison=governance_comparison_routes,
            ),
            lifespan=resources.lifespan,
            request_timeout_seconds=settings.request_timeout_seconds,
            maximum_retained_body_bytes=settings.maximum_retained_body_bytes,
        )
    except BaseException as error:
        _rollback_constructed_resources(engine, github, jwks, keycloak, error)
        if isinstance(
            error,
            (PersistenceError, SQLAlchemyError, ValueError),
        ):
            raise RuntimeDependencyConfigurationError(
                "runtime dependency configuration is invalid"
            ) from error
        raise
    return app, resources


async def _register_production_admission(
    database_dsn: str,
    registration: ProductionAdmissionRegistration,
) -> None:
    if type(registration) is not ProductionAdmissionRegistration:
        raise TypeError("production admission registration must be exact")
    registration_engine = create_postgres_engine(database_dsn)
    try:
        async with PostgresIngressIssuanceUnitOfWork(registration_engine) as unit_of_work:
            await unit_of_work.production_admissions.register(registration)
            await unit_of_work.commit()
    except BaseException as error:
        try:
            await registration_engine.dispose()
        except BaseException:
            error.add_note("production admission registration engine cleanup also failed")
        raise
    await registration_engine.dispose()


def _require_synchronous_composition_context() -> None:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return
    raise RuntimeError("runtime dependencies must be composed before the event loop starts")


def _rollback_constructed_resources(
    engine: AsyncEngine | None,
    github: GitHubAppTransportFactory | None,
    jwks: GitHubActionsJwksProvider | None,
    keycloak: KeycloakIntegration | None,
    primary: BaseException,
) -> None:
    if engine is None and github is None and jwks is None and keycloak is None:
        return
    try:
        asyncio.run(_close_constructed_resources(engine, github, jwks, keycloak))
    except BaseException:
        primary.add_note("runtime composition rollback also failed")


async def _close_constructed_resources(
    engine: AsyncEngine | None,
    github: GitHubAppTransportFactory | None,
    jwks: GitHubActionsJwksProvider | None,
    keycloak: KeycloakIntegration | None,
) -> None:
    failures: list[BaseException] = []
    for resource, close_name in (
        (keycloak, "aclose"),
        (jwks, "aclose"),
        (github, "aclose"),
        (engine, "dispose"),
    ):
        if resource is None:
            continue
        try:
            await getattr(resource, close_name)()
        except BaseException as error:
            failures.append(error)
    if failures:
        primary = failures[0]
        for _additional in failures[1:]:
            primary.add_note("additional runtime composition rollback failure")
        raise RuntimeError("runtime composition rollback failed") from primary


def _validate_github_private_key(value: str) -> None:
    try:
        key = load_pem_private_key(value.encode("utf-8"), password=None)
    except (TypeError, ValueError):
        raise ValueError("GitHub App private key is invalid") from None
    if not isinstance(key, RSAPrivateKey):
        raise ValueError("GitHub App private key must be RSA")


def _scope(value: str) -> RepositoryScope:
    installation_id, _, repository_id = value.partition(":")
    return RepositoryScope(int(installation_id), int(repository_id))

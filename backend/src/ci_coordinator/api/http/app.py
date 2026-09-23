"""FastAPI composition for the non-enforcing dynamic-plan endpoint."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.routing import BaseRoute
from starlette.types import Lifespan

from ci_coordinator.api.http.body_limits import (
    DEFAULT_MAXIMUM_RETAINED_BODY_BYTES,
    BodyLimitPolicy,
    RequestBodyLimitMiddleware,
)
from ci_coordinator.api.http.contracts import ErrorBody
from ci_coordinator.api.http.correlation import CorrelationIdMiddleware
from ci_coordinator.api.http.dependencies import HttpRouteDependencies, PlanRouteDependencies
from ci_coordinator.api.http.errors import (
    ResponseCookieCleanupPolicy,
    TransportError,
    UnexpectedErrorMiddleware,
    framework_http_error,
    repository_access_error_handler,
    request_validation_error,
    transport_error_response,
)
from ci_coordinator.api.http.operator_ui import (
    bundled_operator_ui_directory,
    mount_operator_ui,
)
from ci_coordinator.api.http.public_request_limits import PublicRequestLimitMiddleware
from ci_coordinator.api.http.request_admission import (
    RequestAdmissionMiddleware,
    RequestAdmissionPolicy,
)
from ci_coordinator.api.http.routers.activity import ACTIVITY_REQUEST_LIMITS, build_activity_router
from ci_coordinator.api.http.routers.analytics_configuration import (
    PURPOSE_BODY_LIMIT,
    PURPOSE_REQUEST_LIMITS,
    build_purpose_settings_router,
)
from ci_coordinator.api.http.routers.ci_economics import (
    CI_ECONOMICS_ATTEMPTS_PATH,
    CI_ECONOMICS_JOBS_PATH,
    CI_ECONOMICS_MEASUREMENTS_PATH,
    CI_ECONOMICS_SOURCES_PATH,
    build_ci_economics_router,
)
from ci_coordinator.api.http.routers.ci_economics_budgets import (
    BUDGET_BODY_LIMIT,
    BUDGET_POLICIES_PATH,
    BUDGET_REQUEST_LIMIT,
    BUDGET_SIGNALS_PATH,
    build_ci_economics_budget_router,
)
from ci_coordinator.api.http.routers.ci_economics_sources import (
    SOURCE_BODY_LIMITS,
    SOURCE_REQUEST_LIMITS,
    build_ci_economics_source_router,
)
from ci_coordinator.api.http.routers.ci_history import (
    HISTORY_BODY_LIMIT,
    HISTORY_GAP_REPAIR_BODY_LIMIT,
    HISTORY_GAP_REPAIR_REQUEST_LIMIT,
    HISTORY_REQUEST_LIMIT,
    HISTORY_STATUS_PATH,
    build_ci_history_router,
)
from ci_coordinator.api.http.routers.ci_history_analytics import (
    HISTORY_ANALYTICS_REQUEST_LIMIT,
    build_ci_history_analytics_router,
)
from ci_coordinator.api.http.routers.ci_history_read import (
    HISTORY_RETENTION_BODY_LIMITS,
    HISTORY_RETENTION_REQUEST_LIMITS,
    build_ci_history_read_router,
    history_attempt_detail_request_limit,
    history_read_request_limit,
)
from ci_coordinator.api.http.routers.ci_measurement_report_ingestion import (
    REPORT_BODY_LIMIT,
    REPORT_REQUEST_LIMIT,
    build_measurement_report_ingestion_router,
)
from ci_coordinator.api.http.routers.ci_measurement_reports import (
    REPORT_BUDGET_PATH,
    REPORT_CATALOG_PATH,
    REPORT_COMPARISON_PATH,
    REPORT_READ_PATH,
    build_measurement_report_read_router,
)
from ci_coordinator.api.http.routers.ci_observation import (
    OBSERVATION_BODY_LIMIT,
    OBSERVATION_GAPS_PATH,
    OBSERVATION_REQUEST_LIMIT,
    OBSERVATION_STATUS_PATH,
    OBSERVATION_WORKFLOWS_PATH,
    build_ci_observation_router,
)
from ci_coordinator.api.http.routers.config_lifecycle_queries import (
    CONFIG_SOURCE_PATH,
    CONFIG_STATUS_PATH,
    build_config_lifecycle_query_router,
)
from ci_coordinator.api.http.routers.config_management import (
    CONFIG_ACTIVATIONS_PATH,
    CONFIG_EPOCHS_PATH,
    CONFIG_MANAGEMENT_BODY_LIMITS,
    CONFIG_ROLLBACKS_PATH,
    CONFIG_VALIDATIONS_PATH,
    build_config_management_router,
    config_control_error_payload,
)
from ci_coordinator.api.http.routers.control_plane_identity import (
    KEYCLOAK_IDENTITY_BODY_LIMITS,
    KEYCLOAK_LOGIN_CALLBACK_PATH,
    KEYCLOAK_PUBLIC_REQUEST_LIMITS,
    build_control_plane_identity_router,
)
from ci_coordinator.api.http.routers.github_webhooks import (
    create_github_webhooks_router,
    github_webhook_body_limit,
)
from ci_coordinator.api.http.routers.governance_baselines import (
    GOVERNANCE_BASELINE_BODY_LIMIT,
    build_governance_baseline_router,
)
from ci_coordinator.api.http.routers.governance_comparisons import (
    build_governance_comparison_router,
)
from ci_coordinator.api.http.routers.governance_observation import (
    build_governance_observation_router,
)
from ci_coordinator.api.http.routers.observability import (
    HEALTH_PATH,
    READINESS_PATH,
    VERSIONED_HEALTH_PATH,
    VERSIONED_READINESS_PATH,
    build_observability_router,
)
from ci_coordinator.api.http.routers.operator_controls import (
    OPERATOR_OVERRIDE_BODY_LIMIT,
    build_operator_controls_router,
)
from ci_coordinator.api.http.routers.plan_requests import (
    PLAN_REQUEST_BODY_LIMIT,
    build_plan_request_router,
)
from ci_coordinator.api.http.routers.production_cutover import (
    PRODUCTION_BODY_LIMITS,
    PRODUCTION_REQUEST_LIMITS,
    build_production_cutover_router,
)
from ci_coordinator.api.http.routers.provider_inventory import build_provider_inventory_router
from ci_coordinator.api.http.routers.repository_attestations import (
    REPOSITORY_ATTESTATION_BODY_LIMIT,
    REPOSITORY_ATTESTATION_CALLBACK_PATH,
    REPOSITORY_ATTESTATION_PUBLIC_REQUEST_LIMITS,
    build_repository_attestation_router,
)
from ci_coordinator.api.http.routers.workbench import build_workbench_router
from ci_coordinator.api.http.routers.workflow_discovery import (
    WORKFLOW_DISCOVERY_PATH,
    build_workflow_discovery_router,
)
from ci_coordinator.observability import HttpRequestObservationMiddleware
from ci_coordinator.operator_controls.auth import RepositoryAccessUnavailable

READINESS_REQUEST_CONCURRENCY_LIMIT: Final = 4
WORKFLOW_DISCOVERY_REQUEST_CONCURRENCY_LIMIT: Final = 1
CONFIG_MUTATION_REQUEST_CONCURRENCY_LIMIT: Final = 2
CONFIG_QUERY_REQUEST_CONCURRENCY_LIMIT: Final = 4
CI_ECONOMICS_REQUEST_CONCURRENCY_LIMIT: Final = 4


def create_app(
    dependencies: PlanRouteDependencies | HttpRouteDependencies,
    *,
    lifespan: Lifespan[FastAPI] | None = None,
    request_timeout_seconds: int | None = None,
    maximum_retained_body_bytes: int = DEFAULT_MAXIMUM_RETAINED_BODY_BYTES,
    operator_ui_directory: Path | None = None,
    include_operator_ui: bool = True,
) -> FastAPI:
    routes = _normalize_dependencies(dependencies)
    if routes.activity is not None and request_timeout_seconds is None:
        request_timeout_seconds = 5
    if routes.purpose_settings is not None and request_timeout_seconds is None:
        request_timeout_seconds = 30
    app = FastAPI(
        title="CI Coordinator",
        version="0.1.0",
        lifespan=lifespan,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        swagger_ui_oauth2_redirect_url=None,
    )
    app.add_exception_handler(TransportError, transport_error_response)
    app.add_exception_handler(RequestValidationError, request_validation_error)
    app.add_exception_handler(HTTPException, framework_http_error)
    app.add_exception_handler(
        RepositoryAccessUnavailable,
        repository_access_error_handler(_request_admission_policies(routes)),
    )
    admitted_routes: list[BaseRoute] = list(app.routes)
    if routes.plan is not None:
        router = build_plan_request_router(routes.plan)
        _include_router(app, router, admitted_routes)
    if routes.webhook is not None:
        router = create_github_webhooks_router(routes.webhook)
        _include_router(app, router, admitted_routes)
    if routes.operator_controls is not None:
        router = build_operator_controls_router(routes.operator_controls)
        _include_router(app, router, admitted_routes)
    if routes.config_management is not None:
        router = build_config_management_router(routes.config_management)
        _include_router(app, router, admitted_routes)
        query_router = build_config_lifecycle_query_router(routes.config_management)
        _include_router(app, query_router, admitted_routes)
    if routes.production_cutover is not None:
        _include_router(
            app, build_production_cutover_router(routes.production_cutover), admitted_routes
        )
    if routes.observability is not None:
        router = build_observability_router(routes.observability)
        _include_router(app, router, admitted_routes)
    if routes.workbench is not None:
        router = build_workbench_router(routes.workbench)
        _include_router(app, router, admitted_routes)
    if routes.provider_inventory is not None:
        router = build_provider_inventory_router(routes.provider_inventory)
        _include_router(app, router, admitted_routes)
    if routes.workflow_discovery is not None:
        router = build_workflow_discovery_router(routes.workflow_discovery)
        _include_router(app, router, admitted_routes)
    if routes.governance_observation is not None:
        router = build_governance_observation_router(routes.governance_observation)
        _include_router(app, router, admitted_routes)
    if routes.ci_economics is not None:
        router = build_ci_economics_router(routes.ci_economics)
        _include_router(app, router, admitted_routes)
    if routes.ci_measurement_reports is not None:
        _include_router(
            app,
            build_measurement_report_read_router(routes.ci_measurement_reports),
            admitted_routes,
        )
    if routes.ci_economics_sources is not None:
        _include_router(
            app, build_ci_economics_source_router(routes.ci_economics_sources), admitted_routes
        )
    if routes.ci_economics_budgets is not None:
        _include_router(
            app, build_ci_economics_budget_router(routes.ci_economics_budgets), admitted_routes
        )
    if routes.ci_observation is not None:
        _include_router(app, build_ci_observation_router(routes.ci_observation), admitted_routes)
    if routes.activity is not None:
        _include_router(app, build_activity_router(routes.activity), admitted_routes)
    if routes.ci_history is not None:
        _include_router(app, build_ci_history_router(routes.ci_history), admitted_routes)
    if routes.ci_history_read is not None:
        _include_router(app, build_ci_history_read_router(routes.ci_history_read), admitted_routes)
    if routes.purpose_settings is not None:
        _include_router(
            app, build_purpose_settings_router(routes.purpose_settings), admitted_routes
        )
    if routes.ci_history_analytics is not None:
        _include_router(
            app, build_ci_history_analytics_router(routes.ci_history_analytics), admitted_routes
        )
    if routes.ci_measurement_report_ingestion is not None:
        _include_router(
            app,
            build_measurement_report_ingestion_router(routes.ci_measurement_report_ingestion),
            admitted_routes,
        )
    if routes.control_plane_identity is not None:
        router = build_control_plane_identity_router(routes.control_plane_identity)
        _include_router(app, router, admitted_routes)
    if routes.repository_attestation is not None:
        router = build_repository_attestation_router(routes.repository_attestation)
        _include_router(app, router, admitted_routes)
    if routes.governance_baseline is not None:
        router = build_governance_baseline_router(routes.governance_baseline)
        _include_router(app, router, admitted_routes)
    if routes.governance_comparison is not None:
        router = build_governance_comparison_router(routes.governance_comparison)
        _include_router(app, router, admitted_routes)
    if include_operator_ui:
        ui_directory = operator_ui_directory or bundled_operator_ui_directory()
        if operator_ui_directory is not None or ui_directory.exists() or ui_directory.is_symlink():
            admitted_routes.extend(
                mount_operator_ui(app, ui_directory, identity=routes.control_plane_identity)
            )
    app.add_middleware(
        RequestBodyLimitMiddleware,
        policies=_body_limit_policies(routes),
        maximum_retained_body_bytes=maximum_retained_body_bytes,
    )
    if request_timeout_seconds is not None:
        app.add_middleware(
            RequestAdmissionMiddleware,
            timeout_seconds=request_timeout_seconds,
            policies=_request_admission_policies(routes),
            liveness_paths=frozenset({HEALTH_PATH, VERSIONED_HEALTH_PATH}),
            default_timeout_response={"ok": False, "error": "unavailable"},
        )
    if routes.control_plane_identity is not None or routes.repository_attestation is not None:
        public_request_limits = (
            () if routes.control_plane_identity is None else KEYCLOAK_PUBLIC_REQUEST_LIMITS
        ) + (
            ()
            if routes.repository_attestation is None
            else REPOSITORY_ATTESTATION_PUBLIC_REQUEST_LIMITS
        )
        app.add_middleware(
            PublicRequestLimitMiddleware,
            policies=public_request_limits,
        )
    if routes.observability is not None:
        app.add_middleware(
            HttpRequestObservationMiddleware,
            metrics=routes.observability.metrics,
            admitted_routes=tuple(admitted_routes),
            logger=routes.observability.request_logger,
        )
    app.add_middleware(CorrelationIdMiddleware)
    app.add_middleware(
        UnexpectedErrorMiddleware,
        diagnostics=(None if routes.observability is None else routes.observability.diagnostics),
        cookie_cleanups=_callback_cookie_cleanup_policies(routes),
    )
    return app


def _include_router(
    app: FastAPI,
    router: APIRouter,
    admitted_routes: list[BaseRoute],
) -> None:
    admitted_routes.extend(router.routes)
    app.include_router(router, responses={500: {"model": ErrorBody}})


def _normalize_dependencies(
    dependencies: PlanRouteDependencies | HttpRouteDependencies,
) -> HttpRouteDependencies:
    if isinstance(dependencies, PlanRouteDependencies):
        return HttpRouteDependencies(plan=dependencies)
    if type(dependencies) is not HttpRouteDependencies:
        raise TypeError("FastAPI app requires declared route dependencies")
    if (
        dependencies.plan is None
        and dependencies.activity is None
        and dependencies.webhook is None
        and dependencies.operator_controls is None
        and dependencies.config_management is None
        and dependencies.production_cutover is None
        and dependencies.observability is None
        and dependencies.workbench is None
        and dependencies.provider_inventory is None
        and dependencies.workflow_discovery is None
        and dependencies.governance_observation is None
        and dependencies.ci_economics is None
        and dependencies.ci_economics_sources is None
        and dependencies.ci_economics_budgets is None
        and dependencies.ci_observation is None
        and dependencies.ci_history is None
        and dependencies.ci_history_read is None
        and dependencies.ci_history_analytics is None
        and dependencies.purpose_settings is None
        and dependencies.ci_measurement_reports is None
        and dependencies.ci_measurement_report_ingestion is None
        and dependencies.control_plane_identity is None
        and dependencies.repository_attestation is None
        and dependencies.governance_baseline is None
        and dependencies.governance_comparison is None
    ):
        raise ValueError("FastAPI app requires at least one route dependency")
    return dependencies


def _body_limit_policies(routes: HttpRouteDependencies) -> tuple[BodyLimitPolicy, ...]:
    policies: list[BodyLimitPolicy] = []
    if routes.plan is not None:
        policies.append(PLAN_REQUEST_BODY_LIMIT)
    if routes.webhook is not None:
        policies.append(github_webhook_body_limit())
    if routes.operator_controls is not None:
        policies.append(OPERATOR_OVERRIDE_BODY_LIMIT)
    if routes.config_management is not None:
        policies.extend(CONFIG_MANAGEMENT_BODY_LIMITS)
    if routes.ci_economics_sources is not None:
        policies.extend(SOURCE_BODY_LIMITS)
    if routes.ci_economics_budgets is not None:
        policies.append(BUDGET_BODY_LIMIT)
    if routes.ci_observation is not None:
        policies.append(OBSERVATION_BODY_LIMIT)
    if routes.ci_history is not None:
        policies.extend((HISTORY_BODY_LIMIT, HISTORY_GAP_REPAIR_BODY_LIMIT))
    if routes.purpose_settings is not None:
        policies.append(PURPOSE_BODY_LIMIT)
    if routes.ci_history_read is not None:
        policies.extend(HISTORY_RETENTION_BODY_LIMITS)
    if routes.ci_measurement_report_ingestion is not None:
        policies.append(REPORT_BODY_LIMIT)
    if routes.production_cutover is not None:
        policies.extend(PRODUCTION_BODY_LIMITS)
    if routes.control_plane_identity is not None:
        policies.extend(KEYCLOAK_IDENTITY_BODY_LIMITS)
    if routes.repository_attestation is not None:
        policies.append(REPOSITORY_ATTESTATION_BODY_LIMIT)
    if routes.governance_baseline is not None:
        policies.append(GOVERNANCE_BASELINE_BODY_LIMIT)
    return tuple(policies)


def _request_admission_policies(
    routes: HttpRouteDependencies,
) -> tuple[RequestAdmissionPolicy, ...]:
    config_policies = _config_request_admission_policies(routes)
    if routes.activity is not None:
        config_policies += ACTIVITY_REQUEST_LIMITS
    if routes.production_cutover is not None:
        config_policies += PRODUCTION_REQUEST_LIMITS
    if routes.ci_economics_sources is not None:
        config_policies += SOURCE_REQUEST_LIMITS
    if routes.ci_economics_budgets is not None:
        config_policies += (BUDGET_REQUEST_LIMIT,)
    if routes.ci_observation is not None:
        config_policies += (OBSERVATION_REQUEST_LIMIT,)
    if routes.ci_history is not None:
        config_policies += (HISTORY_REQUEST_LIMIT, HISTORY_GAP_REPAIR_REQUEST_LIMIT)
    if routes.ci_history_read is not None:
        config_policies += HISTORY_RETENTION_REQUEST_LIMITS
        config_policies += (
            history_read_request_limit(CI_ECONOMICS_REQUEST_CONCURRENCY_LIMIT),
            history_attempt_detail_request_limit(CI_ECONOMICS_REQUEST_CONCURRENCY_LIMIT),
        )
    if routes.ci_history_analytics is not None:
        config_policies += (HISTORY_ANALYTICS_REQUEST_LIMIT,)
    if routes.purpose_settings is not None:
        config_policies += PURPOSE_REQUEST_LIMITS
    if routes.ci_measurement_report_ingestion is not None:
        config_policies += (REPORT_REQUEST_LIMIT,)
    config_identities = {(policy.path, policy.methods) for policy in config_policies}
    policies = [
        RequestAdmissionPolicy(
            path=policy.path,
            methods=policy.methods,
            overload_response=policy.overload_response,
            timeout_response=policy.timeout_response,
        )
        for policy in _body_limit_policies(routes)
        if (policy.path, policy.methods) not in config_identities
    ]
    policies.extend(config_policies)
    if routes.observability is not None:
        readiness_response = {"ok": False, "status": "not_ready"}
        policies.extend(
            RequestAdmissionPolicy(
                path=path,
                methods=frozenset({"GET"}),
                overload_response=readiness_response,
                timeout_response=readiness_response,
                concurrency_limit=READINESS_REQUEST_CONCURRENCY_LIMIT,
                admission_key="readiness",
            )
            for path in (READINESS_PATH, VERSIONED_READINESS_PATH)
        )
    if routes.workflow_discovery is not None:
        policies.append(
            RequestAdmissionPolicy(
                path=WORKFLOW_DISCOVERY_PATH,
                methods=frozenset({"GET"}),
                overload_response={"ok": False, "error": "overloaded"},
                timeout_response={"ok": False, "error": "unavailable"},
                concurrency_limit=WORKFLOW_DISCOVERY_REQUEST_CONCURRENCY_LIMIT,
                admission_key="workflow_discovery",
            )
        )
    economics_read_paths: list[str] = []
    if routes.ci_history is not None:
        economics_read_paths.append(HISTORY_STATUS_PATH)
    if routes.ci_economics_budgets is not None:
        economics_read_paths.extend((BUDGET_POLICIES_PATH, BUDGET_SIGNALS_PATH))
    if routes.ci_observation is not None:
        economics_read_paths.extend(
            (OBSERVATION_STATUS_PATH, OBSERVATION_GAPS_PATH, OBSERVATION_WORKFLOWS_PATH)
        )
    if routes.ci_economics is not None:
        economics_read_paths.extend(
            (
                CI_ECONOMICS_ATTEMPTS_PATH,
                CI_ECONOMICS_JOBS_PATH,
                CI_ECONOMICS_MEASUREMENTS_PATH,
                CI_ECONOMICS_SOURCES_PATH,
            )
        )
    if routes.ci_measurement_reports is not None:
        economics_read_paths.extend(
            (REPORT_READ_PATH, REPORT_COMPARISON_PATH, REPORT_BUDGET_PATH, REPORT_CATALOG_PATH)
        )
    if economics_read_paths:
        policies.extend(
            RequestAdmissionPolicy(
                path=path,
                methods=frozenset({"GET"}),
                overload_response={"ok": False, "error": "unavailable"},
                timeout_response={"ok": False, "error": "unavailable"},
                concurrency_limit=CI_ECONOMICS_REQUEST_CONCURRENCY_LIMIT,
                admission_key="ci_economics_query",
                response_headers=((b"cache-control", b"no-store"),),
            )
            for path in economics_read_paths
        )
    return tuple(policies)


def _config_request_admission_policies(
    routes: HttpRouteDependencies,
) -> tuple[RequestAdmissionPolicy, ...]:
    if routes.config_management is None:
        return ()
    mutation_overload = config_control_error_payload("overloaded")
    mutation_timeout = config_control_error_payload("unavailable")
    mutation_policies = tuple(
        RequestAdmissionPolicy(
            path=path,
            methods=frozenset({"POST"}),
            overload_response=mutation_overload,
            timeout_response=mutation_timeout,
            concurrency_limit=CONFIG_MUTATION_REQUEST_CONCURRENCY_LIMIT,
            admission_key="config_mutation",
            response_headers=((b"cache-control", b"no-store"),),
        )
        for path in (
            CONFIG_VALIDATIONS_PATH,
            CONFIG_EPOCHS_PATH,
            CONFIG_ACTIVATIONS_PATH,
            CONFIG_ROLLBACKS_PATH,
        )
    )
    query_response = config_control_error_payload("unavailable")
    query_policies = tuple(
        RequestAdmissionPolicy(
            path=path,
            methods=frozenset({"GET"}),
            overload_response=config_control_error_payload("overloaded"),
            timeout_response=query_response,
            concurrency_limit=CONFIG_QUERY_REQUEST_CONCURRENCY_LIMIT,
            admission_key="config_query",
            response_headers=((b"cache-control", b"no-store"),),
        )
        for path in (CONFIG_STATUS_PATH, CONFIG_SOURCE_PATH)
    )
    return mutation_policies + query_policies


def _callback_cookie_cleanup_policies(
    routes: HttpRouteDependencies,
) -> tuple[ResponseCookieCleanupPolicy, ...]:
    policies: list[ResponseCookieCleanupPolicy] = []
    if routes.control_plane_identity is not None:
        policies.append(
            ResponseCookieCleanupPolicy(
                path=KEYCLOAK_LOGIN_CALLBACK_PATH,
                methods=frozenset({"GET"}),
                name=routes.control_plane_identity.transaction_cookie_name,
                cookie_path=KEYCLOAK_LOGIN_CALLBACK_PATH,
                secure=routes.control_plane_identity.secure_cookies,
                httponly=True,
                samesite="lax",
            )
        )
    if routes.repository_attestation is not None:
        policies.append(
            ResponseCookieCleanupPolicy(
                path=REPOSITORY_ATTESTATION_CALLBACK_PATH,
                methods=frozenset({"GET"}),
                name=routes.repository_attestation.transaction_cookie_name,
                cookie_path=REPOSITORY_ATTESTATION_CALLBACK_PATH,
                secure=routes.repository_attestation.secure_cookies,
                httponly=True,
                samesite="lax",
            )
        )
    return tuple(policies)

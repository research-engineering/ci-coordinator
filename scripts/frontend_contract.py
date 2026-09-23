"""Deterministic OpenAPI projection owned by the operator UI boundary."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Never, cast

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
    GovernanceBaselineRouteDependencies,
    GovernanceComparisonRouteDependencies,
    GovernanceObservationRouteDependencies,
    HttpRouteDependencies,
    MeasurementReportIngestionRouteDependencies,
    MeasurementReportReadRouteDependencies,
    ProviderInventoryRouteDependencies,
    PurposeSettingsRouteDependencies,
    RepositoryAttestationRouteDependencies,
    WorkbenchRouteDependencies,
    WorkflowDiscoveryRouteDependencies,
)
from ci_coordinator.api.http.routers.activity import ACTIVITY_PATHS
from ci_coordinator.api.http.routers.analytics_configuration import (
    PURPOSE_SETTINGS_PATH,
)
from ci_coordinator.api.http.routers.ci_economics import (
    CI_ECONOMICS_ATTEMPTS_PATH,
    CI_ECONOMICS_JOBS_PATH,
    CI_ECONOMICS_MEASUREMENTS_PATH,
    CI_ECONOMICS_SOURCES_PATH,
)
from ci_coordinator.api.http.routers.ci_economics_budgets import (
    BUDGET_CONFIGURATION_PATH,
    BUDGET_POLICIES_PATH,
    BUDGET_SIGNALS_PATH,
)
from ci_coordinator.api.http.routers.ci_economics_sources import (
    SOURCE_DISCOVERY_PATH,
    SOURCE_REGISTRATION_PATH,
)
from ci_coordinator.api.http.routers.ci_history import (
    HISTORY_CONFIGURATION_PATH,
    HISTORY_GAP_REPAIR_PATH,
    HISTORY_STATUS_PATH,
)
from ci_coordinator.api.http.routers.ci_history_analytics import HISTORY_ANALYTICS_PATH
from ci_coordinator.api.http.routers.ci_history_read import (
    HISTORY_ATTEMPT_DETAIL_PATH,
    HISTORY_READ_PATH,
    HISTORY_RETENTION_APPLY_PATH,
    HISTORY_RETENTION_PREVIEW_PATH,
)
from ci_coordinator.api.http.routers.ci_measurement_report_ingestion import (
    REPORT_INGESTION_PATH,
)
from ci_coordinator.api.http.routers.ci_measurement_reports import (
    REPORT_BUDGET_PATH,
    REPORT_CATALOG_PATH,
    REPORT_COMPARISON_PATH,
    REPORT_READ_PATH,
)
from ci_coordinator.api.http.routers.ci_observation import (
    OBSERVATION_CONFIGURATION_PATH,
    OBSERVATION_GAPS_PATH,
    OBSERVATION_STATUS_PATH,
    OBSERVATION_WORKFLOWS_PATH,
)
from ci_coordinator.api.http.routers.config_lifecycle_queries import (
    CONFIG_SOURCE_PATH,
    CONFIG_STATUS_PATH,
)
from ci_coordinator.api.http.routers.config_management import (
    CONFIG_ACTIVATIONS_PATH,
    CONFIG_EPOCHS_PATH,
    CONFIG_ROLLBACKS_PATH,
    CONFIG_VALIDATIONS_PATH,
)
from ci_coordinator.api.http.routers.control_plane_identity import (
    CONTROL_PLANE_SESSION_PATH,
    KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH,
    KEYCLOAK_LOGIN_CALLBACK_PATH,
    KEYCLOAK_LOGIN_START_PATH,
    KEYCLOAK_LOGOUT_PATH,
)
from ci_coordinator.api.http.routers.governance_baselines import (
    GOVERNANCE_BASELINES_PATH,
)
from ci_coordinator.api.http.routers.governance_comparisons import (
    GOVERNANCE_COMPARISON_PATH,
)
from ci_coordinator.api.http.routers.governance_observation import (
    GOVERNANCE_OBSERVATION_PATH,
)
from ci_coordinator.api.http.routers.provider_inventory import (
    PROVIDER_INSTALLATIONS_PATH,
    PROVIDER_REPOSITORIES_PATH,
)
from ci_coordinator.api.http.routers.repository_attestations import (
    REPOSITORY_ATTESTATION_CALLBACK_PATH,
    REPOSITORY_ATTESTATION_START_PATH,
)
from ci_coordinator.api.http.routers.workbench import (
    WORKBENCH_REPOSITORY_PATH,
)
from ci_coordinator.api.http.routers.workflow_discovery import (
    WORKFLOW_DISCOVERY_PATH,
)
from scripts.config_lifecycle_http_profile import (
    assert_config_lifecycle_openapi,
    load_config_lifecycle_http_profile,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = REPO_ROOT / "frontend" / "openapi" / "workbench.openapi.json"


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        raise ValueError(message)


@dataclass(frozen=True, slots=True)
class _OpenApiCookieSettings:
    transaction_cookie_name: str
    secure_cookies: bool = True


def rendered_contract() -> bytes:
    identity = _OpenApiCookieSettings("__Host-ci_coordinator_login")
    attestation = _OpenApiCookieSettings("__Host-ci_coordinator_reviewer")
    app = create_app(
        HttpRouteDependencies(
            activity=cast(ActivityRouteDependencies, object()),
            purpose_settings=cast(PurposeSettingsRouteDependencies, object()),
            workbench=cast(WorkbenchRouteDependencies, object()),
            provider_inventory=cast(ProviderInventoryRouteDependencies, object()),
            workflow_discovery=cast(WorkflowDiscoveryRouteDependencies, object()),
            governance_observation=cast(GovernanceObservationRouteDependencies, object()),
            control_plane_identity=cast(ControlPlaneIdentityRouteDependencies, identity),
            repository_attestation=cast(RepositoryAttestationRouteDependencies, attestation),
            config_management=cast(ConfigManagementRouteDependencies, object()),
            governance_baseline=cast(GovernanceBaselineRouteDependencies, object()),
            governance_comparison=cast(GovernanceComparisonRouteDependencies, object()),
            ci_economics=cast(CiEconomicsRouteDependencies, object()),
            ci_economics_sources=cast(CiEconomicsSourceRouteDependencies, object()),
            ci_economics_budgets=cast(CiEconomicsBudgetRouteDependencies, object()),
            ci_observation=cast(CiObservationRouteDependencies, object()),
            ci_history=cast(CiHistoryRouteDependencies, object()),
            ci_history_read=cast(CiHistoryReadRouteDependencies, object()),
            ci_history_analytics=cast(CiHistoryAnalyticsRouteDependencies, object()),
            ci_measurement_reports=cast(MeasurementReportReadRouteDependencies, object()),
            ci_measurement_report_ingestion=cast(
                MeasurementReportIngestionRouteDependencies, object()
            ),
        ),
        include_operator_ui=False,
    )
    schema = app.openapi()
    if set(schema.get("paths", {})) != {
        *ACTIVITY_PATHS,
        PURPOSE_SETTINGS_PATH,
        WORKBENCH_REPOSITORY_PATH,
        WORKFLOW_DISCOVERY_PATH,
        GOVERNANCE_OBSERVATION_PATH,
        GOVERNANCE_BASELINES_PATH,
        GOVERNANCE_COMPARISON_PATH,
        PROVIDER_INSTALLATIONS_PATH,
        PROVIDER_REPOSITORIES_PATH,
        KEYCLOAK_LOGIN_START_PATH,
        KEYCLOAK_LOGIN_CALLBACK_PATH,
        CONTROL_PLANE_SESSION_PATH,
        KEYCLOAK_LOGOUT_PATH,
        KEYCLOAK_BACK_CHANNEL_LOGOUT_PATH,
        REPOSITORY_ATTESTATION_START_PATH,
        REPOSITORY_ATTESTATION_CALLBACK_PATH,
        CONFIG_EPOCHS_PATH,
        CONFIG_ACTIVATIONS_PATH,
        CONFIG_ROLLBACKS_PATH,
        CONFIG_VALIDATIONS_PATH,
        CONFIG_STATUS_PATH,
        CONFIG_SOURCE_PATH,
        CI_ECONOMICS_ATTEMPTS_PATH,
        CI_ECONOMICS_JOBS_PATH,
        CI_ECONOMICS_MEASUREMENTS_PATH,
        CI_ECONOMICS_SOURCES_PATH,
        SOURCE_DISCOVERY_PATH,
        SOURCE_REGISTRATION_PATH,
        BUDGET_CONFIGURATION_PATH,
        BUDGET_POLICIES_PATH,
        BUDGET_SIGNALS_PATH,
        OBSERVATION_CONFIGURATION_PATH,
        HISTORY_CONFIGURATION_PATH,
        HISTORY_GAP_REPAIR_PATH,
        HISTORY_STATUS_PATH,
        HISTORY_READ_PATH,
        HISTORY_ATTEMPT_DETAIL_PATH,
        HISTORY_RETENTION_APPLY_PATH,
        HISTORY_RETENTION_PREVIEW_PATH,
        HISTORY_ANALYTICS_PATH,
        OBSERVATION_STATUS_PATH,
        OBSERVATION_GAPS_PATH,
        OBSERVATION_WORKFLOWS_PATH,
        REPORT_INGESTION_PATH,
        REPORT_READ_PATH,
        REPORT_COMPARISON_PATH,
        REPORT_BUDGET_PATH,
        REPORT_CATALOG_PATH,
    }:
        raise ValueError("operator UI OpenAPI projection has an unexpected route set")
    assert_config_lifecycle_openapi(schema, load_config_lifecycle_http_profile())
    _replace_recursive_value_schemas(schema)
    return (json.dumps(schema, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _replace_recursive_value_schemas(schema: dict[str, object]) -> None:
    components = schema.get("components")
    if not isinstance(components, dict):
        raise TypeError("operator UI OpenAPI projection has no components")
    schemas = components.get("schemas")
    if not isinstance(schemas, dict) or not {"FactValue", "JsonValue"} <= schemas.keys():
        raise ValueError("operator UI OpenAPI projection has no recursive value schemas")
    schemas["JsonValue"] = {
        "description": (
            "Bounded JSON value admitted by the browser runtime schema; represented as "
            "unknown in generated TypeScript to avoid recursive indexed-type emission."
        )
    }
    schemas["FactValue"] = {
        "anyOf": [
            {"type": "boolean"},
            {"type": "integer"},
            {"type": "number"},
            {"type": "string"},
            {"type": "null"},
            {"items": {}, "type": "array"},
        ],
        "description": (
            "Bounded recursive scalar-array value; nested members are admitted by the browser "
            "runtime schema and represented as unknown to avoid recursive indexed-type emission."
        ),
    }


def write_contract(path: Path = CONTRACT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as output:
            output.write(rendered_contract())
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path.exists():
            temporary_path.unlink()


def check_contract(path: Path = CONTRACT_PATH) -> None:
    try:
        current = path.read_bytes()
    except OSError as error:
        raise ValueError("operator UI OpenAPI projection is missing") from error
    if current != rendered_contract():
        raise ValueError("operator UI OpenAPI projection is stale")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _ArgumentParser(prog="python -m scripts.frontend_contract")
    parser.add_argument("operation", choices=("check", "write"))
    try:
        operation = parser.parse_args(sys.argv[1:] if argv is None else argv).operation
        write_contract() if operation == "write" else check_contract()
    except (OSError, TypeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

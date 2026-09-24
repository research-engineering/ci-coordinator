from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from scripts.frontend_contract import (
    CONTRACT_PATH,
    check_contract,
    rendered_contract,
    write_contract,
)
from scripts.proofkit_inputs import GENERATED_ARTIFACTS


def test_rendered_contract_has_exact_control_plane_workbench_surface() -> None:
    contract = json.loads(rendered_contract())
    repository_path = "/api/v1/workbench/repositories/{installation_id}/{repository_id}"
    discovery_path = f"{repository_path}/workflow-discovery"
    baseline_path = f"{repository_path}/governance-baselines"
    comparison_path = f"{repository_path}/governance-comparison"
    governance_path = f"{repository_path}/governance-observation"
    config_status_path = "/api/v1/config/repositories/{installation_id}/{repository_id}/status"
    config_source_path = (
        "/api/v1/config/repositories/{installation_id}/{repository_id}/epochs/{epoch_id}/source"
    )
    economics_attempts_path = (
        "/api/v1/economics/repositories/{installation_id}/{repository_id}/attempts"
    )
    economics_jobs_path = f"{economics_attempts_path}/{{workflow_run_id}}/{{run_attempt}}/jobs"
    economics_v2 = "/api/v2/economics/repositories/{installation_id}/{repository_id}"
    expected = {
        ("get", "/api/v1/activity/security"): (
            "query_security_activity",
            {"200", "400", "401", "403", "422", "503"},
        ),
        ("get", "/api/v1/activity/security/export"): (
            "export_security_activity",
            {"200", "400", "401", "403", "422", "503"},
        ),
        ("get", "/api/v1/activity/repositories/{installation_id}/{repository_id}"): (
            "query_repository_activity",
            {"200", "400", "401", "403", "422", "503"},
        ),
        (
            "get",
            "/api/v1/activity/repositories/{installation_id}/{repository_id}/export",
        ): (
            "export_repository_activity",
            {"200", "400", "401", "403", "422", "503"},
        ),
        ("post", "/api/v1/auth/keycloak/backchannel-logout"): (
            "apply_keycloak_back_channel_logout",
            {"204", "400", "413", "429", "503"},
        ),
        ("get", "/api/v1/auth/keycloak/callback"): (
            "complete_keycloak_browser_login",
            {"302", "400", "429", "503"},
        ),
        ("post", "/api/v1/auth/keycloak/logout"): (
            "logout_keycloak_browser_session",
            {"200", "401", "403", "413", "422", "503"},
        ),
        ("get", "/api/v1/auth/keycloak/start"): (
            "start_keycloak_browser_login",
            {"302", "400", "429", "503"},
        ),
        ("get", "/api/v1/auth/session"): (
            "get_control_plane_session",
            {"200", "401", "503"},
        ),
        ("post", "/api/v1/config/activations"): (
            "activate_config_epoch",
            {"200", "400", "401", "403", "404", "409", "413", "422", "503"},
        ),
        ("post", "/api/v1/config/epochs"): (
            "register_config_epoch",
            {"200", "201", "400", "401", "403", "409", "413", "422", "503"},
        ),
        ("post", "/api/v1/config/rollbacks"): (
            "rollback_config_epoch",
            {"200", "400", "401", "403", "404", "409", "413", "422", "503"},
        ),
        ("post", "/api/v1/config/validations"): (
            "validate_config_epoch",
            {"200", "400", "401", "403", "413", "422", "503"},
        ),
        ("get", config_status_path): (
            "get_config_epoch_status",
            {"200", "401", "403", "422", "503"},
        ),
        ("get", config_source_path): (
            "export_config_epoch_source",
            {"200", "401", "403", "404", "422", "503"},
        ),
        ("get", economics_attempts_path): (
            "list_repository_ci_economics_attempts",
            {"200", "401", "403", "422", "503"},
        ),
        ("get", economics_jobs_path): (
            "get_ci_economics_attempt_jobs",
            {"200", "401", "403", "404", "422", "503"},
        ),
        (
            "get",
            economics_v2 + "/attempts/{workflow_run_id}/{run_attempt}/measurements",
        ): (
            "get_ci_economics_attempt_measurements",
            {"200", "401", "403", "404", "422", "503"},
        ),
        ("get", economics_v2 + "/sources"): (
            "list_ci_economics_provider_sources",
            {"200", "401", "403", "422", "503"},
        ),
        ("get", economics_v2 + "/sources/{source_id}/reports"): (
            "list_ci_measurement_reports",
            {"200", "401", "403", "404", "422", "503"},
        ),
        ("post", "/api/v2/economics/source-discovery"): (
            "discover_ci_economics_sources",
            {"200", "400", "401", "403", "413", "422", "503"},
        ),
        ("post", "/api/v2/economics/sources"): (
            "register_ci_economics_source",
            {"200", "201", "400", "401", "403", "409", "413", "422", "503"},
        ),
        ("post", "/api/v2/economics/reports"): (
            "record_ci_measurement_report",
            {
                "200",
                "201",
                "400",
                "401",
                "403",
                "409",
                "410",
                "413",
                "422",
                "429",
                "503",
            },
        ),
        ("get", economics_v2 + "/reports/{report_id}"): (
            "get_ci_measurement_report",
            {"200", "401", "403", "404", "422", "503"},
        ),
        ("get", economics_v2 + "/report-comparisons"): (
            "compare_ci_measurement_reports",
            {"200", "401", "403", "404", "422", "503"},
        ),
        ("get", economics_v2 + "/reports/{report_id}/budget"): (
            "evaluate_ci_measurement_report_budget",
            {"200", "401", "403", "404", "422", "503"},
        ),
        ("post", "/api/v2/economics/budget-policies"): (
            "configure_ci_economics_budget",
            {"200", "400", "401", "403", "409", "413", "422", "503"},
        ),
        ("get", economics_v2 + "/budget-policies"): (
            "list_ci_economics_budget_policies",
            {"200", "400", "401", "403", "413", "422", "503"},
        ),
        ("get", economics_v2 + "/budget-signals"): (
            "list_ci_economics_budget_signals",
            {"200", "400", "401", "403", "413", "422", "503"},
        ),
        ("post", "/api/v2/economics/observation"): (
            "configure_ci_observation",
            {"200", "400", "401", "403", "409", "413", "422", "503"},
        ),
        ("post", "/api/v2/economics/history"): (
            "configure_ci_history",
            {"200", "400", "401", "403", "409", "413", "422", "503"},
        ),
        ("get", economics_v2 + "/history"): (
            "get_ci_history_status",
            {"200", "400", "401", "403", "413", "422", "503"},
        ),
        ("get", economics_v2 + "/history/archive/{kind}"): (
            "read_ci_history_archive",
            {"200", "400", "401", "403", "404", "409", "413", "422", "503"},
        ),
        (
            "get",
            economics_v2 + "/history/attempts/{workflow_run_id}/{run_attempt}/detail",
        ): (
            "read_ci_history_attempt_detail",
            {"200", "400", "401", "403", "404", "409", "413", "422", "503"},
        ),
        ("get", economics_v2 + "/history/analytics"): (
            "get_ci_history_analytics",
            {"200", "400", "401", "403", "422", "503"},
        ),
        ("get", economics_v2 + "/history/analytics/settings"): (
            "get_analytics_purpose_settings",
            {"200", "400", "401", "403", "422", "503"},
        ),
        ("put", economics_v2 + "/history/analytics/settings"): (
            "configure_analytics_purpose_settings",
            {"200", "400", "401", "403", "409", "413", "422", "503"},
        ),
        ("post", "/api/v2/economics/history/gaps/retry"): (
            "retry_ci_history_gaps",
            {"200", "400", "401", "403", "409", "413", "422", "503"},
        ),
        ("post", "/api/v2/economics/history/retention/preview"): (
            "preview_ci_history_retention",
            {"200", "400", "401", "403", "404", "409", "413", "422", "503"},
        ),
        ("post", "/api/v2/economics/history/retention/apply"): (
            "apply_ci_history_retention",
            {"200", "400", "401", "403", "404", "409", "413", "422", "503"},
        ),
        ("get", economics_v2 + "/observation"): (
            "get_ci_observation_status",
            {"200", "400", "401", "403", "413", "422", "503"},
        ),
        ("get", economics_v2 + "/observation/gaps"): (
            "list_ci_observation_gaps",
            {"200", "400", "401", "403", "413", "422", "503"},
        ),
        ("get", economics_v2 + "/observation/workflows"): (
            "list_ci_observation_workflows",
            {"200", "400", "401", "403", "413", "422", "503"},
        ),
        ("get", "/api/v1/repository-attestations/github/callback"): (
            "complete_github_repository_attestation",
            {"303", "400", "401", "403", "409", "422", "429", "503"},
        ),
        ("post", "/api/v1/repository-attestations/github/start"): (
            "start_github_repository_attestation",
            {"200", "400", "401", "403", "409", "413", "422", "503"},
        ),
        ("get", "/api/v1/workbench/installations"): (
            "list_operator_provider_installations",
            {"200", "401", "403", "404", "422", "429", "503"},
        ),
        ("get", "/api/v1/workbench/installations/{installation_id}/repositories"): (
            "list_operator_provider_repositories",
            {"200", "401", "403", "404", "409", "422", "429", "503"},
        ),
        ("get", repository_path): (
            "get_repository_workbench_snapshot",
            {"200", "401", "403", "422", "503"},
        ),
        ("get", discovery_path): (
            "discover_repository_workflows",
            {"200", "401", "403", "404", "422", "429", "503"},
        ),
        ("get", governance_path): (
            "observe_repository_effective_governance",
            {"200", "401", "403", "404", "422", "429", "503"},
        ),
        ("get", comparison_path): (
            "compare_repository_effective_governance",
            {"200", "401", "403", "404", "409", "422", "429", "503"},
        ),
        ("get", baseline_path): (
            "read_active_governance_baseline",
            {"200", "401", "403", "422", "503"},
        ),
        ("post", baseline_path): (
            "approve_governance_baseline",
            {"200", "201", "400", "401", "403", "409", "413", "422", "503"},
        ),
    }
    observed = {
        (method, path): (
            operation["operationId"],
            set(operation["responses"]) - {"500"},
        )
        for path, path_item in contract["paths"].items()
        for method, operation in path_item.items()
    }

    assert observed == expected
    for path, path_item in contract["paths"].items():
        for operation in path_item.values():
            error_schema = operation["responses"]["500"]["content"]["application/json"]["schema"]
            assert error_schema == {"$ref": "#/components/schemas/ErrorBody"}, path

    schemes = contract["components"]["securitySchemes"]
    assert set(schemes) == {
        "ControlPlaneBearer",
        "ControlPlaneSession",
        "GitHubActionsOIDC",
    }
    dual_plane_security: list[dict[str, list[object]]] = [
        {"ControlPlaneBearer": []},
        {"ControlPlaneSession": []},
    ]
    for method, path in (
        ("get", "/api/v1/activity/security"),
        ("get", "/api/v1/activity/security/export"),
        ("get", "/api/v1/activity/repositories/{installation_id}/{repository_id}"),
        (
            "get",
            "/api/v1/activity/repositories/{installation_id}/{repository_id}/export",
        ),
        ("post", "/api/v1/config/activations"),
        ("post", "/api/v1/config/epochs"),
        ("post", "/api/v1/config/rollbacks"),
        ("post", "/api/v1/config/validations"),
        ("get", config_status_path),
        ("get", config_source_path),
        ("get", economics_attempts_path),
        ("get", economics_jobs_path),
        (
            "get",
            economics_v2 + "/attempts/{workflow_run_id}/{run_attempt}/measurements",
        ),
        ("get", economics_v2 + "/sources"),
        ("get", economics_v2 + "/sources/{source_id}/reports"),
        ("post", "/api/v2/economics/source-discovery"),
        ("post", "/api/v2/economics/sources"),
        ("get", economics_v2 + "/reports/{report_id}"),
        ("get", economics_v2 + "/report-comparisons"),
        ("get", economics_v2 + "/reports/{report_id}/budget"),
        ("post", "/api/v2/economics/budget-policies"),
        ("get", economics_v2 + "/budget-policies"),
        ("get", economics_v2 + "/budget-signals"),
        ("post", "/api/v2/economics/observation"),
        ("post", "/api/v2/economics/history"),
        ("get", economics_v2 + "/history"),
        ("get", economics_v2 + "/history/archive/{kind}"),
        ("get", economics_v2 + "/history/attempts/{workflow_run_id}/{run_attempt}/detail"),
        ("get", economics_v2 + "/history/analytics"),
        ("get", economics_v2 + "/history/analytics/settings"),
        ("put", economics_v2 + "/history/analytics/settings"),
        ("post", "/api/v2/economics/history/retention/preview"),
        ("post", "/api/v2/economics/history/retention/apply"),
        ("get", economics_v2 + "/observation"),
        ("get", economics_v2 + "/observation/gaps"),
        ("get", economics_v2 + "/observation/workflows"),
        ("get", "/api/v1/workbench/installations"),
        ("post", baseline_path),
    ):
        assert contract["paths"][path][method]["security"] == dual_plane_security
    assert contract["paths"]["/api/v2/economics/reports"]["post"]["security"] == [
        {"GitHubActionsOIDC": []}
    ]
    assert contract["paths"]["/api/v1/auth/session"]["get"]["security"] == [
        {"ControlPlaneSession": []}
    ]
    assert contract["paths"]["/api/v1/repository-attestations/github/start"]["post"][
        "security"
    ] == [{"ControlPlaneSession": []}]

    history_input = contract["components"]["schemas"]["HistoryConfigurationRequest"]
    assert set(history_input["required"]) == {
        "installationId",
        "repositoryId",
        "expectedRevision",
        "configuration",
        "initialCreatedFrom",
        "rescan",
        "operationId",
    }
    assert set(history_input["properties"]) == set(history_input["required"]) | {
        "expandCreatedFrom"
    }
    assert history_input["properties"]["expandCreatedFrom"] == {
        "anyOf": [{"$ref": "#/components/schemas/ObservationTimestamp"}, {"type": "null"}]
    }
    assert history_input["additionalProperties"] is False
    assert "actor" not in history_input["properties"]
    history_mutation = contract["components"]["schemas"]["HistoryMutationResponse"]
    assert set(history_mutation["required"]) == set(history_mutation["properties"])
    assert history_mutation["additionalProperties"] is False
    bad_population = contract["paths"]["/api/v2/economics/history"]["post"]["responses"]["400"]
    assert {
        item["$ref"] for item in bad_population["content"]["application/json"]["schema"]["anyOf"]
    } == {
        "#/components/schemas/EconomicsSourceErrorResponse",
        "#/components/schemas/HistoryMutationResponse",
    }

    for path, maximum_code_length in (
        ("/api/v1/auth/keycloak/callback", 1024),
        ("/api/v1/repository-attestations/github/callback", 512),
    ):
        parameters = contract["paths"][path]["get"]["parameters"]
        assert [(item["name"], item["in"], item["required"]) for item in parameters] == [
            ("code", "query", True),
            ("state", "query", True),
        ] + (
            [("iss", "query", True), ("session_state", "query", False)]
            if path == "/api/v1/auth/keycloak/callback"
            else []
        )
        assert parameters[0]["schema"]["maxLength"] == maximum_code_length
        assert parameters[1]["schema"]["pattern"] == "^[A-Za-z0-9_-]{43}$"

    rate_limited_operations = {
        (method, path, response["content"]["application/json"]["schema"]["$ref"])
        for path, path_item in contract["paths"].items()
        for method, operation in path_item.items()
        if (response := operation["responses"].get("429")) is not None
    }
    assert rate_limited_operations == {
        (
            "post",
            "/api/v2/economics/reports",
            "#/components/schemas/MeasurementReportError",
        ),
        (
            "post",
            "/api/v1/auth/keycloak/backchannel-logout",
            "#/components/schemas/ControlPlaneIdentityErrorResponse",
        ),
        (
            "get",
            "/api/v1/auth/keycloak/callback",
            "#/components/schemas/ControlPlaneIdentityErrorResponse",
        ),
        (
            "get",
            "/api/v1/auth/keycloak/start",
            "#/components/schemas/ControlPlaneIdentityErrorResponse",
        ),
        (
            "get",
            "/api/v1/repository-attestations/github/callback",
            "#/components/schemas/RepositoryAttestationErrorResponse",
        ),
        (
            "get",
            "/api/v1/workbench/installations",
            "#/components/schemas/ProviderInventoryErrorResponse",
        ),
        (
            "get",
            "/api/v1/workbench/installations/{installation_id}/repositories",
            "#/components/schemas/ProviderInventoryErrorResponse",
        ),
        (
            "get",
            comparison_path,
            "#/components/schemas/GovernanceComparisonErrorResponse",
        ),
        (
            "get",
            governance_path,
            "#/components/schemas/GovernanceObservationErrorResponse",
        ),
        (
            "get",
            discovery_path,
            "#/components/schemas/WorkflowDiscoveryErrorResponse",
        ),
    }

    identity_error = contract["components"]["schemas"]["ControlPlaneIdentityErrorResponse"]
    assert identity_error == {
        "additionalProperties": False,
        "properties": {
            "error": {
                "enum": [
                    "forbidden",
                    "invalid_login",
                    "invalid_logout",
                    "overloaded",
                    "rate_limited",
                    "unauthenticated",
                    "unavailable",
                ],
                "title": "Error",
                "type": "string",
            },
            "ok": {"const": False, "title": "Ok", "type": "boolean"},
        },
        "required": ["ok", "error"],
        "title": "ControlPlaneIdentityErrorResponse",
        "type": "object",
    }
    assert "BrowserIdentityErrorResponse" not in contract["components"]["schemas"]
    assert "ProposalReviewResponse" not in contract["components"]["schemas"]
    for operation_path in (
        "/api/v1/workbench/installations/{installation_id}/repositories",
        repository_path,
        comparison_path,
    ):
        operation = contract["paths"][operation_path]["get"]
        assert operation["responses"]["422"]["content"]["application/json"]["schema"] == {
            "$ref": "#/components/schemas/InvalidRequestBody"
        }
    assert contract["paths"][discovery_path]["get"]["responses"]["422"]["content"][
        "application/json"
    ]["schema"] == {
        "anyOf": [
            {"$ref": "#/components/schemas/InvalidRequestBody"},
            {"$ref": "#/components/schemas/WorkflowDiscoveryErrorResponse"},
        ],
        "title": "Response 422 Discover Repository Workflows",
    }
    baseline_responses = contract["paths"][baseline_path]["post"]["responses"]
    assert baseline_responses["400"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/InvalidRequestBody"
    }
    assert baseline_responses["413"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/InvalidRequestBody"
    }
    assert contract["components"]["schemas"]["JsonValue"] == {
        "description": (
            "Bounded JSON value admitted by the browser runtime schema; represented as "
            "unknown in generated TypeScript to avoid recursive indexed-type emission."
        )
    }
    schemas = contract["components"]["schemas"]
    assert schemas["ConfigEpochAcceptedBody"]["properties"]["schemaVersion"]["const"] == (
        "ci-config-epoch-registration-result/v1"
    )
    assert schemas["ConfigActivationAcceptedBody"]["properties"]["schemaVersion"]["const"] == (
        "ci-config-epoch-activation-result/v1"
    )
    assert (
        schemas["ConfigEpochValidationAcceptedBody"]["properties"]["schemaVersion"]["const"]
        == "ci-config-epoch-validation-result/v1"
    )
    assert schemas["ConfigEpochStatusBody"]["properties"]["schemaVersion"]["const"] == (
        "ci-config-epoch-status/v1"
    )
    assert schemas["FactValue"] == {
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
    assert schemas["ProfileCapacityResponse"]["discriminator"]["propertyName"] == ("executionKind")
    scope_properties = schemas["GovernanceScopeResponse"]["properties"]
    assert scope_properties["installationId"] == {
        "maximum": 9_007_199_254_740_991.0,
        "minimum": 1.0,
        "title": "Installationid",
        "type": "integer",
    }
    repository_properties = schemas["GovernanceRepositoryResponse"]["properties"]
    assert repository_properties["defaultBranch"]["maxLength"] == 512
    branch_pattern = repository_properties["defaultBranch"]["pattern"]
    assert branch_pattern == (
        r"^(?![-/])(?!.*(?:\.\.|@\{|//))(?!.*(?:^|/)\.)"
        r"(?!.*(?:^|/)[^/]*\.lock(?:/|$))(?!.*[./]$)"
        r"[^\x00-\x20\x7f~^:?*\[\\]+$"
    )
    assert re.fullmatch(branch_pattern, "@") is not None
    assert re.fullmatch(branch_pattern, "feature/release") is not None
    assert re.fullmatch(branch_pattern, "/main") is None
    assert re.fullmatch(branch_pattern, "main/") is None
    rule_properties = schemas["EffectiveGovernanceRuleResponse"]["properties"]
    assert rule_properties["canonicalJson"]["maxLength"] == 1_048_576
    assert rule_properties["rulesetId"]["minimum"] == 1.0
    observation_properties = schemas["GovernanceObservationResponse"]["properties"]
    assert observation_properties["apiVersion"]["const"] == "2026-03-10"
    assert observation_properties["rules"]["maxItems"] == 1_000
    assert observation_properties["stateDigest"]["pattern"] == "^[0-9a-f]{64}$"
    retry_schema = schemas["GovernanceObservationErrorResponse"]["properties"]["retryAfterSeconds"][
        "anyOf"
    ][0]
    assert retry_schema == {"maximum": 3_600.0, "minimum": 0.0, "type": "integer"}


def test_contract_write_is_reproducible_and_check_detects_drift(tmp_path: Path) -> None:
    path = tmp_path / "workbench.openapi.json"

    write_contract(path)
    first = path.read_bytes()
    write_contract(path)

    assert path.read_bytes() == first == rendered_contract()
    check_contract(path)
    path.write_bytes(first + b" ")
    with pytest.raises(ValueError, match="stale"):
        check_contract(path)


def test_openapi_provenance_closes_every_imported_route_owner() -> None:
    repo_root = CONTRACT_PATH.parents[2]
    profile = json.loads((repo_root / "proofkit/repo-profile.json").read_bytes())
    declared = profile["documents"]["generatedArtifacts"]
    observed = [dict(artifact) for artifact in GENERATED_ARTIFACTS]
    assert declared == observed

    openapi_artifact = next(
        artifact
        for artifact in observed
        if artifact["path"] == "frontend/openapi/workbench.openapi.json"
    )
    source_paths = set(openapi_artifact["sourceOfTruth"])
    assert {
        "backend/src/ci_coordinator/api/http/analytics_configuration_contracts.py",
        "backend/src/ci_coordinator/api/http/routers/analytics_configuration.py",
        "backend/src/ci_coordinator/ci_economics/analytics_configuration.py",
    } <= source_paths
    generator = repo_root / openapi_artifact["generator"]
    syntax = ast.parse(generator.read_bytes(), filename=str(generator))
    imported_route_paths = {
        f"backend/src/{node.module.replace('.', '/')}.py"
        for node in ast.walk(syntax)
        if isinstance(node, ast.ImportFrom)
        and node.module is not None
        and node.module.startswith("ci_coordinator.api.http.routers.")
    }

    assert imported_route_paths <= source_paths
    assert "backend/src/ci_coordinator/api/http/app.py" in source_paths
    assert "backend/src/ci_coordinator/api/http/contracts.py" in source_paths
    assert "backend/src/ci_coordinator/api/http/model_contracts.py" in source_paths
    assert "backend/src/ci_coordinator/governance_observation/model.py" in source_paths
    assert "backend/src/ci_coordinator/kernel/git_reference.py" in source_paths
    assert {
        "backend/src/ci_coordinator/api/http/ci_history_contracts.py",
        "backend/src/ci_coordinator/ci_economics/history_commands.py",
        "backend/src/ci_coordinator/ci_economics/history_configuration.py",
        "backend/src/ci_coordinator/ci_economics/history_payload.py",
        "backend/src/ci_coordinator/ci_economics/observation_payload.py",
        "backend/src/ci_coordinator/ci_economics/payload_model.py",
    } <= source_paths


def test_repository_contract_is_current() -> None:
    check_contract(CONTRACT_PATH)

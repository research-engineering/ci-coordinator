from __future__ import annotations

import importlib
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest
from pydantic import BaseModel, ValidationError
from pydantic.alias_generators import to_camel

from ci_coordinator.api.http.config_lifecycle_contracts import ConfigControlErrorBody
from ci_coordinator.api.http.contracts import PlanRequestBody
from ci_coordinator.api.http.governance_baseline_contracts import (
    GovernanceBaselineRecordResponse,
)
from ci_coordinator.api.http.governance_state_contracts import (
    GovernanceRepositoryResponse,
    GovernanceScopeResponse,
)
from ci_coordinator.api.http.model_contracts import (
    ProjectedResponseModel,
    RequestModel,
    ResponseModel,
)
from ci_coordinator.api.http.routers.control_plane_identity import (
    ControlPlaneUserResponse,
    KeycloakCallbackQuery,
)
from ci_coordinator.api.http.routers.observability import ReadinessResponse
from ci_coordinator.api.http.routers.operator_controls import ForceFullCIOverrideRequest
from ci_coordinator.api.http.routers.provider_inventory import (
    RepositoryScopeResponse as ProviderRepositoryScopeResponse,
)
from ci_coordinator.api.http.routers.workbench import (
    AuditEventResponse,
    NativeProfileExecutionResponse,
)
from ci_coordinator.api.http.routers.workbench import (
    RepositoryScopeResponse as WorkbenchRepositoryScopeResponse,
)
from ci_coordinator.api.http.workflow_discovery_contracts import MatrixDimensionResponse

_BACKEND_ROOT = Path(__file__).resolve().parents[4]
_HTTP_ROOT = _BACKEND_ROOT / "src" / "ci_coordinator" / "api" / "http"
_MODEL_POLICY_PATH = _HTTP_ROOT / "model_contracts.py"
_RESPONSE_ALIAS_EXCEPTIONS: dict[tuple[str, str, str], str] = {}
_RESPONSE_FIELD_ALIASES = {
    (
        "ci_coordinator.api.http.ci_economics_measurement_contracts",
        "EconomicsReconciliationSourceResponse",
        "source_kind",
    ): "sourceKind",
    (
        "ci_coordinator.api.http.ci_economics_source_contracts",
        "EconomicsProviderSourceResponse",
        "source_kind",
    ): "sourceKind",
    (
        "ci_coordinator.api.http.routers.workbench",
        "NativeProfileExecutionResponse",
        "execution_kind",
    ): "executionKind",
    (
        "ci_coordinator.api.http.routers.workbench",
        "ShardedProfileCapacityResponse",
        "execution_kind",
    ): "executionKind",
}
_REQUEST_SERIALIZATION_MODELS = {
    ("ci_coordinator.api.http.contracts", "PlanRequestBody"),
}
_REQUEST_VALIDATION_NAME_EXCEPTIONS = {
    (
        "ci_coordinator.api.http.routers.control_plane_identity",
        "KeycloakCallbackQuery",
        "session_state",
    ): "session_state",
}
_PROJECTED_RESPONSE_MODELS = {
    ("ci_coordinator.api.http.ci_history_read_contracts", "HistoryRetentionResultResponse"),
    ("ci_coordinator.api.http.activity_contracts", "ActivityItemResponse"),
    ("ci_coordinator.api.http.ci_economics_contracts", "CiEconomicsDurationResponse"),
    ("ci_coordinator.api.http.ci_economics_contracts", "CiEconomicsJobResponse"),
    ("ci_coordinator.api.http.ci_economics_contracts", "CiEconomicsJobTimingResponse"),
    ("ci_coordinator.api.http.ci_economics_contracts", "CiEconomicsRunnerResponse"),
    ("ci_coordinator.api.http.ci_measurement_report_contracts", "MeasurementReportOriginResponse"),
    ("ci_coordinator.api.http.governance_state_contracts", "GovernanceRepositoryResponse"),
    ("ci_coordinator.api.http.governance_state_contracts", "GovernanceScopeResponse"),
    ("ci_coordinator.api.http.routers.provider_inventory", "InstallationFailureResponse"),
    ("ci_coordinator.api.http.routers.provider_inventory", "InstallationResponse"),
    ("ci_coordinator.api.http.routers.provider_inventory", "RepositoryResponse"),
    ("ci_coordinator.api.http.routers.provider_inventory", "RepositoryScopeResponse"),
    ("ci_coordinator.api.http.routers.workbench", "AuditEventResponse"),
    ("ci_coordinator.api.http.routers.workbench", "ConfigEpochResponse"),
    ("ci_coordinator.api.http.routers.workbench", "NativeProfileExecutionResponse"),
    ("ci_coordinator.api.http.routers.workbench", "OverrideResponse"),
    ("ci_coordinator.api.http.routers.workbench", "PlanResponse"),
    ("ci_coordinator.api.http.routers.workbench", "ReplayResponse"),
    ("ci_coordinator.api.http.routers.workbench", "RepositoryScopeResponse"),
    ("ci_coordinator.api.http.routers.workbench", "RunFindingResponse"),
    ("ci_coordinator.api.http.routers.workbench", "RunResponse"),
    ("ci_coordinator.api.http.routers.workbench", "ShardedProfileCapacityResponse"),
    ("ci_coordinator.api.http.routers.workbench", "TruncationResponse"),
    ("ci_coordinator.api.http.workflow_discovery_contracts", "AdoptionAssessmentResponse"),
    ("ci_coordinator.api.http.workflow_discovery_contracts", "CallEdgeResponse"),
    ("ci_coordinator.api.http.workflow_discovery_contracts", "ConcurrencyResponse"),
    ("ci_coordinator.api.http.workflow_discovery_contracts", "DiscoveryRepositoryResponse"),
    ("ci_coordinator.api.http.workflow_discovery_contracts", "DiscoveryScopeResponse"),
    ("ci_coordinator.api.http.workflow_discovery_contracts", "FactResponse"),
    ("ci_coordinator.api.http.workflow_discovery_contracts", "JobResponse"),
    ("ci_coordinator.api.http.workflow_discovery_contracts", "MatrixDimensionResponse"),
    ("ci_coordinator.api.http.workflow_discovery_contracts", "PermissionResponse"),
    ("ci_coordinator.api.http.workflow_discovery_contracts", "PermissionsResponse"),
    ("ci_coordinator.api.http.workflow_discovery_contracts", "ProposalDiagnosticResponse"),
    ("ci_coordinator.api.http.workflow_discovery_contracts", "ProvenanceResponse"),
    ("ci_coordinator.api.http.workflow_discovery_contracts", "SourceResponse"),
    ("ci_coordinator.api.http.workflow_discovery_contracts", "StepResponse"),
    ("ci_coordinator.api.http.workflow_discovery_contracts", "UnknownResponse"),
    ("ci_coordinator.api.http.workflow_discovery_contracts", "WorkflowResponse"),
    ("ci_coordinator.api.http.workflow_discovery_contracts", "YamlLocationResponse"),
}
_EXPECTED_HTTP_MODEL_COUNT = 166


def test_every_http_model_uses_exactly_one_owned_policy() -> None:
    models = _concrete_http_models()

    assert ResponseModel.model_config.get("from_attributes") is not True
    assert ProjectedResponseModel.model_config.get("from_attributes") is True
    assert len(models) == _EXPECTED_HTTP_MODEL_COUNT
    assert KeycloakCallbackQuery in models
    for model in models:
        request = issubclass(model, RequestModel)
        response = issubclass(model, ResponseModel)
        assert request != response, _model_id(model)
        projected = issubclass(model, ProjectedResponseModel)
        assert projected == (_model_id(model) in _PROJECTED_RESPONSE_MODELS), _model_id(model)
        _assert_config(model, request=request, projected=projected)
        _assert_alias_relations(model, request=request)


def test_request_admits_only_canonical_wire_names_and_exact_scalar_types() -> None:
    wire = _plan_request_wire()

    request = PlanRequestBody.model_validate(wire)
    assert request.to_wire_mapping() == wire

    for mutation in (
        {**wire, "request_id": wire["requestId"]},
        {**wire, "installationId": "1"},
        {**wire, "unexpected": True},
    ):
        with pytest.raises(ValidationError):
            PlanRequestBody.model_validate(mutation)

    name_only = {**wire, "request_id": wire["requestId"]}
    del name_only["requestId"]
    with pytest.raises(ValidationError):
        PlanRequestBody.model_validate(name_only)
    installation_field = "installation_id"
    with pytest.raises(ValidationError):
        setattr(request, installation_field, "invalid")


def test_response_accepts_internal_names_and_emits_only_wire_names() -> None:
    response = ProviderRepositoryScopeResponse(installation_id=1, repository_id=2)

    assert response.to_wire_mapping() == {"installationId": 1, "repositoryId": 2}
    with pytest.raises(ValidationError):
        ProviderRepositoryScopeResponse.model_validate({"installationId": 1, "repositoryId": 2})
    with pytest.raises(ValidationError):
        ProviderRepositoryScopeResponse(installation_id="1", repository_id=2)  # type: ignore[arg-type]
    installation_field = "installation_id"
    with pytest.raises(ValidationError):
        setattr(response, installation_field, cast(int, "invalid"))

    with pytest.raises(ValidationError):
        WorkbenchRepositoryScopeResponse.model_validate({"installationId": 1, "repositoryId": 2})

    capacity = NativeProfileExecutionResponse(
        profile_id="backend",
        execution_kind="native-job-set",
        job_id="backend-tests",
        witness_count=1,
    )
    assert capacity.to_wire_mapping()["executionKind"] == "native-job-set"
    with pytest.raises(ValidationError):
        NativeProfileExecutionResponse.model_validate(
            {
                "profile_id": "backend",
                "executionKind": "native-job-set",
                "job_id": "backend-tests",
                "witness_count": 1,
            }
        )


def test_response_revalidates_nested_models_and_bounded_trusted_attributes() -> None:
    @dataclass(frozen=True)
    class ScopeProjection:
        installation_id: int
        repository_id: int

    scope = GovernanceScopeResponse.model_validate(ScopeProjection(1, 2))
    assert scope == GovernanceScopeResponse(installation_id=1, repository_id=2)

    touched: list[str] = []

    class HostileScope:
        @property
        def installation_id(self) -> int:
            touched.append("installation_id")
            raise AssertionError("non-projected response read an attribute")

    with pytest.raises(ValidationError):
        ControlPlaneUserResponse.model_validate(HostileScope())
    assert touched == []

    invalid_scope = GovernanceScopeResponse.model_construct(
        installation_id=cast(int, "invalid"),
        repository_id=2,
    )
    with pytest.raises(ValidationError):
        GovernanceRepositoryResponse(
            scope=invalid_scope,
            owner_id=10,
            owner="example",
            name="repository",
            full_name="example/repository",
            default_branch="master",
        )


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_public_float_projection_rejects_nonfinite_values(value: float) -> None:
    with pytest.raises(ValidationError):
        MatrixDimensionResponse(name="python", values=(value,))


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nested_public_json_rejects_nonfinite_values(value: float) -> None:
    with pytest.raises(ValidationError):
        AuditEventResponse(
            sequence=1,
            audit_event_id="audit_" + "a" * 32,
            subject_type="repository",
            subject_id="1:2",
            event_type="test",
            created_at="2026-08-30T12:00:00Z",
            actor="test",
            payload={"nested": [value]},
            payload_hash="a" * 64,
            previous_event_hash=None,
            event_hash="b" * 64,
        )


def test_serialization_schema_marks_emitted_defaults_required() -> None:
    baseline_required = GovernanceBaselineRecordResponse.model_json_schema(mode="serialization")[
        "required"
    ]
    error_required = ConfigControlErrorBody.model_json_schema(mode="serialization")["required"]

    assert "authority" in baseline_required
    assert "diagnostics" in error_required


def test_request_schema_and_response_schema_use_directional_aliases() -> None:
    request_properties = PlanRequestBody.model_json_schema(mode="validation")["properties"]
    response_properties = ProviderRepositoryScopeResponse.model_json_schema(mode="serialization")[
        "properties"
    ]
    readiness_properties = ReadinessResponse.model_json_schema(mode="serialization")["properties"]

    assert "requestId" in request_properties and "request_id" not in request_properties
    assert "installationId" in response_properties and "installation_id" not in response_properties
    assert set(readiness_properties) == {"ok", "status"}


def test_datetime_conversion_is_bounded_to_the_declared_request_field() -> None:
    body = ForceFullCIOverrideRequest.model_validate(_override_wire("2026-08-30T12:00:00Z"))

    assert body.expires_at == datetime(2026, 8, 30, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    "value",
    [
        1_788_091_200,
        1_788_091_200.0,
        "1788091200",
        True,
        None,
        datetime(2026, 8, 30, 12, tzinfo=UTC),
        "2026-08-30x12:00:00+00:00",
        "2026-08-30 12:00:00+00:00",
        "2026-08-30T12:00:00.1234567Z",
        "2026-08-30T12:00:00+0000",
    ],
)
def test_datetime_conversion_rejects_non_iso8601_json_text(value: object) -> None:
    with pytest.raises(ValidationError):
        ForceFullCIOverrideRequest.model_validate(_override_wire(value))


def _concrete_http_models() -> tuple[type[BaseModel], ...]:
    models: set[type[BaseModel]] = set()
    for module in _model_modules():
        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseModel)
                and value.__module__ == module.__name__
            ):
                models.add(value)
    return tuple(sorted(models, key=_model_id))


def _model_modules() -> tuple[ModuleType, ...]:
    modules: list[ModuleType] = []
    for path in sorted(_HTTP_ROOT.rglob("*.py")):
        if path == _MODEL_POLICY_PATH:
            continue
        relative = path.relative_to(_BACKEND_ROOT / "src").with_suffix("")
        modules.append(importlib.import_module(".".join(relative.parts)))
    return tuple(modules)


def _assert_config(model: type[BaseModel], *, request: bool, projected: bool) -> None:
    config = model.model_config
    owner = RequestModel if request else ProjectedResponseModel if projected else ResponseModel
    assert config == owner.model_config, _model_id(model)


def _assert_alias_relations(model: type[BaseModel], *, request: bool) -> None:
    validation_names: list[str] = []
    serialization_names: list[str] = []
    for name, field in model.model_fields.items():
        field_alias = None if request else _RESPONSE_FIELD_ALIASES.get((*_model_id(model), name))
        assert field.alias == field_alias, (_model_id(model), name)
        expected = to_camel(name)
        if request:
            expected = _REQUEST_VALIDATION_NAME_EXCEPTIONS.get((*_model_id(model), name), expected)
            validation = field.validation_alias or name
            assert validation == expected, (_model_id(model), name)
            if expected == name:
                assert field.validation_alias is None, (_model_id(model), name)
            if _model_id(model) in _REQUEST_SERIALIZATION_MODELS:
                serialization = field.serialization_alias or name
                assert serialization == expected, (_model_id(model), name)
            else:
                assert field.serialization_alias is None, (_model_id(model), name)
                serialization = name
        else:
            assert field.validation_alias == field_alias, (_model_id(model), name)
            validation = name
            exception = _RESPONSE_ALIAS_EXCEPTIONS.get((*_model_id(model), name))
            serialization = field.serialization_alias or name
            assert serialization == (exception or expected), (_model_id(model), name)
        assert isinstance(validation, str)
        assert isinstance(serialization, str)
        validation_names.append(validation)
        serialization_names.append(serialization)
    assert len(validation_names) == len(set(validation_names)), _model_id(model)
    assert len(serialization_names) == len(set(serialization_names)), _model_id(model)


def _model_id(model: type[BaseModel]) -> tuple[str, str]:
    return model.__module__, model.__qualname__


def _plan_request_wire() -> dict[str, object]:
    return {
        "schemaVersion": "dynamic-ci-plan-request/v2",
        "requestId": "request-1",
        "installationId": 1,
        "repositoryId": 2,
        "owner": "example",
        "repository": "repository",
        "eventName": "pull_request",
        "ref": "refs/pull/7/merge",
        "baseSha": "a" * 40,
        "headSha": "b" * 40,
        "executionSha": "c" * 40,
        "workflowRunId": 3,
        "runAttempt": 1,
        "pullRequestNumber": 7,
        "mergeGroupHeadRef": None,
    }


def _override_wire(expires_at: object) -> dict[str, object]:
    return {
        "schemaVersion": "operator-override/v1",
        "installationId": 1,
        "repositoryId": 2,
        "operationId": "operation-1",
        "reason": "Investigate provider failure",
        "kind": "force_full_ci",
        "subjectId": "pull-request:7",
        "expiresAt": expires_at,
    }

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from scripts.python_import_boundary_engine import (
    ImportRule,
    PythonSourceInventory,
    inventory_python_sources,
    module_object_authority,
)
from scripts.python_import_boundary_engine import (
    imported_modules as scan_imported_modules,
)


def violating_rule_ids(path: str, imported: str) -> tuple[str, ...]:
    return tuple(
        rule.rule_id for rule in IMPORT_RULES if rule.applies(path) and rule.violates(imported)
    )


def _path_contains(fragment: str) -> Callable[[str], bool]:
    return lambda path: fragment in path


def _process_environment_rule_applies(path: str) -> bool:
    return "/ci_coordinator/" in path and not path.endswith(
        (
            "/ci_coordinator/runtime/environment.py",
            "/ci_coordinator/consumer_contract_lab/node_executable.py",
            "/ci_coordinator/target_artifacts/resources/ci_measurement_reporter.py",
        )
    )


def _os_capability_rule_applies(path: str) -> bool:
    return "/ci_coordinator/" in path and not path.endswith(
        (
            "/ci_coordinator/consumer_contract_lab/bootstrap.py",
            "/ci_coordinator/consumer_contract_lab/git_source.py",
            "/ci_coordinator/consumer_contract_lab/node_executable.py",
            "/ci_coordinator/consumer_contract_lab/process.py",
            "/ci_coordinator/api/http/operator_ui_bundle.py",
            "/ci_coordinator/production_admission/file.py",
            "/ci_coordinator/runtime/environment.py",
            "/ci_coordinator/target_artifacts/workflow.py",
            "/ci_coordinator/target_artifacts/resources/ci_measurement_reporter.py",
            "/ci_coordinator/target_authority_evidence/file.py",
        )
    )


def _runtime_composition_rule_applies(path: str) -> bool:
    return "/ci_coordinator/runtime/" in path and not path.endswith(
        "/ci_coordinator/runtime/environment.py"
    )


def _root_package_marker_rule_applies(path: str) -> bool:
    return path.endswith("/ci_coordinator/__init__.py")


def _target_authority_evidence_replay_rule_applies(path: str) -> bool:
    return "/ci_coordinator/target_authority_evidence/" in path and not path.endswith(
        (
            "/ci_coordinator/target_authority_evidence/__init__.py",
            "/ci_coordinator/target_authority_evidence/cli.py",
            "/ci_coordinator/target_authority_evidence/file.py",
        )
    )


def _target_authority_evidence_facade_rule_applies(path: str) -> bool:
    return path.endswith("/ci_coordinator/target_authority_evidence/__init__.py")


def _target_authority_evidence_publication_rule_applies(path: str) -> bool:
    return path.endswith(
        (
            "/ci_coordinator/target_authority_evidence/cli.py",
            "/ci_coordinator/target_authority_evidence/file.py",
        )
    )


def _github_actions_jwks_rule_applies(path: str) -> bool:
    return path.endswith(
        (
            "/ci_coordinator/integrations/__init__.py",
            "/ci_coordinator/integrations/oidc_jwks.py",
        )
    )


def _runtime_environment_rule_applies(path: str) -> bool:
    return path.endswith("/ci_coordinator/runtime/environment.py")


def _api_http_non_review_surface_rule_applies(path: str) -> bool:
    return "/ci_coordinator/api/http/" in path and not path.endswith(
        (
            "/ci_coordinator/api/http/dependencies.py",
            "/ci_coordinator/api/http/routers/repository_attestations.py",
        )
    )


def _api_http_non_governance_baseline_surface_rule_applies(path: str) -> bool:
    return "/ci_coordinator/api/http/" in path and not path.endswith(
        (
            "/ci_coordinator/api/http/dependencies.py",
            "/ci_coordinator/api/http/governance_baseline_contracts.py",
            "/ci_coordinator/api/http/routers/governance_baselines.py",
        )
    )


_DYNAMIC_LOADING_AUTHORITIES = (
    "builtins",
    "importlib",
    "pkgutil",
    "pydoc",
    "reserved:dynamic-execution",
    "reserved:dynamic-import",
    "runpy",
    "sys.modules",
)

_PROCESS_ENVIRONMENT_AUTHORITIES = (
    "os.environ",
    "os.getenv",
    "os.putenv",
    "os.unsetenv",
)


_STANDARD_LAYER_FORBIDDEN = (
    *_DYNAMIC_LOADING_AUTHORITIES,
    "dotenv",
    "fastapi",
    "httpx",
    "requests",
    "sqlalchemy",
)


@dataclass(frozen=True, slots=True)
class HttpConcreteServicePolicy:
    implementation_module: str
    export_name: str
    facade_modules: tuple[str, ...] = ()
    implementation_module_is_private: bool = False


HTTP_CONCRETE_SERVICE_POLICY = (
    HttpConcreteServicePolicy(
        "ci_coordinator.app.analytics_configuration",
        "PurposeSettingsService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.capacity_planning",
        "CapacityPlanningService",
        ("ci_coordinator.app",),
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.ci_economics",
        "CiEconomicsCollectionService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.ci_economics",
        "CiEconomicsReadService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.ci_economics",
        "CiEconomicsRetentionService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.ci_economics_sources",
        "CiEconomicsSourceService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.ci_economics_budgets",
        "CiEconomicsBudgetService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.ci_history_collection",
        "CiHistoryCollectionService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.ci_history_delivery",
        "CiHistoryDeliveryService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.ci_history_administration",
        "CiHistoryAdministrationService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.ci_history_analytics",
        "CiHistoryAnalyticsService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.ci_history_read",
        "CiHistoryReadService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.ci_observation",
        "CiObservationService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.ci_observation_scanning",
        "CiObservationScanningService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.ci_measurement_report_ingestion",
        "MeasurementReportIngestionService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.ci_measurement_reports",
        "MeasurementReportReadService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.config_admission",
        "ConfigAdmissionService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.config_management",
        "ConfigManagementService",
        ("ci_coordinator.app",),
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.config_queries",
        "ConfigQueryService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.dynamic_plan_service",
        "DynamicPlanService",
        ("ci_coordinator.app",),
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.reconciliation_round",
        "ReconciliationRoundService",
        ("ci_coordinator.app",),
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.governance_baseline",
        "GovernanceBaselineService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.governance_comparison",
        "GovernanceComparisonService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.planning_preparation",
        "PlanningPreparationService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.proposal_review",
        "ProposalReviewService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.production_cutover",
        "ProductionCutoverService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.production_request_evidence",
        "ProductionRequestAuthorityService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.repository_activation",
        "RepositoryActivationAuthorityService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.app.repository_attestation",
        "RepositoryAttestationService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.control_plane_identity.activity_query",
        "ActivityReadService",
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.control_plane_identity.browser",
        "BrowserIdentityService",
        ("ci_coordinator.control_plane_identity",),
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.control_plane_identity.machine",
        "MachineIdentityService",
        ("ci_coordinator.control_plane_identity",),
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.governance_observation.service",
        "GovernanceObservationService",
        ("ci_coordinator.governance_observation",),
        True,
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.operator_controls.service",
        "OperatorOverrideService",
        ("ci_coordinator.operator_controls",),
        True,
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.provider_inventory.service",
        "ProviderInventoryService",
        ("ci_coordinator.provider_inventory",),
        True,
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.runtime.reconciliation_service",
        "PeriodicReconciliationService",
        implementation_module_is_private=True,
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.workbench_read_models.service",
        "RepositoryWorkbenchService",
        ("ci_coordinator.workbench_read_models",),
        True,
    ),
    HttpConcreteServicePolicy(
        "ci_coordinator.workflow_discovery.service",
        "WorkflowDiscoveryService",
        ("ci_coordinator.workflow_discovery",),
        True,
    ),
)
_HTTP_SERVICE_EXPORT_AUTHORITIES = tuple(
    dict.fromkeys(
        f"{module}.{service.export_name}"
        for service in HTTP_CONCRETE_SERVICE_POLICY
        for module in (service.implementation_module, *service.facade_modules)
    )
)
_HTTP_IMPLEMENTATION_MODULE_AUTHORITIES = tuple(
    dict.fromkeys(
        service.implementation_module
        for service in HTTP_CONCRETE_SERVICE_POLICY
        if service.implementation_module_is_private
    )
)
_HTTP_IMPLEMENTATION_AUTHORITIES = (
    *_HTTP_SERVICE_EXPORT_AUTHORITIES,
    *_HTTP_IMPLEMENTATION_MODULE_AUTHORITIES,
)
_HTTP_RESTRICTED_MODULE_OBJECTS = tuple(
    dict.fromkeys(
        module
        for service in HTTP_CONCRETE_SERVICE_POLICY
        for module in (service.implementation_module, *service.facade_modules)
    )
)
_HTTP_RESTRICTED_MODULE_AUTHORITIES = tuple(
    module_object_authority(module) for module in _HTTP_RESTRICTED_MODULE_OBJECTS
)


def imported_modules(path: Path, source_root: Path) -> tuple[str, ...]:
    return scan_imported_modules(
        path,
        source_root,
        ambient_authority_prefixes=(
            *_PROCESS_ENVIRONMENT_AUTHORITIES,
            *_HTTP_IMPLEMENTATION_AUTHORITIES,
            "open",
            "sys.modules",
        ),
        restricted_module_objects=_HTTP_RESTRICTED_MODULE_OBJECTS,
    )


_GLOBAL_CAPABILITY_RULE_IDS = frozenset(
    {
        "python.import-boundary.os-capability",
        "python.import-boundary.process-environment",
    }
)


def _standard_layer_rule(
    path: str,
    *,
    allowed_first_party_prefixes: tuple[str, ...],
    rule_id: str,
    allowed_import_prefixes: tuple[str, ...] | None = None,
) -> ImportRule:
    return ImportRule(
        applies=_path_contains(path),
        forbidden=_STANDARD_LAYER_FORBIDDEN,
        allowed_first_party_prefixes=allowed_first_party_prefixes,
        allowed_import_prefixes=allowed_import_prefixes,
        rule_id=rule_id,
    )


IMPORT_RULES = (
    ImportRule(
        applies=lambda path: path.endswith(
            "/ci_coordinator/target_artifacts/resources/ci_measurement_reporter.py"
        ),
        forbidden=(),
        allowed_first_party_prefixes=(),
        allowed_external_prefixes=(
            "__future__.annotations",
            "argparse",
            "base64",
            "collections.abc.Sequence",
            "contextlib.suppress",
            "datetime",
            "hashlib",
            "json",
            "math",
            "os",
            "pathlib.Path",
            "re",
            "resource",
            "signal",
            "stat",
            "subprocess",
            "sys",
            "time",
            "types.FrameType",
            "typing.cast",
            "urllib.error.HTTPError",
            "urllib.parse",
            "urllib.request",
        ),
        rule_id="python.import-boundary.measurement-reporter",
    ),
    ImportRule(
        applies=_process_environment_rule_applies,
        forbidden=_PROCESS_ENVIRONMENT_AUTHORITIES,
        rule_id="python.import-boundary.process-environment",
    ),
    ImportRule(
        applies=lambda path: path.endswith(
            "/ci_coordinator/consumer_contract_lab/node_executable.py"
        ),
        forbidden=("os.getenv", "os.putenv", "os.unsetenv"),
        rule_id="python.import-boundary.consumer-tool-environment",
    ),
    ImportRule(
        applies=_os_capability_rule_applies,
        forbidden=("os",),
        rule_id="python.import-boundary.os-capability",
    ),
    ImportRule(
        applies=_root_package_marker_rule_applies,
        allowed_first_party_prefixes=(),
        forbidden=_STANDARD_LAYER_FORBIDDEN,
        rule_id="python.import-boundary.root-package-marker",
    ),
    ImportRule(
        applies=_github_actions_jwks_rule_applies,
        allowed_first_party_prefixes=(
            "ci_coordinator.identity_admission",
            "ci_coordinator.integrations",
            "ci_coordinator.kernel",
        ),
        forbidden=(
            *_DYNAMIC_LOADING_AUTHORITIES,
            "dotenv",
            "fastapi",
            "os",
            "sqlalchemy",
        ),
        rule_id="python.import-boundary.github-actions-jwks-integration",
    ),
    ImportRule(
        applies=_runtime_environment_rule_applies,
        allowed_first_party_prefixes=("ci_coordinator.runtime_settings",),
        forbidden=(
            *_DYNAMIC_LOADING_AUTHORITIES,
            "dotenv",
            "fastapi",
            "httpx",
            "sqlalchemy",
        ),
        rule_id="python.import-boundary.runtime-environment",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/kernel/"),
        allowed_first_party_prefixes=("ci_coordinator.kernel",),
        forbidden=(
            *_DYNAMIC_LOADING_AUTHORITIES,
            "dotenv",
            "fastapi",
            "httpx",
            "os",
            "os.environ",
            "requests",
            "sqlalchemy",
        ),
        rule_id="python.import-boundary.kernel",
    ),
    _standard_layer_rule(
        "/ci_coordinator/validation_contract/",
        allowed_first_party_prefixes=(
            "ci_coordinator.kernel",
            "ci_coordinator.validation_contract",
        ),
        rule_id="python.import-boundary.validation-contract",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/identity_admission/"),
        allowed_first_party_prefixes=(
            "ci_coordinator.identity_admission",
            "ci_coordinator.kernel",
        ),
        forbidden=(
            *_DYNAMIC_LOADING_AUTHORITIES,
            "dotenv",
            "ci_coordinator.execution_orchestration",
            "ci_coordinator.plan_issuance",
            "ci_coordinator.planning_core",
            "ci_coordinator.runner_capacity",
            "ci_coordinator.verification_core",
            "fastapi",
            "os",
            "sqlalchemy",
        ),
        rule_id="python.import-boundary.identity-admission",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/audit_replay/"),
        allowed_first_party_prefixes=(
            "ci_coordinator.audit_replay",
            "ci_coordinator.kernel",
        ),
        forbidden=(
            *_DYNAMIC_LOADING_AUTHORITIES,
            "dotenv",
            "ci_coordinator.api",
            "ci_coordinator.app",
            "ci_coordinator.config_control",
            "ci_coordinator.execution_orchestration",
            "ci_coordinator.github_integration",
            "ci_coordinator.identity_admission",
            "ci_coordinator.persistence",
            "ci_coordinator.plan_issuance",
            "ci_coordinator.planning_core",
            "ci_coordinator.repo_context",
            "ci_coordinator.runner_capacity",
            "ci_coordinator.verification_core",
            "fastapi",
            "httpx",
            "os",
            "requests",
            "sqlalchemy",
        ),
        rule_id="python.import-boundary.audit-replay",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/capacity_qualification/"),
        allowed_first_party_prefixes=(
            "ci_coordinator.audit_replay",
            "ci_coordinator.capacity_qualification",
            "ci_coordinator.kernel",
        ),
        allowed_import_prefixes=("importlib.resources",),
        forbidden=(
            *_STANDARD_LAYER_FORBIDDEN,
            "ci_coordinator.api",
            "ci_coordinator.app",
            "ci_coordinator.config_control",
            "ci_coordinator.execution_orchestration",
            "ci_coordinator.github_ingestion",
            "ci_coordinator.identity_admission",
            "ci_coordinator.integrations",
            "ci_coordinator.persistence",
            "ci_coordinator.plan_issuance",
            "ci_coordinator.planning_core",
            "ci_coordinator.production_admission",
            "ci_coordinator.reconciliation",
            "ci_coordinator.runtime",
            "ci_coordinator.runtime_settings",
            "ci_coordinator.verification_core",
            "fastapi",
            "httpx",
            "os",
            "requests",
            "sqlalchemy",
        ),
        rule_id="python.import-boundary.capacity-qualification",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/config_control/"),
        allowed_first_party_prefixes=(
            "ci_coordinator.config_control",
            "ci_coordinator.kernel",
            "ci_coordinator.validation_contract",
        ),
        allowed_import_prefixes=("importlib.resources",),
        forbidden=(
            *_DYNAMIC_LOADING_AUTHORITIES,
            "dotenv",
            "ci_coordinator.api",
            "ci_coordinator.app",
            "ci_coordinator.audit_replay",
            "ci_coordinator.github_integration",
            "ci_coordinator.identity_admission",
            "ci_coordinator.persistence",
            "fastapi",
            "httpx",
            "os",
            "requests",
            "sqlalchemy",
        ),
        rule_id="python.import-boundary.config-control",
    ),
    _standard_layer_rule(
        "/ci_coordinator/config_epochs/",
        allowed_first_party_prefixes=(
            "ci_coordinator.audit_replay",
            "ci_coordinator.config_control",
            "ci_coordinator.config_epochs",
            "ci_coordinator.kernel",
        ),
        rule_id="python.import-boundary.config-epochs",
    ),
    _standard_layer_rule(
        "/ci_coordinator/control_plane_identity/",
        allowed_first_party_prefixes=(
            "ci_coordinator.config_control",
            "ci_coordinator.control_plane_identity",
            "ci_coordinator.kernel",
        ),
        rule_id="python.import-boundary.control-plane-identity",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/repo_context/"),
        allowed_first_party_prefixes=(
            "ci_coordinator.config_control.contracts.RepositoryScope",
            "ci_coordinator.kernel",
            "ci_coordinator.repo_context",
        ),
        forbidden=(
            *_DYNAMIC_LOADING_AUTHORITIES,
            "dotenv",
            "ci_coordinator.api",
            "ci_coordinator.app",
            "ci_coordinator.audit_replay",
            "ci_coordinator.github_ingestion",
            "ci_coordinator.identity_admission",
            "ci_coordinator.integrations",
            "ci_coordinator.persistence",
            "ci_coordinator.planning_core",
            "fastapi",
            "httpx",
            "os",
            "requests",
            "sqlalchemy",
        ),
        rule_id="python.import-boundary.repo-context",
    ),
    _standard_layer_rule(
        "/ci_coordinator/planning_core/",
        allowed_first_party_prefixes=(
            "ci_coordinator.config_control",
            "ci_coordinator.config_epochs",
            "ci_coordinator.kernel",
            "ci_coordinator.planning_core",
            "ci_coordinator.repo_context",
            "ci_coordinator.validation_contract",
        ),
        rule_id="python.import-boundary.planning-core",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/target_authority_relation/"),
        allowed_first_party_prefixes=(
            "ci_coordinator.config_control.contracts.RepositoryScope",
            "ci_coordinator.kernel.canonical_json.CanonicalJsonError",
            "ci_coordinator.kernel.canonical_json.JsonResourceLimits",
            "ci_coordinator.kernel.canonical_json.bounded_canonical_json",
            "ci_coordinator.kernel.hashing.sha256_hex",
            "ci_coordinator.kernel.ordering.utf16_sort_key",
            "ci_coordinator.kernel.strict_json.StrictJsonError",
            "ci_coordinator.kernel.strict_json.load_strict_json",
            "ci_coordinator.target_authority_relation",
        ),
        forbidden=(
            *_STANDARD_LAYER_FORBIDDEN,
            "datetime",
            "os",
            "pathlib",
            "random",
            "secrets",
            "socket",
            "subprocess",
            "time",
            "uuid",
        ),
        rule_id="python.import-boundary.target-authority-relation",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/workflow_authority/"),
        allowed_first_party_prefixes=(
            "ci_coordinator.config_control.contracts.RepositoryScope",
            "ci_coordinator.kernel.canonical_json.bounded_canonical_json",
            "ci_coordinator.kernel.git_reference.git_branch_name_is_admitted",
            "ci_coordinator.kernel.hashing.sha256_hex",
            "ci_coordinator.kernel.ordering.utf16_sort_key",
            "ci_coordinator.kernel.canonical_json.JsonResourceLimits",
            "ci_coordinator.kernel.strict_json.StrictJsonError",
            "ci_coordinator.kernel.strict_json.load_strict_json",
            "ci_coordinator.workflow_authority",
        ),
        forbidden=(
            *_STANDARD_LAYER_FORBIDDEN,
            "datetime",
            "os",
            "pathlib",
            "random",
            "secrets",
            "socket",
            "subprocess",
            "time",
            "uuid",
        ),
        allowed_external_prefixes=(
            "__future__.annotations",
            "collections.abc.Sequence",
            "dataclasses.dataclass",
            "hashlib.sha1",
            "re",
            "typing.Final",
            "typing.Literal",
            "typing.Protocol",
            "typing.Self",
            "typing.cast",
            "urllib.parse.quote",
        ),
        rule_id="python.import-boundary.workflow-authority",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/target_authority_producers/"),
        allowed_first_party_prefixes=(
            "ci_coordinator.config_control.contracts",
            "ci_coordinator.config_control.planning_projection",
            "ci_coordinator.consumer_contract_lab.model",
            "ci_coordinator.execution_orchestration.target_registry",
            "ci_coordinator.governance_observation.model",
            "ci_coordinator.kernel.canonical_json",
            "ci_coordinator.kernel.hashing",
            "ci_coordinator.kernel.ordering",
            "ci_coordinator.kernel.strict_json",
            "ci_coordinator.repo_context.workflow_inventory",
            "ci_coordinator.target_authority_producers",
            "ci_coordinator.target_authority_relation",
            "ci_coordinator.validation_contract.catalog",
            "ci_coordinator.validation_contract.model",
            "ci_coordinator.workflow_authority.evidence",
            "ci_coordinator.workflow_authority.model",
            "ci_coordinator.workflow_discovery.report",
            "ci_coordinator.workflow_discovery.summary",
        ),
        forbidden=(
            *_STANDARD_LAYER_FORBIDDEN,
            "datetime",
            "os",
            "pathlib",
            "random",
            "secrets",
            "socket",
            "subprocess",
            "time",
            "uuid",
        ),
        allowed_external_prefixes=(
            "__future__.annotations",
            "collections.defaultdict",
            "collections.abc.Mapping",
            "dataclasses.dataclass",
            "typing.Final",
            "typing.Literal",
            "typing.Protocol",
            "typing.cast",
        ),
        rule_id="python.import-boundary.target-authority-producers",
    ),
    ImportRule(
        applies=_target_authority_evidence_facade_rule_applies,
        allowed_first_party_prefixes=(
            "ci_coordinator.target_authority_evidence.codec",
            "ci_coordinator.target_authority_evidence.file",
            "ci_coordinator.target_authority_evidence.model",
            "ci_coordinator.target_authority_evidence.replay",
        ),
        forbidden=(*_STANDARD_LAYER_FORBIDDEN,),
        allowed_external_prefixes=(),
        rule_id="python.import-boundary.target-authority-evidence-facade",
    ),
    ImportRule(
        applies=_target_authority_evidence_replay_rule_applies,
        allowed_first_party_prefixes=(
            "ci_coordinator.kernel.canonical_json",
            "ci_coordinator.kernel.hashing",
            "ci_coordinator.kernel.strict_json",
            "ci_coordinator.target_authority_evidence.codec",
            "ci_coordinator.target_authority_evidence.model",
            "ci_coordinator.target_authority_evidence.replay",
            "ci_coordinator.target_authority_producers.codec",
            "ci_coordinator.target_authority_producers.model",
            "ci_coordinator.target_authority_producers.production",
            "ci_coordinator.target_authority_relation",
            "ci_coordinator.workflow_authority",
        ),
        forbidden=(
            *_STANDARD_LAYER_FORBIDDEN,
            "datetime",
            "os",
            "pathlib",
            "random",
            "secrets",
            "socket",
            "subprocess",
            "time",
            "uuid",
        ),
        allowed_external_prefixes=(
            "__future__.annotations",
            "collections.abc.Callable",
            "dataclasses.dataclass",
            "typing.Final",
            "typing.Literal",
            "typing.cast",
        ),
        rule_id="python.import-boundary.target-authority-evidence-replay",
    ),
    ImportRule(
        applies=_target_authority_evidence_publication_rule_applies,
        allowed_first_party_prefixes=(
            "ci_coordinator.kernel.canonical_json",
            "ci_coordinator.target_authority_evidence.codec",
            "ci_coordinator.target_authority_evidence.file",
            "ci_coordinator.target_authority_evidence.model",
        ),
        forbidden=(
            *_STANDARD_LAYER_FORBIDDEN,
            "datetime",
            "random",
            "socket",
            "subprocess",
            "time",
            "uuid",
        ),
        allowed_external_prefixes=(
            "__future__.annotations",
            "argparse",
            "collections.abc.Sequence",
            "dataclasses.dataclass",
            "os",
            "pathlib.Path",
            "secrets",
            "stat",
            "sys",
            "typing.Final",
            "typing.NoReturn",
            "typing.cast",
        ),
        rule_id="python.import-boundary.target-authority-evidence-publication",
    ),
    _standard_layer_rule(
        "/ci_coordinator/operator_controls/",
        allowed_first_party_prefixes=(
            "ci_coordinator.config_control",
            "ci_coordinator.kernel",
            "ci_coordinator.operator_controls",
            "ci_coordinator.planning_core",
            "ci_coordinator.validation_contract",
        ),
        rule_id="python.import-boundary.operator-controls",
    ),
    _standard_layer_rule(
        "/ci_coordinator/observability/",
        allowed_first_party_prefixes=("ci_coordinator.observability",),
        rule_id="python.import-boundary.observability",
    ),
    _standard_layer_rule(
        "/ci_coordinator/ci_economics/",
        allowed_first_party_prefixes=(
            "ci_coordinator.ci_economics",
            "ci_coordinator.config_control",
            "ci_coordinator.kernel",
            "ci_coordinator.reconciliation",
        ),
        allowed_import_prefixes=("importlib.resources",),
        rule_id="python.import-boundary.ci-economics",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/github_ingestion/"),
        allowed_first_party_prefixes=(
            "ci_coordinator.github_ingestion",
            "ci_coordinator.identity_admission",
            "ci_coordinator.kernel",
        ),
        allowed_import_prefixes=("importlib.resources",),
        forbidden=(
            *_DYNAMIC_LOADING_AUTHORITIES,
            "dotenv",
            "ci_coordinator.api",
            "ci_coordinator.app",
            "ci_coordinator.audit_replay",
            "ci_coordinator.config_control",
            "ci_coordinator.integrations",
            "ci_coordinator.persistence",
            "ci_coordinator.planning_core",
            "ci_coordinator.verification_core",
            "fastapi",
            "httpx",
            "os",
            "requests",
            "sqlalchemy",
        ),
        rule_id="python.import-boundary.github-ingestion",
    ),
    _standard_layer_rule(
        "/ci_coordinator/workflow_discovery/",
        allowed_first_party_prefixes=(
            "ci_coordinator.config_control",
            "ci_coordinator.kernel",
            "ci_coordinator.workflow_discovery",
        ),
        rule_id="python.import-boundary.workflow-discovery",
    ),
    _standard_layer_rule(
        "/ci_coordinator/governance_observation/",
        allowed_first_party_prefixes=(
            "ci_coordinator.config_control",
            "ci_coordinator.governance_observation",
            "ci_coordinator.kernel",
        ),
        rule_id="python.import-boundary.governance-observation",
    ),
    _standard_layer_rule(
        "/ci_coordinator/governance_baseline/",
        allowed_first_party_prefixes=(
            "ci_coordinator.audit_replay",
            "ci_coordinator.config_control",
            "ci_coordinator.governance_baseline",
            "ci_coordinator.governance_observation",
            "ci_coordinator.kernel",
        ),
        rule_id="python.import-boundary.governance-baseline",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/governance_comparison/"),
        allowed_first_party_prefixes=(
            "ci_coordinator.governance_baseline",
            "ci_coordinator.governance_comparison",
            "ci_coordinator.governance_observation",
            "ci_coordinator.kernel",
        ),
        forbidden=(*_STANDARD_LAYER_FORBIDDEN, "sys"),
        rule_id="python.import-boundary.governance-comparison",
    ),
    _standard_layer_rule(
        "/ci_coordinator/proposal_review/",
        allowed_first_party_prefixes=(
            "ci_coordinator.audit_replay",
            "ci_coordinator.config_control",
            "ci_coordinator.config_epochs",
            "ci_coordinator.control_plane_identity",
            "ci_coordinator.kernel",
            "ci_coordinator.proposal_review",
        ),
        rule_id="python.import-boundary.proposal-review",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/integrations/github/"),
        allowed_first_party_prefixes=(
            "ci_coordinator.ci_economics",
            "ci_coordinator.config_control",
            "ci_coordinator.control_plane_identity",
            "ci_coordinator.execution_orchestration",
            "ci_coordinator.governance_observation",
            "ci_coordinator.integrations",
            "ci_coordinator.kernel",
            "ci_coordinator.operator_controls.auth.RepositoryAccessUnavailable",
            "ci_coordinator.plan_issuance",
            "ci_coordinator.proposal_review",
            "ci_coordinator.production_admission.evidence_lookup.ProductionEvidenceLookup",
            "ci_coordinator.production_admission.ports.CurrentProductionSources",
            "ci_coordinator.provider_inventory",
            "ci_coordinator.reconciliation",
            "ci_coordinator.repo_context",
            "ci_coordinator.runner_capacity",
            "ci_coordinator.target_artifacts.requester",
            "ci_coordinator.target_authority_producers.sources.ProviderAuthoritySources",
            "ci_coordinator.workflow_authority",
            "ci_coordinator.workflow_discovery",
        ),
        forbidden=(
            *_DYNAMIC_LOADING_AUTHORITIES,
            "ci_coordinator.api",
            "ci_coordinator.app",
            "ci_coordinator.audit_replay",
            "ci_coordinator.github_ingestion",
            "ci_coordinator.identity_admission",
            "ci_coordinator.persistence",
            "ci_coordinator.planning_core",
            "ci_coordinator.verification_core",
            "sqlalchemy",
        ),
        rule_id="python.import-boundary.github-integration",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/integrations/keycloak/"),
        allowed_first_party_prefixes=(
            "ci_coordinator.control_plane_identity",
            "ci_coordinator.integrations.keycloak",
            "ci_coordinator.kernel",
        ),
        forbidden=(
            *_DYNAMIC_LOADING_AUTHORITIES,
            "ci_coordinator.api",
            "ci_coordinator.app",
            "ci_coordinator.persistence",
            "ci_coordinator.runtime",
            "ci_coordinator.runtime_settings",
            "sqlalchemy",
        ),
        rule_id="python.import-boundary.keycloak-integration",
    ),
    _standard_layer_rule(
        "/ci_coordinator/provider_inventory/",
        allowed_first_party_prefixes=(
            "ci_coordinator.config_control",
            "ci_coordinator.control_plane_identity",
            "ci_coordinator.kernel",
            "ci_coordinator.provider_inventory",
        ),
        rule_id="python.import-boundary.provider-inventory",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/api/"),
        forbidden=(
            *_DYNAMIC_LOADING_AUTHORITIES,
            *_HTTP_RESTRICTED_MODULE_AUTHORITIES,
            *_HTTP_IMPLEMENTATION_AUTHORITIES,
            "ci_coordinator.integrations",
            "ci_coordinator.persistence",
            "ci_coordinator.planning_core",
            "ci_coordinator.verification_core",
            "sqlalchemy",
        ),
        rule_id="python.import-boundary.api-http",
    ),
    ImportRule(
        applies=_api_http_non_review_surface_rule_applies,
        forbidden=(
            "ci_coordinator.app.proposal_review",
            "ci_coordinator.proposal_review",
        ),
        rule_id="python.import-boundary.api-proposal-review",
    ),
    ImportRule(
        applies=_api_http_non_governance_baseline_surface_rule_applies,
        forbidden=(
            "ci_coordinator.app.governance_baseline",
            "ci_coordinator.governance_baseline",
        ),
        rule_id="python.import-boundary.api-governance-baseline",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/app/"),
        allowed_private_first_party_prefixes=("ci_coordinator.app",),
        allowed_first_party_prefixes=(
            "ci_coordinator.github_ingestion.seeds",
            "ci_coordinator.app",
            "ci_coordinator.audit_replay",
            "ci_coordinator.ci_economics",
            "ci_coordinator.config_control",
            "ci_coordinator.config_epochs",
            "ci_coordinator.control_plane_identity",
            "ci_coordinator.execution_orchestration",
            "ci_coordinator.governance_baseline",
            "ci_coordinator.governance_comparison",
            "ci_coordinator.governance_observation",
            "ci_coordinator.identity_admission",
            "ci_coordinator.kernel",
            "ci_coordinator.observability",
            "ci_coordinator.operator_controls",
            "ci_coordinator.plan_issuance",
            "ci_coordinator.planning_core",
            "ci_coordinator.production_admission",
            "ci_coordinator.proposal_review",
            "ci_coordinator.reconciliation",
            "ci_coordinator.repo_context",
            "ci_coordinator.runner_capacity",
            "ci_coordinator.shadow_mode",
            "ci_coordinator.verification_core",
            "ci_coordinator.workbench_read_models",
            "ci_coordinator.workflow_discovery",
        ),
        forbidden=(
            *_DYNAMIC_LOADING_AUTHORITIES,
            "ci_coordinator.api.http",
            "ci_coordinator.integrations",
            "ci_coordinator.persistence",
            "ci_coordinator.planning_core.internal",
            "ci_coordinator.runtime",
            "ci_coordinator.runtime_settings",
            "fastapi",
            "sqlalchemy",
        ),
        rule_id="python.import-boundary.app-use-cases",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/persistence/"),
        allowed_first_party_prefixes=(
            "ci_coordinator.app",
            "ci_coordinator.audit_replay",
            "ci_coordinator.ci_economics",
            "ci_coordinator.config_control",
            "ci_coordinator.config_epochs",
            "ci_coordinator.control_plane_identity",
            "ci_coordinator.execution_orchestration",
            "ci_coordinator.github_ingestion",
            "ci_coordinator.governance_baseline",
            "ci_coordinator.governance_observation",
            "ci_coordinator.kernel",
            "ci_coordinator.operator_controls",
            "ci_coordinator.persistence",
            "ci_coordinator.plan_issuance",
            "ci_coordinator.production_admission",
            "ci_coordinator.proposal_review",
            "ci_coordinator.reconciliation",
            "ci_coordinator.shadow_mode",
            "ci_coordinator.validation_contract",
            "ci_coordinator.workbench_read_models",
        ),
        allowed_import_prefixes=("importlib.resources",),
        forbidden=(
            *_DYNAMIC_LOADING_AUTHORITIES,
            "dotenv",
            "fastapi",
            "httpx",
            "os",
            "requests",
        ),
        rule_id="python.import-boundary.persistence",
    ),
    _standard_layer_rule(
        "/ci_coordinator/reconciliation/",
        allowed_first_party_prefixes=(
            "ci_coordinator.execution_orchestration",
            "ci_coordinator.kernel",
            "ci_coordinator.reconciliation",
        ),
        rule_id="python.import-boundary.reconciliation",
    ),
    _standard_layer_rule(
        "/ci_coordinator/shadow_mode/",
        allowed_first_party_prefixes=(
            "ci_coordinator.kernel",
            "ci_coordinator.shadow_mode",
        ),
        rule_id="python.import-boundary.shadow-mode",
    ),
    _standard_layer_rule(
        "/ci_coordinator/agent_risk_advice/",
        allowed_first_party_prefixes=(
            "ci_coordinator.agent_risk_advice",
            "ci_coordinator.kernel",
            "ci_coordinator.planning_core",
            "ci_coordinator.repo_context",
            "ci_coordinator.validation_contract",
        ),
        rule_id="python.import-boundary.agent-risk-advice",
    ),
    _standard_layer_rule(
        "/ci_coordinator/verification_core/",
        allowed_first_party_prefixes=(
            "ci_coordinator.agent_risk_advice",
            "ci_coordinator.kernel",
            "ci_coordinator.planning_core",
            "ci_coordinator.repo_context",
            "ci_coordinator.validation_contract",
            "ci_coordinator.verification_core",
        ),
        rule_id="python.import-boundary.verification-core",
    ),
    _standard_layer_rule(
        "/ci_coordinator/production_admission/",
        allowed_first_party_prefixes=(
            "ci_coordinator.config_control",
            "ci_coordinator.identity_admission",
            "ci_coordinator.kernel",
            "ci_coordinator.production_admission",
            "ci_coordinator.runner_capacity",
            "ci_coordinator.reconciliation.contract.ReconciliationContract",
            "ci_coordinator.reconciliation.subject.ReconciliationSubject",
            "ci_coordinator.repo_context.workflow_inventory.is_workflow_path",
            "ci_coordinator.target_authority_evidence.codec.decode_target_authority_evidence",
            "ci_coordinator.target_authority_evidence.model.AdmittedTargetAuthorityEvidence",
            "ci_coordinator.target_authority_evidence.model.UnactivatedEvidenceBundle",
            "ci_coordinator.target_authority_evidence.replay.replay_target_authority_evidence",
            "ci_coordinator.target_authority_producers.sources.ProviderAuthoritySources",
            "ci_coordinator.verification_core",
            "ci_coordinator.workflow_authority.evidence.WorkflowAuthorityEvidence",
            "ci_coordinator.workflow_authority.model.WorkflowAuthorityRepository",
        ),
        rule_id="python.import-boundary.production-admission",
    ),
    _standard_layer_rule(
        "/ci_coordinator/plan_issuance/",
        allowed_first_party_prefixes=(
            "ci_coordinator.config_control",
            "ci_coordinator.execution_orchestration",
            "ci_coordinator.identity_admission",
            "ci_coordinator.kernel",
            "ci_coordinator.plan_issuance",
            "ci_coordinator.production_admission",
            "ci_coordinator.reconciliation",
            "ci_coordinator.repo_context",
            "ci_coordinator.runner_capacity",
            "ci_coordinator.validation_contract",
            "ci_coordinator.verification_core",
        ),
        rule_id="python.import-boundary.plan-issuance",
    ),
    _standard_layer_rule(
        "/ci_coordinator/runner_capacity/",
        allowed_first_party_prefixes=(
            "ci_coordinator.execution_orchestration",
            "ci_coordinator.kernel",
            "ci_coordinator.repo_context",
            "ci_coordinator.runner_capacity",
            "ci_coordinator.validation_contract",
            "ci_coordinator.verification_core",
        ),
        rule_id="python.import-boundary.runner-capacity",
    ),
    _standard_layer_rule(
        "/ci_coordinator/execution_orchestration/",
        allowed_first_party_prefixes=(
            "ci_coordinator.execution_orchestration",
            "ci_coordinator.kernel",
            "ci_coordinator.validation_contract",
        ),
        rule_id="python.import-boundary.execution-orchestration",
    ),
    _standard_layer_rule(
        "/ci_coordinator/target_artifacts/",
        allowed_first_party_prefixes=(
            "ci_coordinator.execution_orchestration",
            "ci_coordinator.kernel",
            "ci_coordinator.repo_context",
            "ci_coordinator.runner_capacity",
            "ci_coordinator.runtime_settings",
            "ci_coordinator.target_artifacts",
        ),
        allowed_import_prefixes=("importlib.resources",),
        rule_id="python.import-boundary.target-artifacts",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/consumer_contract_lab/"),
        allowed_first_party_prefixes=(
            "ci_coordinator.app",
            "ci_coordinator.config_control",
            "ci_coordinator.config_epochs",
            "ci_coordinator.consumer_contract_lab",
            "ci_coordinator.execution_orchestration",
            "ci_coordinator.identity_admission",
            "ci_coordinator.kernel",
            "ci_coordinator.plan_issuance",
            "ci_coordinator.production_admission",
            "ci_coordinator.reconciliation",
            "ci_coordinator.repo_context",
            "ci_coordinator.runner_capacity",
            "ci_coordinator.runtime_settings",
            "ci_coordinator.target_artifacts",
        ),
        forbidden=(
            *_STANDARD_LAYER_FORBIDDEN,
            "ci_coordinator.api",
            "ci_coordinator.integrations",
            "ci_coordinator.persistence",
            "ci_coordinator.runtime",
        ),
        rule_id="python.import-boundary.consumer-contract-lab",
    ),
    _standard_layer_rule(
        "/ci_coordinator/workbench_read_models/",
        allowed_first_party_prefixes=(
            "ci_coordinator.audit_replay",
            "ci_coordinator.config_control",
            "ci_coordinator.workbench_read_models",
        ),
        rule_id="python.import-boundary.workbench-read-models",
    ),
    _standard_layer_rule(
        "/ci_coordinator/runtime_settings/",
        allowed_first_party_prefixes=(
            "ci_coordinator.kernel",
            "ci_coordinator.runtime_settings",
        ),
        allowed_import_prefixes=("importlib.resources",),
        rule_id="python.import-boundary.runtime-settings",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/replay_cli/"),
        allowed_first_party_prefixes=(
            "ci_coordinator.audit_replay",
            "ci_coordinator.kernel",
            "ci_coordinator.persistence",
            "ci_coordinator.replay_cli",
            "ci_coordinator.runtime.environment",
            "ci_coordinator.runtime_settings",
        ),
        forbidden=(
            *_DYNAMIC_LOADING_AUTHORITIES,
            "dotenv",
            "fastapi",
            "httpx",
            "os",
            "requests",
        ),
        rule_id="python.import-boundary.replay-cli",
    ),
    ImportRule(
        applies=_path_contains("/ci_coordinator/database_access_cli/"),
        allowed_first_party_prefixes=(
            "ci_coordinator.database_access_cli",
            "ci_coordinator.kernel",
            "ci_coordinator.persistence",
            "ci_coordinator.runtime.environment",
            "ci_coordinator.runtime_settings",
        ),
        forbidden=(
            *_DYNAMIC_LOADING_AUTHORITIES,
            "dotenv",
            "fastapi",
            "httpx",
            "os",
            "requests",
        ),
        rule_id="python.import-boundary.database-access-cli",
    ),
    ImportRule(
        applies=_runtime_composition_rule_applies,
        allowed_first_party_prefixes=(
            "ci_coordinator.repo_context.diff_model",
            "ci_coordinator.app",
            "ci_coordinator.audit_replay",
            "ci_coordinator.api.http",
            "ci_coordinator.ci_economics",
            "ci_coordinator.config_control",
            "ci_coordinator.config_epochs",
            "ci_coordinator.control_plane_identity",
            "ci_coordinator.github_ingestion",
            "ci_coordinator.governance_baseline",
            "ci_coordinator.governance_observation",
            "ci_coordinator.identity_admission",
            "ci_coordinator.integrations",
            "ci_coordinator.kernel",
            "ci_coordinator.observability",
            "ci_coordinator.operator_controls",
            "ci_coordinator.persistence",
            "ci_coordinator.plan_issuance",
            "ci_coordinator.production_admission",
            "ci_coordinator.provider_inventory",
            "ci_coordinator.reconciliation",
            "ci_coordinator.runtime",
            "ci_coordinator.runtime_settings",
            "ci_coordinator.workbench_read_models",
            "ci_coordinator.workflow_discovery",
        ),
        forbidden=(
            *_DYNAMIC_LOADING_AUTHORITIES,
            "dotenv",
            "httpx",
            "os",
            "os.environ",
            "requests",
        ),
        rule_id="python.import-boundary.runtime-composition",
    ),
)


type ImportRuleCoverageViolationKind = Literal["missing-rule"]


@dataclass(frozen=True, slots=True)
class ImportRuleCoverageViolation:
    context_name: str
    kind: ImportRuleCoverageViolationKind
    rule_id: str
    source_path: Path


def import_rule_coverage_violations(
    source_root: Path,
    *,
    rules: tuple[ImportRule, ...] = IMPORT_RULES,
    inventory: PythonSourceInventory | None = None,
) -> tuple[ImportRuleCoverageViolation, ...]:
    source_inventory = inventory or inventory_python_sources(source_root)
    context_rules = tuple(rule for rule in rules if rule.rule_id not in _GLOBAL_CAPABILITY_RULE_IDS)
    violations: list[ImportRuleCoverageViolation] = []
    for path in source_inventory.files:
        relative = path.relative_to(source_root)
        normalized = f"/repo/backend/src/ci_coordinator/{relative.as_posix()}"
        if any(rule.applies(normalized) for rule in context_rules):
            continue
        context_name = relative.parts[0] if len(relative.parts) > 1 else "root-package"
        violations.append(
            ImportRuleCoverageViolation(
                context_name=context_name,
                kind="missing-rule",
                rule_id=f"python.import-boundary.{context_name.replace('_', '-')}",
                source_path=path,
            )
        )
    return tuple(violations)


type GovernanceRuleCoverageViolationKind = Literal[
    "duplicate-rule",
    "missing-rule",
    "orphan-rule",
    "rule-not-applicable",
    "symlinked-package",
]


@dataclass(frozen=True, slots=True)
class GovernanceRuleCoverageViolation:
    context_name: str
    kind: GovernanceRuleCoverageViolationKind
    rule_id: str


def governance_rule_coverage_violations(
    source_root: Path,
    *,
    rules: tuple[ImportRule, ...] = IMPORT_RULES,
    inventory: PythonSourceInventory | None = None,
) -> tuple[GovernanceRuleCoverageViolation, ...]:
    source_inventory = inventory or inventory_python_sources(source_root)
    files_by_context: dict[str, list[Path]] = {}
    symlinks_by_context: dict[str, list[Path]] = {}
    for path in source_inventory.files:
        context_name = _governance_context_name(path, source_root)
        if context_name is not None:
            files_by_context.setdefault(context_name, []).append(path)
    for path in source_inventory.symlinks:
        context_name = _governance_context_name(path, source_root)
        if context_name is not None:
            symlinks_by_context.setdefault(context_name, []).append(path)
    contexts = tuple(sorted({*files_by_context, *symlinks_by_context}))
    expected_rule_ids = {
        context_name: f"python.import-boundary.{context_name.replace('_', '-')}"
        for context_name in contexts
    }
    governance_rules = tuple(
        rule for rule in rules if rule.rule_id.startswith("python.import-boundary.governance-")
    )
    violations = [
        GovernanceRuleCoverageViolation(
            context_name,
            "symlinked-package",
            expected_rule_ids[context_name],
        )
        for context_name in contexts
        if context_name in symlinks_by_context
    ]
    for context_name, rule_id in expected_rule_ids.items():
        matching_rules = tuple(rule for rule in governance_rules if rule.rule_id == rule_id)
        if not matching_rules:
            violations.append(
                GovernanceRuleCoverageViolation(context_name, "missing-rule", rule_id)
            )
        elif len(matching_rules) > 1:
            violations.append(
                GovernanceRuleCoverageViolation(context_name, "duplicate-rule", rule_id)
            )
        elif any(
            not matching_rules[0].applies(
                f"/repo/backend/src/ci_coordinator/{path.relative_to(source_root).as_posix()}"
            )
            for path in files_by_context.get(context_name, ())
        ):
            violations.append(
                GovernanceRuleCoverageViolation(context_name, "rule-not-applicable", rule_id)
            )
    expected_ids = frozenset(expected_rule_ids.values())
    for rule in governance_rules:
        if rule.rule_id not in expected_ids:
            context_name = rule.rule_id.removeprefix("python.import-boundary.").replace("-", "_")
            violations.append(
                GovernanceRuleCoverageViolation(context_name, "orphan-rule", rule.rule_id)
            )
    return tuple(
        sorted(
            violations,
            key=lambda violation: (
                violation.context_name,
                violation.kind,
                violation.rule_id,
            ),
        )
    )


def _governance_context_name(path: Path, source_root: Path) -> str | None:
    relative = path.relative_to(source_root)
    if not relative.parts or not relative.parts[0].startswith("governance_"):
        return None
    return relative.parts[0]

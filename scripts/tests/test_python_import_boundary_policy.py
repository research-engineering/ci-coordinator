from __future__ import annotations

import ast
from dataclasses import replace
from importlib.util import resolve_name
from pathlib import Path

import pytest
import scripts.python_import_authority_scanner as authority_scanner
from scripts.python_import_boundary_engine import (
    inventory_python_sources,
    module_object_authority,
)
from scripts.python_import_boundary_policy import (
    HTTP_CONCRETE_SERVICE_POLICY,
    IMPORT_RULES,
    GovernanceRuleCoverageViolation,
    governance_rule_coverage_violations,
    import_rule_coverage_violations,
    imported_modules,
    violating_rule_ids,
)
from scripts.python_witness import PythonWitness

import ci_coordinator.app as app_facade
import ci_coordinator.persistence as persistence_facade

CONTEXT_PATHS = (
    (
        "python.import-boundary.config-epochs",
        "/repo/backend/src/ci_coordinator/config_epochs/contracts.py",
    ),
    (
        "python.import-boundary.capacity-qualification",
        "/repo/backend/src/ci_coordinator/capacity_qualification/admission.py",
    ),
    (
        "python.import-boundary.governance-comparison",
        "/repo/backend/src/ci_coordinator/governance_comparison/comparison.py",
    ),
    (
        "python.import-boundary.observability",
        "/repo/backend/src/ci_coordinator/observability/health.py",
    ),
    (
        "python.import-boundary.ci-economics",
        "/repo/backend/src/ci_coordinator/ci_economics/collection.py",
    ),
    (
        "python.import-boundary.operator-controls",
        "/repo/backend/src/ci_coordinator/operator_controls/service.py",
    ),
    (
        "python.import-boundary.planning-core",
        "/repo/backend/src/ci_coordinator/planning_core/planner.py",
    ),
    (
        "python.import-boundary.proposal-review",
        "/repo/backend/src/ci_coordinator/proposal_review/model.py",
    ),
    (
        "python.import-boundary.replay-cli",
        "/repo/backend/src/ci_coordinator/replay_cli/__init__.py",
    ),
    (
        "python.import-boundary.database-access-cli",
        "/repo/backend/src/ci_coordinator/database_access_cli/__init__.py",
    ),
    (
        "python.import-boundary.runtime-settings",
        "/repo/backend/src/ci_coordinator/runtime_settings/admission.py",
    ),
    (
        "python.import-boundary.target-artifacts",
        "/repo/backend/src/ci_coordinator/target_artifacts/renderer.py",
    ),
    (
        "python.import-boundary.target-authority-relation",
        "/repo/backend/src/ci_coordinator/target_authority_relation/comparison.py",
    ),
    (
        "python.import-boundary.workflow-authority",
        "/repo/backend/src/ci_coordinator/workflow_authority/evidence.py",
    ),
    (
        "python.import-boundary.target-authority-producers",
        "/repo/backend/src/ci_coordinator/target_authority_producers/enumeration.py",
    ),
    (
        "python.import-boundary.target-authority-evidence-replay",
        "/repo/backend/src/ci_coordinator/target_authority_evidence/replay.py",
    ),
    (
        "python.import-boundary.consumer-contract-lab",
        "/repo/backend/src/ci_coordinator/consumer_contract_lab/runner.py",
    ),
    (
        "python.import-boundary.validation-contract",
        "/repo/backend/src/ci_coordinator/validation_contract/catalog.py",
    ),
    (
        "python.import-boundary.workbench-read-models",
        "/repo/backend/src/ci_coordinator/workbench_read_models/service.py",
    ),
)


@pytest.mark.parametrize(
    ("path", "allowed", "forbidden"),
    [
        (
            "/repo/backend/src/ci_coordinator/app/planning_preparation.py",
            "ci_coordinator.github_ingestion.seeds.PushSeed",
            "ci_coordinator.runtime.planning_preparation",
        ),
        (
            "/repo/backend/src/ci_coordinator/runtime/planning_preparation.py",
            "ci_coordinator.repo_context.diff_model.RepositoryEpoch",
            "ci_coordinator.repo_context.dependency_graph.build_dependency_graph",
        ),
    ],
)
def test_preparation_crosses_only_the_admitted_data_owner_edges(
    path: str, allowed: str, forbidden: str
) -> None:
    assert violating_rule_ids(path, allowed) == ()
    assert violating_rule_ids(path, forbidden)


@pytest.mark.parametrize(
    ("module", "admitted"),
    [
        ("ci_coordinator.target_artifacts.requester", True),
        ("ci_coordinator.operator_controls.auth.RepositoryAccessUnavailable", True),
        ("ci_coordinator.operator_controls.auth.ControlPlaneScopeAuthorizer", False),
        ("ci_coordinator.operator_controls.auth", False),
        ("ci_coordinator.target_artifacts.renderer", False),
        ("ci_coordinator.target_artifacts.cli", False),
        ("ci_coordinator.target_artifacts", False),
        ("ci_coordinator.production_admission.evidence_lookup.ProductionEvidenceLookup", True),
        ("ci_coordinator.production_admission.ports.CurrentProductionSources", True),
        ("ci_coordinator.target_authority_producers.sources.ProviderAuthoritySources", True),
        ("ci_coordinator.production_admission.authority.ProductionAdmissionGrant", False),
        ("ci_coordinator.production_admission.ports.ProductionCutoverStore", False),
        ("ci_coordinator.target_authority_producers.sources.TargetArtifactSources", False),
    ],
)
def test_github_artifact_dependency_is_limited_to_the_requester_capability(
    module: str,
    admitted: bool,
) -> None:
    path = "/repo/backend/src/ci_coordinator/integrations/github/adapter_snapshot.py"
    assert (
        "python.import-boundary.github-integration" not in violating_rule_ids(path, module)
    ) is admitted
    for owner in ("repo_context", "execution_orchestration"):
        assert violating_rule_ids(f"/repo/backend/src/ci_coordinator/{owner}/consumer.py", module)


@pytest.mark.parametrize(("rule_id", "path"), CONTEXT_PATHS)
def test_context_rules_reject_direct_process_environment_access(rule_id: str, path: str) -> None:
    assert any(rule.rule_id == rule_id and rule.applies(path) for rule in IMPORT_RULES)
    assert "python.import-boundary.process-environment" in violating_rule_ids(path, "os.getenv")
    assert "python.import-boundary.os-capability" in violating_rule_ids(path, "os")


@pytest.mark.parametrize("module", ["os", "os.environ", "subprocess", "resource", "urllib.request"])
def test_exported_reporter_has_narrow_standalone_capabilities(module: str) -> None:
    root = "/repo/backend/src/ci_coordinator/target_artifacts/resources/"
    assert violating_rule_ids(root + "ci_measurement_reporter.py", module) == ()
    if module in {"os", "os.environ"}:
        assert "python.import-boundary.os-capability" in violating_rule_ids(
            root + "sibling.py", module
        )


@pytest.mark.parametrize(
    "module",
    [
        "ci_coordinator.kernel",
        "ci_coordinator.runtime",
        "pydantic",
        "httpx2",
        "requests",
        "importlib",
    ],
)
def test_exported_reporter_cannot_depend_on_service_or_installed_packages(module: str) -> None:
    path = "/repo/backend/src/ci_coordinator/target_artifacts/resources/ci_measurement_reporter.py"
    assert "python.import-boundary.measurement-reporter" in violating_rule_ids(path, module)


def test_exported_reporter_admits_only_the_owned_http_error_cleanup_capability() -> None:
    path = "/repo/backend/src/ci_coordinator/target_artifacts/resources/ci_measurement_reporter.py"
    rule = "python.import-boundary.measurement-reporter"
    assert rule not in violating_rule_ids(path, "urllib.error.HTTPError")
    assert rule in violating_rule_ids(path, "urllib.error")
    assert rule in violating_rule_ids(path, "urllib.error.URLError")


def test_app_negative_probes_reject_infrastructure() -> None:
    app_path = "/repo/backend/src/ci_coordinator/app/config_management.py"

    for forbidden in (
        "ci_coordinator.database_access_cli",
        "ci_coordinator.integrations.github.app_client",
        "ci_coordinator.persistence.errors",
        "ci_coordinator.replay_cli",
        "ci_coordinator.runtime.composition",
        "ci_coordinator.runtime_settings.admission",
        "ci_coordinator.target_artifacts",
    ):
        assert "python.import-boundary.app-use-cases" in violating_rule_ids(
            app_path,
            forbidden,
        )


@pytest.mark.parametrize(
    ("module", "admitted"),
    [
        ("ci_coordinator.ci_economics.observation_scan.validate_observation_worker_id", True),
        ("ci_coordinator.app._orchestration", True),
        ("ci_coordinator.ci_economics._observation_values.digest", False),
        ("ci_coordinator.planning_core._private_rules", False),
        ("ci_coordinator.kernel.public_module._private_value", False),
    ],
)
def test_application_imports_only_public_capability_apis(module: str, admitted: bool) -> None:
    path = "/repo/backend/src/ci_coordinator/app/consumer.py"
    assert (
        "python.import-boundary.app-use-cases" not in violating_rule_ids(path, module)
    ) is admitted


@pytest.mark.parametrize(
    "source",
    [
        "from ci_coordinator.ci_economics._observation_values import digest",
        "import ci_coordinator.ci_economics._observation_values as values",
        "from ci_coordinator.ci_economics import _observation_values as values",
        "from ..ci_economics._observation_values import digest",
    ],
)
def test_private_capability_import_syntax_reaches_the_application_gate(
    tmp_path: Path, source: str
) -> None:
    root = tmp_path / "ci_coordinator"
    path = root / "app" / "consumer.py"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    assert any(
        "python.import-boundary.app-use-cases" in violating_rule_ids(str(path), module)
        for module in imported_modules(path, root)
    )


def test_capacity_qualification_rejects_runtime_provider_and_persistence_authority() -> None:
    path = "/repo/backend/src/ci_coordinator/capacity_qualification/admission.py"

    for forbidden in (
        "ci_coordinator.api.http.app",
        "ci_coordinator.integrations.github.app_client",
        "ci_coordinator.persistence.connection",
        "ci_coordinator.production_admission.codec",
        "ci_coordinator.runtime.composition",
    ):
        assert "python.import-boundary.capacity-qualification" in violating_rule_ids(
            path,
            forbidden,
        )
    for admitted in (
        "ci_coordinator.audit_replay.checkpoint",
        "ci_coordinator.capacity_qualification.model",
        "ci_coordinator.kernel.ed25519",
    ):
        assert violating_rule_ids(path, admitted) == ()


def test_ci_economics_rejects_transport_persistence_and_runtime_authority() -> None:
    path = "/repo/backend/src/ci_coordinator/ci_economics/collection.py"

    for forbidden in (
        "ci_coordinator.api.http.app",
        "ci_coordinator.app.ci_economics",
        "ci_coordinator.integrations.github.ci_economics_provider",
        "ci_coordinator.persistence.ci_economics_repository",
        "ci_coordinator.runtime.composition",
        "fastapi",
        "httpx",
        "sqlalchemy",
    ):
        assert "python.import-boundary.ci-economics" in violating_rule_ids(path, forbidden)
    for admitted in (
        "ci_coordinator.ci_economics.model",
        "ci_coordinator.config_control.RepositoryScope",
        "ci_coordinator.kernel.hash_object",
        "ci_coordinator.reconciliation.ReconciliationSubject",
        "importlib.resources.files",
    ):
        assert violating_rule_ids(path, admitted) == ()


@pytest.mark.parametrize(
    "path",
    (
        "/repo/backend/src/ci_coordinator/app/ci_economics.py",
        "/repo/backend/src/ci_coordinator/integrations/github/ci_economics_provider.py",
        "/repo/backend/src/ci_coordinator/persistence/ci_economics_repository.py",
        "/repo/backend/src/ci_coordinator/runtime/composition.py",
    ),
)
def test_ci_economics_mechanism_edges_point_inward(path: str) -> None:
    assert violating_rule_ids(path, "ci_coordinator.ci_economics.model") == ()


def test_http_rejects_provider_adapters_and_concrete_use_case_implementations() -> None:
    route_path = "/repo/backend/src/ci_coordinator/api/http/routers/provider_inventory.py"

    for forbidden in (
        "ci_coordinator.app.planning_preparation.PlanningPreparationService",
        module_object_authority("ci_coordinator.app.planning_preparation"),
        "ci_coordinator.app.repository_attestation.RepositoryAttestationService",
        "ci_coordinator.control_plane_identity.BrowserIdentityService",
        module_object_authority("ci_coordinator.control_plane_identity.browser"),
        "ci_coordinator.integrations.github",
        "ci_coordinator.provider_inventory.ProviderInventoryService",
    ):
        assert "python.import-boundary.api-http" in violating_rule_ids(route_path, forbidden)
    for admitted in (
        "ci_coordinator.app.config_management.ConfigManagementUseCase",
        "ci_coordinator.app.repository_attestation.RepositoryAttestationUseCase",
        "ci_coordinator.control_plane_identity.BrowserIdentityUseCase",
        "ci_coordinator.provider_inventory.ports.ProviderInventoryUseCase",
    ):
        assert violating_rule_ids(route_path, admitted) == ()


@pytest.mark.parametrize(
    "source",
    (
        """\
import ci_coordinator.provider_inventory as inventory

service = inventory.ProviderInventoryService
""",
        """\
from ci_coordinator import provider_inventory as inventory

service = inventory.ProviderInventoryService
""",
        """\
import ci_coordinator.provider_inventory as inventory

service = getattr(inventory, "ProviderInventoryService")
""",
        """\
import ci_coordinator.provider_inventory as inventory

service = getattr(inventory, f"ProviderInventoryService")
""",
    ),
)
def test_http_static_alias_cannot_hide_concrete_use_case_implementation(
    tmp_path: Path,
    source: str,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    module_path = source_root / "api" / "http" / "routers" / "provider_inventory.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(source, encoding="utf-8")

    imports = imported_modules(module_path, source_root)

    authority = "ci_coordinator.provider_inventory.ProviderInventoryService"
    assert authority in imports
    assert "python.import-boundary.api-http" in violating_rule_ids(
        str(module_path),
        authority,
    )


@pytest.mark.parametrize(
    ("source", "module"),
    (
        (
            "import ci_coordinator.provider_inventory as inventory\n",
            "ci_coordinator.provider_inventory",
        ),
        (
            "from ci_coordinator import provider_inventory as inventory\n",
            "ci_coordinator.provider_inventory",
        ),
        (
            "from ci_coordinator.app import config_management as config\n",
            "ci_coordinator.app.config_management",
        ),
        (
            "import ci_coordinator.app.dynamic_plan_service as planning\n",
            "ci_coordinator.app.dynamic_plan_service",
        ),
        (
            "import ci_coordinator.app as application\n",
            "ci_coordinator.app",
        ),
        (
            "import ci_coordinator.app.governance_baseline as baseline\n",
            "ci_coordinator.app.governance_baseline",
        ),
        (
            "import ci_coordinator as root\nconfig = root.app.config_management\n",
            "ci_coordinator.app.config_management",
        ),
    ),
)
def test_http_rejects_restricted_service_module_objects(
    tmp_path: Path,
    source: str,
    module: str,
) -> None:
    module_path, imports = _scan_http_module(tmp_path, source)

    authority = module_object_authority(module)
    assert authority in imports
    assert "python.import-boundary.api-http" in violating_rule_ids(str(module_path), authority)


def test_http_concrete_service_policy_covers_definitions_and_facades() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    source_root = repository_root / "backend" / "src" / "ci_coordinator"
    discovered = _discover_concrete_service_policy(source_root)
    configured = {
        (
            service.implementation_module,
            service.export_name,
            tuple(
                sorted(
                    (facade_module, service.export_name) for facade_module in service.facade_modules
                )
            ),
            service.implementation_module_is_private,
        )
        for service in HTTP_CONCRETE_SERVICE_POLICY
    }

    assert configured == discovered


def _discover_concrete_service_policy(
    source_root: Path,
) -> set[tuple[str, str, tuple[tuple[str, str], ...], bool]]:
    definitions: dict[str, tuple[str, str]] = {}
    source_inventory = inventory_python_sources(source_root)
    for path in source_inventory.files:
        module = _source_module(path, source_root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if (
                isinstance(node, ast.ClassDef)
                and node.name.endswith("Service")
                and not _inherits_protocol(node)
            ):
                definitions[f"{module}.{node.name}"] = (module, node.name)

    facade_edges: list[tuple[str, str]] = []
    for path in source_inventory.files:
        if path.name != "__init__.py":
            continue
        facade = _source_module(path, source_root)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if not isinstance(node, ast.ImportFrom):
                continue
            base = (
                node.module or ""
                if node.level == 0
                else resolve_name(f"{'.' * node.level}{node.module or ''}", facade)
            )
            for alias in node.names:
                source_authority = f"{base}.{alias.name}" if base else alias.name
                public_name = alias.asname or alias.name
                facade_edges.append((source_authority, f"{facade}.{public_name}"))

    resolved_routes = {authority: {authority} for authority in definitions}
    changed = True
    while changed:
        changed = False
        for source_authority, facade_authority in facade_edges:
            roots = resolved_routes.get(source_authority, set())
            routed = resolved_routes.setdefault(facade_authority, set())
            previous_count = len(routed)
            routed.update(roots)
            changed = changed or len(routed) != previous_count

    facades: dict[str, set[tuple[str, str]]] = {authority: set() for authority in definitions}
    for facade_authority, roots in resolved_routes.items():
        facade_module, separator, public_name = facade_authority.rpartition(".")
        if not separator:
            continue
        for root in roots:
            if facade_authority == root:
                continue
            facades[root].add((facade_module, public_name))

    return {
        (
            module,
            export_name,
            tuple(sorted(facades[authority])),
            module.endswith(".service")
            or module == "ci_coordinator.runtime.reconciliation_service",
        )
        for authority, (module, export_name) in definitions.items()
    }


def test_concrete_service_inventory_preserves_aliased_facade_name(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    package = source_root / "feature"
    package.mkdir(parents=True)
    (package / "service.py").write_text(
        "class ExampleService:\n    pass\n",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        "from .service import ExampleService as PublicService\n",
        encoding="utf-8",
    )

    assert _discover_concrete_service_policy(source_root) == {
        (
            "ci_coordinator.feature.service",
            "ExampleService",
            (("ci_coordinator.feature", "PublicService"),),
            True,
        )
    }


def test_concrete_service_inventory_resolves_transitive_facade_routes(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    package = source_root / "feature"
    facade = source_root / "app"
    package.mkdir(parents=True)
    facade.mkdir()
    (package / "service.py").write_text(
        "class ExampleService:\n    pass\n",
        encoding="utf-8",
    )
    (package / "__init__.py").write_text(
        "from .service import ExampleService as PublicService\n",
        encoding="utf-8",
    )
    (facade / "__init__.py").write_text(
        "from ci_coordinator.feature import PublicService as FinalService\n",
        encoding="utf-8",
    )

    assert _discover_concrete_service_policy(source_root) == {
        (
            "ci_coordinator.feature.service",
            "ExampleService",
            (
                ("ci_coordinator.app", "FinalService"),
                ("ci_coordinator.feature", "PublicService"),
            ),
            True,
        )
    }


def test_concrete_service_inventory_preserves_definition_target_facade_route(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    alpha = source_root / "alpha"
    beta = source_root / "beta"
    alpha.mkdir(parents=True)
    beta.mkdir()
    (alpha / "__init__.py").write_text(
        "class AlphaService:\n"
        "    pass\n"
        "from ci_coordinator.beta.service import BetaService as AlphaService\n",
        encoding="utf-8",
    )
    (beta / "service.py").write_text(
        "class BetaService:\n    pass\n",
        encoding="utf-8",
    )

    assert _discover_concrete_service_policy(source_root) == {
        ("ci_coordinator.alpha", "AlphaService", (), False),
        (
            "ci_coordinator.beta.service",
            "BetaService",
            (("ci_coordinator.alpha", "AlphaService"),),
            True,
        ),
    }


def _source_module(path: Path, source_root: Path) -> str:
    parts = ("ci_coordinator", *path.relative_to(source_root).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _inherits_protocol(node: ast.ClassDef) -> bool:
    return any(
        (isinstance(base, ast.Name) and base.id == "Protocol")
        or (isinstance(base, ast.Attribute) and base.attr == "Protocol")
        for base in node.bases
    )


@pytest.mark.parametrize(
    "source",
    (
        "from ci_coordinator.provider_inventory import ProviderInventoryUseCase\n",
        "from ci_coordinator.provider_inventory.ports import ProviderInventoryUseCase\n",
        "from ci_coordinator.provider_inventory.model import RepositorySummary\n",
    ),
)
def test_http_accepts_exact_port_and_data_imports(tmp_path: Path, source: str) -> None:
    module_path, imports = _scan_http_module(tmp_path, source)

    assert all(violating_rule_ids(str(module_path), authority) == () for authority in imports)


@pytest.mark.parametrize(
    ("source", "authority"),
    (
        (
            "from ci_coordinator.provider_inventory import ProviderInventoryService\n",
            "ci_coordinator.provider_inventory.ProviderInventoryService",
        ),
        (
            "from ci_coordinator.provider_inventory.service import ProviderInventoryService\n",
            "ci_coordinator.provider_inventory.service.ProviderInventoryService",
        ),
    ),
)
def test_http_rejects_exact_concrete_service_imports(
    tmp_path: Path,
    source: str,
    authority: str,
) -> None:
    module_path, imports = _scan_http_module(tmp_path, source)

    assert authority in imports
    assert "python.import-boundary.api-http" in violating_rule_ids(str(module_path), authority)


def _scan_http_module(tmp_path: Path, source: str) -> tuple[Path, tuple[str, ...]]:
    source_root = tmp_path / "ci_coordinator"
    module_path = source_root / "api" / "http" / "routers" / "provider_inventory.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(source, encoding="utf-8")
    return module_path, imported_modules(module_path, source_root)


def test_http_owns_only_request_shaped_protocols() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    http_root = repository_root / "backend" / "src" / "ci_coordinator" / "api" / "http"
    protocols = {
        (source.relative_to(http_root).as_posix(), node.name)
        for source in http_root.rglob("*.py")
        for node in ast.parse(source.read_text(encoding="utf-8")).body
        if isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and base.id == "Protocol" for base in node.bases)
    }

    assert protocols == {
        ("dependencies.py", "ActionsRunAuthenticator"),
        ("dependencies.py", "ControlPlaneAuthenticator"),
        ("dependencies.py", "ControlPlaneMutationAdmission"),
        ("dependencies.py", "PlanRequestAuthenticator"),
        ("webhook_ingress.py", "WebhookIngressUseCase"),
    }


def test_every_python_source_has_a_contextual_import_rule(tmp_path: Path) -> None:
    repository_root = Path(__file__).resolve().parents[2]
    source_root = repository_root / "backend" / "src" / "ci_coordinator"

    assert import_rule_coverage_violations(source_root) == ()

    unregistered = tmp_path / "ci_coordinator" / "new_context" / "service.py"
    unregistered.parent.mkdir(parents=True)
    unregistered.write_text("from ci_coordinator.persistence import connection\n", encoding="utf-8")
    violations = import_rule_coverage_violations(tmp_path / "ci_coordinator")
    assert len(violations) == 1
    assert violations[0].context_name == "new_context"
    assert violations[0].kind == "missing-rule"


def test_replay_cli_allows_its_persistence_and_environment_dependencies() -> None:
    replay_path = "/repo/backend/src/ci_coordinator/replay_cli/__init__.py"

    assert violating_rule_ids(replay_path, "ci_coordinator.persistence.connection") == ()
    assert violating_rule_ids(replay_path, "ci_coordinator.runtime.environment") == ()


def test_database_access_cli_allows_only_its_deployment_dependencies() -> None:
    access_path = "/repo/backend/src/ci_coordinator/database_access_cli/__init__.py"

    assert (
        violating_rule_ids(access_path, "ci_coordinator.persistence.runtime_principal_access") == ()
    )
    assert violating_rule_ids(access_path, "ci_coordinator.runtime.environment") == ()
    assert "python.import-boundary.database-access-cli" in violating_rule_ids(
        access_path, "ci_coordinator.app"
    )


def test_execution_and_query_dependencies_are_acyclic() -> None:
    execution_path = "/repo/backend/src/ci_coordinator/execution_orchestration/target_registry.py"
    capacity_path = "/repo/backend/src/ci_coordinator/runner_capacity/inputs.py"
    workbench_path = "/repo/backend/src/ci_coordinator/workbench_read_models/service.py"
    persistence_path = "/repo/backend/src/ci_coordinator/persistence/workbench_repository.py"
    validation_path = "/repo/backend/src/ci_coordinator/validation_contract/catalog.py"

    assert violating_rule_ids(capacity_path, "ci_coordinator.execution_orchestration") == ()
    assert "python.import-boundary.execution-orchestration" in violating_rule_ids(
        execution_path, "ci_coordinator.runner_capacity"
    )
    assert violating_rule_ids(persistence_path, "ci_coordinator.workbench_read_models") == ()
    assert "python.import-boundary.workbench-read-models" in violating_rule_ids(
        workbench_path, "ci_coordinator.persistence"
    )
    assert "python.import-boundary.validation-contract" in violating_rule_ids(
        validation_path, "ci_coordinator.planning_core"
    )


def test_target_authority_relation_rejects_mechanism_and_policy_dependencies() -> None:
    relation_path = "/repo/backend/src/ci_coordinator/target_authority_relation/comparison.py"

    for forbidden in (
        "ci_coordinator.api.http",
        "ci_coordinator.app",
        "ci_coordinator.config_control",
        "ci_coordinator.config_control.admit_policy_document",
        "ci_coordinator.integrations.github",
        "ci_coordinator.kernel",
        "ci_coordinator.kernel.clock.SystemClock",
        "ci_coordinator.persistence",
        "ci_coordinator.planning_core",
        "ci_coordinator.production_admission",
        "ci_coordinator.runtime",
        "ci_coordinator.workflow_discovery",
        "datetime",
        "fastapi",
        "httpx",
        "os",
        "pathlib",
        "random",
        "secrets",
        "socket",
        "sqlalchemy",
        "subprocess",
        "time",
        "uuid",
    ):
        assert "python.import-boundary.target-authority-relation" in violating_rule_ids(
            relation_path,
            forbidden,
        )
    for admitted in (
        "ci_coordinator.config_control.contracts.RepositoryScope",
        "ci_coordinator.kernel.canonical_json.CanonicalJsonError",
        "ci_coordinator.kernel.canonical_json.JsonResourceLimits",
        "ci_coordinator.kernel.canonical_json.bounded_canonical_json",
        "ci_coordinator.kernel.hashing.sha256_hex",
        "ci_coordinator.kernel.ordering.utf16_sort_key",
        "ci_coordinator.kernel.strict_json.StrictJsonError",
        "ci_coordinator.kernel.strict_json.load_strict_json",
        "ci_coordinator.target_authority_relation.model",
    ):
        assert violating_rule_ids(relation_path, admitted) == ()


def test_workflow_authority_owns_only_pure_git_object_evidence() -> None:
    authority_path = "/repo/backend/src/ci_coordinator/workflow_authority/evidence.py"

    for forbidden in (
        "ci_coordinator.api",
        "ci_coordinator.app",
        "ci_coordinator.config_control",
        "ci_coordinator.integrations.github",
        "ci_coordinator.persistence",
        "ci_coordinator.runtime",
        "ci_coordinator.target_authority_producers",
        "datetime",
        "glob",
        "httpx2",
        "io",
        "open",
        "os",
        "pathlib",
        "shutil",
        "subprocess",
        "tempfile",
        "time",
    ):
        assert "python.import-boundary.workflow-authority" in violating_rule_ids(
            authority_path,
            forbidden,
        )
    for admitted in (
        "ci_coordinator.config_control.contracts.RepositoryScope",
        "ci_coordinator.kernel.canonical_json.bounded_canonical_json",
        "ci_coordinator.kernel.strict_json.load_strict_json",
        "ci_coordinator.workflow_authority.model",
        "dataclasses.dataclass",
        "hashlib.sha1",
        "urllib.parse.quote",
    ):
        assert violating_rule_ids(authority_path, admitted) == ()


def test_target_authority_producer_edges_are_closed_and_inward() -> None:
    producer_path = "/repo/backend/src/ci_coordinator/target_authority_producers/enumeration.py"
    integration_path = "/repo/backend/src/ci_coordinator/integrations/github/workflow_authority.py"

    for admitted in (
        "ci_coordinator.config_control.planning_projection",
        "ci_coordinator.consumer_contract_lab.model",
        "ci_coordinator.execution_orchestration.target_registry",
        "ci_coordinator.governance_observation.model",
        "ci_coordinator.kernel.ordering",
        "ci_coordinator.repo_context.workflow_inventory",
        "ci_coordinator.target_authority_relation",
        "ci_coordinator.validation_contract.model",
        "ci_coordinator.workflow_authority.evidence",
        "ci_coordinator.workflow_discovery.report",
        "typing.Protocol",
    ):
        assert violating_rule_ids(producer_path, admitted) == ()
    for forbidden in (
        "ci_coordinator.api",
        "ci_coordinator.app",
        "ci_coordinator.config_control",
        "ci_coordinator.execution_orchestration",
        "ci_coordinator.governance_observation",
        "ci_coordinator.integrations.github",
        "ci_coordinator.persistence",
        "ci_coordinator.repo_context",
        "ci_coordinator.runtime",
        "ci_coordinator.validation_contract",
        "ci_coordinator.workflow_authority",
        "ci_coordinator.workflow_discovery",
        "httpx2",
        "glob",
        "io",
        "open",
        "os",
        "shutil",
        "subprocess",
        "tempfile",
        "time",
    ):
        assert "python.import-boundary.target-authority-producers" in violating_rule_ids(
            producer_path,
            forbidden,
        )
    assert violating_rule_ids(integration_path, "ci_coordinator.workflow_authority") == ()
    assert "python.import-boundary.workflow-authority" in violating_rule_ids(
        "/repo/backend/src/ci_coordinator/workflow_authority/evidence.py",
        "ci_coordinator.integrations.github",
    )


@pytest.mark.parametrize(
    ("module", "admitted"),
    [
        (
            "ci_coordinator.target_authority_evidence.model.UnactivatedEvidenceBundle",
            True,
        ),
        (
            "ci_coordinator.target_authority_evidence.replay.replay_target_authority_evidence",
            True,
        ),
        ("ci_coordinator.target_authority_evidence", False),
        ("ci_coordinator.target_authority_evidence.model", False),
        (
            "ci_coordinator.target_authority_evidence.model.AdmittedTargetAuthorityEvidence",
            True,
        ),
        (
            "ci_coordinator.target_authority_evidence.codec.decode_target_authority_evidence",
            True,
        ),
        ("ci_coordinator.target_authority_evidence.codec", False),
        (
            "ci_coordinator.target_authority_evidence.codec.encode_target_authority_evidence",
            False,
        ),
        ("ci_coordinator.target_authority_evidence.replay", False),
        ("ci_coordinator.target_authority_evidence.file", False),
        ("ci_coordinator.target_authority_evidence.cli", False),
        (
            "ci_coordinator.target_authority_producers.sources.ProviderAuthoritySources",
            True,
        ),
        (
            "ci_coordinator.target_authority_producers.sources.TargetArtifactSources",
            False,
        ),
        ("ci_coordinator.target_authority_producers.sources", False),
        ("ci_coordinator.workflow_authority.evidence.WorkflowAuthorityEvidence", True),
        ("ci_coordinator.workflow_authority.evidence", False),
        ("ci_coordinator.reconciliation.contract.ReconciliationContract", True),
        ("ci_coordinator.reconciliation.subject.ReconciliationSubject", True),
        ("ci_coordinator.reconciliation.state_store.register_subject", False),
        ("ci_coordinator.reconciliation.convergence.acquire_reconciliation_claim", False),
        ("ci_coordinator.reconciliation", False),
        ("ci_coordinator.integrations.github.workflow_authority", False),
    ],
)
def test_production_admission_accepts_only_exact_pure_evidence_edges(
    module: str, admitted: bool
) -> None:
    path = "/repo/backend/src/ci_coordinator/production_admission/relation_admission.py"
    assert (
        "python.import-boundary.production-admission" not in violating_rule_ids(path, module)
    ) is admitted


def test_target_authority_evidence_separates_replay_from_file_publication() -> None:
    facade_path = "/repo/backend/src/ci_coordinator/target_authority_evidence/__init__.py"
    replay_path = "/repo/backend/src/ci_coordinator/target_authority_evidence/replay.py"
    file_path = "/repo/backend/src/ci_coordinator/target_authority_evidence/file.py"

    for admitted in (
        "ci_coordinator.target_authority_evidence.codec",
        "ci_coordinator.target_authority_evidence.file",
        "ci_coordinator.target_authority_evidence.model",
        "ci_coordinator.target_authority_evidence.replay",
    ):
        assert violating_rule_ids(facade_path, admitted) == ()
    for forbidden in ("os", "ci_coordinator.persistence"):
        assert "python.import-boundary.target-authority-evidence-facade" in (
            violating_rule_ids(facade_path, forbidden)
        )

    for admitted in (
        "ci_coordinator.target_authority_producers.codec",
        "ci_coordinator.target_authority_producers.model",
        "ci_coordinator.target_authority_producers.production",
        "ci_coordinator.target_authority_relation",
        "ci_coordinator.workflow_authority",
    ):
        assert violating_rule_ids(replay_path, admitted) == ()
    for forbidden in (
        "os",
        "pathlib",
        "ci_coordinator.persistence",
        "ci_coordinator.target_authority_evidence.cli",
        "ci_coordinator.target_authority_evidence.file",
        "ci_coordinator.target_authority_producers",
    ):
        assert "python.import-boundary.target-authority-evidence-replay" in violating_rule_ids(
            replay_path,
            forbidden,
        )

    for admitted in (
        "os",
        "pathlib.Path",
        "secrets",
        "ci_coordinator.target_authority_evidence.codec",
    ):
        assert violating_rule_ids(file_path, admitted) == ()
    for forbidden in (
        "ci_coordinator.integrations.github",
        "ci_coordinator.persistence",
        "ci_coordinator.target_authority_evidence.replay",
        "subprocess",
    ):
        assert "python.import-boundary.target-authority-evidence-publication" in (
            violating_rule_ids(file_path, forbidden)
        )


def test_pure_capabilities_reject_ambient_filesystem_authority() -> None:
    paths = (
        "/repo/backend/src/ci_coordinator/workflow_authority/evidence.py",
        "/repo/backend/src/ci_coordinator/target_authority_producers/enumeration.py",
    )

    for path in paths:
        rule_id = (
            "python.import-boundary.workflow-authority"
            if "/workflow_authority/" in path
            else "python.import-boundary.target-authority-producers"
        )
        for authority in ("glob", "io", "open", "shutil", "tempfile"):
            assert rule_id in violating_rule_ids(path, authority)


def test_ast_projection_exposes_imported_and_builtin_filesystem_authority(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    package = source_root / "workflow_authority"
    package.mkdir(parents=True)
    module_path = package / "probe.py"
    module_path.write_text(
        "import tempfile\n\ndef read(path):\n    return open(path).read()\n",
        encoding="utf-8",
    )

    imports = imported_modules(module_path, source_root)

    assert {"open", "tempfile"}.issubset(imports)
    assert all(
        "python.import-boundary.workflow-authority"
        in violating_rule_ids(str(module_path), authority)
        for authority in ("open", "tempfile")
    )


def test_repo_context_admits_only_the_exact_repository_scope_owner() -> None:
    path = "/repo/backend/src/ci_coordinator/repo_context/workflow_inventory.py"

    assert (
        violating_rule_ids(
            path,
            "ci_coordinator.config_control.contracts.RepositoryScope",
        )
        == ()
    )
    assert "python.import-boundary.repo-context" in violating_rule_ids(
        path,
        "ci_coordinator.config_control",
    )


def test_rollback_coverage_dependency_points_from_planning_to_epoch_port() -> None:
    planning_path = "/repo/backend/src/ci_coordinator/planning_core/rollback_coverage.py"
    epoch_path = "/repo/backend/src/ci_coordinator/config_epochs/rollback.py"

    assert violating_rule_ids(planning_path, "ci_coordinator.config_epochs.rollback") == ()
    assert "python.import-boundary.config-epochs" in violating_rule_ids(
        epoch_path,
        "ci_coordinator.planning_core",
    )


def test_runtime_environment_remains_production_process_environment_owner() -> None:
    environment_path = "/repo/backend/src/ci_coordinator/runtime/environment.py"
    process_environment_rule = next(
        rule
        for rule in IMPORT_RULES
        if rule.rule_id == "python.import-boundary.process-environment"
    )

    assert violating_rule_ids(environment_path, "os") == ()
    assert violating_rule_ids(environment_path, "os.getenv") == ()
    assert not process_environment_rule.applies(environment_path)


def test_offline_node_selection_does_not_grant_environment_authority_to_siblings() -> None:
    owner = "/repo/backend/src/ci_coordinator/consumer_contract_lab/node_executable.py"
    assert violating_rule_ids(owner, "os") == ()
    assert violating_rule_ids(owner, "os.environ") == ()
    for forbidden in ("os.getenv", "os.putenv", "os.unsetenv"):
        assert "python.import-boundary.consumer-tool-environment" in violating_rule_ids(
            owner,
            forbidden,
        )
    root = Path(__file__).resolve().parents[2]
    package = root / "backend/src/ci_coordinator/consumer_contract_lab"
    modules = sorted(package.rglob("*.py"))
    assert len(modules) > 1
    for module in modules:
        if module.name == "node_executable.py" and module.parent == package:
            continue
        path = f"/repo/{module.relative_to(root).as_posix()}"
        assert "python.import-boundary.process-environment" in violating_rule_ids(
            path, "os.environ"
        ), path


@pytest.mark.parametrize(
    ("rule_id", "path"),
    [
        (
            "python.import-boundary.root-package-marker",
            "/repo/backend/src/ci_coordinator/__init__.py",
        ),
        (
            "python.import-boundary.github-actions-jwks-integration",
            "/repo/backend/src/ci_coordinator/integrations/oidc_jwks.py",
        ),
        (
            "python.import-boundary.github-actions-jwks-integration",
            "/repo/backend/src/ci_coordinator/integrations/__init__.py",
        ),
        (
            "python.import-boundary.runtime-environment",
            "/repo/backend/src/ci_coordinator/runtime/environment.py",
        ),
    ],
)
def test_exact_authority_files_have_closed_import_rules(rule_id: str, path: str) -> None:
    matching = tuple(rule for rule in IMPORT_RULES if rule.rule_id == rule_id)

    assert len(matching) == 1
    assert matching[0].applies(path)


def test_secure_receipt_reader_owns_only_filesystem_os_capability() -> None:
    reader_path = "/repo/backend/src/ci_coordinator/production_admission/file.py"

    assert violating_rule_ids(reader_path, "os") == ()
    assert "python.import-boundary.process-environment" in violating_rule_ids(
        reader_path, "os.environ"
    )


def test_target_artifact_workflow_owns_only_filesystem_os_capability() -> None:
    workflow_path = "/repo/backend/src/ci_coordinator/target_artifacts/workflow.py"

    assert violating_rule_ids(workflow_path, "os") == ()
    assert "python.import-boundary.process-environment" in violating_rule_ids(
        workflow_path, "os.environ"
    )


def test_operator_ui_bundle_owns_only_filesystem_os_capability() -> None:
    bundle_path = "/repo/backend/src/ci_coordinator/api/http/operator_ui_bundle.py"

    assert violating_rule_ids(bundle_path, "os") == ()
    assert "python.import-boundary.process-environment" in violating_rule_ids(
        bundle_path, "os.environ"
    )
    assert "python.import-boundary.os-capability" in violating_rule_ids(
        "/repo/backend/src/ci_coordinator/api/http/operator_ui.py",
        "os",
    )


@pytest.mark.parametrize(
    "filename",
    ["bootstrap.py", "git_source.py", "process.py"],
)
def test_consumer_contract_lab_os_capability_is_owned_by_exact_files(
    filename: str,
) -> None:
    owner = f"/repo/backend/src/ci_coordinator/consumer_contract_lab/{filename}"

    assert violating_rule_ids(owner, "os") == ()
    assert "python.import-boundary.process-environment" in violating_rule_ids(owner, "os.environ")
    assert "python.import-boundary.os-capability" in violating_rule_ids(
        "/repo/backend/src/ci_coordinator/consumer_contract_lab/runner.py",
        "os",
    )


def test_consumer_contract_lab_has_no_provider_or_production_runtime_edge() -> None:
    lab_path = "/repo/backend/src/ci_coordinator/consumer_contract_lab/runner.py"

    for forbidden in (
        "ci_coordinator.api",
        "ci_coordinator.integrations.github",
        "ci_coordinator.persistence",
        "ci_coordinator.runtime",
    ):
        assert "python.import-boundary.consumer-contract-lab" in violating_rule_ids(
            lab_path, forbidden
        )


def test_new_authority_edges_are_directional() -> None:
    authority_path = "/repo/backend/src/ci_coordinator/production_admission/authority.py"
    issuance_path = "/repo/backend/src/ci_coordinator/plan_issuance/issuer.py"
    persistence_directory = "/repo/backend/src/ci_coordinator/persistence"
    persistence_path = f"{persistence_directory}/production_admission_repository.py"
    composition_path = "/repo/backend/src/ci_coordinator/runtime/composition.py"

    assert violating_rule_ids(authority_path, "ci_coordinator.verification_core") == ()
    assert "python.import-boundary.production-admission" in violating_rule_ids(
        authority_path, "ci_coordinator.persistence"
    )
    assert violating_rule_ids(issuance_path, "ci_coordinator.production_admission") == ()
    assert violating_rule_ids(persistence_path, "ci_coordinator.production_admission") == ()
    assert violating_rule_ids(composition_path, "ci_coordinator.production_admission") == ()


def test_provider_inventory_edge_terminates_at_composition_root() -> None:
    composition_path = "/repo/backend/src/ci_coordinator/runtime/composition.py"
    provider_path = "/repo/backend/src/ci_coordinator/provider_inventory/service.py"

    assert violating_rule_ids(composition_path, "ci_coordinator.provider_inventory") == ()
    assert "python.import-boundary.provider-inventory" in violating_rule_ids(
        provider_path, "ci_coordinator.runtime"
    )


def test_workflow_discovery_edges_are_inward_and_composed_only_at_runtime_root() -> None:
    discovery_path = "/repo/backend/src/ci_coordinator/workflow_discovery/service.py"
    integration_path = "/repo/backend/src/ci_coordinator/integrations/github/workflow_discovery.py"
    composition_path = "/repo/backend/src/ci_coordinator/runtime/composition.py"

    assert violating_rule_ids(discovery_path, "ci_coordinator.config_control") == ()
    assert "python.import-boundary.workflow-discovery" in violating_rule_ids(
        discovery_path, "ci_coordinator.integrations"
    )
    assert violating_rule_ids(integration_path, "ci_coordinator.workflow_discovery") == ()
    assert violating_rule_ids(composition_path, "ci_coordinator.workflow_discovery") == ()


def test_governance_observation_edges_are_inward_and_composed_only_at_runtime_root() -> None:
    observation_path = "/repo/backend/src/ci_coordinator/governance_observation/service.py"
    integration_path = (
        "/repo/backend/src/ci_coordinator/integrations/github/governance_observation.py"
    )
    composition_path = "/repo/backend/src/ci_coordinator/runtime/composition.py"

    assert violating_rule_ids(observation_path, "ci_coordinator.config_control") == ()
    assert "python.import-boundary.governance-observation" in violating_rule_ids(
        observation_path, "ci_coordinator.integrations"
    )
    assert violating_rule_ids(integration_path, "ci_coordinator.governance_observation") == ()
    assert violating_rule_ids(composition_path, "ci_coordinator.governance_observation") == ()


def test_governance_baseline_edges_preserve_domain_and_capability_boundaries() -> None:
    domain_path = "/repo/backend/src/ci_coordinator/governance_baseline/model.py"
    persistence_path = (
        "/repo/backend/src/ci_coordinator/persistence/governance_baseline_repository.py"
    )
    api_path = "/repo/backend/src/ci_coordinator/api/http/app.py"
    dependency_path = "/repo/backend/src/ci_coordinator/api/http/dependencies.py"
    contract_path = "/repo/backend/src/ci_coordinator/api/http/governance_baseline_contracts.py"
    route_path = "/repo/backend/src/ci_coordinator/api/http/routers/governance_baselines.py"
    composition_path = "/repo/backend/src/ci_coordinator/runtime/composition.py"

    assert violating_rule_ids(domain_path, "ci_coordinator.governance_observation") == ()
    assert "python.import-boundary.governance-baseline" in violating_rule_ids(
        domain_path, "ci_coordinator.persistence"
    )
    assert violating_rule_ids(persistence_path, "ci_coordinator.governance_baseline") == ()
    for admitted in (
        "ci_coordinator.governance_baseline",
        "ci_coordinator.app.governance_baseline",
    ):
        assert violating_rule_ids(dependency_path, admitted) == ()
        assert violating_rule_ids(contract_path, admitted) == ()
        assert violating_rule_ids(route_path, admitted) == ()
        assert "python.import-boundary.api-governance-baseline" in violating_rule_ids(
            api_path, admitted
        )
    assert violating_rule_ids(composition_path, "ci_coordinator.governance_baseline") == ()
    assert not hasattr(app_facade, "GovernanceBaselineService")
    assert not hasattr(persistence_facade, "PostgresGovernanceBaselineUnitOfWork")


def test_governance_comparison_edges_preserve_pure_domain_boundary() -> None:
    comparison_path = "/repo/backend/src/ci_coordinator/governance_comparison/comparison.py"

    for admitted in (
        "ci_coordinator.governance_baseline",
        "ci_coordinator.governance_comparison",
        "ci_coordinator.governance_observation",
        "ci_coordinator.kernel",
    ):
        assert violating_rule_ids(comparison_path, admitted) == ()
    for forbidden in (
        "ci_coordinator.app",
        "ci_coordinator.integrations",
        "ci_coordinator.persistence",
    ):
        assert "python.import-boundary.governance-comparison" in violating_rule_ids(
            comparison_path, forbidden
        )


def test_every_governance_context_has_an_exact_import_boundary_rule() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    contexts_root = repository_root / "backend" / "src" / "ci_coordinator"

    assert governance_rule_coverage_violations(contexts_root) == ()


def test_governance_rule_coverage_ignores_empty_directories_and_rejects_namespace_packages(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    for context_name in (
        "governance_baseline",
        "governance_comparison",
        "governance_observation",
    ):
        package = source_root / context_name
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
    (source_root / "governance_scratch").mkdir()

    assert governance_rule_coverage_violations(source_root) == ()

    new_package = source_root / "governance_scratch"
    (new_package / "module.py").write_text("", encoding="utf-8")
    assert governance_rule_coverage_violations(source_root) == (
        GovernanceRuleCoverageViolation(
            context_name="governance_scratch",
            kind="missing-rule",
            rule_id="python.import-boundary.governance-scratch",
        ),
    )


def test_governance_comparison_rejects_standard_library_dynamic_loaders(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    module_path = source_root / "governance_comparison" / "comparison.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(
        """\
import pkgutil
import pydoc
import runpy
import sys


def escape() -> tuple[object, ...]:
    return (
        pkgutil.resolve_name("ci_coordinator.persistence:connection"),
        pydoc.locate("ci_coordinator.persistence.connection"),
        runpy.run_module("ci_coordinator.persistence"),
        sys.modules["ci_coordinator.persistence"],
    )
""",
        encoding="utf-8",
    )

    imports = imported_modules(module_path, source_root)
    for authority in ("pkgutil", "pydoc", "runpy", "sys", "sys.modules"):
        assert authority in imports
        assert "python.import-boundary.governance-comparison" in violating_rule_ids(
            str(module_path),
            authority,
        )


def test_governance_comparison_rejects_reflective_builtin_import(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    module_path = source_root / "governance_comparison" / "comparison.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(
        """\
builtins_object = globals()["__builtins__"]
importer = builtins_object["__" + "import__"]
importer("ci_coordinator.persistence.connection", fromlist=["*"])
""",
        encoding="utf-8",
    )

    assert "reserved:dynamic-import" in imported_modules(module_path, source_root)
    assert "python.import-boundary.governance-comparison" in violating_rule_ids(
        str(module_path),
        "reserved:dynamic-import",
    )


@pytest.mark.parametrize(
    "source",
    (
        "from ci_coordinator.provider_inventory import __builtins__ as owner\n",
        'namespace = {}\nvalue = namespace["__" + "import__"]\n',
        'namespace = object()\nvalue = getattr(namespace, f"__import__")\n',
    ),
)
def test_dynamic_import_token_projection_is_mechanism_sensitive(
    tmp_path: Path,
    source: str,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    module_path = source_root / "governance_comparison" / "comparison.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(source, encoding="utf-8")

    assert "reserved:dynamic-import" in imported_modules(module_path, source_root)


def test_governance_rule_coverage_checks_every_actual_python_file(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    package = source_root / "governance_comparison"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "comparison.py").write_text("", encoding="utf-8")
    comparison_rule = next(
        rule
        for rule in IMPORT_RULES
        if rule.rule_id == "python.import-boundary.governance-comparison"
    )
    narrow_rule = replace(
        comparison_rule,
        applies=lambda path: path.endswith("/ci_coordinator/governance_comparison/__init__.py"),
    )

    assert governance_rule_coverage_violations(
        source_root,
        rules=(narrow_rule,),
    ) == (
        GovernanceRuleCoverageViolation(
            context_name="governance_comparison",
            kind="rule-not-applicable",
            rule_id="python.import-boundary.governance-comparison",
        ),
    )


def test_governance_rule_coverage_rejects_nested_symlink_without_following_it(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    package = source_root / "governance_comparison"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    external_package = tmp_path / "external"
    external_package.mkdir()
    external_module = external_package / "hidden.py"
    external_module.write_text("", encoding="utf-8")
    nested_symlink = package / "nested"
    nested_symlink.symlink_to(external_package, target_is_directory=True)

    inventory = inventory_python_sources(source_root)

    assert inventory.symlinks == (nested_symlink,)
    assert external_module not in inventory.files
    comparison_rule = next(
        rule
        for rule in IMPORT_RULES
        if rule.rule_id == "python.import-boundary.governance-comparison"
    )
    assert governance_rule_coverage_violations(
        source_root,
        rules=(comparison_rule,),
        inventory=inventory,
    ) == (
        GovernanceRuleCoverageViolation(
            context_name="governance_comparison",
            kind="symlinked-package",
            rule_id="python.import-boundary.governance-comparison",
        ),
    )


def test_standalone_import_boundary_rejects_any_source_symlink(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    backend_root = tmp_path / "backend"
    package = backend_root / "src" / "ci_coordinator" / "kernel"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    external_package = tmp_path / "external"
    external_package.mkdir()
    (external_package / "hidden.py").write_text("", encoding="utf-8")
    nested_symlink = package / "nested"
    nested_symlink.symlink_to(external_package, target_is_directory=True)
    witness = PythonWitness(
        backend_root=backend_root,
        environment={},
        mode="import-boundary",
        python_executable="python",
        repo_root=tmp_path,
    )

    with pytest.raises(SystemExit, match="1"):
        witness.run_import_boundary()

    output = capsys.readouterr().out
    assert "python import-boundary source inventory contains symlinks" in output
    assert "backend/src/ci_coordinator/kernel/nested" in output


def test_control_plane_identity_and_keycloak_edges_point_inward() -> None:
    identity_path = "/repo/backend/src/ci_coordinator/control_plane_identity/browser.py"
    keycloak_path = "/repo/backend/src/ci_coordinator/integrations/keycloak/client.py"
    persistence_path = (
        "/repo/backend/src/ci_coordinator/persistence/control_plane_session_repository.py"
    )
    composition_path = "/repo/backend/src/ci_coordinator/runtime/control_plane_composition.py"

    assert violating_rule_ids(identity_path, "ci_coordinator.kernel") == ()
    assert "python.import-boundary.control-plane-identity" in violating_rule_ids(
        identity_path, "ci_coordinator.integrations.keycloak"
    )
    assert violating_rule_ids(keycloak_path, "ci_coordinator.control_plane_identity") == ()
    assert "python.import-boundary.keycloak-integration" in violating_rule_ids(
        keycloak_path, "ci_coordinator.persistence"
    )
    assert violating_rule_ids(persistence_path, "ci_coordinator.control_plane_identity") == ()
    assert violating_rule_ids(composition_path, "ci_coordinator.control_plane_identity") == ()
    assert violating_rule_ids(composition_path, "ci_coordinator.integrations.keycloak") == ()


def test_proposal_review_edges_preserve_domain_and_transport_boundaries() -> None:
    review_path = "/repo/backend/src/ci_coordinator/proposal_review/model.py"
    persistence_path = "/repo/backend/src/ci_coordinator/persistence/proposal_review_repository.py"
    api_path = "/repo/backend/src/ci_coordinator/api/http/app.py"
    dependency_path = "/repo/backend/src/ci_coordinator/api/http/dependencies.py"
    review_route_path = (
        "/repo/backend/src/ci_coordinator/api/http/routers/repository_attestations.py"
    )

    assert violating_rule_ids(review_path, "ci_coordinator.config_control") == ()
    assert "python.import-boundary.proposal-review" in violating_rule_ids(
        review_path, "ci_coordinator.workflow_discovery"
    )
    assert violating_rule_ids(persistence_path, "ci_coordinator.proposal_review") == ()
    for admitted in (
        "ci_coordinator.proposal_review",
        "ci_coordinator.app.proposal_review",
    ):
        assert violating_rule_ids(dependency_path, admitted) == ()
        assert violating_rule_ids(review_route_path, admitted) == ()
        assert "python.import-boundary.api-proposal-review" in violating_rule_ids(
            api_path, admitted
        )
    for forbidden in (
        "ci_coordinator.persistence",
        "ci_coordinator.persistence.proposal_review_adapter",
        "ci_coordinator.persistence.proposal_review_unit_of_work",
    ):
        assert "python.import-boundary.api-http" in violating_rule_ids(api_path, forbidden)
    assert not hasattr(app_facade, "ProposalReviewService")
    assert not hasattr(persistence_facade, "PostgresProposalReviewUnitOfWork")
    assert not hasattr(persistence_facade, "TransactionalProposalReviewStore")


def test_rule_catalog_preserves_source_order() -> None:
    assert tuple(rule.rule_id for rule in IMPORT_RULES) == (
        "python.import-boundary.measurement-reporter",
        "python.import-boundary.process-environment",
        "python.import-boundary.consumer-tool-environment",
        "python.import-boundary.os-capability",
        "python.import-boundary.root-package-marker",
        "python.import-boundary.github-actions-jwks-integration",
        "python.import-boundary.runtime-environment",
        "python.import-boundary.kernel",
        "python.import-boundary.validation-contract",
        "python.import-boundary.identity-admission",
        "python.import-boundary.audit-replay",
        "python.import-boundary.capacity-qualification",
        "python.import-boundary.config-control",
        "python.import-boundary.config-epochs",
        "python.import-boundary.control-plane-identity",
        "python.import-boundary.repo-context",
        "python.import-boundary.planning-core",
        "python.import-boundary.target-authority-relation",
        "python.import-boundary.workflow-authority",
        "python.import-boundary.target-authority-producers",
        "python.import-boundary.target-authority-evidence-facade",
        "python.import-boundary.target-authority-evidence-replay",
        "python.import-boundary.target-authority-evidence-publication",
        "python.import-boundary.operator-controls",
        "python.import-boundary.observability",
        "python.import-boundary.ci-economics",
        "python.import-boundary.github-ingestion",
        "python.import-boundary.workflow-discovery",
        "python.import-boundary.governance-observation",
        "python.import-boundary.governance-baseline",
        "python.import-boundary.governance-comparison",
        "python.import-boundary.proposal-review",
        "python.import-boundary.github-integration",
        "python.import-boundary.keycloak-integration",
        "python.import-boundary.provider-inventory",
        "python.import-boundary.api-http",
        "python.import-boundary.api-proposal-review",
        "python.import-boundary.api-governance-baseline",
        "python.import-boundary.app-use-cases",
        "python.import-boundary.persistence",
        "python.import-boundary.reconciliation",
        "python.import-boundary.shadow-mode",
        "python.import-boundary.agent-risk-advice",
        "python.import-boundary.verification-core",
        "python.import-boundary.production-admission",
        "python.import-boundary.plan-issuance",
        "python.import-boundary.runner-capacity",
        "python.import-boundary.execution-orchestration",
        "python.import-boundary.target-artifacts",
        "python.import-boundary.consumer-contract-lab",
        "python.import-boundary.workbench-read-models",
        "python.import-boundary.runtime-settings",
        "python.import-boundary.replay-cli",
        "python.import-boundary.database-access-cli",
        "python.import-boundary.runtime-composition",
    )


def test_ast_projection_preserves_absolute_relative_and_reserved_imports(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    module_path = source_root / "package" / "service.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(
        """\
from __future__ import annotations

import os.path
from . import sibling
from ..kernel import canonical
from importlib import resources


def load() -> tuple[object, object]:
    return __import__(\"dynamic\"), eval(\"1\")
""",
        encoding="utf-8",
    )

    assert imported_modules(module_path, source_root) == (
        "__future__.annotations",
        "ci_coordinator.kernel.canonical",
        "ci_coordinator.package.sibling",
        "importlib.resources",
        "os.path",
        "reserved:dynamic-execution",
        "reserved:dynamic-import",
    )


def test_from_import_projects_only_the_acquired_allowed_authority(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    module_path = source_root / "config_control" / "resources.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text("from importlib import resources\n", encoding="utf-8")

    imports = imported_modules(module_path, source_root)

    assert imports == ("importlib.resources",)
    assert violating_rule_ids(str(module_path), imports[0]) == ()


def test_ast_projection_resolves_aliased_process_environment_access(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    module_path = source_root / "package" / "service.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(
        """\
import os as operating_system
from os import environ as process_environment
from os import getenv as read_environment


def load() -> tuple[object, object, object]:
    return (
        operating_system.environ,
        process_environment,
        read_environment("NAME"),
    )
""",
        encoding="utf-8",
    )

    imports = imported_modules(module_path, source_root)
    assert "os.environ" in imports
    assert "os.getenv" in imports


def test_ast_projection_rejects_bare_sensitive_root_escape(tmp_path: Path) -> None:
    source_root = tmp_path / "ci_coordinator"
    module_path = source_root / "package" / "service.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(
        "import os\n\nprocess_environment_owner = os\n",
        encoding="utf-8",
    )

    imports = imported_modules(module_path, source_root)

    assert {"os.environ", "os.getenv", "os.putenv", "os.unsetenv"} <= set(imports)


@pytest.mark.parametrize(
    ("body", "projects_environment"),
    (
        (
            "def load(operating_system: object):\n    return operating_system.environ\n",
            False,
        ),
        ("def load():\n    return operating_system.environ\n", True),
        ("class Owner:\n    environment = operating_system.environ\n", True),
        (
            "values = [operating_system.environ for operating_system in sources]\n",
            False,
        ),
    ),
)
def test_ast_projection_respects_lexical_import_provenance(
    tmp_path: Path,
    body: str,
    projects_environment: bool,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    module_path = source_root / "package" / "service.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(
        f"import os as operating_system\n\n{body}",
        encoding="utf-8",
    )

    imports = imported_modules(module_path, source_root)

    assert ("os.environ" in imports) is projects_environment


@pytest.mark.parametrize(
    ("body", "projects_modules"),
    (
        ("def inspect():\n    return runtime.modules\n", True),
        (
            (
                "def inspect():\n"
                "    try:\n"
                "        raise RuntimeError\n"
                "    except Exception as runtime:\n"
                "        return runtime.modules\n"
            ),
            False,
        ),
        (
            (
                "def inspect(value: object):\n"
                "    match value:\n"
                "        case runtime:\n"
                "            return runtime.modules\n"
            ),
            False,
        ),
        ("def inspect[runtime](value: runtime):\n    return runtime.modules\n", False),
        ("class Owner[runtime]:\n    value = runtime.modules\n", False),
        (
            "class Owner[runtime]:\n    def inspect(self):\n        return runtime.modules\n",
            False,
        ),
        ("class Owner[runtime]:\n    inspect = lambda self: runtime.modules\n", False),
        (
            "class Owner[runtime]:\n    values = [runtime.modules for _ in range(1)]\n",
            False,
        ),
        (
            (
                "class Owner[runtime]:\n"
                "    class Nested:\n"
                "        def inspect(self):\n"
                "            return runtime.modules\n"
            ),
            False,
        ),
        (
            (
                "class Owner:\n"
                "    import os as runtime\n"
                "    def inspect(self):\n"
                "        return runtime.modules\n"
            ),
            True,
        ),
        ("type Owner[runtime] = runtime.modules\n", False),
        (
            "def inspect[runtime](value=runtime.modules):\n    return value\n",
            True,
        ),
        (
            (
                "def inspect(values: list[object]):\n"
                "    [(runtime := value) for value in values]\n"
                "    return runtime.modules\n"
            ),
            False,
        ),
    ),
)
def test_ast_projection_uses_cpython_lexical_binders(
    tmp_path: Path,
    body: str,
    projects_modules: bool,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    module_path = source_root / "package" / "service.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(f"import sys as runtime\n\n{body}", encoding="utf-8")

    imports = imported_modules(module_path, source_root)

    assert ("sys.modules" in imports) is projects_modules


@pytest.mark.parametrize(
    ("body", "projects_modules"),
    (
        (
            (
                "class Owner:\n"
                "    import sys as runtime\n"
                "    def inspect(self):\n"
                "        return runtime.modules\n"
            ),
            False,
        ),
        (
            (
                "class Outer:\n"
                "    import sys as runtime\n"
                "    class Inner:\n"
                "        def inspect(self):\n"
                "            return runtime.modules\n"
            ),
            False,
        ),
        (
            (
                "def build():\n"
                "    import sys as runtime\n"
                "    class Inner:\n"
                "        def inspect(self):\n"
                "            return runtime.modules\n"
                "    return Inner\n"
            ),
            True,
        ),
        (
            (
                "class Outer:\n"
                "    import sys as runtime\n"
                "    class Inner(runtime.modules.__class__):\n"
                "        pass\n"
            ),
            True,
        ),
    ),
)
def test_class_scope_preserves_only_cpython_parent_authorities(
    tmp_path: Path,
    body: str,
    projects_modules: bool,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    module_path = source_root / "package" / "service.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(
        f"class Safe:\n    modules = object()\n\nruntime = Safe()\n\n{body}",
        encoding="utf-8",
    )

    imports = imported_modules(module_path, source_root)

    assert (
        any(
            authority == "sys.modules" or authority.startswith("sys.modules.")
            for authority in imports
        )
        is projects_modules
    )


@pytest.mark.parametrize(
    ("source", "projects_modules"),
    (
        (
            (
                "class Safe:\n"
                "    modules = object()\n"
                "runtime = Safe()\n"
                "class Outer:\n"
                "    global runtime\n"
                "    import sys as runtime\n"
                "    class Inner:\n"
                "        observed = runtime.modules\n"
            ),
            True,
        ),
        (
            (
                "def build():\n"
                "    runtime = object()\n"
                "    class Outer:\n"
                "        nonlocal runtime\n"
                "        import sys as runtime\n"
                "        class Inner:\n"
                "            observed = runtime.modules\n"
            ),
            True,
        ),
        (
            (
                "class Safe:\n"
                "    modules = object()\n"
                "runtime = Safe()\n"
                "def build():\n"
                "    runtime = Safe()\n"
                "    class Outer:\n"
                "        global runtime\n"
                "        import sys as runtime\n"
                "        class Inner:\n"
                "            observed = runtime.modules\n"
            ),
            False,
        ),
        (
            (
                "import sys as runtime\n"
                "def outer():\n"
                "    runtime = object()\n"
                "    def inner():\n"
                "        global runtime\n"
                "        return runtime.modules\n"
            ),
            True,
        ),
        (
            (
                "import sys as runtime\n"
                "def outer():\n"
                "    runtime = object()\n"
                "    def selector():\n"
                "        global runtime\n"
                "        def reader():\n"
                "            return runtime.modules\n"
            ),
            True,
        ),
        (
            (
                "class Safe:\n"
                "    modules = object()\n"
                "runtime = Safe()\n"
                "def outer():\n"
                "    import sys as runtime\n"
                "    def inner():\n"
                "        global runtime\n"
                "        return runtime.modules\n"
            ),
            False,
        ),
        (
            (
                "def outer():\n"
                "    runtime = object()\n"
                "    def setter():\n"
                "        nonlocal runtime\n"
                "        import sys as runtime\n"
                "    def reader():\n"
                "        return runtime.modules\n"
            ),
            True,
        ),
    ),
)
def test_ast_projection_respects_cpython_binding_owners(
    tmp_path: Path,
    source: str,
    projects_modules: bool,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    module_path = source_root / "package" / "service.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(source, encoding="utf-8")

    imports = imported_modules(module_path, source_root)

    assert ("sys.modules" in imports) is projects_modules


@pytest.mark.parametrize(
    ("body", "projects_modules"),
    (
        (
            "def inspect[runtime]():\n    global runtime\n    return runtime.modules\n",
            True,
        ),
        (
            "def inspect[runtime]():\n    return runtime.modules\n",
            False,
        ),
        (
            (
                "def inspect[runtime]():\n"
                "    global runtime\n"
                "    def nested():\n"
                "        return runtime.modules\n"
            ),
            True,
        ),
        (
            "class Owner[runtime]:\n    global runtime\n    observed = runtime.modules\n",
            True,
        ),
        (
            "class Owner[runtime]:\n    observed = runtime.modules\n",
            False,
        ),
        (
            (
                "class Owner[runtime]:\n"
                "    global runtime\n"
                "    def inspect(self):\n"
                "        return runtime.modules\n"
            ),
            False,
        ),
    ),
)
def test_generic_parameter_and_global_precedence_matches_cpython(
    tmp_path: Path,
    body: str,
    projects_modules: bool,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    module_path = source_root / "package" / "service.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(f"import sys as runtime\n\n{body}", encoding="utf-8")

    imports = imported_modules(module_path, source_root)

    assert ("sys.modules" in imports) is projects_modules


def test_postponed_variable_annotation_does_not_require_a_lambda_scope(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    module_path = source_root / "package" / "service.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(
        "from __future__ import annotations\n"
        "import sys as runtime\n\n"
        "value: (lambda: runtime.modules)\n",
        encoding="utf-8",
    )

    assert "sys.modules" not in imported_modules(module_path, source_root)


@pytest.mark.parametrize(
    ("annotation", "projects_modules"),
    (
        ("lambda: runtime.modules", False),
        ("lambda value=runtime.modules: value", True),
        ("[runtime.modules for _ in range(1)]", True),
    ),
)
def test_postponed_annotation_projection_distinguishes_latent_lambda_body(
    tmp_path: Path,
    annotation: str,
    projects_modules: bool,
) -> None:
    source_root = tmp_path / "ci_coordinator"
    module_path = source_root / "package" / "service.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(
        "from __future__ import annotations\n"
        "import sys as runtime\n\n"
        f"def inspect(value: ({annotation})):\n"
        "    return value\n",
        encoding="utf-8",
    )

    imports = imported_modules(module_path, source_root)

    assert ("sys.modules" in imports) is projects_modules


def test_symtable_binder_oracle_fails_when_local_symbols_are_removed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        authority_scanner,
        "_local_symbol_names",
        lambda _symbols: frozenset(),
    )
    source_root = tmp_path / "ci_coordinator"
    module_path = source_root / "package" / "service.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(
        "import sys as runtime\n\n"
        "def inspect():\n"
        "    try:\n"
        "        raise RuntimeError\n"
        "    except Exception as runtime:\n"
        "        return runtime.modules\n",
        encoding="utf-8",
    )

    assert "sys.modules" in imported_modules(module_path, source_root)


def test_generic_binder_oracle_fails_when_ast_parameter_masks_are_removed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        authority_scanner,
        "_type_parameter_names",
        lambda _parameters: frozenset(),
    )
    source_root = tmp_path / "ci_coordinator"
    module_path = source_root / "package" / "service.py"
    module_path.parent.mkdir(parents=True)
    module_path.write_text(
        "import sys as runtime\n\n"
        "class Owner[runtime]:\n"
        "    def inspect(self):\n"
        "        return runtime.modules\n",
        encoding="utf-8",
    )

    assert "sys.modules" in imported_modules(module_path, source_root)

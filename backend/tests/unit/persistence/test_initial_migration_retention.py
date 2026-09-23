from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from ci_coordinator.persistence.compatibility_contracts import (
    CapabilityDeclaration,
    build_declaration,
    declaration_hash,
)
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.schema_capabilities import CI_ECONOMICS_EVIDENCE

_MIGRATION_DIRECTORY = Path(__file__).parents[3] / "alembic" / "versions"
_MIGRATION_PATH = _MIGRATION_DIRECTORY / "20260716_0001_initial_schema.py"
_REGISTRATION_MIGRATION_PATH = (
    _MIGRATION_DIRECTORY / "20260901_0002_config_epoch_registration_operations.py"
)
_ECONOMICS_MIGRATION_PATH = _MIGRATION_DIRECTORY / "20260904_0003_ci_economics_evidence.py"
_RETAINED_DATA_TABLES = (
    "active_config_epochs",
    "audit_events",
    "config_epoch_activations",
    "config_epochs",
    "control_plane_logout_replays",
    "control_plane_sessions",
    "governance_baseline_operations",
    "governance_baselines",
    "issued_plan_envelopes",
    "operator_overrides",
    "production_admission_authorities",
    "production_admission_scope_bindings",
    "reconciliation_observations",
    "reconciliation_results",
    "reconciliation_subjects",
    "repository_attestation_transactions",
    "shadow_evidence",
    "webhook_deliveries",
    "workflow_proposal_reviews",
)
_CAPABILITY_IDS = (
    "audit-ledger/v1",
    "config-epoch-lifecycle/v1",
    "control-plane-identity-state/v1",
    "database-compatibility-protocol/v1",
    "governance-baseline-state/v1",
    "operator-override-state/v1",
    "proposal-review-registration/v1",
    "runtime-ingress-issuance-state/v1",
    "runtime-shadow-reconciliation-state/v1",
    "webhook-body-identity/v1",
)


def test_published_bootstrap_is_the_root_of_the_forward_migration_chain() -> None:
    migration = _load_migration()

    assert tuple(path.name for path in sorted(_MIGRATION_DIRECTORY.glob("*.py"))) == (
        "20260716_0001_initial_schema.py",
        "20260901_0002_config_epoch_registration_operations.py",
        "20260904_0003_ci_economics_evidence.py",
        "20260906_0004_retire_pre_cutover_capabilities.py",
        "20260906_0005_production_generation_cutover.py",
        "20260907_0006_retire_economics_evidence_v1.py",
        "20260908_0007_source_aware_economics.py",
        "20260909_0008_retire_economics_evidence_v2.py",
        "20260909_0009_persistent_economics_budgets.py",
        "20260910_0010_repository_observation.py",
        "20260912_0011_actions_history.py",
        "20260913_0012_administrator_activity.py",
        "20260913_0013_analytics_purpose.py",
        "20260915_0014_retire_economics_evidence_v3.py",
        "20260915_0015_total_collection_state.py",
    )
    assert migration.revision == "20260716_0001"
    assert migration.down_revision is None
    assert tuple(capability_id for capability_id, _digest in migration._CAPABILITIES) == (
        _CAPABILITY_IDS
    )


def test_bootstrap_declaration_hash_binds_the_complete_capability_set() -> None:
    migration = _load_migration()
    declaration = build_declaration(
        load_bundled_profile(),
        generation=1,
        revision_id=migration.revision,
        parent_revision_id=None,
        transition_kind="bootstrap",
        capabilities=tuple(
            CapabilityDeclaration(capability_id, descriptor_hash)
            for capability_id, descriptor_hash in migration._CAPABILITIES
        ),
    )

    assert declaration.capabilities == tuple(
        sorted(declaration.capabilities, key=lambda item: item.capability_id.encode("utf-8"))
    )
    assert declaration.declaration_hash == migration._DECLARATION_HASH


def test_registration_migration_is_one_exact_additive_successor() -> None:
    migration = _load_migration(
        _REGISTRATION_MIGRATION_PATH,
        "config_epoch_registration_migration",
    )

    assert migration.revision == "20260901_0002"
    assert migration.down_revision == "20260716_0001"
    assert migration._PREDECESSOR.revision_id == migration.down_revision
    assert migration._SUCCESSOR.parent_revision_id == migration.down_revision
    assert migration._SUCCESSOR.generation == migration._PREDECESSOR.generation + 1
    assert tuple(item.capability_id for item in migration._SUCCESSOR.capabilities) == tuple(
        sorted((*_CAPABILITY_IDS, "config-epoch-registration-operations/v1"))
    )


def test_ci_economics_migration_is_one_exact_additive_successor() -> None:
    registration = _load_migration(
        _REGISTRATION_MIGRATION_PATH,
        "config_epoch_registration_migration_for_economics",
    )
    migration = _load_migration(
        _ECONOMICS_MIGRATION_PATH,
        "ci_economics_migration",
    )

    assert migration.revision == "20260904_0003"
    assert migration.down_revision == registration.revision
    assert migration._PREDECESSOR == registration._SUCCESSOR
    assert CI_ECONOMICS_EVIDENCE.declaration() == migration._ECONOMICS_CAPABILITY
    assert migration._SUCCESSOR.parent_revision_id == migration.down_revision
    assert migration._SUCCESSOR.generation == migration._PREDECESSOR.generation + 1
    assert migration._SUCCESSOR.declaration_hash == declaration_hash(
        generation=migration._SUCCESSOR.generation,
        lineage_id=migration._SUCCESSOR.lineage_id,
        revision_id=migration._SUCCESSOR.revision_id,
        parent_revision_id=migration._SUCCESSOR.parent_revision_id,
        transition_kind=migration._SUCCESSOR.transition_kind,
        protocol_version=migration._SUCCESSOR.protocol_version,
        capabilities=migration._SUCCESSOR.capabilities,
    )
    assert tuple(item.capability_id for item in migration._SUCCESSOR.capabilities) == tuple(
        sorted(
            (
                *_CAPABILITY_IDS,
                "ci-economics-evidence/v1",
                "config-epoch-registration-operations/v1",
            )
        )
    )


def test_retained_data_catalog_is_exact() -> None:
    migration = _load_migration()

    assert migration._RETAINED_DATA_TABLES == _RETAINED_DATA_TABLES


@pytest.mark.parametrize("retained_table", _RETAINED_DATA_TABLES)
def test_each_retained_table_blocks_baseline_downgrade(
    monkeypatch: pytest.MonkeyPatch,
    retained_table: str,
) -> None:
    migration = _load_migration()
    bind = _RetainedTableBind(retained_table)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(migration, "op", SimpleNamespace(get_bind=lambda: bind))

    with pytest.raises(RuntimeError, match="refusing to remove retained CI Coordinator data"):
        migration._assert_no_retained_product_data()


class _RetainedTableBind:
    def __init__(self, retained_table: str) -> None:
        self._retained_table = retained_table

    def scalar(self, statement: object) -> bool:
        sql = str(statement)
        if "FROM ci_coordinator.audit_ledger_head" in sql:
            return True
        return f"FROM ci_coordinator.{self._retained_table})" in sql


def _load_migration(
    path: Path = _MIGRATION_PATH,
    module_name: str = "initial_schema_retention",
) -> ModuleType:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AssertionError("baseline migration could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

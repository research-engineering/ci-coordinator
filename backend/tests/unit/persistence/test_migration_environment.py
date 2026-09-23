from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

_ENVIRONMENT_PATH = Path(__file__).parents[3] / "alembic" / "env.py"
_BASELINE_MIGRATION_PATH = (
    Path(__file__).parents[3] / "alembic" / "versions" / "20260716_0001_initial_schema.py"
)


def test_migration_environment_requires_one_online_outer_transaction_and_fence() -> None:
    source = _ENVIRONMENT_PATH.read_text(encoding="utf-8")
    module = ast.parse(source)
    functions = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
    offline = functions["run_migrations_offline"]
    online = functions["run_migrations_online"]

    assert len(offline.body) == 1
    assert isinstance(offline.body[0], ast.Raise)
    assert isinstance(offline.body[0].exc, ast.Call)
    assert "online transactional execution" in ast.unparse(offline.body[0].exc)

    configure_calls = [
        node
        for node in ast.walk(online)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "configure"
    ]
    assert len(configure_calls) == 1
    keywords = {keyword.arg: keyword.value for keyword in configure_calls[0].keywords}
    assert isinstance(keywords["transaction_per_migration"], ast.Constant)
    assert keywords["transaction_per_migration"].value is False
    assert isinstance(keywords["transactional_ddl"], ast.Constant)
    assert keywords["transactional_ddl"].value is True
    assert isinstance(keywords["version_table_schema"], ast.Constant)
    assert keywords["version_table_schema"].value == "public"

    outer_transaction = source.index("with connection.begin():")
    exclusive_fence = source.index("SELECT pg_catalog.pg_advisory_xact_lock")
    configure = source.index("context.configure(")
    assert outer_transaction < exclusive_fence < configure
    assert "autocommit_block" not in source
    assert "CREATE INDEX CONCURRENTLY" not in source
    assert "DROP INDEX CONCURRENTLY" not in source


def test_baseline_publication_cannot_run_before_attestation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    migration = _load_baseline_migration()
    for helper_name in (
        "_create_audit_ledger",
        "_create_compatibility_protocol",
        "_create_config_epoch_lifecycle",
        "_create_proposal_review_registration",
        "_create_control_plane_identity_state",
        "_create_runtime_state",
        "_create_shadow_reconciliation_state",
        "_create_operator_override_state",
        "_create_governance_baseline_state",
        "_install_immutability_guards",
        "_configure_extended_storage",
        "_revoke_public_access",
        "_seed_pristine_audit_head",
        "_assert_prepublication_state",
    ):
        monkeypatch.setattr(migration, helper_name, lambda: None)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(migration, "op", SimpleNamespace(execute=lambda _statement: None))

    published = False

    def reject_attestation() -> None:
        raise RuntimeError("attestation rejected")

    def publish() -> None:
        nonlocal published
        published = True

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(migration, "_attest_baseline_catalog_and_privileges", reject_attestation)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(migration, "_publish_baseline_declaration", publish)

    with pytest.raises(RuntimeError, match="attestation rejected"):
        migration.upgrade()
    assert not published


def _load_baseline_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "initial_schema_migration", _BASELINE_MIGRATION_PATH
    )
    if spec is None or spec.loader is None:
        raise AssertionError("baseline migration could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

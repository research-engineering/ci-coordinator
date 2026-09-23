from __future__ import annotations

from pathlib import Path

import pytest

from ci_coordinator.runtime_settings import (
    INVENTORY_ID,
    INVENTORY_SCHEMA_VERSION,
    CallerInventoryRejection,
    admit_caller_inventory,
    load_bundled_caller_inventory,
    parse_caller_inventory,
)


def test_caller_inventory_rejects_duplicate_targets_and_unclassified_records() -> None:
    duplicate_target = {
        "schemaVersion": INVENTORY_SCHEMA_VERSION,
        "inventoryId": INVENTORY_ID,
        "callers": [_caller("one", "Dockerfile"), _caller("two", "Dockerfile")],
    }
    unclassified = {
        "schemaVersion": INVENTORY_SCHEMA_VERSION,
        "inventoryId": INVENTORY_ID,
        "callers": [{"callerId": "one"}],
    }

    assert admit_caller_inventory(duplicate_target) == CallerInventoryRejection(
        "duplicate_caller_target", "callers"
    )
    assert admit_caller_inventory(unclassified) == CallerInventoryRejection(
        "unclassified_caller", "callers[0]"
    )


def test_caller_inventory_accepts_several_callers_from_one_artifact() -> None:
    result = admit_caller_inventory(
        {
            "schemaVersion": INVENTORY_SCHEMA_VERSION,
            "inventoryId": INVENTORY_ID,
            "callers": [
                _caller("container", "Dockerfile"),
                {
                    **_caller("health-check", "Dockerfile"),
                    "currentTarget": "python -c health",
                },
            ],
        }
    )

    assert not isinstance(result, CallerInventoryRejection)
    assert len(result.records) == 2


def test_bundled_inventory_matches_declared_runtime_authority_targets() -> None:
    inventory = load_bundled_caller_inventory()
    records = {record.caller_id: record for record in inventory.records}
    dockerfile = _repo_path("Dockerfile").read_text(encoding="utf-8")
    assert {caller_id: record.current_target for caller_id, record in records.items()} == {
        "container.healthcheck": '["python", "-m", "ci_coordinator.runtime.healthcheck"]',
        "container.command": "python -m ci_coordinator.runtime",
        "python.replay.console": ("ci-coordinator-audit-replay = ci_coordinator.replay_cli:main"),
        "python.database-access.console": (
            "ci-coordinator-database-access = ci_coordinator.database_access_cli:main"
        ),
        "python.target-artifacts.console": (
            "ci-coordinator-target-artifacts = ci_coordinator.target_artifacts.cli:main"
        ),
        "python.runtime.module": "python -m ci_coordinator.runtime",
    }
    assert f"CMD {records['container.healthcheck'].current_target}" in dockerfile
    assert 'CMD ["python", "-m", "ci_coordinator.runtime"]' in dockerfile


def test_caller_inventory_rejects_duplicate_json_keys_before_admission() -> None:
    raw_inventory = (
        b'{"schemaVersion":"ci-coordinator-runtime-caller-inventory/v1",'
        b'"inventoryId":"ci-coordinator/runtime-callers/v1",'
        b'"callers":[],"callers":[]}'
    )

    with pytest.raises(ValueError, match="duplicate-free"):
        parse_caller_inventory(raw_inventory)


def _caller(caller_id: str, path: str) -> dict[str, object]:
    return {
        "callerId": caller_id,
        "path": path,
        "kind": "container",
        "currentTarget": "python -m ci_coordinator.runtime",
    }


def _repo_path(name: str) -> Path:
    return Path(__file__).parents[4] / name

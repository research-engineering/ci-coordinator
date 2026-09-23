from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import timedelta, timezone
from typing import cast

import pytest

from ci_coordinator.ci_economics.observation import ObservationConfiguration
from ci_coordinator.ci_economics.observation_commands import ConfigureObservation
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

from .observation_factories import NOW, SCOPE, observation


@pytest.mark.parametrize("ids", [None, (10,), (20, 10), tuple(range(1, 33))])
@pytest.mark.parametrize("days", [0, 1, 6])
def test_valid_configuration_preserves_population_and_canonicalizes_order(
    ids: tuple[int, ...] | None, days: int
) -> None:
    configuration = ObservationConfiguration(True, ids, days)
    assert configuration.workflow_ids == (None if ids is None else tuple(sorted(ids)))
    assert configuration.selects(10) == (ids is None or 10 in ids)
    assert configuration.selects(99) == (ids is None)
    assert replace(configuration) == configuration
    assert configuration.canonical_mapping() == {
        "enabled": True,
        "selector": {
            "kind": "all" if ids is None else "selected",
            "workflowIds": None if ids is None else sorted(ids),
        },
        "backfillDays": days,
    }


@pytest.mark.parametrize(
    "ids",
    [
        (),
        (1, 1),
        (True,),
        (0,),
        (-1,),
        (1.0,),
        ("1",),
        (MAX_SAFE_JSON_INTEGER + 1,),
        tuple(range(1, 34)),
    ],
)
def test_invalid_selector_cannot_become_all(ids: object) -> None:
    with pytest.raises(ValueError):
        ObservationConfiguration(True, cast(tuple[int, ...], ids), 1)


@pytest.mark.parametrize("ids", [[], {1}, "all", False])
def test_selector_rejects_non_tuple_containers(ids: object) -> None:
    with pytest.raises(TypeError):
        ObservationConfiguration(True, cast(tuple[int, ...], ids), 1)


@pytest.mark.parametrize("value", [None, False, 1.0, "1", -1, 7])
def test_backfill_bounds_are_not_coerced(value: object) -> None:
    with pytest.raises(ValueError):
        ObservationConfiguration(True, None, cast(int, value))


@pytest.mark.parametrize("value", [None, 0, 1, "true"])
def test_enabled_is_an_exact_boolean(value: object) -> None:
    with pytest.raises(TypeError):
        ObservationConfiguration(cast(bool, value), None, 1)


@pytest.mark.parametrize("value", [True, 0, -1, 1.0, "1", MAX_SAFE_JSON_INTEGER + 1])
def test_runtime_selection_rejects_invalid_identity(value: object) -> None:
    with pytest.raises(ValueError):
        observation().configuration.selects(cast(int, value))


def test_disabled_configuration_keeps_its_selector_without_authorizing_execution() -> None:
    selected = ObservationConfiguration(False, (20, 10), 0)
    assert selected.selects(10)
    assert not selected.enabled
    assert selected.selector_digest == ObservationConfiguration(True, (10, 20), 6).selector_digest
    assert selected.selector_digest != ObservationConfiguration(True, None, 6).selector_digest


def test_snapshot_normalizes_timezone_without_changing_instant_or_digest() -> None:
    snapshot = observation()
    shifted = replace(snapshot, configured_at=NOW.astimezone(timezone(timedelta(hours=2))))
    assert shifted == snapshot
    assert shifted.snapshot_digest == snapshot.snapshot_digest
    expected = {
        "schemaVersion": "ci-economics-observation/v1",
        "installationId": 101,
        "repositoryId": 202,
        "revision": 3,
        "configuration": {
            "enabled": True,
            "selector": {"kind": "selected", "workflowIds": [10, 20]},
            "backfillDays": 1,
        },
        "configuredAt": "2026-09-09T12:00:00+00:00",
    }
    assert snapshot.canonical_mapping() == expected
    independent_bytes = json.dumps(expected, sort_keys=True, separators=(",", ":")).encode()
    assert snapshot.snapshot_digest == hashlib.sha256(independent_bytes).hexdigest()


def _command() -> ConfigureObservation:
    return ConfigureObservation(
        SCOPE, 3, observation().configuration, "operation-1", "keycloak:alice"
    )


@pytest.mark.parametrize(
    "operand",
    [
        "installation",
        "repository",
        "revision",
        "operation",
        "actor",
        "enabled",
        "selector",
        "backfill",
    ],
)
def test_command_digest_binds_each_semantic_operand(operand: str) -> None:
    command = _command()
    configuration = command.configuration
    changes = {
        "installation": replace(command, scope=RepositoryScope(102, 202)),
        "repository": replace(command, scope=RepositoryScope(101, 203)),
        "revision": replace(command, expected_revision=4),
        "operation": replace(command, operation_id="operation-2"),
        "actor": replace(command, actor="keycloak:bob"),
        "enabled": replace(command, configuration=replace(configuration, enabled=False)),
        "selector": replace(command, configuration=replace(configuration, workflow_ids=None)),
        "backfill": replace(command, configuration=replace(configuration, backfill_days=0)),
    }
    assert changes[operand].command_digest != command.command_digest
    assert command.audit_key == "ci-economics-observation:101:202:operation-1"
    assert command.next_snapshot(NOW).revision == 4


def test_equivalent_selector_order_has_one_command_identity() -> None:
    command = _command()
    assert (
        replace(command, configuration=ObservationConfiguration(True, (20, 10), 1)).command_digest
        == command.command_digest
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_revision", -1),
        ("expected_revision", True),
        ("expected_revision", 1.0),
        ("expected_revision", MAX_SAFE_JSON_INTEGER),
        ("operation_id", ""),
        ("operation_id", "a" * 129),
        ("operation_id", "has space"),
        ("operation_id", "\u00e9"),
        ("operation_id", None),
        ("actor", ""),
        ("actor", "a" * 513),
        ("actor", "a\x00b"),
        ("actor", "\ud800"),
        ("actor", None),
    ],
)
def test_invalid_command_identity_is_rejected(field: str, value: object) -> None:
    command = _command()
    with pytest.raises(ValueError):
        ConfigureObservation(
            command.scope,
            cast(int, value) if field == "expected_revision" else command.expected_revision,
            command.configuration,
            cast(str, value) if field == "operation_id" else command.operation_id,
            cast(str, value) if field == "actor" else command.actor,
        )


@pytest.mark.parametrize("revision", [0, MAX_SAFE_JSON_INTEGER - 1])
def test_command_revision_boundaries_permit_one_increment(revision: int) -> None:
    command = replace(_command(), expected_revision=revision)
    assert command.next_snapshot(NOW).revision == revision + 1


@pytest.mark.parametrize("field,value", [("scope", {}), ("configuration", {})])
def test_command_rejects_non_domain_operands(field: str, value: object) -> None:
    command = _command()
    with pytest.raises(TypeError):
        ConfigureObservation(
            cast(RepositoryScope, value) if field == "scope" else command.scope,
            command.expected_revision,
            cast(ObservationConfiguration, value)
            if field == "configuration"
            else command.configuration,
            command.operation_id,
            command.actor,
        )

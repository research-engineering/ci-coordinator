"""Strict parsing and loading of the runtime-authority entry-point inventory."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from importlib.resources import files
from typing import cast

from ci_coordinator.runtime_settings.authority_contracts import (
    CallerInventory,
    CallerInventoryRejection,
    CallerRecord,
)

_REQUIRED_KEYS = frozenset({"callerId", "path", "kind", "currentTarget"})
_ROOT_KEYS = frozenset({"schemaVersion", "inventoryId", "callers"})
INVENTORY_RESOURCE_NAME = "runtime-caller-inventory.v1.json"
INVENTORY_SCHEMA_VERSION = "ci-coordinator-runtime-caller-inventory/v1"
INVENTORY_ID = "ci-coordinator/runtime-callers/v1"


def admit_caller_inventory(document: object) -> CallerInventory | CallerInventoryRejection:
    if type(document) is not dict:
        return CallerInventoryRejection("invalid_setting_value", "inventory")
    mapping = cast(Mapping[str, object], document)
    if (
        mapping.get("schemaVersion") != INVENTORY_SCHEMA_VERSION
        or mapping.get("inventoryId") != INVENTORY_ID
        or set(mapping) != _ROOT_KEYS
    ):
        return CallerInventoryRejection("invalid_setting_value", "inventory")
    callers = mapping.get("callers")
    if type(callers) is not list or not callers:
        return CallerInventoryRejection("unclassified_caller", "callers")
    records: list[CallerRecord] = []
    for index, raw_record in enumerate(cast(Sequence[object], callers)):
        record = _admit_record(raw_record, index)
        if isinstance(record, CallerInventoryRejection):
            return record
        records.append(record)
    caller_ids = [record.caller_id for record in records]
    if len(caller_ids) != len(set(caller_ids)):
        return CallerInventoryRejection("duplicate_caller_id", "callers")
    path_targets = [(record.path, record.current_target) for record in records]
    if len(path_targets) != len(set(path_targets)):
        return CallerInventoryRejection("duplicate_caller_target", "callers")
    return CallerInventory(tuple(records))


def parse_caller_inventory(raw_document: bytes) -> CallerInventory:
    if type(raw_document) is not bytes:
        raise ValueError("caller inventory must be exact bytes")
    try:
        document = json.loads(raw_document, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("caller inventory must be duplicate-free UTF-8 JSON") from error
    admitted = admit_caller_inventory(document)
    if isinstance(admitted, CallerInventoryRejection):
        raise ValueError(
            f"caller inventory admission failed: {admitted.code}:{admitted.field_name}"
        )
    return admitted


def load_bundled_caller_inventory() -> CallerInventory:
    raw_document = (
        files("ci_coordinator.runtime_settings.resources")
        .joinpath(INVENTORY_RESOURCE_NAME)
        .read_bytes()
    )
    return parse_caller_inventory(raw_document)


def _admit_record(raw_record: object, index: int) -> CallerRecord | CallerInventoryRejection:
    if type(raw_record) is not dict:
        return CallerInventoryRejection("invalid_setting_value", f"callers[{index}]")
    record = cast(Mapping[str, object], raw_record)
    if set(record) != _REQUIRED_KEYS:
        return CallerInventoryRejection("unclassified_caller", f"callers[{index}]")
    try:
        kind = record["kind"]
        if kind not in {
            "container",
            "python_console_script",
            "python_module",
            "workflow",
            "deployment",
            "generated_entrypoint",
        }:
            raise ValueError("kind")
        return CallerRecord(
            caller_id=_required_text(record, "callerId"),
            path=_required_text(record, "path"),
            kind=kind,
            current_target=_required_text(record, "currentTarget"),
        )
    except (TypeError, ValueError):
        return CallerInventoryRejection("invalid_caller_target", f"callers[{index}]")


def _required_text(record: Mapping[str, object], name: str) -> str:
    value = record[name]
    if type(value) is not str:
        raise ValueError(name)
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("caller inventory contains a duplicate key")
        result[key] = value
    return result

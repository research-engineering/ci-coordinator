"""Admission for the finite, package-bound entrypoint discovery disposition."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from importlib.resources import files
from typing import cast

from ci_coordinator.runtime_settings.authority_contracts import (
    CallerInventory,
    CallerKind,
    EntrypointDiscoveryProfile,
    EntrypointDisposition,
    EntrypointDispositionInventory,
    EntrypointDispositionRecord,
    EntrypointDispositionRejection,
)

_ROOT_KEYS = frozenset({"schemaVersion", "dispositionId", "discovery", "sources"})
_DISCOVERY_KEYS = frozenset(
    {
        "pythonProjectManifests",
        "pythonModuleEntrypoints",
        "containerFiles",
        "workflowDirectories",
        "deploymentDirectories",
        "generatedEntrypointDirectories",
        "rootDeploymentFiles",
    }
)
_SOURCE_KEYS = frozenset(
    {"sourceId", "path", "kind", "currentTarget", "memberIds", "disposition", "callerId"}
)
ENTRYPOINT_DISPOSITION_RESOURCE_NAME = "runtime-entrypoint-disposition.v1.json"
ENTRYPOINT_DISPOSITION_SCHEMA_VERSION = "ci-coordinator-runtime-entrypoint-disposition/v1"
ENTRYPOINT_DISPOSITION_ID = "ci-coordinator/runtime-entrypoint-disposition/v1"


def admit_entrypoint_disposition(
    document: object,
    caller_inventory: CallerInventory,
) -> EntrypointDispositionInventory | EntrypointDispositionRejection:
    """Admit an exhaustive source disposition and bind runtime rows to callers."""
    if type(document) is not dict or type(caller_inventory) is not CallerInventory:
        return EntrypointDispositionRejection("invalid_entrypoint_disposition", "document")
    mapping = cast(Mapping[str, object], document)
    if (
        mapping.get("schemaVersion") != ENTRYPOINT_DISPOSITION_SCHEMA_VERSION
        or mapping.get("dispositionId") != ENTRYPOINT_DISPOSITION_ID
        or set(mapping) != _ROOT_KEYS
    ):
        return EntrypointDispositionRejection("invalid_entrypoint_disposition", "document")
    discovery = _admit_discovery(mapping.get("discovery"))
    if isinstance(discovery, EntrypointDispositionRejection):
        return discovery
    raw_sources = mapping.get("sources")
    if type(raw_sources) is not list or not raw_sources:
        return EntrypointDispositionRejection("unclassified_entrypoint_source", "sources")
    records: list[EntrypointDispositionRecord] = []
    for index, raw_source in enumerate(cast(Sequence[object], raw_sources)):
        record = _admit_record(raw_source, index)
        if isinstance(record, EntrypointDispositionRejection):
            return record
        records.append(record)
    try:
        inventory = EntrypointDispositionInventory(discovery, tuple(records))
    except ValueError:
        return EntrypointDispositionRejection("duplicate_entrypoint_source", "sources")
    return _bind_runtime_sources(inventory, caller_inventory)


def parse_entrypoint_disposition(
    raw_document: bytes,
    caller_inventory: CallerInventory,
) -> EntrypointDispositionInventory:
    """Parse duplicate-free exact resource bytes before binding runtime authority."""
    if type(raw_document) is not bytes:
        raise ValueError("entrypoint disposition must be exact bytes")
    try:
        document = json.loads(raw_document, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("entrypoint disposition must be duplicate-free UTF-8 JSON") from error
    admitted = admit_entrypoint_disposition(document, caller_inventory)
    if isinstance(admitted, EntrypointDispositionRejection):
        raise ValueError(
            f"entrypoint disposition admission failed: {admitted.code}:{admitted.field_name}"
        )
    return admitted


def load_bundled_entrypoint_disposition(
    caller_inventory: CallerInventory,
) -> EntrypointDispositionInventory:
    raw_document = (
        files("ci_coordinator.runtime_settings.resources")
        .joinpath(ENTRYPOINT_DISPOSITION_RESOURCE_NAME)
        .read_bytes()
    )
    return parse_entrypoint_disposition(raw_document, caller_inventory)


def _admit_discovery(
    raw_discovery: object,
) -> EntrypointDiscoveryProfile | EntrypointDispositionRejection:
    if type(raw_discovery) is not dict:
        return EntrypointDispositionRejection("invalid_entrypoint_disposition", "discovery")
    discovery = cast(Mapping[str, object], raw_discovery)
    if set(discovery) != _DISCOVERY_KEYS:
        return EntrypointDispositionRejection("invalid_entrypoint_disposition", "discovery")
    try:
        return EntrypointDiscoveryProfile(
            python_project_manifests=_ordered_paths(discovery, "pythonProjectManifests"),
            python_module_entrypoints=_ordered_paths(discovery, "pythonModuleEntrypoints"),
            container_files=_ordered_paths(discovery, "containerFiles"),
            workflow_directories=_ordered_paths(discovery, "workflowDirectories"),
            deployment_directories=_ordered_paths(discovery, "deploymentDirectories"),
            generated_entrypoint_directories=_ordered_paths(
                discovery, "generatedEntrypointDirectories"
            ),
            root_deployment_files=_ordered_paths(discovery, "rootDeploymentFiles"),
        )
    except (TypeError, ValueError):
        return EntrypointDispositionRejection("invalid_entrypoint_disposition", "discovery")


def _admit_record(
    raw_record: object,
    index: int,
) -> EntrypointDispositionRecord | EntrypointDispositionRejection:
    field = f"sources[{index}]"
    if type(raw_record) is not dict:
        return EntrypointDispositionRejection("unclassified_entrypoint_source", field)
    record = cast(Mapping[str, object], raw_record)
    if set(record) != _SOURCE_KEYS:
        return EntrypointDispositionRejection("unclassified_entrypoint_source", field)
    try:
        caller_id = record["callerId"]
        if caller_id is not None and type(caller_id) is not str:
            raise ValueError("caller id")
        return EntrypointDispositionRecord(
            source_id=_required_text(record, "sourceId"),
            path=_required_text(record, "path"),
            kind=cast(CallerKind, record["kind"]),
            current_target=_required_text(record, "currentTarget"),
            member_ids=_ordered_member_ids(record),
            disposition=cast(EntrypointDisposition, record["disposition"]),
            caller_id=caller_id,
        )
    except (KeyError, TypeError, ValueError):
        return EntrypointDispositionRejection("invalid_entrypoint_target", field)


def _bind_runtime_sources(
    inventory: EntrypointDispositionInventory,
    caller_inventory: CallerInventory,
) -> EntrypointDispositionInventory | EntrypointDispositionRejection:
    runtime_sources = tuple(
        source for source in inventory.records if source.disposition == "runtime_authority"
    )
    unknown_sources = tuple(
        source for source in inventory.records if source.disposition == "unknown"
    )
    if unknown_sources:
        return EntrypointDispositionRejection(
            "unknown_entrypoint_source", unknown_sources[0].source_id
        )
    caller_by_id = {record.caller_id: record for record in caller_inventory.records}
    source_by_caller_id = {source.caller_id: source for source in runtime_sources}
    if len(source_by_caller_id) != len(runtime_sources) or set(source_by_caller_id) != set(
        caller_by_id
    ):
        return EntrypointDispositionRejection("unmatched_runtime_authority", "callers")
    for caller_id, source in source_by_caller_id.items():
        if caller_id is None:
            return EntrypointDispositionRejection("unmatched_runtime_authority", "callers")
        caller = caller_by_id[caller_id]
        if (
            source.path != caller.path
            or source.kind != caller.kind
            or source.current_target != caller.current_target
        ):
            return EntrypointDispositionRejection("unmatched_runtime_authority", caller_id)
    return inventory


def _ordered_paths(record: Mapping[str, object], name: str) -> tuple[str, ...]:
    values = record[name]
    if type(values) is not list:
        raise ValueError(name)
    return tuple(_require_item(value, name) for value in cast(Sequence[object], values))


def _ordered_member_ids(record: Mapping[str, object]) -> tuple[str, ...]:
    values = record["memberIds"]
    if type(values) is not list:
        raise ValueError("memberIds")
    return tuple(_require_item(value, "memberIds") for value in cast(Sequence[object], values))


def _required_text(record: Mapping[str, object], name: str) -> str:
    return _require_item(record[name], name)


def _require_item(value: object, name: str) -> str:
    if type(value) is not str:
        raise ValueError(name)
    return value


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("entrypoint disposition contains a duplicate key")
        result[key] = value
    return result

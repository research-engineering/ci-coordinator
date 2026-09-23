"""Strict mapping admission for target-authority relation values."""

from __future__ import annotations

from typing import cast

from ci_coordinator.config_control.contracts import RepositoryScope
from ci_coordinator.kernel.strict_json import StrictJsonError, load_strict_json

from ._canonical import encode_mapping
from .limits import MAX_RELATION_DOCUMENT_BYTES
from .model import (
    TARGET_AUTHORITY_EPOCH_SCHEMA,
    TARGET_AUTHORITY_PRODUCER_SCHEMA,
    TARGET_AUTHORITY_ROW_SCHEMA,
    TARGET_AUTHORITY_SUBJECT_SCHEMA,
    AuthorityDisposition,
    AuthorityField,
    AuthorityFieldEntry,
    AuthorityFieldState,
    EpochComponent,
    ProducerIdentity,
    RelationEpochPhase,
    TargetAuthorityEpoch,
    TargetAuthorityKey,
    TargetAuthorityRow,
    TargetAuthorityRowFamily,
    TargetAuthoritySubject,
)


def admit_document(body: bytes, schema: str) -> dict[str, object]:
    try:
        value = load_strict_json(body, max_bytes=MAX_RELATION_DOCUMENT_BYTES)
    except StrictJsonError as error:
        raise ValueError("target-authority document is not strict bounded JSON") from error
    mapping = exact_object(value, "document")
    if mapping.get("schemaVersion") != schema:
        raise ValueError("target-authority document schema is not admitted")
    if encode_mapping(mapping) != body:
        raise ValueError("target-authority document must already be canonical")
    return mapping


def decode_row_mapping(value: object) -> TargetAuthorityRow:
    mapping = exact_keys(
        value,
        {
            "schemaVersion",
            "key",
            "disposition",
            "semanticOwner",
            "sourceLocator",
            "fields",
        },
        "target-authority row",
    )
    if mapping["schemaVersion"] != TARGET_AUTHORITY_ROW_SCHEMA:
        raise ValueError("target-authority row schema is not admitted")
    disposition = exact_string(mapping["disposition"], "row disposition")
    return TargetAuthorityRow(
        key=decode_key_mapping(mapping["key"]),
        disposition=cast(AuthorityDisposition, disposition),
        semantic_owner=exact_string(mapping["semanticOwner"], "semantic owner"),
        source_locator=exact_string(mapping["sourceLocator"], "source locator"),
        fields=tuple(
            decode_field_entry_mapping(item) for item in exact_list(mapping["fields"], "row fields")
        ),
    )


def decode_key_mapping(value: object) -> TargetAuthorityKey:
    mapping = exact_keys(value, {"family", "memberId"}, "target-authority key")
    return TargetAuthorityKey(
        cast(TargetAuthorityRowFamily, exact_string(mapping["family"], "row family")),
        exact_string(mapping["memberId"], "row member id"),
    )


def decode_field_entry_mapping(value: object) -> AuthorityFieldEntry:
    mapping = exact_keys(value, {"name", "field"}, "authority field entry")
    return AuthorityFieldEntry(
        exact_string(mapping["name"], "authority field name"),
        decode_field_mapping(mapping["field"]),
    )


def decode_field_mapping(value: object) -> AuthorityField:
    mapping = exact_object(value, "authority field")
    state = cast(AuthorityFieldState, exact_string(mapping.get("state"), "authority field state"))
    if state == "present":
        exact_key_set(mapping, {"state", "value"}, "present authority field")
        return AuthorityField.present(mapping["value"])
    if state == "not_applicable":
        exact_key_set(mapping, {"state"}, "not-applicable authority field")
        return AuthorityField.not_applicable()
    if state == "unknown":
        exact_key_set(mapping, {"state", "reason"}, "unknown authority field")
        return AuthorityField.unknown(exact_string(mapping["reason"], "unknown reason"))
    raise ValueError("authority field state is not admitted")


def decode_subject_mapping(value: object) -> TargetAuthoritySubject:
    mapping = exact_keys(
        value,
        {"schemaVersion", "installationId", "repositoryId", "authorityId"},
        "target-authority subject",
    )
    if mapping["schemaVersion"] != TARGET_AUTHORITY_SUBJECT_SCHEMA:
        raise ValueError("target-authority subject schema is not admitted")
    return TargetAuthoritySubject(
        RepositoryScope(
            exact_integer(mapping["installationId"], "installation id"),
            exact_integer(mapping["repositoryId"], "repository id"),
        ),
        exact_string(mapping["authorityId"], "authority id"),
    )


def decode_epoch_mapping(value: object) -> TargetAuthorityEpoch:
    mapping = exact_keys(
        value,
        {
            "schemaVersion",
            "phase",
            "sourceManifest",
            "providerGovernance",
            "policy",
            "catalog",
            "registry",
            "owner",
        },
        "target-authority epoch",
    )
    if mapping["schemaVersion"] != TARGET_AUTHORITY_EPOCH_SCHEMA:
        raise ValueError("target-authority epoch schema is not admitted")
    return TargetAuthorityEpoch(
        phase=cast(RelationEpochPhase, exact_string(mapping["phase"], "epoch phase")),
        source_manifest=decode_epoch_component_mapping(mapping["sourceManifest"]),
        provider_governance=decode_epoch_component_mapping(mapping["providerGovernance"]),
        policy=decode_epoch_component_mapping(mapping["policy"]),
        catalog=decode_epoch_component_mapping(mapping["catalog"]),
        registry=decode_epoch_component_mapping(mapping["registry"]),
        owner=decode_epoch_component_mapping(mapping["owner"]),
    )


def decode_epoch_component_mapping(value: object) -> EpochComponent:
    mapping = exact_object(value, "epoch component")
    state = cast(AuthorityFieldState, exact_string(mapping.get("state"), "epoch component state"))
    if state == "present":
        exact_key_set(mapping, {"state", "digest"}, "present epoch component")
        return EpochComponent.present(exact_string(mapping["digest"], "epoch digest"))
    if state == "not_applicable":
        exact_key_set(mapping, {"state"}, "not-applicable epoch component")
        return EpochComponent.not_applicable()
    if state == "unknown":
        exact_key_set(mapping, {"state", "reason"}, "unknown epoch component")
        return EpochComponent.unknown(exact_string(mapping["reason"], "epoch unknown reason"))
    raise ValueError("epoch component state is not admitted")


def decode_producer_mapping(value: object) -> ProducerIdentity:
    mapping = exact_keys(
        value,
        {"schemaVersion", "producerId", "version"},
        "producer identity",
    )
    if mapping["schemaVersion"] != TARGET_AUTHORITY_PRODUCER_SCHEMA:
        raise ValueError("producer identity schema is not admitted")
    return ProducerIdentity(
        exact_string(mapping["producerId"], "producer id"),
        exact_string(mapping["version"], "producer version"),
    )


def exact_object(value: object, name: str) -> dict[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise TypeError(f"{name} must be an exact string-keyed object")
    return cast(dict[str, object], value)


def exact_keys(value: object, keys: set[str], name: str) -> dict[str, object]:
    mapping = exact_object(value, name)
    exact_key_set(mapping, keys, name)
    return mapping


def exact_key_set(mapping: dict[str, object], keys: set[str], name: str) -> None:
    if set(mapping) != keys:
        raise ValueError(f"{name} keys are not exact")


def exact_list(value: object, name: str) -> list[object]:
    if type(value) is not list:
        raise TypeError(f"{name} must be an exact list")
    return cast(list[object], value)


def exact_string(value: object, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be an exact string")
    return value


def exact_integer(value: object, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an exact integer")
    return value


def exact_boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{name} must be an exact boolean")
    return value

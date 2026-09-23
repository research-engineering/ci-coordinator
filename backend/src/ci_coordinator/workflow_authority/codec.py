"""Strict canonical codecs for stable workflow authority artifacts."""

from __future__ import annotations

from typing import cast

from ci_coordinator.config_control.contracts import RepositoryScope
from ci_coordinator.kernel.canonical_json import bounded_canonical_json
from ci_coordinator.kernel.strict_json import StrictJsonError, load_strict_json

from .errors import WorkflowAuthorityError
from .limits import MANIFEST_JSON_LIMITS, MAX_MANIFEST_BYTES, MAX_MANIFEST_ENTRIES
from .model import (
    WORKFLOW_AUTHORITY_MANIFEST_SCHEMA,
    WORKFLOW_SOURCE_BINDING_SCHEMA,
    WorkflowAuthorityManifest,
    WorkflowAuthorityRepository,
    WorkflowCommitRequest,
    WorkflowManifestEntry,
    WorkflowSourceBinding,
)

_BINDING_MAX_BYTES = 32_768


def encode_manifest(value: WorkflowAuthorityManifest) -> bytes:
    if type(value) is not WorkflowAuthorityManifest:
        raise TypeError("manifest codec requires an exact WorkflowAuthorityManifest")
    return value.canonical_bytes


def decode_manifest(content: bytes) -> WorkflowAuthorityManifest:
    mapping = _canonical_mapping(content, max_bytes=MAX_MANIFEST_BYTES)
    _exact_keys(
        mapping,
        {"schemaVersion", "repository", "objectFormat", "workflowsTreeId", "entries"},
    )
    if mapping["schemaVersion"] != WORKFLOW_AUTHORITY_MANIFEST_SCHEMA:
        raise _codec_error("workflow manifest schema is not admitted")
    raw_entries = mapping["entries"]
    if type(raw_entries) is not list or len(raw_entries) > MAX_MANIFEST_ENTRIES:
        raise _codec_error("workflow manifest entries are not a bounded array")
    try:
        value = WorkflowAuthorityManifest(
            repository=_repository(mapping["repository"]),
            object_format=cast(str, mapping["objectFormat"]),  # type: ignore[arg-type]
            workflows_tree_id=_string(mapping["workflowsTreeId"]),
            entries=tuple(_manifest_entry(item) for item in raw_entries),
        )
    except (TypeError, ValueError) as error:
        raise _codec_error("workflow manifest values are invalid") from error
    if value.canonical_bytes != content:
        raise _codec_error("workflow manifest bytes are not canonical")
    return value


def encode_source_binding(value: WorkflowSourceBinding) -> bytes:
    if type(value) is not WorkflowSourceBinding:
        raise TypeError("source-binding codec requires an exact WorkflowSourceBinding")
    return value.canonical_bytes


def decode_source_binding(content: bytes) -> WorkflowSourceBinding:
    mapping = _canonical_mapping(content, max_bytes=_BINDING_MAX_BYTES)
    _exact_keys(
        mapping,
        {
            "schemaVersion",
            "repository",
            "providerRequest",
            "sourceCommitId",
            "commitResponseSha256",
            "rootTreeId",
            "ancestorTreeIds",
            "manifestDigest",
        },
    )
    if mapping["schemaVersion"] != WORKFLOW_SOURCE_BINDING_SCHEMA:
        raise _codec_error("workflow source-binding schema is not admitted")
    ancestors = mapping["ancestorTreeIds"]
    request = _mapping(mapping["providerRequest"])
    _exact_keys(
        request,
        {"operation", "method", "path", "apiVersion", "query", "bodyAbsent"},
    )
    if type(ancestors) is not list or len(ancestors) != 2:
        raise _codec_error("workflow source binding requires the exact ancestor chain")
    try:
        value = WorkflowSourceBinding(
            repository=_repository(mapping["repository"]),
            provider_request=WorkflowCommitRequest(
                operation=cast(str, request["operation"]),  # type: ignore[arg-type]
                method=cast(str, request["method"]),  # type: ignore[arg-type]
                path=_string(request["path"]),
                api_version=_string(request["apiVersion"]),
                query=_empty_query(request["query"]),
                body_absent=_boolean(request["bodyAbsent"]),
            ),
            source_commit_id=_string(mapping["sourceCommitId"]),
            commit_response_sha256=_string(mapping["commitResponseSha256"]),
            root_tree_id=_string(mapping["rootTreeId"]),
            github_tree_id=_string(ancestors[0]),
            workflows_tree_id=_string(ancestors[1]),
            manifest_digest=_string(mapping["manifestDigest"]),
        )
    except (TypeError, ValueError) as error:
        raise _codec_error("workflow source-binding values are invalid") from error
    if value.canonical_bytes != content:
        raise _codec_error("workflow source-binding bytes are not canonical")
    return value


def _repository(value: object) -> WorkflowAuthorityRepository:
    mapping = _mapping(value)
    _exact_keys(
        mapping,
        {"installationId", "repositoryId", "owner", "name", "defaultBranch"},
    )
    return WorkflowAuthorityRepository(
        RepositoryScope(
            _integer(mapping["installationId"]),
            _integer(mapping["repositoryId"]),
        ),
        _string(mapping["owner"]),
        _string(mapping["name"]),
        _string(mapping["defaultBranch"]),
    )


def _manifest_entry(value: object) -> WorkflowManifestEntry:
    mapping = _mapping(value)
    _exact_keys(
        mapping,
        {
            "path",
            "mode",
            "objectType",
            "objectId",
            "declaredSize",
            "observedSize",
            "blobSha256",
        },
    )
    return WorkflowManifestEntry(
        path=_string(mapping["path"]),
        mode=cast(str, mapping["mode"]),  # type: ignore[arg-type]
        object_type=cast(str, mapping["objectType"]),  # type: ignore[arg-type]
        object_id=_string(mapping["objectId"]),
        declared_size=_optional_integer(mapping["declaredSize"]),
        observed_size=_optional_integer(mapping["observedSize"]),
        blob_sha256=_optional_string(mapping["blobSha256"]),
    )


def _canonical_mapping(content: bytes, *, max_bytes: int) -> dict[str, object]:
    if type(content) is not bytes:
        raise TypeError("workflow authority codec requires exact bytes")
    try:
        value = load_strict_json(content, max_bytes=max_bytes)
        mapping = _mapping(value)
        canonical = bounded_canonical_json(
            mapping,
            max_bytes=max_bytes,
            resource_limits=MANIFEST_JSON_LIMITS,
        )
    except (StrictJsonError, TypeError, ValueError) as error:
        raise _codec_error("workflow authority document is not admitted JSON") from error
    if canonical != content:
        raise _codec_error("workflow authority document is not canonical")
    return mapping


def _mapping(value: object) -> dict[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise _codec_error("workflow authority JSON value must be an object")
    return cast(dict[str, object], value)


def _exact_keys(mapping: dict[str, object], expected: set[str]) -> None:
    if mapping.keys() != expected:
        raise _codec_error("workflow authority JSON object keys are not exact")


def _string(value: object) -> str:
    if type(value) is not str:
        raise _codec_error("workflow authority JSON field must be a string")
    return value


def _optional_string(value: object) -> str | None:
    return None if value is None else _string(value)


def _integer(value: object) -> int:
    if type(value) is not int:
        raise _codec_error("workflow authority JSON field must be an integer")
    return value


def _optional_integer(value: object) -> int | None:
    return None if value is None else _integer(value)


def _empty_query(value: object) -> tuple[tuple[str, str], ...]:
    if type(value) is not list or value:
        raise _codec_error("workflow commit request query must be an empty array")
    return ()


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise _codec_error("workflow authority JSON field must be a boolean")
    return value


def _codec_error(message: str) -> WorkflowAuthorityError:
    return WorkflowAuthorityError("codec_rejected", message)

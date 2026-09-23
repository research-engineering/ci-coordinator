from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.documentation_graph_contract import (
    DocumentationGraphPolicy,
    DocumentationLimits,
    ReadinessClaimPolicy,
    safe_relative_path,
)
from scripts.documentation_graph_filesystem import read_policy_source
from scripts.proofkit_common import as_array, as_object
from scripts.proofkit_common import exact_fields as _exact_keys
from scripts.proofkit_common import nonempty_string as _nonempty_string

DEFAULT_PROFILE_PATH = Path(
    "docs/specs/ci-coordinator-proofkit-adoption/documentation-graph-profile.v1.json"
)
_URI_SCHEME = re.compile(r"[a-z][a-z0-9+.-]*")
_LIMIT_CEILINGS = DocumentationLimits(
    max_document_count=4_096,
    max_document_bytes=4 * 1024 * 1024,
    max_inventory_bytes=64 * 1024 * 1024,
    max_link_count=65_536,
    max_repository_path_count=250_000,
    max_total_bytes=64 * 1024 * 1024,
)


def load_policy(
    repo_root: Path,
    profile_path: Path = DEFAULT_PROFILE_PATH,
) -> DocumentationGraphPolicy:
    raw = _parse_profile(read_policy_source(repo_root, profile_path))
    _exact_keys(
        raw,
        {
            "allowedExternalSchemes",
            "limits",
            "markdownGlobs",
            "nonClaims",
            "profileId",
            "readinessClaim",
            "rootPaths",
            "schemaVersion",
        },
        "documentation graph profile",
    )
    if raw.get("schemaVersion") != 1:
        raise ValueError("documentation graph profile schemaVersion must equal 1")
    if raw.get("profileId") != "ci-coordinator.documentation-graph":
        raise ValueError("documentation graph profileId is invalid")

    limits = as_object(raw.get("limits"), "documentation graph limits")
    _exact_keys(
        limits,
        {
            "maxDocumentBytes",
            "maxDocumentCount",
            "maxInventoryBytes",
            "maxLinkCount",
            "maxRepositoryPathCount",
            "maxTotalBytes",
        },
        "documentation graph limits",
    )
    readiness = as_object(raw.get("readinessClaim"), "readiness claim policy")
    _exact_keys(
        readiness,
        {"marker", "ownerPath", "subjectHeadingLevel"},
        "readiness claim policy",
    )
    allowed_external_schemes = _ordered_strings(
        raw.get("allowedExternalSchemes"), "allowed external schemes"
    )
    _ordered_strings(raw.get("nonClaims"), "documentation graph non-claims")
    for scheme in allowed_external_schemes:
        if _URI_SCHEME.fullmatch(scheme) is None:
            raise ValueError(f"invalid allowed external scheme: {scheme}")
    policy = DocumentationGraphPolicy(
        allowed_external_schemes=frozenset(allowed_external_schemes),
        limits=DocumentationLimits(
            max_document_count=_positive_integer(
                limits.get("maxDocumentCount"), "maxDocumentCount"
            ),
            max_document_bytes=_positive_integer(
                limits.get("maxDocumentBytes"), "maxDocumentBytes"
            ),
            max_inventory_bytes=_positive_integer(
                limits.get("maxInventoryBytes"), "maxInventoryBytes"
            ),
            max_link_count=_positive_integer(limits.get("maxLinkCount"), "maxLinkCount"),
            max_repository_path_count=_positive_integer(
                limits.get("maxRepositoryPathCount"), "maxRepositoryPathCount"
            ),
            max_total_bytes=_positive_integer(limits.get("maxTotalBytes"), "maxTotalBytes"),
        ),
        markdown_globs=_ordered_strings(raw.get("markdownGlobs"), "markdown globs"),
        readiness_claim=ReadinessClaimPolicy(
            marker=_nonempty_string(readiness.get("marker"), "readiness marker"),
            owner_path=safe_relative_path(
                _nonempty_string(readiness.get("ownerPath"), "readiness owner path"),
                "readiness owner path",
            ),
            subject_heading_level=_heading_level(
                readiness.get("subjectHeadingLevel"), "readiness subject heading level"
            ),
        ),
        root_paths=tuple(
            safe_relative_path(path, "documentation root path")
            for path in _ordered_strings(raw.get("rootPaths"), "documentation root paths")
        ),
    )
    if not policy.allowed_external_schemes:
        raise ValueError("documentation graph requires an external scheme allowlist")
    if policy.limits.max_document_bytes > policy.limits.max_total_bytes:
        raise ValueError("maxDocumentBytes must not exceed maxTotalBytes")
    if policy.limits.max_document_count > policy.limits.max_repository_path_count:
        raise ValueError("maxDocumentCount must not exceed maxRepositoryPathCount")
    enforce_limit_ceilings(policy.limits)
    return policy


def _ordered_strings(value: object, label: str) -> tuple[str, ...]:
    values = tuple(_nonempty_string(item, label) for item in as_array(value, label))
    if not values:
        raise ValueError(f"{label} must be non-empty")
    if len(values) != len(set(values)):
        raise ValueError(f"{label} contains duplicates")
    if list(values) != sorted(values):
        raise ValueError(f"{label} must use canonical order")
    return values


def _positive_integer(value: object, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _heading_level(value: object, label: str) -> int:
    level = _positive_integer(value, label)
    if level > 6:
        raise ValueError(f"{label} must be between 1 and 6")
    return level


def enforce_limit_ceilings(limits: DocumentationLimits) -> None:
    values = (
        (
            "maxDocumentCount",
            limits.max_document_count,
            _LIMIT_CEILINGS.max_document_count,
        ),
        (
            "maxDocumentBytes",
            limits.max_document_bytes,
            _LIMIT_CEILINGS.max_document_bytes,
        ),
        (
            "maxInventoryBytes",
            limits.max_inventory_bytes,
            _LIMIT_CEILINGS.max_inventory_bytes,
        ),
        ("maxLinkCount", limits.max_link_count, _LIMIT_CEILINGS.max_link_count),
        (
            "maxRepositoryPathCount",
            limits.max_repository_path_count,
            _LIMIT_CEILINGS.max_repository_path_count,
        ),
        ("maxTotalBytes", limits.max_total_bytes, _LIMIT_CEILINGS.max_total_bytes),
    )
    for label, value, ceiling in values:
        if value > ceiling:
            raise ValueError(f"{label} exceeds implementation ceiling {ceiling}")


def _parse_profile(source: str) -> dict[str, object]:
    try:
        value: object = json.loads(
            source,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as error:
        raise ValueError(
            f"documentation graph profile did not contain JSON: {error.msg}"
        ) from error
    return as_object(value, "documentation graph profile")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"documentation graph profile has duplicate key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError(f"documentation graph profile has non-finite constant: {value}")

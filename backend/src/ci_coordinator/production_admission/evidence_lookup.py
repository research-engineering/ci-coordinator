from __future__ import annotations

import re
from dataclasses import dataclass

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import canonical_json, load_strict_json
from ci_coordinator.production_admission._fields import _exact_object, _positive_integer, _text
from ci_coordinator.repo_context.workflow_inventory import is_workflow_path
from ci_coordinator.workflow_authority.model import WorkflowAuthorityRepository

MAX_PRODUCTION_LOOKUP_BYTES = 262_144
_LOOKUP_SCHEMA = "ci-coordinator.production-evidence-lookup/v1"


@dataclass(frozen=True, slots=True)
class ProductionEvidenceLookup:
    """Bounded acquisition coordinates, never independent permission to omit work."""

    repository: WorkflowAuthorityRepository
    source_commit: str
    provider_paths: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.repository) is not WorkflowAuthorityRepository:
            raise TypeError("production lookup requires an exact repository")
        if (
            type(self.source_commit) is not str
            or re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", self.source_commit) is None
        ):
            raise ValueError("production lookup requires an exact source commit")
        if (
            type(self.provider_paths) is not tuple
            or not 1 <= len(self.provider_paths) <= 64
            or any(not is_workflow_path(path) for path in self.provider_paths)
            or tuple(sorted(set(self.provider_paths))) != self.provider_paths
        ):
            raise ValueError("production lookup paths must be bounded and canonical")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": _LOOKUP_SCHEMA,
            "repository": self.repository.to_mapping(),
            "sourceCommit": self.source_commit,
            "providerPaths": list(self.provider_paths),
        }

    @property
    def canonical_bytes(self) -> bytes:
        return canonical_json(self.to_mapping())


def decode_production_lookup(content: bytes) -> ProductionEvidenceLookup:
    root = _exact_object(
        load_strict_json(content, max_bytes=MAX_PRODUCTION_LOOKUP_BYTES),
        {"schemaVersion", "repository", "sourceCommit", "providerPaths"},
    )
    if root["schemaVersion"] != _LOOKUP_SCHEMA or type(root["providerPaths"]) is not list:
        raise ValueError("production lookup shape is invalid")
    repository = _exact_object(
        root["repository"], {"installationId", "repositoryId", "owner", "name", "defaultBranch"}
    )
    value = ProductionEvidenceLookup(
        repository=WorkflowAuthorityRepository(
            scope=RepositoryScope(
                _positive_integer(repository["installationId"]),
                _positive_integer(repository["repositoryId"]),
            ),
            owner=_text(repository["owner"]),
            name=_text(repository["name"]),
            default_branch=_text(repository["defaultBranch"]),
        ),
        source_commit=_text(root["sourceCommit"]),
        provider_paths=tuple(_text(path) for path in root["providerPaths"]),
    )
    if value.canonical_bytes != content:
        raise ValueError("production lookup bytes are not canonical")
    return value

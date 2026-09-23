from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath


@dataclass(frozen=True, slots=True)
class DocumentationLimits:
    max_document_count: int
    max_document_bytes: int
    max_inventory_bytes: int
    max_link_count: int
    max_repository_path_count: int
    max_total_bytes: int


@dataclass(frozen=True, slots=True)
class ReadinessClaimPolicy:
    marker: str
    owner_path: str
    subject_heading_level: int


@dataclass(frozen=True, slots=True)
class DocumentationGraphPolicy:
    allowed_external_schemes: frozenset[str]
    limits: DocumentationLimits
    markdown_globs: tuple[str, ...]
    readiness_claim: ReadinessClaimPolicy
    root_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LinkReference:
    destination: str
    line: int
    creates_graph_edge: bool


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    anchors: frozenset[str]
    links: tuple[LinkReference, ...]
    readiness_subjects: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DocumentationGraphSummary:
    document_count: int
    external_link_count: int
    local_link_count: int


class DocumentationGraphError(ValueError):
    def __init__(self, issues: Iterable[str]) -> None:
        ordered = tuple(sorted(set(issues)))
        super().__init__("\n".join(ordered))
        self.issues = ordered


def safe_relative_path(value: str, label: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"{label} must be a safe repository-relative path")
    return path.as_posix()


def contains_control(value: str) -> bool:
    return any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)

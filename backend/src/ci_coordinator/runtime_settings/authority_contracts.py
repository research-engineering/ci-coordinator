"""Immutable runtime-authority inventory contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

type CallerKind = Literal[
    "container",
    "python_console_script",
    "python_module",
    "workflow",
    "deployment",
    "generated_entrypoint",
]
type EntrypointDisposition = Literal["runtime_authority", "non_runtime", "unknown"]

_CALLER_KINDS = frozenset[CallerKind](
    {
        "container",
        "python_console_script",
        "python_module",
        "workflow",
        "deployment",
        "generated_entrypoint",
    }
)
_ENTRYPOINT_DISPOSITIONS = frozenset[EntrypointDisposition](
    {"runtime_authority", "non_runtime", "unknown"}
)


@dataclass(frozen=True, slots=True)
class CallerRecord:
    caller_id: str
    path: str
    kind: CallerKind
    current_target: str

    def __post_init__(self) -> None:
        if type(self.caller_id) is not str or not self.caller_id or len(self.caller_id) > 128:
            raise ValueError("caller id must be bounded non-empty text")
        _require_relative_path(self.path, "caller path")
        if self.kind not in _CALLER_KINDS:
            raise ValueError("caller kind is not admitted")
        if (
            type(self.current_target) is not str
            or not self.current_target
            or len(self.current_target) > 1024
        ):
            raise ValueError("caller target must be bounded non-empty text")


@dataclass(frozen=True, slots=True)
class CallerInventory:
    records: tuple[CallerRecord, ...]

    def __post_init__(self) -> None:
        if not self.records:
            raise ValueError("caller inventory cannot be empty")
        ids = [record.caller_id for record in self.records]
        path_targets = [(record.path, record.current_target) for record in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("caller inventory contains duplicate caller ids")
        if len(path_targets) != len(set(path_targets)):
            raise ValueError("caller inventory contains duplicate caller targets")


@dataclass(frozen=True, slots=True)
class CallerInventoryRejection:
    code: Literal[
        "invalid_setting_value",
        "duplicate_caller_id",
        "duplicate_caller_target",
        "unclassified_caller",
        "invalid_caller_target",
    ]
    field_name: str


@dataclass(frozen=True, slots=True)
class EntrypointDiscoveryProfile:
    """Finite source roots whose executable declarations must be classified."""

    python_project_manifests: tuple[str, ...]
    python_module_entrypoints: tuple[str, ...]
    container_files: tuple[str, ...]
    workflow_directories: tuple[str, ...]
    deployment_directories: tuple[str, ...]
    generated_entrypoint_directories: tuple[str, ...]
    root_deployment_files: tuple[str, ...]

    def __post_init__(self) -> None:
        for name, paths in (
            ("Python project manifests", self.python_project_manifests),
            ("Python module entrypoints", self.python_module_entrypoints),
            ("container files", self.container_files),
            ("workflow directories", self.workflow_directories),
            ("deployment directories", self.deployment_directories),
            ("generated entrypoint directories", self.generated_entrypoint_directories),
            ("root deployment files", self.root_deployment_files),
        ):
            if type(paths) is not tuple or not paths or tuple(sorted(set(paths))) != paths:
                raise ValueError(f"{name} must be a non-empty ordered unique tuple")
            for path in paths:
                _require_relative_path(path, name)


@dataclass(frozen=True, slots=True)
class EntrypointDispositionRecord:
    """One classified runtime-entrypoint source or finite source group."""

    source_id: str
    path: str
    kind: CallerKind
    current_target: str
    member_ids: tuple[str, ...]
    disposition: EntrypointDisposition
    caller_id: str | None

    def __post_init__(self) -> None:
        _require_bounded_text(self.source_id, "entrypoint source id", maximum=256)
        _require_relative_path(self.path, "entrypoint path")
        if self.kind not in _CALLER_KINDS:
            raise ValueError("entrypoint kind is not admitted")
        _require_bounded_text(self.current_target, "entrypoint target", maximum=16_384)
        if (
            type(self.member_ids) is not tuple
            or not self.member_ids
            or tuple(sorted(set(self.member_ids))) != self.member_ids
        ):
            raise ValueError("entrypoint members must be a non-empty ordered unique tuple")
        for member_id in self.member_ids:
            _require_bounded_text(member_id, "entrypoint member id", maximum=256)
        if self.disposition not in _ENTRYPOINT_DISPOSITIONS:
            raise ValueError("entrypoint disposition is not admitted")
        if self.disposition == "runtime_authority":
            _require_bounded_text(self.caller_id, "entrypoint caller id", maximum=128)
        elif self.caller_id is not None:
            raise ValueError("non-runtime entrypoint source cannot name a caller")


@dataclass(frozen=True, slots=True)
class EntrypointDispositionInventory:
    """A complete, package-bound disposition for one finite discovery profile."""

    discovery: EntrypointDiscoveryProfile
    records: tuple[EntrypointDispositionRecord, ...]

    def __post_init__(self) -> None:
        if type(self.discovery) is not EntrypointDiscoveryProfile:
            raise ValueError("entrypoint disposition requires an exact discovery profile")
        if type(self.records) is not tuple or not self.records:
            raise ValueError("entrypoint disposition cannot be empty")
        source_ids = tuple(record.source_id for record in self.records)
        if tuple(sorted(set(source_ids))) != source_ids:
            raise ValueError("entrypoint disposition source ids must be unique and ordered")


@dataclass(frozen=True, slots=True)
class EntrypointDispositionRejection:
    code: Literal[
        "invalid_entrypoint_disposition",
        "duplicate_entrypoint_source",
        "unclassified_entrypoint_source",
        "invalid_entrypoint_target",
        "unknown_entrypoint_source",
        "unmatched_runtime_authority",
    ]
    field_name: str


def _require_relative_path(value: object, name: str) -> None:
    if (
        type(value) is not str
        or not value
        or len(value) > 1024
        or value.startswith("/")
        or "\\" in value
    ):
        raise ValueError(f"{name} is not admitted")


def _require_bounded_text(value: object, name: str, *, maximum: int) -> None:
    if type(value) is not str or not value or len(value) > maximum:
        raise ValueError(f"{name} must be bounded non-empty text")

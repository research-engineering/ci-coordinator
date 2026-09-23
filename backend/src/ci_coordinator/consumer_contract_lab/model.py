"""Exact target-owned inputs and local-only receipt values."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

from ci_coordinator.kernel import git_branch_name_is_admitted, utf16_sort_key
from ci_coordinator.repo_context.freshness import is_safe_relative_path

PROFILE_SCHEMA: Final = "ci-coordinator-consumer-lab-profile/v1"
SCENARIO_SCHEMA: Final = "ci-coordinator-consumer-lab-scenarios/v1"
RECEIPT_SCHEMA: Final = "ci-coordinator-consumer-lab-receipt/v1"

type EventName = Literal["merge_group", "pull_request", "push"]
type ExpectedMode = Literal["selected", "fallback"]
type SourceKind = Literal["commit", "worktree"]
type ChangeStatus = Literal["added", "modified", "removed", "renamed", "copied", "changed"]

_EVENTS = frozenset({"merge_group", "pull_request", "push"})
_MODES = frozenset({"selected", "fallback"})
_STATUSES = frozenset({"added", "modified", "removed", "renamed", "copied", "changed"})
_IDENTIFIER = re.compile(r"[a-z][a-z0-9._-]{0,63}")
_GIT_COMMIT = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_WORKFLOW_PATH = re.compile(r"\.github/workflows/[^/\\]+\.ya?ml")
_JOB_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,127}")
_OWNER = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}")


@dataclass(frozen=True, slots=True)
class FileBinding:
    path: str
    sha256: str

    def __post_init__(self) -> None:
        _require_path(self.path, "file binding path")
        _require_sha256(self.sha256, "file binding digest")

    def to_mapping(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


@dataclass(frozen=True, slots=True)
class ConsumerRepository:
    installation_id: int
    repository_id: int
    owner: str
    name: str
    default_branch: str

    def __post_init__(self) -> None:
        for name, value in (
            ("installation id", self.installation_id),
            ("repository id", self.repository_id),
        ):
            if type(value) is not int or not 1 <= value <= 9_007_199_254_740_991:
                raise ValueError(f"consumer {name} must be a positive safe integer")
        if type(self.owner) is not str or _OWNER.fullmatch(self.owner) is None:
            raise ValueError("consumer repository owner is invalid")
        if type(self.name) is not str or _REPOSITORY.fullmatch(self.name) is None:
            raise ValueError("consumer repository name is invalid")
        _require_text(self.default_branch, "consumer default branch", maximum_bytes=255)
        if not git_branch_name_is_admitted(self.default_branch):
            raise ValueError("consumer default branch must be a canonical Git branch name")

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    def to_mapping(self) -> dict[str, object]:
        return {
            "installationId": self.installation_id,
            "repositoryId": self.repository_id,
            "owner": self.owner,
            "name": self.name,
            "defaultBranch": self.default_branch,
        }


@dataclass(frozen=True, slots=True)
class ConsumerLabProfile:
    profile_id: str
    expected_coordinator_commit: str
    repository: ConsumerRepository
    workflow_path: str
    event_surface: tuple[EventName, ...]
    target_artifacts_directory: str
    dynamic_policy: FileBinding
    scenario_corpus: FileBinding
    target_bindings: tuple[FileBinding, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.profile_id, "consumer lab profile id")
        if _GIT_COMMIT.fullmatch(self.expected_coordinator_commit) is None:
            raise ValueError("expected coordinator commit must be a 40-character Git id")
        if type(self.repository) is not ConsumerRepository:
            raise TypeError("consumer lab profile requires an exact repository")
        if (
            type(self.workflow_path) is not str
            or _WORKFLOW_PATH.fullmatch(self.workflow_path) is None
            or len(self.workflow_path.encode("utf-8")) > 256
        ):
            raise ValueError("consumer lab workflow path is invalid")
        if (
            type(self.event_surface) is not tuple
            or not self.event_surface
            or any(event not in _EVENTS for event in self.event_surface)
            or self.event_surface != tuple(sorted(set(self.event_surface), key=utf16_sort_key))
        ):
            raise ValueError("consumer lab event surface must be non-empty and canonical")
        _require_path(self.target_artifacts_directory, "target artifacts directory")
        if type(self.dynamic_policy) is not FileBinding:
            raise TypeError("consumer lab profile requires an exact dynamic policy binding")
        if type(self.scenario_corpus) is not FileBinding:
            raise TypeError("consumer lab profile requires an exact scenario corpus binding")
        if (
            type(self.target_bindings) is not tuple
            or len(self.target_bindings) > 32
            or any(type(item) is not FileBinding for item in self.target_bindings)
        ):
            raise TypeError("consumer lab target bindings must be a bounded exact tuple")
        binding_paths = tuple(item.path for item in self.target_bindings)
        if binding_paths != tuple(sorted(set(binding_paths), key=utf16_sort_key)):
            raise ValueError("consumer lab target bindings must be canonical")
        owned_paths = (
            self.dynamic_policy.path,
            self.scenario_corpus.path,
            *binding_paths,
        )
        if len(owned_paths) != len(set(owned_paths)):
            raise ValueError("consumer lab authored bindings must not overlap")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": PROFILE_SCHEMA,
            "profileId": self.profile_id,
            "expectedCoordinatorCommit": self.expected_coordinator_commit,
            "repository": self.repository.to_mapping(),
            "workflowPath": self.workflow_path,
            "eventSurface": list(self.event_surface),
            "targetArtifactsDirectory": self.target_artifacts_directory,
            "dynamicPolicy": self.dynamic_policy.to_mapping(),
            "scenarioCorpus": self.scenario_corpus.to_mapping(),
            "targetBindings": [item.to_mapping() for item in self.target_bindings],
        }


@dataclass(frozen=True, slots=True)
class ScenarioChange:
    path: str
    status: ChangeStatus
    previous_path: str | None

    def __post_init__(self) -> None:
        _require_path(self.path, "scenario change path")
        if self.status not in _STATUSES:
            raise ValueError("scenario change status is invalid")
        if self.previous_path is not None:
            _require_path(self.previous_path, "scenario previous path")
        if self.status == "renamed" and self.previous_path is None:
            raise ValueError("renamed scenario change requires a previous path")
        if self.status != "renamed" and self.previous_path is not None:
            raise ValueError("only renamed scenario changes may carry a previous path")

    @property
    def identity(self) -> tuple[bytes, bytes, bytes]:
        return (
            utf16_sort_key(self.path),
            utf16_sort_key(self.previous_path or ""),
            utf16_sort_key(self.status),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "path": self.path,
            "status": self.status,
            "previousPath": self.previous_path,
        }


@dataclass(frozen=True, slots=True)
class ExpectedOutcome:
    mode: ExpectedMode
    selected_jobs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.mode not in _MODES:
            raise ValueError("scenario expected mode is invalid")
        if (
            type(self.selected_jobs) is not tuple
            or len(self.selected_jobs) > 64
            or any(
                type(job_id) is not str or _JOB_ID.fullmatch(job_id) is None
                for job_id in self.selected_jobs
            )
            or self.selected_jobs != tuple(sorted(set(self.selected_jobs), key=utf16_sort_key))
        ):
            raise ValueError("scenario selected jobs must be bounded and canonical")
        if (self.mode == "selected") != bool(self.selected_jobs):
            raise ValueError("selected mode and selected jobs must agree")

    def to_mapping(self) -> dict[str, object]:
        return {"mode": self.mode, "selectedJobs": list(self.selected_jobs)}


@dataclass(frozen=True, slots=True)
class ConsumerLabScenario:
    scenario_id: str
    event_name: EventName
    changes: tuple[ScenarioChange, ...]
    expected_outcome: ExpectedOutcome

    def __post_init__(self) -> None:
        _require_identifier(self.scenario_id, "consumer lab scenario id")
        if self.event_name not in _EVENTS:
            raise ValueError("consumer lab scenario event is invalid")
        if (
            type(self.changes) is not tuple
            or len(self.changes) > 1_000
            or any(type(item) is not ScenarioChange for item in self.changes)
        ):
            raise TypeError("scenario changes must be a bounded exact tuple")
        identities = tuple(item.identity for item in self.changes)
        if identities != tuple(sorted(set(identities))):
            raise ValueError("scenario changes must be canonical and unique")
        if type(self.expected_outcome) is not ExpectedOutcome:
            raise TypeError("consumer lab scenario requires an exact expected outcome")

    def to_mapping(self) -> dict[str, object]:
        return {
            "scenarioId": self.scenario_id,
            "eventName": self.event_name,
            "changes": [item.to_mapping() for item in self.changes],
            "expectedOutcome": self.expected_outcome.to_mapping(),
        }


@dataclass(frozen=True, slots=True)
class ScenarioCorpus:
    profile_id: str
    scenarios: tuple[ConsumerLabScenario, ...]

    def __post_init__(self) -> None:
        _require_identifier(self.profile_id, "scenario corpus profile id")
        if (
            type(self.scenarios) is not tuple
            or not self.scenarios
            or len(self.scenarios) > 128
            or any(type(item) is not ConsumerLabScenario for item in self.scenarios)
        ):
            raise TypeError("scenario corpus must contain a bounded exact scenario tuple")
        scenario_ids = tuple(item.scenario_id for item in self.scenarios)
        if scenario_ids != tuple(sorted(set(scenario_ids), key=utf16_sort_key)):
            raise ValueError("scenario corpus must be canonical and unique")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": SCENARIO_SCHEMA,
            "profileId": self.profile_id,
            "scenarios": [item.to_mapping() for item in self.scenarios],
        }


@dataclass(frozen=True, slots=True)
class GitSourceSnapshot:
    head: str
    dirty_paths: tuple[str, ...]
    source_kind: SourceKind

    def __post_init__(self) -> None:
        if _GIT_COMMIT.fullmatch(self.head) is None:
            raise ValueError("Git source snapshot head is invalid")
        if (
            type(self.dirty_paths) is not tuple
            or self.dirty_paths != tuple(sorted(set(self.dirty_paths), key=utf16_sort_key))
            or any(not is_safe_relative_path(path) for path in self.dirty_paths)
        ):
            raise ValueError("Git source dirty paths must be safe and canonical")
        if self.source_kind not in {"commit", "worktree"}:
            raise ValueError("Git source kind is invalid")
        if (self.source_kind == "commit") != (not self.dirty_paths):
            raise ValueError("Git source kind must agree with dirty paths")

    def to_mapping(self) -> dict[str, object]:
        return {
            "head": self.head,
            "dirtyPaths": list(self.dirty_paths),
            "sourceKind": self.source_kind,
        }


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        _require_path(self.path, "contract manifest path")
        _require_sha256(self.sha256, "contract manifest digest")
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ValueError("contract manifest file size must be non-negative")

    def to_mapping(self) -> dict[str, object]:
        return {"path": self.path, "sha256": self.sha256, "sizeBytes": self.size_bytes}


def _require_identifier(value: object, name: str) -> None:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical identifier")


def _require_sha256(value: object, name: str) -> None:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be lowercase SHA-256 hexadecimal")


def _require_path(value: object, name: str) -> None:
    if (
        type(value) is not str
        or len(value.encode("utf-8")) > 4_096
        or not is_safe_relative_path(value)
    ):
        raise ValueError(f"{name} must be a safe bounded repository path")


def _require_text(value: object, name: str, *, maximum_bytes: int) -> None:
    if (
        type(value) is not str
        or not value
        or len(value.encode("utf-8")) > maximum_bytes
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(f"{name} must be bounded Unicode scalar text")

"""Bounded strict JSON admission for target-owned laboratory contracts."""

from __future__ import annotations

from typing import Final, cast

from ci_coordinator.consumer_contract_lab.model import (
    PROFILE_SCHEMA,
    SCENARIO_SCHEMA,
    ChangeStatus,
    ConsumerLabProfile,
    ConsumerLabScenario,
    ConsumerRepository,
    EventName,
    ExpectedMode,
    ExpectedOutcome,
    FileBinding,
    ScenarioChange,
    ScenarioCorpus,
)
from ci_coordinator.kernel import StrictJsonError, canonical_json, load_strict_json

MAX_PROFILE_BYTES: Final = 131_072
MAX_SCENARIO_CORPUS_BYTES: Final = 1_048_576


class ConsumerLabAdmissionError(ValueError):
    """A target-owned laboratory contract is outside the finite language."""


def parse_consumer_lab_profile(content: bytes) -> ConsumerLabProfile:
    try:
        value = load_strict_json(content, max_bytes=MAX_PROFILE_BYTES)
        _require_canonical_line(content, value)
        root = _exact_object(
            value,
            {
                "schemaVersion",
                "profileId",
                "expectedCoordinatorCommit",
                "repository",
                "workflowPath",
                "eventSurface",
                "targetArtifactsDirectory",
                "dynamicPolicy",
                "scenarioCorpus",
                "targetBindings",
            },
        )
        if root["schemaVersion"] != PROFILE_SCHEMA:
            raise ValueError("consumer lab profile schema is unsupported")
        repository = _exact_object(
            root["repository"],
            {"installationId", "repositoryId", "owner", "name", "defaultBranch"},
        )
        return ConsumerLabProfile(
            profile_id=_text(root["profileId"]),
            expected_coordinator_commit=_text(root["expectedCoordinatorCommit"]),
            repository=ConsumerRepository(
                installation_id=_integer(repository["installationId"]),
                repository_id=_integer(repository["repositoryId"]),
                owner=_text(repository["owner"]),
                name=_text(repository["name"]),
                default_branch=_text(repository["defaultBranch"]),
            ),
            workflow_path=_text(root["workflowPath"]),
            event_surface=tuple(_event_name(item) for item in _array(root["eventSurface"])),
            target_artifacts_directory=_text(root["targetArtifactsDirectory"]),
            dynamic_policy=_file_binding(root["dynamicPolicy"]),
            scenario_corpus=_file_binding(root["scenarioCorpus"]),
            target_bindings=tuple(_file_binding(item) for item in _array(root["targetBindings"])),
        )
    except (KeyError, StrictJsonError, TypeError, ValueError) as error:
        raise ConsumerLabAdmissionError("consumer lab profile is invalid") from error


def parse_scenario_corpus(content: bytes) -> ScenarioCorpus:
    try:
        value = load_strict_json(content, max_bytes=MAX_SCENARIO_CORPUS_BYTES)
        _require_canonical_line(content, value)
        root = _exact_object(value, {"schemaVersion", "profileId", "scenarios"})
        if root["schemaVersion"] != SCENARIO_SCHEMA:
            raise ValueError("consumer lab scenario schema is unsupported")
        return ScenarioCorpus(
            profile_id=_text(root["profileId"]),
            scenarios=tuple(_scenario(item) for item in _array(root["scenarios"])),
        )
    except (KeyError, StrictJsonError, TypeError, ValueError) as error:
        raise ConsumerLabAdmissionError("consumer lab scenario corpus is invalid") from error


def _scenario(value: object) -> ConsumerLabScenario:
    record = _exact_object(
        value,
        {"scenarioId", "eventName", "changes", "expectedOutcome"},
    )
    expected = _exact_object(record["expectedOutcome"], {"mode", "selectedJobs"})
    return ConsumerLabScenario(
        scenario_id=_text(record["scenarioId"]),
        event_name=_event_name(record["eventName"]),
        changes=tuple(_change(item) for item in _array(record["changes"])),
        expected_outcome=ExpectedOutcome(
            mode=_expected_mode(expected["mode"]),
            selected_jobs=tuple(_text(item) for item in _array(expected["selectedJobs"])),
        ),
    )


def _change(value: object) -> ScenarioChange:
    record = _exact_object(value, {"path", "status", "previousPath"})
    previous = record["previousPath"]
    return ScenarioChange(
        path=_text(record["path"]),
        status=_change_status(record["status"]),
        previous_path=None if previous is None else _text(previous),
    )


def _file_binding(value: object) -> FileBinding:
    record = _exact_object(value, {"path", "sha256"})
    return FileBinding(path=_text(record["path"]), sha256=_text(record["sha256"]))


def _require_canonical_line(content: bytes, value: object) -> None:
    if canonical_json(value) + b"\n" != content:
        raise ValueError("consumer lab contract must be canonical JSON with one newline")


def _exact_object(value: object, fields: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise ValueError("consumer lab object shape is invalid")
    return cast(dict[str, object], value)


def _array(value: object) -> list[object]:
    if type(value) is not list:
        raise ValueError("consumer lab array is invalid")
    return cast(list[object], value)


def _text(value: object) -> str:
    if type(value) is not str:
        raise ValueError("consumer lab text is invalid")
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise ValueError("consumer lab integer is invalid")
    return value


def _event_name(value: object) -> EventName:
    text = _text(value)
    if text not in {"merge_group", "pull_request", "push"}:
        raise ValueError("consumer lab event is invalid")
    return cast(EventName, text)


def _expected_mode(value: object) -> ExpectedMode:
    text = _text(value)
    if text not in {"selected", "fallback"}:
        raise ValueError("consumer lab expected mode is invalid")
    return cast(ExpectedMode, text)


def _change_status(value: object) -> ChangeStatus:
    text = _text(value)
    if text not in {"added", "modified", "removed", "renamed", "copied", "changed"}:
        raise ValueError("consumer lab change status is invalid")
    return cast(ChangeStatus, text)

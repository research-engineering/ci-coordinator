"""Bounded strict JSON admission for target execution registries."""

from __future__ import annotations

import re
from typing import Final, cast

from ci_coordinator.execution_orchestration.adapter_bundle import (
    MAX_TARGET_ADAPTER_FILES,
    TargetAdapterFileBinding,
)
from ci_coordinator.execution_orchestration.target_registry import (
    MAX_TARGET_SERVICE_PROFILE_IDS,
    ExecutionKind,
    TargetExecutionRegistry,
    TargetJobBinding,
    TargetProfileBinding,
    TargetWorkflowBinding,
)
from ci_coordinator.kernel import StrictJsonError, load_strict_json

TARGET_EXECUTION_REGISTRY_SCHEMA_VERSION: Final = "dynamic-ci-target-execution-registry/v1"
TARGET_EXECUTION_REGISTRY_PATH: Final = ".ci-coordinator/execution-registry.v1.json"
MAX_TARGET_EXECUTION_REGISTRY_BYTES: Final = 131_072
MAX_TARGET_EXECUTION_PROFILES: Final = 64
MAX_TARGET_EXECUTION_WORKFLOWS: Final = 32

_IDENTIFIER: Final = re.compile(r"[a-z][a-z0-9._-]{0,63}")
_VERSION: Final = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}")


def parse_target_execution_registry(content: bytes) -> TargetExecutionRegistry | None:
    try:
        value = load_strict_json(content, max_bytes=MAX_TARGET_EXECUTION_REGISTRY_BYTES)
        root = _exact_object(
            value,
            {"adapterFiles", "generator", "profiles", "schemaVersion", "workflows"},
        )
        if root["schemaVersion"] != TARGET_EXECUTION_REGISTRY_SCHEMA_VERSION:
            return None
        generator = _exact_object(root["generator"], {"id", "version"})
        raw_profiles = root["profiles"]
        if type(raw_profiles) is not list or not 1 <= len(raw_profiles) <= (
            MAX_TARGET_EXECUTION_PROFILES
        ):
            return None
        raw_workflows = root["workflows"]
        if type(raw_workflows) is not list or not 1 <= len(raw_workflows) <= (
            MAX_TARGET_EXECUTION_WORKFLOWS
        ):
            return None
        raw_adapter_files = root["adapterFiles"]
        if type(raw_adapter_files) is not list or not 1 <= len(raw_adapter_files) <= (
            MAX_TARGET_ADAPTER_FILES
        ):
            return None
        return TargetExecutionRegistry(
            generator_id=_matching_text(generator["id"], _IDENTIFIER),
            generator_version=_matching_text(generator["version"], _VERSION),
            adapter_files=tuple(_adapter_file(item) for item in raw_adapter_files),
            workflows=tuple(_workflow(item) for item in raw_workflows),
            profiles=tuple(_profile(item) for item in raw_profiles),
        )
    except (KeyError, StrictJsonError, TypeError, ValueError):
        return None


def _adapter_file(value: object) -> TargetAdapterFileBinding:
    record = _exact_object(value, {"path", "sha256"})
    return TargetAdapterFileBinding(
        path=_bounded_text(record["path"], maximum_bytes=256),
        sha256=_bounded_text(record["sha256"], maximum_bytes=64),
    )


def _profile(value: object) -> TargetProfileBinding:
    record = _exact_object(
        value,
        {
            "profileId",
            "workflowPath",
            "jobId",
            "executionKind",
            "runnerProfileId",
            "permissionProfileId",
            "credentialProfileId",
            "fixtureProfileId",
            "serviceProfileIds",
            "capacityClassId",
        },
    )
    services = record["serviceProfileIds"]
    if type(services) is not list:
        raise ValueError("service profile ids must be an array")
    if len(services) > MAX_TARGET_SERVICE_PROFILE_IDS:
        raise ValueError("service profile ids exceed the admitted maximum")
    return TargetProfileBinding(
        profile_id=_bounded_text(record["profileId"], maximum_bytes=64),
        workflow_path=_bounded_text(record["workflowPath"], maximum_bytes=256),
        job_id=_bounded_text(record["jobId"], maximum_bytes=128),
        execution_kind=_execution_kind(record["executionKind"]),
        runner_profile_id=_bounded_text(record["runnerProfileId"], maximum_bytes=64),
        permission_profile_id=_bounded_text(record["permissionProfileId"], maximum_bytes=64),
        credential_profile_id=_bounded_text(record["credentialProfileId"], maximum_bytes=64),
        fixture_profile_id=_bounded_text(record["fixtureProfileId"], maximum_bytes=64),
        service_profile_ids=tuple(_bounded_text(item, maximum_bytes=64) for item in services),
        capacity_class_id=_bounded_text(record["capacityClassId"], maximum_bytes=64),
    )


def _workflow(value: object) -> TargetWorkflowBinding:
    record = _exact_object(
        value,
        {
            "executionJobs",
            "executionKind",
            "fallbackJobId",
            "gateJobId",
            "gateSignalName",
            "planRequestJobId",
            "planRequestWorkflowRef",
            "planJobId",
            "requiredJobIds",
            "workflowPath",
        },
    )
    execution_jobs = record["executionJobs"]
    required_job_ids = record["requiredJobIds"]
    if type(execution_jobs) is not list or type(required_job_ids) is not list:
        raise ValueError("workflow job bindings must be arrays")
    return TargetWorkflowBinding(
        workflow_path=_bounded_text(record["workflowPath"], maximum_bytes=256),
        execution_kind=_execution_kind(record["executionKind"]),
        execution_jobs=tuple(_job(item) for item in execution_jobs),
        plan_request_job_id=_bounded_text(record["planRequestJobId"], maximum_bytes=128),
        plan_request_workflow_ref=_bounded_text(
            record["planRequestWorkflowRef"],
            maximum_bytes=512,
        ),
        plan_job_id=_bounded_text(record["planJobId"], maximum_bytes=128),
        fallback_job_id=_optional_job_id(record["fallbackJobId"]),
        gate_job_id=_bounded_text(record["gateJobId"], maximum_bytes=128),
        gate_signal_name=_bounded_text(record["gateSignalName"], maximum_bytes=256),
        required_job_ids=tuple(_bounded_text(item, maximum_bytes=128) for item in required_job_ids),
    )


def _job(value: object) -> TargetJobBinding:
    record = _exact_object(value, {"jobId", "needs"})
    needs = record["needs"]
    if type(needs) is not list:
        raise ValueError("workflow execution job dependencies must be an array")
    return TargetJobBinding(
        job_id=_bounded_text(record["jobId"], maximum_bytes=128),
        needs=tuple(_bounded_text(item, maximum_bytes=128) for item in needs),
    )


def _execution_kind(value: object) -> ExecutionKind:
    if value == "witness-shards":
        return "witness-shards"
    if value == "native-job-set":
        return "native-job-set"
    raise ValueError("target execution kind is invalid")


def _optional_job_id(value: object) -> str | None:
    return None if value is None else _bounded_text(value, maximum_bytes=128)


def _exact_object(value: object, keys: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("target registry object shape is invalid")
    return cast(dict[str, object], value)


def _bounded_text(value: object, *, maximum_bytes: int) -> str:
    if (
        type(value) is not str
        or not value
        or len(value.encode("utf-8")) > maximum_bytes
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise ValueError("target registry text is invalid")
    return value


def _matching_text(value: object, pattern: re.Pattern[str]) -> str:
    if type(value) is not str or pattern.fullmatch(value) is None:
        raise ValueError("target registry identity is invalid")
    return value

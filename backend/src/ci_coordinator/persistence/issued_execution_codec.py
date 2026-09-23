"""Strict decoder for the non-executable signed execution projection."""

from __future__ import annotations

from typing import cast

from ci_coordinator.execution_orchestration import ProviderSignal
from ci_coordinator.plan_issuance import (
    FullCiExecution,
    SelectedExecution,
    SignedExecution,
    SignedExecutionShard,
    SignedNativeProfileExecution,
    SignedProfileExecution,
)
from ci_coordinator.validation_contract import ExecutionProfile, ShardingPolicy


def decode_execution(value: object) -> SignedExecution:
    mapping = _mapping(value, "execution")
    mode = mapping.get("mode")
    if mode == "full-ci":
        record = _record(mapping, {"mode", "reason"}, "FullCI execution")
        return FullCiExecution(mode="full-ci", reason=_text(record["reason"], "fallback reason"))
    if mode != "selected":
        raise ValueError("signed execution mode is unsupported")
    record = _record(
        mapping,
        {
            "mode",
            "executionKind",
            "workflowPath",
            "gateProviderSignal",
            "verifiedPlanId",
            "deterministicPlanId",
            "catalogHash",
            "targetRegistryHash",
            "selectedObligationIds",
            "omittedObligationIds",
            "selectedWitnessIds",
            "testManifestId",
            "profiles",
        },
        "selected execution",
    )
    execution_kind = record["executionKind"]
    if execution_kind not in {"witness-shards", "native-job-set"}:
        raise ValueError("selected execution kind is unsupported")
    return SelectedExecution(
        mode="selected",
        execution_kind=execution_kind,
        workflow_path=_text(record["workflowPath"], "execution workflow path"),
        gate_provider_signal=_provider_signal(record["gateProviderSignal"]),
        verified_plan_id=_text(record["verifiedPlanId"], "verified plan id"),
        deterministic_plan_id=_text(
            record["deterministicPlanId"],
            "deterministic plan id",
        ),
        catalog_hash=_text(record["catalogHash"], "validation catalog hash"),
        target_registry_hash=_text(
            record["targetRegistryHash"],
            "target execution registry hash",
        ),
        selected_obligation_ids=_text_tuple(
            record["selectedObligationIds"],
            "selected obligation ids",
        ),
        omitted_obligation_ids=_text_tuple(
            record["omittedObligationIds"],
            "omitted obligation ids",
            allow_empty=True,
        ),
        selected_witness_ids=_text_tuple(
            record["selectedWitnessIds"],
            "selected witness ids",
        ),
        test_manifest_id=_optional_text(record["testManifestId"], "test manifest id"),
        profiles=tuple(_profile(item) for item in _array(record["profiles"], "execution profiles")),
    )


def _profile(value: object) -> SignedProfileExecution | SignedNativeProfileExecution:
    mapping = _mapping(value, "profile execution")
    execution_kind = mapping.get("executionKind")
    if execution_kind == "native-job-set":
        record = _record(
            mapping,
            {"executionKind", "profile", "jobId", "witnessIds"},
            "native profile execution",
        )
        return SignedNativeProfileExecution(
            profile=_execution_profile(record["profile"]),
            job_id=_text(record["jobId"], "native profile job id"),
            witness_ids=_text_tuple(
                record["witnessIds"],
                "native profile witness ids",
            ),
        )
    if execution_kind != "witness-shards":
        raise ValueError("profile execution kind is unsupported")
    record = _record(
        mapping,
        {
            "executionKind",
            "profile",
            "shards",
            "maxParallel",
            "capacityMode",
            "capacityReason",
        },
        "sharded profile execution",
    )
    mode = record["capacityMode"]
    if mode not in {"optimized", "conservative"}:
        raise ValueError("capacity mode is unsupported")
    return SignedProfileExecution(
        profile=_execution_profile(record["profile"]),
        shards=tuple(_shard(item) for item in _array(record["shards"], "execution shards")),
        max_parallel=_integer(record["maxParallel"], "profile max parallel"),
        capacity_mode=mode,
        capacity_reason=_optional_text(record["capacityReason"], "capacity reason"),
    )


def _execution_profile(value: object) -> ExecutionProfile:
    record = _record(
        value,
        {
            "profileId",
            "runnerProfileId",
            "permissionProfileId",
            "credentialProfileId",
            "fixtureProfileId",
            "serviceProfileIds",
            "capacityClassId",
            "shardingPolicy",
        },
        "execution profile",
    )
    return ExecutionProfile(
        profile_id=_text(record["profileId"], "profile id"),
        runner_profile_id=_text(record["runnerProfileId"], "runner profile id"),
        permission_profile_id=_text(
            record["permissionProfileId"],
            "permission profile id",
        ),
        credential_profile_id=_text(
            record["credentialProfileId"],
            "credential profile id",
        ),
        fixture_profile_id=_text(record["fixtureProfileId"], "fixture profile id"),
        service_profile_ids=_text_tuple(
            record["serviceProfileIds"],
            "service profile ids",
            allow_empty=True,
        ),
        capacity_class_id=_text(record["capacityClassId"], "capacity class id"),
        sharding_policy=_sharding_policy(record["shardingPolicy"]),
    )


def _sharding_policy(value: object) -> ShardingPolicy:
    record = _record(
        value,
        {
            "maxShards",
            "maxParallel",
            "maxItemsPerShard",
            "setupSecondsPerShard",
            "cpuWeight",
            "wallWeight",
            "operatorWeight",
        },
        "sharding policy",
    )
    return ShardingPolicy(
        max_shards=_integer(record["maxShards"], "maximum shards"),
        max_parallel=_integer(record["maxParallel"], "maximum parallel shards"),
        max_items_per_shard=_integer(
            record["maxItemsPerShard"],
            "maximum items per shard",
        ),
        setup_seconds_per_shard=_number(
            record["setupSecondsPerShard"],
            "shard setup seconds",
        ),
        cpu_weight=_number(record["cpuWeight"], "CPU weight"),
        wall_weight=_number(record["wallWeight"], "wall-time weight"),
        operator_weight=_number(record["operatorWeight"], "operator-cost weight"),
    )


def _shard(value: object) -> SignedExecutionShard:
    record = _record(
        value,
        {
            "shardId",
            "manifestId",
            "executionProfileId",
            "witnessIds",
            "testIds",
            "providerSignal",
        },
        "execution shard",
    )
    return SignedExecutionShard(
        shard_id=_text(record["shardId"], "shard id"),
        manifest_id=_text(record["manifestId"], "manifest id"),
        execution_profile_id=_text(record["executionProfileId"], "execution profile id"),
        witness_ids=_text_tuple(record["witnessIds"], "shard witness ids"),
        test_ids=_text_tuple(record["testIds"], "shard test ids"),
        provider_signal=_provider_signal(record["providerSignal"]),
    )


def _provider_signal(value: object) -> ProviderSignal:
    record = _record(
        value,
        {
            "signalId",
            "jobName",
            "kind",
            "executionProfileId",
            "shardId",
            "workflowPath",
            "jobId",
        },
        "provider signal",
    )
    kind = record["kind"]
    if kind not in {"derived-shard", "declared-native"}:
        raise ValueError("provider signal kind is unsupported")
    return ProviderSignal(
        signal_id=_text(record["signalId"], "provider signal id"),
        job_name=_text(record["jobName"], "provider signal job name"),
        kind=kind,
        execution_profile_id=_optional_text(
            record["executionProfileId"],
            "provider signal profile id",
        ),
        shard_id=_optional_text(record["shardId"], "provider signal shard id"),
        workflow_path=_optional_text(
            record["workflowPath"],
            "provider signal workflow path",
        ),
        job_id=_optional_text(record["jobId"], "provider signal job id"),
    )


def _record(value: object, expected: set[str], context: str) -> dict[str, object]:
    mapping = _mapping(value, context)
    if set(mapping) != expected:
        raise ValueError(f"{context} has unexpected or missing fields")
    return mapping


def _mapping(value: object, context: str) -> dict[str, object]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError(f"{context} must be an object")
    return cast(dict[str, object], value)


def _array(value: object, context: str) -> list[object]:
    if type(value) is not list:
        raise ValueError(f"{context} must be an array")
    return cast(list[object], value)


def _text_tuple(value: object, context: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    items = _array(value, context)
    if not allow_empty and not items:
        raise ValueError(f"{context} must not be empty")
    return tuple(_text(item, context + " item") for item in items)


def _text(value: object, context: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{context} must be non-empty text")
    return value


def _optional_text(value: object, context: str) -> str | None:
    return None if value is None else _text(value, context)


def _integer(value: object, context: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{context} must be an integer")
    return value


def _number(value: object, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{context} must be a number")
    return float(value)

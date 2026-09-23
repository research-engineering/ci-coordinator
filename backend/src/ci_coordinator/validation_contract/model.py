"""Immutable validation obligations and executable witness profiles."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.validation_contract.depth import ValidationDepth, depth_rank

_IDENTIFIER = re.compile(r"[a-z][a-z0-9._-]{0,63}")
_MAX_PROFILE_SHARDS = 256
MAX_SERVICE_PROFILE_IDS = 16


@dataclass(frozen=True, slots=True)
class ShardingPolicy:
    max_shards: int
    max_parallel: int
    max_items_per_shard: int
    setup_seconds_per_shard: float
    cpu_weight: float = 1.0
    wall_weight: float = 1.0
    operator_weight: float = 1.0

    def __post_init__(self) -> None:
        if type(self.max_shards) is not int or not 1 <= self.max_shards <= _MAX_PROFILE_SHARDS:
            raise ValueError("max_shards must be an integer in [1, 256]")
        if type(self.max_parallel) is not int or not 1 <= self.max_parallel <= self.max_shards:
            raise ValueError("max_parallel must be an integer in [1, max_shards]")
        if type(self.max_items_per_shard) is not int or not 1 <= self.max_items_per_shard <= 10_000:
            raise ValueError("max_items_per_shard must be an integer in [1, 10000]")
        if not _bounded_number(self.setup_seconds_per_shard, maximum=3600):
            raise ValueError("setup_seconds_per_shard must be a finite number in [0, 3600]")
        for name, value in (
            ("cpu_weight", self.cpu_weight),
            ("wall_weight", self.wall_weight),
            ("operator_weight", self.operator_weight),
        ):
            if not _bounded_number(value, maximum=1000):
                raise ValueError(f"{name} must be a finite number in [0, 1000]")
        if not any((self.cpu_weight, self.wall_weight, self.operator_weight)):
            raise ValueError("at least one sharding objective weight must be positive")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "maxShards": self.max_shards,
            "maxParallel": self.max_parallel,
            "maxItemsPerShard": self.max_items_per_shard,
            "setupSecondsPerShard": float(self.setup_seconds_per_shard),
            "cpuWeight": float(self.cpu_weight),
            "wallWeight": float(self.wall_weight),
            "operatorWeight": float(self.operator_weight),
        }


@dataclass(frozen=True, slots=True)
class ExecutionProfile:
    profile_id: str
    runner_profile_id: str
    permission_profile_id: str
    credential_profile_id: str
    fixture_profile_id: str
    service_profile_ids: tuple[str, ...]
    capacity_class_id: str
    sharding_policy: ShardingPolicy

    def __post_init__(self) -> None:
        if type(self.sharding_policy) is not ShardingPolicy:
            raise TypeError("sharding_policy must be an exact ShardingPolicy")
        _require_identifier(self.profile_id, field_name="profile_id")
        _require_identifier(self.runner_profile_id, field_name="runner_profile_id")
        _require_identifier(self.permission_profile_id, field_name="permission_profile_id")
        _require_identifier(self.credential_profile_id, field_name="credential_profile_id")
        _require_identifier(self.fixture_profile_id, field_name="fixture_profile_id")
        _require_canonical_identifiers(
            self.service_profile_ids,
            field_name="service_profile_ids",
            allow_empty=True,
        )
        if len(self.service_profile_ids) > MAX_SERVICE_PROFILE_IDS:
            raise ValueError("service_profile_ids exceeds the admitted maximum")
        _require_identifier(self.capacity_class_id, field_name="capacity_class_id")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "profileId": self.profile_id,
            "runnerProfileId": self.runner_profile_id,
            "permissionProfileId": self.permission_profile_id,
            "credentialProfileId": self.credential_profile_id,
            "fixtureProfileId": self.fixture_profile_id,
            "serviceProfileIds": list(self.service_profile_ids),
            "capacityClassId": self.capacity_class_id,
            "shardingPolicy": self.sharding_policy.to_identity_mapping(),
        }


@dataclass(frozen=True, slots=True)
class ExecutableWitness:
    witness_id: str
    execution_profile_id: str
    supported_depths: tuple[ValidationDepth, ...]

    def __post_init__(self) -> None:
        _require_exact_tuple(self.supported_depths, field_name="supported_depths")
        _require_identifier(self.witness_id, field_name="witness_id")
        _require_identifier(self.execution_profile_id, field_name="execution_profile_id")
        if not self.supported_depths:
            raise ValueError("supported_depths must not be empty")
        if tuple(sorted(set(self.supported_depths), key=depth_rank)) != self.supported_depths:
            raise ValueError("supported_depths must be canonical")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "witnessId": self.witness_id,
            "executionProfileId": self.execution_profile_id,
            "supportedDepths": list(self.supported_depths),
        }


@dataclass(frozen=True, slots=True)
class ValidationObligation:
    obligation_id: str
    responsibility_paths: tuple[str, ...]
    responsibility_risk_classes: tuple[str, ...]
    required_witness_ids: tuple[str, ...]
    default_depth: ValidationDepth
    full_depth: ValidationDepth
    omit_allowed: bool

    def __post_init__(self) -> None:
        _require_identifier(self.obligation_id, field_name="obligation_id")
        _require_canonical_strings(
            self.responsibility_paths,
            field_name="responsibility_paths",
            allow_empty=True,
        )
        _require_canonical_identifiers(
            self.responsibility_risk_classes,
            field_name="responsibility_risk_classes",
            allow_empty=True,
        )
        _require_canonical_identifiers(
            self.required_witness_ids,
            field_name="required_witness_ids",
        )
        if depth_rank(self.default_depth) > depth_rank(self.full_depth):
            raise ValueError("default_depth must not exceed full_depth")
        if type(self.omit_allowed) is not bool:
            raise TypeError("omit_allowed must be a boolean")
        if self.omit_allowed and not (
            self.responsibility_paths or self.responsibility_risk_classes
        ):
            raise ValueError("an omittable obligation must own a responsibility surface")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "obligationId": self.obligation_id,
            "responsibility": {
                "paths": list(self.responsibility_paths),
                "riskClasses": list(self.responsibility_risk_classes),
            },
            "requiredWitnessIds": list(self.required_witness_ids),
            "defaultDepth": self.default_depth,
            "fullDepth": self.full_depth,
            "omitAllowed": self.omit_allowed,
        }


def _bounded_number(value: object, *, maximum: float) -> bool:
    if type(value) is int:
        number = float(value)
    elif type(value) is float:
        number = value
    else:
        return False
    return math.isfinite(number) and 0 <= number <= maximum


def _require_identifier(value: object, *, field_name: str) -> None:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a canonical identifier")


def _require_canonical_identifiers(
    values: tuple[str, ...],
    *,
    field_name: str,
    allow_empty: bool = False,
) -> None:
    for value in values:
        _require_identifier(value, field_name=field_name + " item")
    _require_canonical_strings(values, field_name=field_name, allow_empty=allow_empty)


def _require_canonical_strings(
    values: tuple[str, ...],
    *,
    field_name: str,
    allow_empty: bool = False,
) -> None:
    _require_exact_tuple(values, field_name=field_name)
    if (not allow_empty and not values) or any(
        type(value) is not str or not value for value in values
    ):
        raise ValueError(f"{field_name} must contain non-empty strings")
    if tuple(sorted(set(values), key=utf16_sort_key)) != values:
        raise ValueError(f"{field_name} must be canonical")


def _require_exact_tuple(values: object, *, field_name: str) -> None:
    if type(values) is not tuple:
        raise TypeError(f"{field_name} must be an exact tuple")

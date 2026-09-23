"""Typed, non-executable payload projected from verified execution authority."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from ci_coordinator.execution_orchestration import ExecutionKind, ProviderSignal
from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.runner_capacity import ShardPlan, TrustedExecutionProjection
from ci_coordinator.validation_contract import ExecutableWitness, ExecutionProfile
from ci_coordinator.verification_core import VerifiedPlan

_JOB_ID = re.compile(r"[A-Za-z_][A-Za-z0-9_-]{0,127}")


@dataclass(frozen=True, slots=True)
class SignedExecutionShard:
    shard_id: str
    manifest_id: str
    execution_profile_id: str
    witness_ids: tuple[str, ...]
    test_ids: tuple[str, ...]
    provider_signal: ProviderSignal

    def __post_init__(self) -> None:
        _require_canonical_ids(self.witness_ids, field_name="shard witness ids")
        _require_canonical_ids(self.test_ids, field_name="shard test ids")
        expected = ProviderSignal.derive(
            execution_profile_id=self.execution_profile_id,
            shard_id=self.shard_id,
        )
        if self.provider_signal != expected:
            raise ValueError("execution shard provider signal is not derived")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "shardId": self.shard_id,
            "manifestId": self.manifest_id,
            "executionProfileId": self.execution_profile_id,
            "witnessIds": list(self.witness_ids),
            "testIds": list(self.test_ids),
            "providerSignal": self.provider_signal.to_identity_mapping(),
        }


@dataclass(frozen=True, slots=True)
class SignedProfileExecution:
    profile: ExecutionProfile
    shards: tuple[SignedExecutionShard, ...]
    max_parallel: int
    capacity_mode: Literal["optimized", "conservative"]
    capacity_reason: str | None

    def __post_init__(self) -> None:
        if type(self.profile) is not ExecutionProfile:
            raise TypeError("signed profile execution requires an exact profile")
        if type(self.shards) is not tuple or any(
            type(item) is not SignedExecutionShard for item in self.shards
        ):
            raise TypeError("signed profile shards must be exact")
        if not self.shards or any(
            item.execution_profile_id != self.profile.profile_id for item in self.shards
        ):
            raise ValueError("signed profile execution must contain one non-empty profile")
        shard_ids = tuple(item.shard_id for item in self.shards)
        if tuple(sorted(set(shard_ids), key=utf16_sort_key)) != shard_ids:
            raise ValueError("signed profile shards must be canonical")
        if (
            type(self.max_parallel) is not int
            or not 1 <= self.max_parallel <= len(self.shards)
            or self.max_parallel > self.profile.sharding_policy.max_parallel
        ):
            raise ValueError("signed profile parallelism is outside its static policy")
        if self.capacity_mode == "optimized":
            if self.capacity_reason is not None:
                raise ValueError("optimized execution cannot carry a fallback reason")
        elif self.capacity_mode == "conservative":
            if type(self.capacity_reason) is not str or not self.capacity_reason:
                raise ValueError("conservative execution requires a bounded reason")
        else:
            raise ValueError("capacity mode is not admitted")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "executionKind": "witness-shards",
            "profile": self.profile.to_identity_mapping(),
            "shards": [item.to_identity_mapping() for item in self.shards],
            "maxParallel": self.max_parallel,
            "capacityMode": self.capacity_mode,
            "capacityReason": self.capacity_reason,
        }


@dataclass(frozen=True, slots=True)
class SignedNativeProfileExecution:
    profile: ExecutionProfile
    job_id: str
    witness_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.profile) is not ExecutionProfile:
            raise TypeError("native profile execution requires an exact profile")
        if type(self.job_id) is not str or _JOB_ID.fullmatch(self.job_id) is None:
            raise ValueError("native profile execution job id is not canonical")
        _require_canonical_ids(self.witness_ids, field_name="native profile witness ids")

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "executionKind": "native-job-set",
            "profile": self.profile.to_identity_mapping(),
            "jobId": self.job_id,
            "witnessIds": list(self.witness_ids),
        }


type SignedSelectedProfile = SignedProfileExecution | SignedNativeProfileExecution


@dataclass(frozen=True, slots=True)
class SelectedExecution:
    mode: Literal["selected"]
    execution_kind: ExecutionKind
    workflow_path: str
    gate_provider_signal: ProviderSignal
    verified_plan_id: str
    deterministic_plan_id: str
    catalog_hash: str
    target_registry_hash: str
    selected_obligation_ids: tuple[str, ...]
    omitted_obligation_ids: tuple[str, ...]
    selected_witness_ids: tuple[str, ...]
    test_manifest_id: str | None
    profiles: tuple[SignedSelectedProfile, ...]

    def __post_init__(self) -> None:
        if self.mode != "selected":
            raise ValueError("selected execution mode is invalid")
        if self.execution_kind not in {"witness-shards", "native-job-set"}:
            raise ValueError("selected execution kind is invalid")
        if type(self.workflow_path) is not str or not self.workflow_path:
            raise ValueError("selected execution workflow path must be non-empty")
        if type(self.gate_provider_signal) is not ProviderSignal:
            raise TypeError("selected execution gate signal must be exact")
        if (
            self.gate_provider_signal.kind != "declared-native"
            or self.gate_provider_signal.workflow_path != self.workflow_path
        ):
            raise ValueError("selected execution gate does not bind its workflow")
        for name, value in (
            ("verified_plan_id", self.verified_plan_id),
            ("deterministic_plan_id", self.deterministic_plan_id),
        ):
            if type(value) is not str or not value:
                raise ValueError(f"{name} must be non-empty")
        for name, value in (
            ("catalog_hash", self.catalog_hash),
            ("target_registry_hash", self.target_registry_hash),
        ):
            if (
                type(value) is not str
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{name} must be lowercase SHA-256 hexadecimal")
        _require_canonical_ids(
            self.selected_obligation_ids,
            field_name="selected obligation ids",
        )
        _require_canonical_ids(
            self.omitted_obligation_ids,
            field_name="omitted obligation ids",
            allow_empty=True,
        )
        if set(self.selected_obligation_ids) & set(self.omitted_obligation_ids):
            raise ValueError("selected and omitted obligation ids must be disjoint")
        _require_canonical_ids(self.selected_witness_ids, field_name="selected witness ids")
        if type(self.profiles) is not tuple:
            raise TypeError("selected execution profiles must be exact")
        profile_ids = tuple(item.profile.profile_id for item in self.profiles)
        _require_canonical_ids(profile_ids, field_name="selected execution profiles")
        executed_witnesses = (
            self._validate_sharded_profiles()
            if self.execution_kind == "witness-shards"
            else self._validate_native_profiles()
        )
        if executed_witnesses != set(self.selected_witness_ids):
            raise ValueError("selected execution must cover every selected witness")

    def _validate_sharded_profiles(self) -> set[str]:
        if any(type(item) is not SignedProfileExecution for item in self.profiles):
            raise TypeError("sharded execution profiles must be exact")
        if type(self.test_manifest_id) is not str or not self.test_manifest_id:
            raise ValueError("sharded execution must bind one exact test manifest")
        sharded = tuple(item for item in self.profiles if type(item) is SignedProfileExecution)
        manifest_ids = {shard.manifest_id for profile in sharded for shard in profile.shards}
        if manifest_ids != {self.test_manifest_id}:
            raise ValueError("selected execution must bind one exact test manifest")
        shard_ids = [shard.shard_id for profile in sharded for shard in profile.shards]
        test_ids = [
            test_id for profile in sharded for shard in profile.shards for test_id in shard.test_ids
        ]
        if len(shard_ids) != len(set(shard_ids)):
            raise ValueError("selected execution shard identities must be unique")
        if len(test_ids) != len(set(test_ids)):
            raise ValueError("selected execution test identities must be unique")
        return {
            witness_id
            for profile in sharded
            for shard in profile.shards
            for witness_id in shard.witness_ids
        }

    def _validate_native_profiles(self) -> set[str]:
        if self.test_manifest_id is not None:
            raise ValueError("native execution cannot claim a test manifest")
        if any(type(item) is not SignedNativeProfileExecution for item in self.profiles):
            raise TypeError("native execution profiles must be exact")
        native = tuple(item for item in self.profiles if type(item) is SignedNativeProfileExecution)
        job_ids = tuple(item.job_id for item in native)
        if len(job_ids) != len(set(job_ids)):
            raise ValueError("native execution jobs must be unique")
        witness_ids = [witness_id for profile in native for witness_id in profile.witness_ids]
        if len(witness_ids) != len(set(witness_ids)):
            raise ValueError("native execution witness ownership must be unique")
        return set(witness_ids)

    @classmethod
    def project(
        cls,
        verified_plan: VerifiedPlan,
        projection: TrustedExecutionProjection,
    ) -> SelectedExecution:
        if (
            type(verified_plan) is not VerifiedPlan
            or type(projection) is not TrustedExecutionProjection
        ):
            raise TypeError("selected execution projection requires exact plan values")
        if projection.verified_plan_id != verified_plan.execution_plan_id:
            raise ValueError("execution projection does not bind the verified plan")
        selected_witness_ids = tuple(item.witness_id for item in verified_plan.selected_witnesses)
        witness_by_id = {item.witness_id: item for item in verified_plan.catalog.witnesses}
        selected_profile_ids = tuple(
            sorted(
                {
                    witness_by_id[witness_id].execution_profile_id
                    for witness_id in selected_witness_ids
                },
                key=utf16_sort_key,
            )
        )
        if selected_profile_ids != projection.selected_profile_ids:
            raise ValueError("execution projection does not bind the selected profiles")
        registry = projection.target_registry
        workflow = registry.workflow(projection.workflow_path)
        if workflow is None or workflow.execution_kind != projection.execution_kind:
            raise ValueError("execution projection workflow is not admitted")
        bindings = {item.profile_id: item for item in registry.profiles}
        profile_by_id = {item.profile_id: item for item in verified_plan.catalog.execution_profiles}
        profiles, test_manifest_id = _project_profiles(
            verified_plan=verified_plan,
            projection=projection,
            selected_profile_ids=selected_profile_ids,
            selected_witness_ids=selected_witness_ids,
            witness_by_id=witness_by_id,
            profile_by_id=profile_by_id,
            job_by_profile_id={
                profile_id: bindings[profile_id].job_id for profile_id in selected_profile_ids
            },
        )
        return cls(
            mode="selected",
            execution_kind=projection.execution_kind,
            workflow_path=projection.workflow_path,
            gate_provider_signal=workflow.provider_signal,
            verified_plan_id=verified_plan.execution_plan_id,
            deterministic_plan_id=verified_plan.deterministic_plan_id,
            catalog_hash=verified_plan.catalog.catalog_hash,
            target_registry_hash=projection.target_registry_hash,
            selected_obligation_ids=tuple(
                item.obligation_id for item in verified_plan.selected_obligations
            ),
            omitted_obligation_ids=tuple(
                item.obligation_id for item in verified_plan.omitted_obligations
            ),
            selected_witness_ids=selected_witness_ids,
            test_manifest_id=test_manifest_id,
            profiles=profiles,
        )

    def to_identity_mapping(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "executionKind": self.execution_kind,
            "workflowPath": self.workflow_path,
            "gateProviderSignal": self.gate_provider_signal.to_identity_mapping(),
            "verifiedPlanId": self.verified_plan_id,
            "deterministicPlanId": self.deterministic_plan_id,
            "catalogHash": self.catalog_hash,
            "targetRegistryHash": self.target_registry_hash,
            "selectedObligationIds": list(self.selected_obligation_ids),
            "omittedObligationIds": list(self.omitted_obligation_ids),
            "selectedWitnessIds": list(self.selected_witness_ids),
            "testManifestId": self.test_manifest_id,
            "profiles": [item.to_identity_mapping() for item in self.profiles],
        }


@dataclass(frozen=True, slots=True)
class FullCiExecution:
    mode: Literal["full-ci"]
    reason: str

    def __post_init__(self) -> None:
        if self.mode != "full-ci" or type(self.reason) is not str or not self.reason:
            raise ValueError("FullCI execution requires a non-empty reason")

    def to_identity_mapping(self) -> dict[str, str]:
        return {"mode": self.mode, "reason": self.reason}


type SignedExecution = SelectedExecution | FullCiExecution


def _project_profiles(
    *,
    verified_plan: VerifiedPlan,
    projection: TrustedExecutionProjection,
    selected_profile_ids: tuple[str, ...],
    selected_witness_ids: tuple[str, ...],
    witness_by_id: dict[str, ExecutableWitness],
    profile_by_id: dict[str, ExecutionProfile],
    job_by_profile_id: dict[str, str],
) -> tuple[tuple[SignedSelectedProfile, ...], str | None]:
    if projection.execution_kind == "native-job-set":
        return (
            tuple(
                SignedNativeProfileExecution(
                    profile=profile_by_id[profile_id],
                    job_id=job_by_profile_id[profile_id],
                    witness_ids=tuple(
                        witness_id
                        for witness_id in selected_witness_ids
                        if witness_by_id[witness_id].execution_profile_id == profile_id
                    ),
                )
                for profile_id in selected_profile_ids
            ),
            None,
        )
    shard_plan = projection.shard_plan
    if type(shard_plan) is not ShardPlan:
        raise ValueError("sharded execution projection has no shard plan")
    if shard_plan.manifest.catalog.catalog_hash != verified_plan.source_plan.catalog_hash:
        raise ValueError("shard plan catalog does not bind the verified plan")
    if set(shard_plan.manifest.selected_witness_ids) != set(selected_witness_ids):
        raise ValueError("test manifest does not cover the verified witnesses")
    test_by_id = {item.test_id: item for item in shard_plan.manifest.tests}
    return (
        tuple(
            SignedProfileExecution(
                profile=profile_by_id[profile_plan.profile_id],
                shards=tuple(
                    SignedExecutionShard(
                        shard_id=shard.shard_id,
                        manifest_id=shard.manifest_id,
                        execution_profile_id=shard.profile_id,
                        witness_ids=tuple(
                            sorted(
                                {test_by_id[test_id].witness_id for test_id in shard.test_ids},
                                key=utf16_sort_key,
                            )
                        ),
                        test_ids=shard.test_ids,
                        provider_signal=ProviderSignal.derive(
                            execution_profile_id=shard.profile_id,
                            shard_id=shard.shard_id,
                        ),
                    )
                    for shard in profile_plan.shards
                ),
                max_parallel=profile_plan.max_parallel,
                capacity_mode=profile_plan.mode,
                capacity_reason=profile_plan.reason,
            )
            for profile_plan in shard_plan.profile_plans
        ),
        shard_plan.manifest_id,
    )


def _require_canonical_ids(
    values: tuple[str, ...],
    *,
    field_name: str,
    allow_empty: bool = False,
) -> None:
    if (not allow_empty and not values) or any(
        type(value) is not str or not value for value in values
    ):
        raise ValueError(field_name + " must contain non-empty strings")
    if tuple(sorted(set(values), key=utf16_sort_key)) != values:
        raise ValueError(field_name + " must be canonical")

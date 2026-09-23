from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from ci_coordinator.kernel import hash_object, utf16_sort_key
from ci_coordinator.repo_context.diff_model import DiffContext, RepositoryEpoch
from ci_coordinator.repo_context.freshness import validate_path_pattern


class PlanningPolicyProjection(Protocol):
    def assert_integrity(self) -> None: ...

    @property
    def epoch_id(self) -> str: ...

    @property
    def compiled_policy_hash(self) -> str: ...

    @property
    def policy_hash(self) -> str: ...

    @property
    def dependency_graph_source(self) -> Literal["configured", "generated"]: ...

    @property
    def global_risk_paths(self) -> tuple[str, ...]: ...

    @property
    def risk_classes(self) -> tuple[str, ...]: ...


if TYPE_CHECKING:
    from ci_coordinator.repo_context.dependency_graph import DependencyGraphContext


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    epoch_id: str
    compiled_policy_hash: str
    policy_hash: str
    dependency_graph_source: Literal["configured", "generated"]
    global_risk_paths: tuple[str, ...]
    risk_classes: tuple[str, ...]

    def __post_init__(self) -> None:
        for field_name, value in (
            ("epoch_id", self.epoch_id),
            ("compiled_policy_hash", self.compiled_policy_hash),
            ("policy_hash", self.policy_hash),
        ):
            if (
                type(value) is not str
                or len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise ValueError(f"{field_name} must be lowercase SHA-256 hexadecimal")
        _require_sorted_unique(self.risk_classes, field_name="risk_classes")
        _require_sorted_unique(self.global_risk_paths, field_name="global_risk_paths")
        if self.dependency_graph_source not in {"configured", "generated"}:
            raise ValueError("dependency_graph_source is not admitted")
        if any(validate_path_pattern(path) is not None for path in self.global_risk_paths):
            raise ValueError("global_risk_paths contains an invalid pattern")

    @classmethod
    def from_projection(cls, projection: PlanningPolicyProjection) -> PolicySnapshot:
        projection.assert_integrity()
        return cls(
            epoch_id=projection.epoch_id,
            compiled_policy_hash=projection.compiled_policy_hash,
            policy_hash=projection.policy_hash,
            dependency_graph_source=projection.dependency_graph_source,
            global_risk_paths=projection.global_risk_paths,
            risk_classes=projection.risk_classes,
        )


@dataclass(frozen=True, slots=True)
class PlanningInput:
    repo_epoch: RepositoryEpoch
    diff: DiffContext
    dependency_graph: DependencyGraphContext
    policy: PolicySnapshot
    input_hash: str
    fallback_reasons: tuple[str, ...]

    @property
    def full_ci_invalidating(self) -> bool:
        return bool(self.fallback_reasons)


def build_planning_input(
    repo_epoch: RepositoryEpoch,
    diff: DiffContext,
    dependency_graph: DependencyGraphContext,
    policy: PolicySnapshot,
) -> PlanningInput:
    reasons = set(diff.invalidating_reasons)
    reasons.update(dependency_graph.invalidating_reasons)
    if repo_epoch.base_sha != diff.base_sha or repo_epoch.head_sha != diff.head_sha:
        reasons.add("repo_epoch_diff_mismatch")
    repo_epoch_hash = hash_object(repo_epoch.to_identity_mapping())
    if dependency_graph.admitted_repo_epoch_hash != repo_epoch_hash:
        reasons.add("graph_repo_epoch_mismatch")
    if dependency_graph.admitted_diff_hash != diff.diff_hash:
        reasons.add("graph_diff_mismatch")
    if dependency_graph.admitted_config_epoch_id != policy.epoch_id:
        reasons.add("graph_config_epoch_mismatch")
    if dependency_graph.admitted_policy_hash != policy.policy_hash:
        reasons.add("graph_policy_hash_mismatch")
    if dependency_graph.admitted_compiled_policy_hash != policy.compiled_policy_hash:
        reasons.add("graph_compiled_policy_hash_mismatch")
    input_hash = hash_object(
        {
            "repoEpoch": repo_epoch.to_identity_mapping(),
            "configEpochId": policy.epoch_id,
            "compiledPolicyHash": policy.compiled_policy_hash,
            "diffHash": diff.diff_hash,
            "policyHash": policy.policy_hash,
            "dependencyGraphHash": dependency_graph.graph_hash,
        }
    )
    return PlanningInput(
        repo_epoch=repo_epoch,
        diff=diff,
        dependency_graph=dependency_graph,
        policy=policy,
        input_hash=input_hash,
        fallback_reasons=tuple(sorted(reasons, key=utf16_sort_key)),
    )


def _require_sorted_unique(values: tuple[str, ...], *, field_name: str) -> None:
    if any(type(value) is not str or not value for value in values):
        raise ValueError(f"{field_name} must contain non-empty strings")
    if tuple(sorted(set(values), key=utf16_sort_key)) != values:
        raise ValueError(f"{field_name} must be sorted and unique by ECMAScript UTF-16 order")

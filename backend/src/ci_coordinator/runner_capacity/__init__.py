"""Profile-safe, coverage-preserving runner-capacity scheduling."""

from ci_coordinator.runner_capacity.evidence import (
    MAX_OBSERVED_RUNNER_LABELS,
    ObservedSelfHostedRunner,
    VisibleRunnerGroup,
    project_runner_snapshot,
)
from ci_coordinator.runner_capacity.inputs import (
    MAX_CAPACITY_CLASS_SELECTORS,
    CapacityClassSelector,
    TrustedCapacityManifest,
    TrustedExecutionInputs,
    TrustedExecutionProjection,
)
from ci_coordinator.runner_capacity.manifest import build_test_manifest
from ci_coordinator.runner_capacity.manifest_codec import (
    MAX_EXPECTED_SECONDS,
    MAX_TEST_MANIFEST_BYTES,
    MAX_TEST_MANIFEST_TESTS,
    MIN_EXPECTED_SECONDS,
    ParsedTestManifest,
    parse_test_manifest,
)
from ci_coordinator.runner_capacity.model import (
    MAX_GITHUB_MATRIX_JOBS,
    ManifestTest,
    ProfileShardPlan,
    RunnerCapacity,
    RunnerSnapshot,
    Shard,
    ShardPlan,
    TestManifest,
)
from ci_coordinator.runner_capacity.optimizer import optimize_shards
from ci_coordinator.runner_capacity.selector_binding import bind_capacity_class_selectors
from ci_coordinator.validation_contract import ShardingPolicy

__all__ = [
    "MAX_CAPACITY_CLASS_SELECTORS",
    "MAX_EXPECTED_SECONDS",
    "MAX_GITHUB_MATRIX_JOBS",
    "MAX_OBSERVED_RUNNER_LABELS",
    "MAX_TEST_MANIFEST_BYTES",
    "MAX_TEST_MANIFEST_TESTS",
    "MIN_EXPECTED_SECONDS",
    "CapacityClassSelector",
    "ManifestTest",
    "ObservedSelfHostedRunner",
    "ParsedTestManifest",
    "ProfileShardPlan",
    "RunnerCapacity",
    "RunnerSnapshot",
    "Shard",
    "ShardPlan",
    "ShardingPolicy",
    "TestManifest",
    "TrustedCapacityManifest",
    "TrustedExecutionInputs",
    "TrustedExecutionProjection",
    "VisibleRunnerGroup",
    "bind_capacity_class_selectors",
    "build_test_manifest",
    "optimize_shards",
    "parse_test_manifest",
    "project_runner_snapshot",
]

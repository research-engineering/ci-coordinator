"""Shared algebra for configured validation and executable CI work."""

from ci_coordinator.validation_contract.catalog import ValidationCatalog
from ci_coordinator.validation_contract.depth import (
    VALIDATION_DEPTHS,
    ValidationDepth,
    depth_rank,
    max_depth,
    validation_depth,
)
from ci_coordinator.validation_contract.model import (
    MAX_SERVICE_PROFILE_IDS,
    ExecutableWitness,
    ExecutionProfile,
    ShardingPolicy,
    ValidationObligation,
)

__all__ = [
    "MAX_SERVICE_PROFILE_IDS",
    "VALIDATION_DEPTHS",
    "ExecutableWitness",
    "ExecutionProfile",
    "ShardingPolicy",
    "ValidationCatalog",
    "ValidationDepth",
    "ValidationObligation",
    "depth_rank",
    "max_depth",
    "validation_depth",
]

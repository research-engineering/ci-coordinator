"""Read-only effective-governance observation boundary."""

from ci_coordinator.governance_observation.codec import (
    MAX_GOVERNANCE_STATE_BYTES,
    decode_governance_state,
    encode_governance_state,
)
from ci_coordinator.governance_observation.model import (
    GOVERNANCE_JSON_LIMITS,
    GOVERNANCE_STATE_SCHEMA,
    MAX_GOVERNANCE_AGGREGATE_RULE_BYTES,
    MAX_GOVERNANCE_DEFAULT_BRANCH_BYTES,
    MAX_GOVERNANCE_RULE_BYTES,
    MAX_GOVERNANCE_RULES,
    EffectiveGovernanceRule,
    GovernanceFailureReason,
    GovernanceObservation,
    GovernanceObservationForbidden,
    GovernanceObservationOutcome,
    GovernanceObservationUnavailable,
    GovernanceReadResult,
    GovernanceRepository,
    GovernanceState,
)
from ci_coordinator.governance_observation.ports import (
    GovernanceObservationAuthorizer,
    GovernanceObservationUseCase,
    GovernanceStateReader,
)
from ci_coordinator.governance_observation.service import GovernanceObservationService

__all__ = [
    "GOVERNANCE_JSON_LIMITS",
    "GOVERNANCE_STATE_SCHEMA",
    "MAX_GOVERNANCE_AGGREGATE_RULE_BYTES",
    "MAX_GOVERNANCE_DEFAULT_BRANCH_BYTES",
    "MAX_GOVERNANCE_RULES",
    "MAX_GOVERNANCE_RULE_BYTES",
    "MAX_GOVERNANCE_STATE_BYTES",
    "EffectiveGovernanceRule",
    "GovernanceFailureReason",
    "GovernanceObservation",
    "GovernanceObservationAuthorizer",
    "GovernanceObservationForbidden",
    "GovernanceObservationOutcome",
    "GovernanceObservationService",
    "GovernanceObservationUnavailable",
    "GovernanceObservationUseCase",
    "GovernanceReadResult",
    "GovernanceRepository",
    "GovernanceState",
    "GovernanceStateReader",
    "decode_governance_state",
    "encode_governance_state",
]

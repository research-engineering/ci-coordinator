"""Independent plan admission and monotonic-coverage verification."""

from ci_coordinator.verification_core.coverage import CoverageComparison, compare_coverage
from ci_coordinator.verification_core.deterministic_admission import (
    DeterministicPlanRejectionReason,
    admit_deterministic_plan,
    validate_omission_proof,
)
from ci_coordinator.verification_core.model import VerificationEvidence, VerifiedPlan
from ci_coordinator.verification_core.verifier import verify

__all__ = [
    "CoverageComparison",
    "DeterministicPlanRejectionReason",
    "VerificationEvidence",
    "VerifiedPlan",
    "admit_deterministic_plan",
    "compare_coverage",
    "validate_omission_proof",
    "verify",
]

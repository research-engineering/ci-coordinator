"""Kernel boundary package."""

from ci_coordinator.kernel.admission import (
    AdmissionLease,
    GrowingAdmissionLease,
    NoQueueAdmission,
    WeightedNoQueueAdmission,
)
from ci_coordinator.kernel.canonical_json import (
    CanonicalJsonError,
    JsonResourceLimits,
    bounded_canonical_json,
    canonical_json,
    is_safe_json_integer,
    try_canonical_json,
)
from ci_coordinator.kernel.clock import (
    Clock,
    FixedClock,
    MonotonicClock,
    SystemClock,
    SystemMonotonicClock,
)
from ci_coordinator.kernel.ed25519 import (
    admit_ed25519_public_key,
    verify_canonical_ed25519_signature,
)
from ci_coordinator.kernel.git_reference import git_branch_name_is_admitted
from ci_coordinator.kernel.hashing import hash_object, sha256_hex, try_hash_object
from ci_coordinator.kernel.ordering import utf16_sort_key
from ci_coordinator.kernel.result import Err, Ok, ResultValue
from ci_coordinator.kernel.strict_json import StrictJsonError, load_strict_json

__all__ = [
    "AdmissionLease",
    "CanonicalJsonError",
    "Clock",
    "Err",
    "FixedClock",
    "GrowingAdmissionLease",
    "JsonResourceLimits",
    "MonotonicClock",
    "NoQueueAdmission",
    "Ok",
    "ResultValue",
    "StrictJsonError",
    "SystemClock",
    "SystemMonotonicClock",
    "WeightedNoQueueAdmission",
    "admit_ed25519_public_key",
    "bounded_canonical_json",
    "canonical_json",
    "git_branch_name_is_admitted",
    "hash_object",
    "is_safe_json_integer",
    "load_strict_json",
    "sha256_hex",
    "try_canonical_json",
    "try_hash_object",
    "utf16_sort_key",
    "verify_canonical_ed25519_signature",
]

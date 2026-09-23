from __future__ import annotations

from sqlalchemy import MetaData

from ci_coordinator.audit_replay.subjects import AUDIT_SUBJECT_TYPES
from ci_coordinator.persistence.runtime_state_profile import (
    load_bundled_runtime_state_profile,
)
from ci_coordinator.persistence.shadow_reconciliation_state_profile import (
    load_bundled_shadow_reconciliation_state_profile,
)

metadata = MetaData()
APPLICATION_SCHEMA = "ci_coordinator"
_safe_integer_max = 9_007_199_254_740_991
_subject_type_literals = ", ".join(f"'{value}'" for value in AUDIT_SUBJECT_TYPES)
_runtime_state_profile = load_bundled_runtime_state_profile()
_shadow_reconciliation_state_profile = load_bundled_shadow_reconciliation_state_profile()

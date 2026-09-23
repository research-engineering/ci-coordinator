from __future__ import annotations

from ci_coordinator.reconciliation.findings import ReconciliationResult
from ci_coordinator.reconciliation.state_machine import (
    ReconciliationInput,
    classify_observed_state,
)


def reconcile(reconciliation: ReconciliationInput) -> ReconciliationResult:
    return classify_observed_state(reconciliation)

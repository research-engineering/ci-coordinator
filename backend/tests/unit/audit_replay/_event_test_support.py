from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ci_coordinator.audit_replay import AuditEventInput

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_VECTORS = (
    REPO_ROOT / "fixtures" / "conformance" / "v1" / "product-contract-vectors.v1.json"
)
MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991


def audit_replay_oracle() -> Any:
    return json.loads(CONTRACT_VECTORS.read_text(encoding="utf8"))["cases"]["auditReplay"]


def scalar_event_input(
    payload: dict[str, Any],
    *,
    created_at: str = "2026-07-09T00:00:00.000Z",
    idempotency_key: str = "audit-scalar-domain",
) -> AuditEventInput:
    return AuditEventInput(
        idempotency_key=idempotency_key,
        subject_type="dynamic-ci-plan",
        subject_id="scalar-domain",
        event_type="scalar-domain.checked",
        created_at=created_at,
        actor="ci-coordinator",
        payload=payload,
    )

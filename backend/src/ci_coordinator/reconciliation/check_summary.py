from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ci_coordinator.reconciliation.findings import ReconciliationResult

type CheckConclusion = Literal["success", "pending", "failure"]


@dataclass(frozen=True, slots=True)
class CheckSummary:
    subject_id: str
    conclusion: CheckConclusion
    summary: str


def summarize_check(result: ReconciliationResult) -> CheckSummary:
    conclusion: CheckConclusion
    if result.state == "success":
        conclusion = "success"
    elif result.state == "pending":
        conclusion = "pending"
    else:
        conclusion = "failure"
    lines = [f"Reconciliation state: {result.state}"]
    lines.extend(f"- {finding.message}" for finding in result.findings)
    return CheckSummary(
        subject_id=result.subject_id,
        conclusion=conclusion,
        summary="\n".join(lines),
    )

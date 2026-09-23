from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from ci_coordinator.ci_economics.model import AttemptIdentity
from ci_coordinator.kernel import sha256_hex
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

REPORT_JOB_PAGE_SIZE: Final = 100
MAX_REPORT_JOB_PAGES: Final = 20
_REPOSITORY = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?/[A-Za-z0-9_.-]{1,100}")


def measurement_report_audience(plan_audience: str) -> str:
    if (
        type(plan_audience) is not str
        or not plan_audience
        or plan_audience != plan_audience.strip()
        or len(plan_audience) > 512
    ):
        raise ValueError("measurement report audience requires an admitted plan audience")
    return "urn:ci-coordinator:measurement-report:v1:" + sha256_hex(plan_audience.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class ProviderReportJobBinding:
    attempt: AttemptIdentity
    repository: str
    provider_job_id: int
    check_run_id: int
    evidence_digest: str

    def __post_init__(self) -> None:
        if type(self.attempt) is not AttemptIdentity:
            raise TypeError("report binding requires an exact attempt")
        if type(self.repository) is not str or _REPOSITORY.fullmatch(self.repository) is None:
            raise ValueError("report binding requires an exact repository name")
        if any(
            type(value) is not int or not 1 <= value <= MAX_SAFE_JSON_INTEGER
            for value in (self.provider_job_id, self.check_run_id)
        ):
            raise ValueError("report binding requires positive safe job and check-run IDs")
        if (
            type(self.evidence_digest) is not str
            or re.fullmatch(r"[0-9a-f]{64}", self.evidence_digest) is None
        ):
            raise ValueError("report binding requires an exact evidence digest")

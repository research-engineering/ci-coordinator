from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RejectedIdentity:
    reason_code: str
    message: str
    verified_at: datetime
    verifier_version: str
    body_sha256: str | None = None
    claim_hash: str | None = None

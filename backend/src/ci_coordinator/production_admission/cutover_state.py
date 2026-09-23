from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.production_admission.current_evidence import CurrentProductionEvidence
from ci_coordinator.production_admission.model import (
    _require_aware_utc,
    _require_digest,
)
from ci_coordinator.production_admission.relation import MAX_PRODUCTION_GENERATION

_AUTHORITY_ID = re.compile(r"production_admission_[0-9a-f]{32}")
_LATCH_ID = re.compile(r"override_[0-9a-f]{32}")


@dataclass(frozen=True, slots=True)
class ProductionScopeState:
    scope: RepositoryScope
    revision: int
    generation: int = 0
    revoked_through_generation: int = 0
    active_authority_id: str | None = None
    active_subject_digest: str | None = None
    staged_authority_id: str | None = None
    latch_override_id: str | None = None
    latch_applied_at: datetime | None = None

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("production scope state requires an exact repository scope")
        _require_counter(self.revision, minimum=1)
        _require_counter(self.generation)
        _require_counter(self.revoked_through_generation)
        if self.revoked_through_generation > self.generation:
            raise ValueError("revocation cannot exceed the current production generation")
        if self.generation == 0:
            if self.active_authority_id is not None or self.active_subject_digest is not None:
                raise ValueError("initial production state cannot name active authority")
        else:
            _require_authority_id(self.active_authority_id)
            _require_digest(self.active_subject_digest, "active production subject")
        if self.staged_authority_id is not None:
            _require_authority_id(self.staged_authority_id)
        if self.latch_override_id is None:
            if self.latch_applied_at is not None:
                raise ValueError("production latch timestamp requires its exact identity")
        else:
            _require_latch_id(self.latch_override_id)
            _require_aware_utc(self.latch_applied_at, "production latch time")
            if self.revoked_through_generation != self.generation:
                raise ValueError("a production latch must revoke the current generation")

    def admits_current(
        self, evidence: CurrentProductionEvidence, *, authority_id: str, database_now: datetime
    ) -> bool:
        return (
            type(evidence) is CurrentProductionEvidence
            and self.generation > self.revoked_through_generation
            and self.latch_override_id is None
            and self.active_authority_id == authority_id
            and self.scope == evidence.candidate.scope
            and self.revision == evidence.scope_revision
            and self.generation == evidence.scope_grant.relation.generation
            and self.active_subject_digest == evidence.scope_grant.admission_subject_digest
            and evidence.is_current_at(database_now)
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "installationId": self.scope.installation_id,
            "repositoryId": self.scope.repository_id,
            "revision": self.revision,
            "generation": self.generation,
            "revokedThroughGeneration": self.revoked_through_generation,
            "activeAuthorityId": self.active_authority_id,
            "activeSubjectDigest": self.active_subject_digest,
            "stagedAuthorityId": self.staged_authority_id,
            "latchOverrideId": self.latch_override_id,
            "latchAppliedAt": None
            if self.latch_applied_at is None
            else self.latch_applied_at.isoformat(timespec="microseconds").replace("+00:00", "Z"),
        }


def _require_counter(value: object, *, minimum: int = 0) -> None:
    if type(value) is not int or not minimum <= value <= MAX_PRODUCTION_GENERATION:
        raise ValueError("production scope counter is outside the exact safe-integer domain")


def _require_authority_id(value: object) -> None:
    if type(value) is not str or _AUTHORITY_ID.fullmatch(value) is None:
        raise ValueError("production authority identity is invalid")


def _require_latch_id(value: object) -> None:
    if type(value) is not str or _LATCH_ID.fullmatch(value) is None:
        raise ValueError("production latch identity is invalid")

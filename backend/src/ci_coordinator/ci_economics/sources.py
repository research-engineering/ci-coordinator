from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final, Literal

from ci_coordinator.ci_economics.model import AttemptIdentity
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import hash_object
from ci_coordinator.reconciliation import ReconciliationContract, ReconciliationSubject

_DIGEST = re.compile(r"[0-9a-f]{64}")
MAX_PROVIDER_SOURCES_PER_REPOSITORY: Final = 10_000
MAX_PROVIDER_SOURCE_BATCH_SIZE: Final = 100

type ProviderSourceRegistrationResult = Literal[
    "registered", "replayed", "source_conflict", "outside_source_window", "capacity_reached"
]


@dataclass(frozen=True, slots=True)
class ReconciliationCollectionSource:
    subject: ReconciliationSubject
    contract: ReconciliationContract

    def __post_init__(self) -> None:
        if type(self.subject) is not ReconciliationSubject:
            raise TypeError("collection source requires an exact reconciliation subject")
        if type(self.contract) is not ReconciliationContract:
            raise TypeError("collection source requires an exact reconciliation contract")

    @property
    def kind(self) -> Literal["reconciliation"]:
        return "reconciliation"

    @property
    def source_id(self) -> str:
        return self.subject.subject_id

    @property
    def attempt(self) -> AttemptIdentity:
        return AttemptIdentity(
            RepositoryScope(self.subject.installation_id, self.subject.repository_id),
            self.subject.workflow_run_id,
            self.subject.run_attempt,
            self.subject.head_sha,
        )


@dataclass(frozen=True, slots=True)
class ProviderRunCollectionSource:
    attempt: AttemptIdentity
    run_created_at: datetime
    provider_api_version: str
    source_evidence_digest: str

    def __post_init__(self) -> None:
        if type(self.attempt) is not AttemptIdentity:
            raise TypeError("provider source requires an exact attempt identity")
        if (
            type(self.run_created_at) is not datetime
            or self.run_created_at.tzinfo is None
            or self.run_created_at.utcoffset() is None
        ):
            raise ValueError("provider run creation time must be timezone-aware")
        object.__setattr__(self, "run_created_at", self.run_created_at.astimezone(UTC))
        if (
            type(self.provider_api_version) is not str
            or len(self.provider_api_version) != 10
            or date.fromisoformat(self.provider_api_version).isoformat()
            != self.provider_api_version
        ):
            raise ValueError("provider API version must be an ISO calendar date")
        if (
            type(self.source_evidence_digest) is not str
            or _DIGEST.fullmatch(self.source_evidence_digest) is None
        ):
            raise ValueError("provider source evidence must have a lowercase SHA-256 digest")

    @property
    def kind(self) -> Literal["provider_run"]:
        return "provider_run"

    @property
    def source_id(self) -> str:
        # Re-observing conflicting provenance must collide, not create a fresh lifetime.
        return hash_object(
            {
                "schemaVersion": "ci-economics-provider-run-source/v1",
                "attempt": self.attempt.canonical_mapping(),
            }
        )


type CollectionSource = ReconciliationCollectionSource | ProviderRunCollectionSource

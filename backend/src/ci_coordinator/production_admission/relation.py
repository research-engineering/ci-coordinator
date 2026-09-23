from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from ci_coordinator.kernel import hash_object

PRODUCTION_RELATION_BINDING_SCHEMA: Final = "ci-coordinator.production-relation-binding/v1"
MAX_PRODUCTION_GENERATION: Final = 9_007_199_254_740_991
_DIGEST = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True, slots=True)
class ProductionRelationBinding:
    generation: int
    predecessor_generation: int
    evidence_bundle_digest: str
    relation_subject_digest: str
    relation_epoch_digest: str
    relation_closure_digest: str
    workflow_manifest_digest: str
    source_binding_digest: str
    provider_authority_digest: str
    owner_epoch_digest: str

    def __post_init__(self) -> None:
        if (
            type(self.generation) is not int
            or type(self.predecessor_generation) is not int
            or not 1 <= self.generation <= MAX_PRODUCTION_GENERATION
            or self.predecessor_generation != self.generation - 1
        ):
            raise ValueError("production generation must be the exact bounded successor")
        for name, value in (
            ("evidence bundle", self.evidence_bundle_digest),
            ("relation subject", self.relation_subject_digest),
            ("relation epoch", self.relation_epoch_digest),
            ("relation closure", self.relation_closure_digest),
            ("workflow manifest", self.workflow_manifest_digest),
            ("source binding", self.source_binding_digest),
            ("provider authority", self.provider_authority_digest),
            ("owner epoch", self.owner_epoch_digest),
        ):
            if type(value) is not str or _DIGEST.fullmatch(value) is None:
                raise ValueError(f"production {name} must be an exact SHA-256 digest")

    @property
    def binding_digest(self) -> str:
        return hash_object(self.to_mapping())

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": PRODUCTION_RELATION_BINDING_SCHEMA,
            "generation": self.generation,
            "predecessorGeneration": self.predecessor_generation,
            "evidenceBundleDigest": self.evidence_bundle_digest,
            "relationSubjectDigest": self.relation_subject_digest,
            "relationEpochDigest": self.relation_epoch_digest,
            "relationClosureDigest": self.relation_closure_digest,
            "workflowManifestDigest": self.workflow_manifest_digest,
            "sourceBindingDigest": self.source_binding_digest,
            "providerAuthorityDigest": self.provider_authority_digest,
            "ownerEpochDigest": self.owner_epoch_digest,
        }

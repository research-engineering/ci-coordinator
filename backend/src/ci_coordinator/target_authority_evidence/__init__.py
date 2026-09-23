"""Replayable offline evidence for one dormant target-authority epoch."""

from .codec import decode_target_authority_evidence, encode_target_authority_evidence
from .file import (
    PublishedTargetAuthorityEvidence,
    publish_target_authority_evidence,
    read_target_authority_evidence,
)
from .model import (
    EVIDENCE_ROLES,
    TARGET_AUTHORITY_EVIDENCE_SCHEMA,
    AdmittedTargetAuthorityEvidence,
    EvidenceArtifact,
    TargetAuthorityEvidenceError,
    TargetAuthorityEvidenceValues,
    UnactivatedEvidenceBundle,
)
from .replay import build_target_authority_evidence, replay_target_authority_evidence

__all__ = [
    "EVIDENCE_ROLES",
    "TARGET_AUTHORITY_EVIDENCE_SCHEMA",
    "AdmittedTargetAuthorityEvidence",
    "EvidenceArtifact",
    "PublishedTargetAuthorityEvidence",
    "TargetAuthorityEvidenceError",
    "TargetAuthorityEvidenceValues",
    "UnactivatedEvidenceBundle",
    "build_target_authority_evidence",
    "decode_target_authority_evidence",
    "encode_target_authority_evidence",
    "publish_target_authority_evidence",
    "read_target_authority_evidence",
    "replay_target_authority_evidence",
]

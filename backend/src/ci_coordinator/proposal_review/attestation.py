"""One-use repository-attestation values for proposal review."""

from __future__ import annotations

from dataclasses import dataclass, field

from ci_coordinator.control_plane_identity import (
    GitHubReviewerPrincipal,
    ReviewerStepUpBinding,
)


@dataclass(frozen=True, slots=True)
class RepositoryAttestationTransaction:
    transaction_digest: bytes = field(repr=False)
    binding: ReviewerStepUpBinding

    def __post_init__(self) -> None:
        if type(self.transaction_digest) is not bytes or len(self.transaction_digest) != 32:
            raise ValueError("repository-attestation transaction digest must be exact")
        if type(self.binding) is not ReviewerStepUpBinding:
            raise TypeError("repository-attestation transaction binding must be exact")


@dataclass(frozen=True, slots=True)
class RepositoryAttestationEvidence:
    transaction: RepositoryAttestationTransaction
    reviewer: GitHubReviewerPrincipal

    def __post_init__(self) -> None:
        if type(self.transaction) is not RepositoryAttestationTransaction:
            raise TypeError("repository-attestation evidence requires an exact transaction")
        if type(self.reviewer) is not GitHubReviewerPrincipal:
            raise TypeError("repository-attestation evidence requires an exact reviewer")
        binding = self.transaction.binding
        if (
            self.reviewer.observed_at < binding.issued_at
            or self.reviewer.observed_at >= binding.expires_at
            or self.reviewer.expires_at != binding.expires_at
        ):
            raise ValueError("repository-attestation observation exceeds its exact transaction")


@dataclass(frozen=True, slots=True)
class RepositoryAttestationRegistered:
    pass


@dataclass(frozen=True, slots=True)
class RepositoryAttestationCapacityExceeded:
    pass


@dataclass(frozen=True, slots=True)
class RepositoryAttestationRegistrationRejected:
    pass


type RepositoryAttestationRegistration = (
    RepositoryAttestationRegistered
    | RepositoryAttestationCapacityExceeded
    | RepositoryAttestationRegistrationRejected
)

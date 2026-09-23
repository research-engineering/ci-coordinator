"""Independent registration and observation producers for target authority."""

from .codec import (
    decode_observation_candidates,
    decode_owner_projection_policy,
    decode_registration_candidates,
    encode_observation_candidates,
    encode_owner_projection_policy,
    encode_registration_candidates,
)
from .enumeration import ObservationSources, enumerate_observation_candidates
from .model import (
    OBSERVATION_AUTHORITY_DOMAIN_SCHEMA,
    OBSERVATION_CANDIDATE_SET_SCHEMA,
    OWNER_PROJECTION_POLICY_SCHEMA,
    REGISTRATION_CANDIDATE_SET_SCHEMA,
    ObservationCandidateSet,
    ObservationProduction,
    ObservedCandidate,
    OwnerProjectionPolicy,
    ProjectionRule,
    RegistrationCandidateSet,
    RegistrationProduction,
    TargetAuthorityProducerError,
)
from .production import produce_observation, produce_registration, project_registration
from .registration_candidates import enumerate_registration_candidates
from .sources import ProviderAuthoritySources, TargetArtifactSources

__all__ = [
    "OBSERVATION_AUTHORITY_DOMAIN_SCHEMA",
    "OBSERVATION_CANDIDATE_SET_SCHEMA",
    "OWNER_PROJECTION_POLICY_SCHEMA",
    "REGISTRATION_CANDIDATE_SET_SCHEMA",
    "ObservationCandidateSet",
    "ObservationProduction",
    "ObservationSources",
    "ObservedCandidate",
    "OwnerProjectionPolicy",
    "ProjectionRule",
    "ProviderAuthoritySources",
    "RegistrationCandidateSet",
    "RegistrationProduction",
    "TargetArtifactSources",
    "TargetAuthorityProducerError",
    "decode_observation_candidates",
    "decode_owner_projection_policy",
    "decode_registration_candidates",
    "encode_observation_candidates",
    "encode_owner_projection_policy",
    "encode_registration_candidates",
    "enumerate_observation_candidates",
    "enumerate_registration_candidates",
    "produce_observation",
    "produce_registration",
    "project_registration",
]

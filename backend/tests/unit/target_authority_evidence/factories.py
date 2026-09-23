"""One source-, producer-, and relation-closed evidence fixture."""

from __future__ import annotations

from dataclasses import dataclass

from target_authority_producers.factories import ProducerFixture, producer_fixture

from ci_coordinator.target_authority_evidence import (
    AdmittedTargetAuthorityEvidence,
    TargetAuthorityEvidenceValues,
    build_target_authority_evidence,
)
from ci_coordinator.target_authority_producers import produce_observation, produce_registration
from ci_coordinator.target_authority_relation import (
    UnactivatedRelationClosure,
    close_target_authority_relation,
)


@dataclass(frozen=True, slots=True)
class EvidenceFixture:
    producer: ProducerFixture
    values: TargetAuthorityEvidenceValues
    admitted: AdmittedTargetAuthorityEvidence


def evidence_fixture(*, local_requester: bool = False) -> EvidenceFixture:
    producer = producer_fixture(local_requester=local_requester)
    registration = produce_registration(
        producer.expected,
        producer.target_artifacts,
        producer.policy,
        producer.registration_producer,
    )
    observation = produce_observation(
        producer.candidates,
        producer.policy,
        producer.observation_producer,
        producer.subject,
        producer.expected.epoch,
    )
    closure = close_target_authority_relation(
        producer.baseline,
        producer.delta,
        registration.inventory,
        observation.inventory,
        observation.raw_domain,
        observation.projection_ledger,
    )
    if not isinstance(closure, UnactivatedRelationClosure):
        raise AssertionError("evidence fixture relation did not close")
    values = TargetAuthorityEvidenceValues(
        phase_zero_baseline=producer.baseline,
        transition_delta=producer.delta,
        expected_relation=producer.expected,
        workflow_manifest=producer.workflow_evidence.manifest,
        source_binding=producer.workflow_evidence.source_binding,
        registration_candidates=producer.registration_candidates,
        observation_candidates=producer.candidates,
        owner_projection_policy=producer.policy,
        registration_inventory=registration.inventory,
        observation_inventory=observation.inventory,
        raw_candidate_domain=observation.raw_domain,
        projection_ledger=observation.projection_ledger,
        relation_closure=closure,
    )
    return EvidenceFixture(producer, values, build_target_authority_evidence(values))

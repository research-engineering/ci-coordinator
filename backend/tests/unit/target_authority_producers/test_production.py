from __future__ import annotations

from dataclasses import replace

import pytest

from ci_coordinator.target_authority_producers import (
    ObservationCandidateSet,
    OwnerProjectionPolicy,
    ProjectionRule,
    TargetArtifactSources,
    TargetAuthorityProducerError,
    produce_observation,
    produce_registration,
)
from ci_coordinator.target_authority_relation import (
    EpochComponent,
    ExpectedTargetAuthorityRelation,
    TargetAuthorityEpoch,
    UnactivatedRelationClosure,
    close_target_authority_relation,
)

from .factories import digest, observed_candidate, producer_fixture


def test_independent_producers_close_the_exact_transition_relation() -> None:
    fixture = producer_fixture()

    registration = produce_registration(
        fixture.expected,
        fixture.target_artifacts,
        fixture.policy,
        fixture.registration_producer,
    )
    observation = produce_observation(
        fixture.candidates,
        fixture.policy,
        fixture.observation_producer,
        fixture.subject,
        fixture.expected.epoch,
    )
    closure = close_target_authority_relation(
        fixture.baseline,
        fixture.delta,
        registration.inventory,
        observation.inventory,
        observation.raw_domain,
        observation.projection_ledger,
    )

    assert isinstance(closure, UnactivatedRelationClosure)
    assert closure.to_mapping()["authorityState"] == "unactivated"


def test_observation_rejects_a_policy_that_omits_an_enumerated_candidate() -> None:
    fixture = producer_fixture()
    policy = OwnerProjectionPolicy(
        fixture.subject,
        fixture.policy.workflow_manifest_digest,
        fixture.policy.target_artifact_epoch_digest,
        fixture.policy.observation_authority_domain_digest,
        fixture.policy.registration_declaration_domain_digest,
        fixture.policy.rules[:-1],
    )
    epoch = _epoch_with_owner(fixture.expected.epoch, policy.policy_digest)

    with pytest.raises(TargetAuthorityProducerError) as raised:
        produce_observation(
            fixture.candidates,
            policy,
            fixture.observation_producer,
            fixture.subject,
            epoch,
        )

    assert raised.value.code == "candidate_classification_domain_mismatch"


def test_observation_rejects_candidate_omission_from_the_bound_domain() -> None:
    fixture = producer_fixture()
    candidates = replace(
        fixture.candidates,
        candidates=fixture.candidates.candidates[:-1],
    )

    with pytest.raises(TargetAuthorityProducerError) as raised:
        produce_observation(
            candidates,
            fixture.policy,
            fixture.observation_producer,
            fixture.subject,
            fixture.expected.epoch,
        )

    assert raised.value.code == "owner_policy_domain_mismatch"


def test_observation_rejects_stale_candidate_evidence() -> None:
    fixture = producer_fixture()
    first = fixture.policy.rules[0]
    rules = (replace(first, evidence_digest=digest("stale")), *fixture.policy.rules[1:])
    policy = OwnerProjectionPolicy(
        fixture.subject,
        fixture.policy.workflow_manifest_digest,
        fixture.policy.target_artifact_epoch_digest,
        fixture.policy.observation_authority_domain_digest,
        fixture.policy.registration_declaration_domain_digest,
        rules,
    )
    epoch = _epoch_with_owner(fixture.expected.epoch, policy.policy_digest)

    with pytest.raises(TargetAuthorityProducerError) as raised:
        produce_observation(
            fixture.candidates,
            policy,
            fixture.observation_producer,
            fixture.subject,
            epoch,
        )

    assert raised.value.code == "candidate_evidence_mismatch"


def test_observation_rejects_correlated_candidates_that_disagree_on_full_row() -> None:
    fixture = producer_fixture()
    original = next(
        candidate
        for candidate in fixture.candidates.candidates
        if candidate.candidate_id == "workflow:.github/workflows/ci.yml"
    )
    conflicting = observed_candidate(
        "workflow_blob:.github/workflows/ci.yml",
        "workflow_blob",
        original.source_locator,
        revision=2,
    )
    candidates = ObservationCandidateSet(
        scope=fixture.candidates.scope,
        source_commit_id=fixture.candidates.source_commit_id,
        source_binding_digest=fixture.candidates.source_binding_digest,
        workflow_manifest_digest=fixture.candidates.workflow_manifest_digest,
        discovery_report_digest=fixture.candidates.discovery_report_digest,
        provider_authority_digest=fixture.candidates.provider_authority_digest,
        target_artifact_epoch_digest=fixture.candidates.target_artifact_epoch_digest,
        target_policy_digest=fixture.candidates.target_policy_digest,
        validation_catalog_digest=fixture.candidates.validation_catalog_digest,
        target_registry_digest=fixture.candidates.target_registry_digest,
        candidates=tuple(
            sorted(
                (*fixture.candidates.candidates, conflicting),
                key=lambda item: item.candidate_id,
            )
        ),
    )
    original_rule = next(
        rule
        for rule in fixture.policy.rules
        if rule.candidate_id == "workflow:.github/workflows/ci.yml"
    )
    conflicting_rule = ProjectionRule(
        conflicting.candidate_id,
        conflicting.evidence_digest,
        original_rule.row_key,
        original_rule.disposition,
        original_rule.semantic_owner,
        original_rule.source_locator,
    )
    policy = OwnerProjectionPolicy(
        fixture.subject,
        fixture.policy.workflow_manifest_digest,
        fixture.policy.target_artifact_epoch_digest,
        candidates.authority_domain_digest,
        fixture.policy.registration_declaration_domain_digest,
        tuple(
            sorted((*fixture.policy.rules, conflicting_rule), key=lambda item: item.candidate_id)
        ),
    )
    epoch = _epoch_with_owner(fixture.expected.epoch, policy.policy_digest)

    with pytest.raises(TargetAuthorityProducerError) as raised:
        produce_observation(
            candidates,
            policy,
            fixture.observation_producer,
            fixture.subject,
            epoch,
        )

    assert raised.value.code == "correlated_projection_mismatch"


def test_observation_rejects_cross_epoch_candidate_components() -> None:
    fixture = producer_fixture()
    candidates = replace(fixture.candidates, target_policy_digest=digest("other-policy"))

    with pytest.raises(TargetAuthorityProducerError) as raised:
        produce_observation(
            candidates,
            fixture.policy,
            fixture.observation_producer,
            fixture.subject,
            fixture.expected.epoch,
        )

    assert raised.value.code == "observation_epoch_mismatch"


def test_observation_rejects_forged_target_artifact_epoch() -> None:
    fixture = producer_fixture()
    candidates = replace(
        fixture.candidates,
        target_artifact_epoch_digest=digest("other-target-artifacts"),
    )

    with pytest.raises(TargetAuthorityProducerError) as raised:
        produce_observation(
            candidates,
            fixture.policy,
            fixture.observation_producer,
            fixture.subject,
            fixture.expected.epoch,
        )

    assert raised.value.code == "owner_policy_source_mismatch"


def test_registration_rejects_owner_policy_key_domain_omission() -> None:
    fixture = producer_fixture()
    policy = OwnerProjectionPolicy(
        fixture.subject,
        fixture.policy.workflow_manifest_digest,
        fixture.policy.target_artifact_epoch_digest,
        fixture.policy.observation_authority_domain_digest,
        fixture.policy.registration_declaration_domain_digest,
        fixture.policy.rules[:-1],
    )
    epoch = _epoch_with_owner(fixture.expected.epoch, policy.policy_digest)
    expected = ExpectedTargetAuthorityRelation(
        fixture.expected.subject,
        epoch,
        fixture.expected.baseline_digest,
        fixture.expected.delta_digest,
        fixture.expected.rows,
    )

    with pytest.raises(TargetAuthorityProducerError) as raised:
        produce_registration(
            expected,
            fixture.target_artifacts,
            policy,
            fixture.registration_producer,
        )

    assert raised.value.code == "registration_key_domain_mismatch"


def test_registration_rejects_changed_target_artifact_sources() -> None:
    fixture = producer_fixture()
    version = "c" * 40
    changed_sources = TargetArtifactSources(
        fixture.target_artifacts.policy,
        replace(fixture.target_artifacts.registry, generator_version=version),
        replace(fixture.target_artifacts.lab_profile, expected_coordinator_commit=version),
        fixture.target_artifacts.scenarios,
    )

    with pytest.raises(TargetAuthorityProducerError) as raised:
        produce_registration(
            fixture.expected,
            changed_sources,
            fixture.policy,
            fixture.registration_producer,
        )

    assert raised.value.code == "owner_policy_source_mismatch"


def _epoch_with_owner(epoch: TargetAuthorityEpoch, owner_digest: str) -> TargetAuthorityEpoch:
    return replace(epoch, owner=EpochComponent.present(owner_digest))

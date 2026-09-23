from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from ci_coordinator.target_authority_relation import (
    AuthorityField,
    CandidateProjection,
    ProducerIdentity,
    ProjectionLedger,
    RawCandidate,
    RelationRejected,
    TargetAuthorityInventory,
    TargetAuthorityKey,
    TargetAuthorityRow,
    UnactivatedRelationClosure,
    close_target_authority_relation,
)

from .factories import RelationFixture, digest, relation_fixture, row, target_epoch


def _compare(fixture: RelationFixture) -> UnactivatedRelationClosure | RelationRejected:
    return close_target_authority_relation(
        fixture.baseline,
        fixture.delta,
        fixture.registration,
        fixture.observation,
        fixture.raw_domain,
        fixture.projection_ledger,
    )


type FixtureMutation = Callable[[RelationFixture], RelationFixture]


def _incomplete_registration(fixture: RelationFixture) -> RelationFixture:
    return replace(
        fixture,
        registration=replace(fixture.registration, complete=False),
    )


def _incomplete_observation(fixture: RelationFixture) -> RelationFixture:
    return replace(
        fixture,
        observation=replace(fixture.observation, complete=False),
    )


def _incomplete_raw_domain(fixture: RelationFixture) -> RelationFixture:
    return replace(
        fixture,
        raw_domain=replace(fixture.raw_domain, complete=False),
    )


def _incomplete_projection(fixture: RelationFixture) -> RelationFixture:
    return replace(
        fixture,
        projection_ledger=replace(fixture.projection_ledger, complete=False),
    )


def _wrong_registration_domain(fixture: RelationFixture) -> RelationFixture:
    return replace(
        fixture,
        registration=replace(
            fixture.registration,
            source_domain_digest=digest("wrong-registration-domain"),
        ),
    )


def _wrong_registration_count(fixture: RelationFixture) -> RelationFixture:
    return replace(
        fixture,
        registration=replace(
            fixture.registration,
            source_item_count=fixture.registration.source_item_count + 1,
        ),
    )


def _wrong_observation_domain(fixture: RelationFixture) -> RelationFixture:
    return replace(
        fixture,
        observation=replace(
            fixture.observation,
            source_domain_digest=digest("wrong-observation-domain"),
        ),
    )


def _wrong_observation_count(fixture: RelationFixture) -> RelationFixture:
    return replace(
        fixture,
        observation=replace(
            fixture.observation,
            source_item_count=fixture.observation.source_item_count + 1,
        ),
    )


def _wrong_subject(fixture: RelationFixture) -> RelationFixture:
    subject = replace(
        fixture.expected.subject,
        scope=replace(fixture.expected.subject.scope, repository_id=99),
    )
    return replace(
        fixture,
        registration=replace(fixture.registration, subject=subject),
    )


def _extra_registration_row(fixture: RelationFixture) -> RelationFixture:
    rows = tuple(
        sorted(
            (*fixture.registration.rows, row("provider_gate", "extra-registration-gate")),
            key=lambda item: item.key.sort_key,
        )
    )
    return replace(
        fixture,
        registration=replace(fixture.registration, rows=rows),
    )


def _mismatched_registration_row(fixture: RelationFixture) -> RelationFixture:
    first, *remainder = fixture.registration.rows
    rows = (replace(first, semantic_owner="mismatched-registration-owner"), *remainder)
    return replace(
        fixture,
        registration=replace(fixture.registration, rows=rows),
    )


def _unknown_registration_row(fixture: RelationFixture) -> RelationFixture:
    first, *remainder = fixture.registration.rows
    first_field, *remaining_fields = first.fields
    unknown = replace(
        first,
        fields=(
            replace(first_field, field=AuthorityField.unknown("registration unavailable")),
            *remaining_fields,
        ),
    )
    return replace(
        fixture,
        registration=replace(fixture.registration, rows=(unknown, *remainder)),
    )


def _foreign_projection(fixture: RelationFixture) -> RelationFixture:
    foreign = CandidateProjection(
        "foreign-candidate",
        fixture.expected.rows[0].key,
        fixture.expected.rows[0].row_digest,
    )
    projections = tuple(
        sorted(
            (*fixture.projection_ledger.projections, foreign),
            key=lambda item: item.sort_key,
        )
    )
    return replace(
        fixture,
        projection_ledger=replace(fixture.projection_ledger, projections=projections),
    )


def _projection_to_missing_row(fixture: RelationFixture) -> RelationFixture:
    first, *remainder = fixture.projection_ledger.projections
    missing_key = TargetAuthorityKey(first.row_key.family, f"{first.row_key.member_id}:missing")
    projection = replace(first, row_key=missing_key)
    projections = tuple(sorted((projection, *remainder), key=lambda item: item.sort_key))
    return replace(
        fixture,
        projection_ledger=replace(fixture.projection_ledger, projections=projections),
    )


def _projection_with_wrong_digest(fixture: RelationFixture) -> RelationFixture:
    first, *remainder = fixture.projection_ledger.projections
    projection = replace(first, row_digest=digest("wrong-projection-row"))
    projections = tuple(sorted((projection, *remainder), key=lambda item: item.sort_key))
    return replace(
        fixture,
        projection_ledger=replace(fixture.projection_ledger, projections=projections),
    )


def _unprojected_observation_row(fixture: RelationFixture) -> RelationFixture:
    return replace(
        fixture,
        projection_ledger=replace(
            fixture.projection_ledger,
            projections=fixture.projection_ledger.projections[1:],
        ),
    )


def _mismatched_observation_domain_producer(fixture: RelationFixture) -> RelationFixture:
    raw_domain = replace(
        fixture.raw_domain,
        producer=ProducerIdentity("other-observation-domain-producer", "1"),
    )
    return replace(
        fixture,
        raw_domain=raw_domain,
        observation=replace(
            fixture.observation,
            source_domain_digest=raw_domain.domain_digest,
        ),
    )


def _unknown_observation_row(fixture: RelationFixture) -> tuple[TargetAuthorityRow, ...]:
    first, *remainder = fixture.expected.rows
    first_field, *remaining_fields = first.fields
    unknown = replace(
        first,
        fields=(
            replace(first_field, field=AuthorityField.unknown("observation unavailable")),
            *remaining_fields,
        ),
    )
    return (unknown, *remainder)


def test_exact_independent_inventories_emit_only_unactivated_closure() -> None:
    outcome = _compare(relation_fixture())

    assert isinstance(outcome, UnactivatedRelationClosure)
    assert outcome.to_mapping()["authorityState"] == "unactivated"
    assert outcome.row_count == 3
    assert outcome.candidate_count == 3


@pytest.mark.parametrize(
    ("mutate", "code"),
    (
        (_incomplete_registration, "registration_incomplete"),
        (_incomplete_observation, "observation_incomplete"),
        (_incomplete_raw_domain, "raw_domain_incomplete"),
        (_incomplete_projection, "projection_incomplete"),
    ),
)
def test_incomplete_evidence_never_closes(mutate: FixtureMutation, code: str) -> None:
    fixture = mutate(relation_fixture())

    outcome = _compare(fixture)

    assert isinstance(outcome, RelationRejected)
    assert code in {finding.code for finding in outcome.findings}


@pytest.mark.parametrize(
    ("mutate", "code"),
    (
        (_wrong_registration_domain, "registration_source_domain_mismatch"),
        (_wrong_registration_count, "registration_source_count_mismatch"),
        (_wrong_observation_domain, "observation_source_domain_mismatch"),
        (_wrong_observation_count, "observation_source_count_mismatch"),
    ),
)
def test_inventory_source_domain_metadata_is_a_checked_binding(
    mutate: FixtureMutation,
    code: str,
) -> None:
    outcome = _compare(mutate(relation_fixture()))

    assert isinstance(outcome, RelationRejected)
    assert code in {finding.code for finding in outcome.findings}


@pytest.mark.parametrize(
    ("mutate", "code"),
    (
        (_wrong_subject, "subject_mismatch"),
        (_extra_registration_row, "extra_registration_row"),
        (_mismatched_registration_row, "registration_row_mismatch"),
        (_unknown_registration_row, "unknown_registration_row"),
        (_foreign_projection, "projection_incomplete"),
        (_projection_to_missing_row, "projection_row_missing"),
        (_projection_with_wrong_digest, "projection_row_mismatch"),
        (_unprojected_observation_row, "unprojected_observation_row"),
        (_mismatched_observation_domain_producer, "producer_identity_collision"),
    ),
)
def test_each_relation_binding_has_a_direct_counterexample(
    mutate: FixtureMutation,
    code: str,
) -> None:
    outcome = _compare(mutate(relation_fixture()))

    assert isinstance(outcome, RelationRejected)
    assert code in {finding.code for finding in outcome.findings}


def test_closure_recomputes_the_expected_relation_from_baseline_and_delta() -> None:
    fixture = relation_fixture()
    fixture = replace(
        fixture,
        delta=replace(fixture.delta, baseline_digest=digest("unrelated-baseline")),
    )

    outcome = _compare(fixture)

    assert isinstance(outcome, RelationRejected)
    assert {finding.code for finding in outcome.findings} == {"baseline_digest_mismatch"}


@pytest.mark.parametrize(
    ("rows", "code"),
    (
        (lambda fixture: fixture.expected.rows[1:], "missing_observation_row"),
        (
            lambda fixture: tuple(
                sorted(
                    (*fixture.expected.rows, row("provider_gate", "extra-gate")),
                    key=lambda item: item.key.sort_key,
                )
            ),
            "extra_observation_row",
        ),
        (
            lambda fixture: tuple(
                sorted(
                    (
                        replace(fixture.expected.rows[0], semantic_owner="changed-owner"),
                        *fixture.expected.rows[1:],
                    ),
                    key=lambda item: item.key.sort_key,
                )
            ),
            "observation_row_mismatch",
        ),
        (_unknown_observation_row, "unknown_observation_row"),
    ),
)
def test_missing_extra_mismatched_and_unknown_rows_are_distinct(
    rows: Callable[[RelationFixture], tuple[TargetAuthorityRow, ...]],
    code: str,
) -> None:
    fixture = relation_fixture()
    observation_rows = rows(fixture)
    fixture = replace(
        fixture,
        observation=replace(
            fixture.observation,
            rows=observation_rows,
        ),
    )

    outcome = _compare(fixture)

    assert isinstance(outcome, RelationRejected)
    assert code in {finding.code for finding in outcome.findings}


def test_agreed_omission_by_both_producers_still_conflicts_with_expected_relation() -> None:
    fixture = relation_fixture()
    omitted = fixture.expected.rows[1:]
    fixture = replace(
        fixture,
        registration=replace(fixture.registration, rows=omitted),
        observation=replace(fixture.observation, rows=omitted),
    )

    outcome = _compare(fixture)

    assert isinstance(outcome, RelationRejected)
    assert {finding.code for finding in outcome.findings} >= {
        "missing_registration_row",
        "missing_observation_row",
    }


def test_candidate_domain_must_be_total_and_independently_produced() -> None:
    fixture = relation_fixture()
    unknown = replace(
        fixture.raw_domain.candidates[0],
        state="unknown",
        evidence_digest=None,
        reason="parse failed",
    )
    raw_domain = replace(
        fixture.raw_domain,
        candidates=(unknown, *fixture.raw_domain.candidates[1:]),
    )
    projection_ledger = replace(
        fixture.projection_ledger,
        projections=fixture.projection_ledger.projections[1:],
    )
    fixture = replace(fixture, raw_domain=raw_domain, projection_ledger=projection_ledger)

    outcome = _compare(fixture)

    assert isinstance(outcome, RelationRejected)
    assert {finding.code for finding in outcome.findings} >= {
        "candidate_unknown",
        "candidate_unclassified",
    }


def test_multiple_candidate_projections_and_producer_aliasing_are_rejected() -> None:
    fixture = relation_fixture()
    first = fixture.projection_ledger.projections[0]
    second = CandidateProjection(
        first.candidate_id,
        fixture.expected.rows[1].key,
        fixture.expected.rows[1].row_digest,
    )
    ledger = ProjectionLedger(
        fixture.observation.producer,
        fixture.expected.subject,
        fixture.expected.epoch,
        True,
        tuple(
            sorted(
                (*fixture.projection_ledger.projections, second),
                key=lambda item: item.sort_key,
            )
        ),
    )
    registration = replace(fixture.registration, producer=fixture.observation.producer)
    fixture = replace(fixture, registration=registration, projection_ledger=ledger)

    outcome = _compare(fixture)

    assert isinstance(outcome, RelationRejected)
    assert {finding.code for finding in outcome.findings} >= {
        "candidate_multiply_classified",
        "producer_identity_collision",
    }


def test_candidate_kind_cannot_be_projected_to_an_unrelated_row_family() -> None:
    fixture = relation_fixture()
    first = fixture.raw_domain.candidates[0]
    assert first.kind == "job"
    raw_domain = replace(
        fixture.raw_domain,
        candidates=(replace(first, kind="workflow"), *fixture.raw_domain.candidates[1:]),
    )
    observation = replace(
        fixture.observation,
        source_domain_digest=raw_domain.domain_digest,
    )

    outcome = _compare(replace(fixture, raw_domain=raw_domain, observation=observation))

    assert isinstance(outcome, RelationRejected)
    assert "projection_kind_mismatch" in {finding.code for finding in outcome.findings}


def test_epoch_and_workflow_manifest_must_match_exactly() -> None:
    fixture = relation_fixture()
    other_epoch = target_epoch(source_manifest=digest("other-source"))
    observation = TargetAuthorityInventory(
        "observation",
        ProducerIdentity("observation-producer", "1"),
        fixture.expected.subject,
        other_epoch,
        digest("other-source"),
        fixture.observation.source_domain_digest,
        fixture.observation.source_item_count,
        True,
        fixture.observation.rows,
    )
    fixture = replace(fixture, observation=observation)

    outcome = _compare(fixture)

    assert isinstance(outcome, RelationRejected)
    assert {finding.code for finding in outcome.findings} >= {
        "epoch_mismatch",
        "workflow_manifest_mismatch",
    }


def test_raw_candidate_constructor_preserves_unknown_without_digest() -> None:
    candidate = RawCandidate(
        "candidate",
        "workflow_blob",
        ".github/workflows/full.yml",
        "unknown",
        reason="blob unavailable",
    )

    assert candidate.evidence_digest is None
    assert candidate.state == "unknown"

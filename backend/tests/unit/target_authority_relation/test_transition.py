from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from ci_coordinator.target_authority_relation import (
    AuthorityIntroduction,
    AuthorityTransitionDelta,
    BaselineDisposition,
    ExpectedTargetAuthorityRelation,
    PhaseZeroBaseline,
    ProducerIdentity,
    RelationRejected,
    TargetAuthorityKey,
    apply_transition,
)
from ci_coordinator.target_authority_relation.limits import MAX_RELATION_ROWS

from .factories import (
    RelationFixture,
    digest,
    native_epoch,
    relation_fixture,
    row,
    subject,
    target_epoch,
)


def test_closed_delta_conserves_every_baseline_member_and_attributes_successors() -> None:
    fixture = relation_fixture()

    assert isinstance(fixture.expected, ExpectedTargetAuthorityRelation)
    assert len(fixture.expected.rows) == 3
    assert fixture.expected.baseline_digest == fixture.baseline.baseline_digest
    assert fixture.expected.delta_digest == fixture.delta.delta_digest


@pytest.mark.parametrize(
    ("mutate", "code"),
    (
        (
            lambda fixture: replace(
                fixture.delta,
                dispositions=fixture.delta.dispositions[1:],
            ),
            "missing_baseline_disposition",
        ),
        (
            lambda fixture: replace(
                fixture.delta,
                dispositions=tuple(
                    sorted(
                        (
                            *fixture.delta.dispositions,
                            BaselineDisposition(
                                row("provider_gate", "unseen-gate").key,
                                "retired",
                                (),
                                "not present in baseline",
                            ),
                        ),
                        key=lambda item: item.sort_key,
                    )
                ),
            ),
            "extra_baseline_disposition",
        ),
        (
            lambda fixture: replace(
                fixture.delta,
                dispositions=tuple(
                    replace(item, successors=(row(revision=2),))
                    if item.kind == "retained"
                    else item
                    for item in fixture.delta.dispositions
                ),
            ),
            "invalid_retention",
        ),
        (
            lambda fixture: replace(
                fixture.delta,
                dispositions=tuple(
                    replace(
                        item,
                        successors=(
                            next(
                                row for row in fixture.baseline.rows if row.key == item.predecessor
                            ),
                        ),
                    )
                    if item.kind == "replaced"
                    else item
                    for item in fixture.delta.dispositions
                ),
            ),
            "invalid_replacement",
        ),
    ),
)
def test_transition_counterexamples_produce_no_expected_relation(
    mutate: Callable[[RelationFixture], AuthorityTransitionDelta],
    code: str,
) -> None:
    fixture = relation_fixture()
    mutant = mutate(fixture)

    outcome = apply_transition(fixture.baseline, mutant)

    assert isinstance(outcome, RelationRejected)
    assert code in {finding.code for finding in outcome.findings}


def test_empty_phase_zero_baseline_is_unrepresentable() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        PhaseZeroBaseline(
            subject(),
            native_epoch(),
            ProducerIdentity("baseline", "1"),
            digest("native-owner"),
            (),
        )


def test_complete_retirement_cannot_create_vacuous_expected_relation() -> None:
    baseline_row = row()
    baseline = PhaseZeroBaseline(
        subject(),
        native_epoch(),
        ProducerIdentity("baseline", "1"),
        digest("native-owner"),
        (baseline_row,),
    )
    delta = AuthorityTransitionDelta(
        subject(),
        baseline.baseline_digest,
        target_epoch(),
        ProducerIdentity("transition", "1"),
        digest("target-owner"),
        (BaselineDisposition(baseline_row.key, "retired", (), "retired by owner"),),
    )

    outcome = apply_transition(baseline, delta)

    assert isinstance(outcome, RelationRejected)
    assert {finding.code for finding in outcome.findings} == {"relation_became_empty"}


def test_successor_key_collision_is_rejected() -> None:
    fixture = relation_fixture()
    collision = AuthorityIntroduction(fixture.expected.rows[0], "duplicate successor")
    delta = replace(
        fixture.delta,
        introductions=tuple(
            sorted((*fixture.delta.introductions, collision), key=lambda item: item.sort_key)
        ),
    )

    outcome = apply_transition(fixture.baseline, delta)

    assert isinstance(outcome, RelationRejected)
    assert "successor_key_collision" in {finding.code for finding in outcome.findings}


def test_retired_key_cannot_be_reintroduced_as_a_disguised_replacement() -> None:
    fixture = relation_fixture()
    retained = next(item for item in fixture.delta.dispositions if item.kind == "retained")
    disguised_retirement = replace(
        retained,
        kind="retired",
        successors=(),
        reason="claimed retirement",
    )
    delta = replace(
        fixture.delta,
        dispositions=tuple(
            sorted(
                (
                    disguised_retirement,
                    *(item for item in fixture.delta.dispositions if item is not retained),
                ),
                key=lambda item: item.sort_key,
            )
        ),
        introductions=tuple(
            sorted(
                (
                    *fixture.delta.introductions,
                    AuthorityIntroduction(retained.successors[0], "disguised replacement"),
                ),
                key=lambda item: item.sort_key,
            )
        ),
    )

    outcome = apply_transition(fixture.baseline, delta)

    assert isinstance(outcome, RelationRejected)
    assert "invalid_introduction" in {finding.code for finding in outcome.findings}


def test_transition_disposition_count_is_bounded_before_comparison() -> None:
    dispositions = tuple(
        BaselineDisposition(
            TargetAuthorityKey("job", f"job-{index:05d}"),
            "retired",
            (),
            "bounded fixture",
        )
        for index in range(MAX_RELATION_ROWS + 1)
    )

    with pytest.raises(ValueError, match="dispositions exceed the relation bound"):
        AuthorityTransitionDelta(
            subject(),
            digest("baseline"),
            target_epoch(),
            ProducerIdentity("transition", "1"),
            digest("target-owner"),
            dispositions,
        )


def test_owner_approval_must_equal_its_exact_epoch_owner() -> None:
    with pytest.raises(ValueError, match="native owner epoch"):
        PhaseZeroBaseline(
            subject(),
            native_epoch(),
            ProducerIdentity("baseline", "1"),
            digest("other-owner"),
            (row(),),
        )

    fixture = relation_fixture()
    with pytest.raises(ValueError, match="adapted owner epoch"):
        replace(fixture.delta, owner_approval_digest=digest("other-owner"))

"""Small canonical relation fixtures shared by focused witnesses."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from ci_coordinator.config_control.contracts import RepositoryScope
from ci_coordinator.target_authority_relation import (
    AuthorityField,
    AuthorityFieldEntry,
    AuthorityIntroduction,
    AuthorityTransitionDelta,
    BaselineDisposition,
    CandidateProjection,
    EpochComponent,
    ExpectedTargetAuthorityRelation,
    PhaseZeroBaseline,
    ProducerIdentity,
    ProjectionLedger,
    RawCandidate,
    RawCandidateDomain,
    TargetAuthorityEpoch,
    TargetAuthorityInventory,
    TargetAuthorityKey,
    TargetAuthorityRow,
    TargetAuthorityRowFamily,
    TargetAuthoritySubject,
    apply_transition,
    row_field_names,
)


def digest(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def subject() -> TargetAuthoritySubject:
    return TargetAuthoritySubject(RepositoryScope(11, 22), "repository-validation")


def native_epoch() -> TargetAuthorityEpoch:
    return TargetAuthorityEpoch(
        phase="native_baseline",
        source_manifest=EpochComponent.present(digest("native-source")),
        provider_governance=EpochComponent.present(digest("native-provider")),
        policy=EpochComponent.not_applicable(),
        catalog=EpochComponent.not_applicable(),
        registry=EpochComponent.not_applicable(),
        owner=EpochComponent.present(digest("native-owner")),
    )


def target_epoch(*, source_manifest: str | None = None) -> TargetAuthorityEpoch:
    return TargetAuthorityEpoch(
        phase="adapted_target",
        source_manifest=EpochComponent.present(source_manifest or digest("target-source")),
        provider_governance=EpochComponent.present(digest("target-provider")),
        policy=EpochComponent.present(digest("target-policy")),
        catalog=EpochComponent.present(digest("target-catalog")),
        registry=EpochComponent.present(digest("target-registry")),
        owner=EpochComponent.present(digest("target-owner")),
    )


def row(
    family: TargetAuthorityRowFamily = "workflow",
    member_id: str = ".github/workflows/full.yml",
    *,
    revision: int = 1,
    unknown_field: str | None = None,
) -> TargetAuthorityRow:
    fields = tuple(
        AuthorityFieldEntry(
            name,
            (
                AuthorityField.unknown("fixture uncertainty")
                if name == unknown_field
                else AuthorityField.present({"field": name, "revision": revision})
            ),
        )
        for name in row_field_names(family)
    )
    return TargetAuthorityRow(
        TargetAuthorityKey(family, member_id),
        "authority",
        "target-owner",
        f"target://{family}/{member_id}",
        fields,
    )


@dataclass(frozen=True, slots=True)
class RelationFixture:
    baseline: PhaseZeroBaseline
    delta: AuthorityTransitionDelta
    expected: ExpectedTargetAuthorityRelation
    registration: TargetAuthorityInventory
    observation: TargetAuthorityInventory
    raw_domain: RawCandidateDomain
    projection_ledger: ProjectionLedger


def relation_fixture() -> RelationFixture:
    relation_subject = subject()
    workflow = row()
    old_job = row("job", ".github/workflows/full.yml#test")
    baseline_rows = tuple(sorted((workflow, old_job), key=lambda item: item.key.sort_key))
    baseline = PhaseZeroBaseline(
        relation_subject,
        native_epoch(),
        ProducerIdentity("phase-zero-owner-inventory", "1"),
        digest("native-owner"),
        baseline_rows,
    )
    new_job = row("job", ".github/workflows/full.yml#test", revision=2)
    profile = row("validation_profile", "backend-tests")
    dispositions = tuple(
        sorted(
            (
                BaselineDisposition(
                    workflow.key,
                    "retained",
                    (workflow,),
                    "native workflow retained",
                ),
                BaselineDisposition(old_job.key, "replaced", (new_job,), "job authority adapted"),
            ),
            key=lambda item: item.sort_key,
        )
    )
    delta = AuthorityTransitionDelta(
        relation_subject,
        baseline.baseline_digest,
        target_epoch(),
        ProducerIdentity("owner-transition", "1"),
        digest("target-owner"),
        dispositions,
        (AuthorityIntroduction(profile, "registered validation profile"),),
    )
    expected = apply_transition(baseline, delta)
    if not isinstance(expected, ExpectedTargetAuthorityRelation):
        raise AssertionError("canonical fixture transition was unexpectedly rejected")

    registration_producer = ProducerIdentity("registration-producer", "1")
    observation_producer = ProducerIdentity("observation-producer", "1")
    manifest_digest = target_epoch().source_manifest.digest
    if manifest_digest is None:
        raise AssertionError("canonical target epoch lost its manifest digest")
    candidates = tuple(
        RawCandidate(
            candidate_id=f"candidate-{index:02d}",
            kind=item.key.family,
            source_locator=item.source_locator,
            state="present",
            evidence_digest=digest(f"candidate-evidence-{index}"),
        )
        for index, item in enumerate(expected.rows)
    )
    raw_domain = RawCandidateDomain(
        observation_producer,
        relation_subject,
        target_epoch(),
        True,
        candidates,
    )
    registration = TargetAuthorityInventory(
        "registration",
        registration_producer,
        relation_subject,
        target_epoch(),
        manifest_digest,
        expected.relation_digest,
        len(expected.rows),
        True,
        expected.rows,
    )
    observation = TargetAuthorityInventory(
        "observation",
        observation_producer,
        relation_subject,
        target_epoch(),
        manifest_digest,
        raw_domain.domain_digest,
        len(candidates),
        True,
        expected.rows,
    )
    projections = tuple(
        sorted(
            (
                CandidateProjection(candidate.candidate_id, item.key, item.row_digest)
                for candidate, item in zip(candidates, expected.rows, strict=True)
            ),
            key=lambda item: item.sort_key,
        )
    )
    projection_ledger = ProjectionLedger(
        observation_producer,
        relation_subject,
        target_epoch(),
        True,
        projections,
    )
    return RelationFixture(
        baseline,
        delta,
        expected,
        registration,
        observation,
        raw_domain,
        projection_ledger,
    )

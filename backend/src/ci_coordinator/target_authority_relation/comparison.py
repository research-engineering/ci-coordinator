"""Total exact comparator for independently produced relation evidence."""

from __future__ import annotations

from collections import defaultdict

from .inventory import (
    CandidateProjection,
    ProjectionLedger,
    RawCandidateDomain,
    TargetAuthorityInventory,
)
from .model import TargetAuthorityKey, TargetAuthorityRow
from .outcomes import (
    RelationComparisonOutcome,
    RelationFinding,
    RelationFindingCode,
    RelationRejected,
    UnactivatedRelationClosure,
    canonical_findings,
)
from .transition import (
    AuthorityTransitionDelta,
    ExpectedTargetAuthorityRelation,
    PhaseZeroBaseline,
    apply_transition,
)


def close_target_authority_relation(
    baseline: PhaseZeroBaseline,
    delta: AuthorityTransitionDelta,
    registration: TargetAuthorityInventory,
    observation: TargetAuthorityInventory,
    raw_domain: RawCandidateDomain,
    projection_ledger: ProjectionLedger,
) -> RelationComparisonOutcome:
    transition = apply_transition(baseline, delta)
    if isinstance(transition, RelationRejected):
        return transition
    return _compare_derived_relation(
        transition,
        registration,
        observation,
        raw_domain,
        projection_ledger,
    )


def _compare_derived_relation(
    expected: ExpectedTargetAuthorityRelation,
    registration: TargetAuthorityInventory,
    observation: TargetAuthorityInventory,
    raw_domain: RawCandidateDomain,
    projection_ledger: ProjectionLedger,
) -> RelationComparisonOutcome:
    if (
        type(registration) is not TargetAuthorityInventory
        or registration.kind != "registration"
        or type(observation) is not TargetAuthorityInventory
        or observation.kind != "observation"
    ):
        raise TypeError(
            "relation comparator requires exact registration and observation inventories"
        )
    if (
        type(raw_domain) is not RawCandidateDomain
        or type(projection_ledger) is not ProjectionLedger
    ):
        raise TypeError("relation comparator requires exact raw-domain and projection evidence")

    findings: list[RelationFinding] = []
    artifacts = {
        "registration": (registration.subject, registration.epoch),
        "observation": (observation.subject, observation.epoch),
        "raw_domain": (raw_domain.subject, raw_domain.epoch),
        "projection_ledger": (projection_ledger.subject, projection_ledger.epoch),
    }
    for name, (subject, epoch) in artifacts.items():
        if subject != expected.subject:
            findings.append(RelationFinding("subject_mismatch", name))
        if epoch != expected.epoch:
            findings.append(
                RelationFinding(
                    "epoch_mismatch",
                    name,
                    expected.epoch.epoch_digest,
                    epoch.epoch_digest,
                )
            )

    if registration.producer == observation.producer:
        findings.append(RelationFinding("producer_identity_collision"))
    if (
        raw_domain.producer != observation.producer
        or projection_ledger.producer != observation.producer
    ):
        findings.append(RelationFinding("producer_identity_collision", "observation_domain"))

    expected_manifest = expected.epoch.source_manifest.digest
    if expected_manifest is None:
        raise AssertionError("validated adapted-target epoch lost its source manifest")
    for name, manifest in (
        ("registration", registration.workflow_manifest_digest),
        ("observation", observation.workflow_manifest_digest),
    ):
        if manifest != expected_manifest:
            findings.append(
                RelationFinding(
                    "workflow_manifest_mismatch",
                    name,
                    expected_manifest,
                    manifest,
                )
            )

    if not registration.complete:
        findings.append(RelationFinding("registration_incomplete"))
    if not observation.complete:
        findings.append(RelationFinding("observation_incomplete"))
    if not raw_domain.complete:
        findings.append(RelationFinding("raw_domain_incomplete"))
    if not projection_ledger.complete:
        findings.append(RelationFinding("projection_incomplete"))

    _compare_source_domain(
        registration,
        expected.relation_digest,
        len(expected.rows),
        digest_code="registration_source_domain_mismatch",
        count_code="registration_source_count_mismatch",
        findings=findings,
    )
    _compare_source_domain(
        observation,
        raw_domain.domain_digest,
        len(raw_domain.candidates),
        digest_code="observation_source_domain_mismatch",
        count_code="observation_source_count_mismatch",
        findings=findings,
    )

    expected_rows = _rows_by_key(expected.rows)
    registration_rows = _rows_by_key(registration.rows)
    observation_rows = _rows_by_key(observation.rows)
    _compare_inventory(
        expected_rows,
        registration_rows,
        missing_code="missing_registration_row",
        extra_code="extra_registration_row",
        mismatch_code="registration_row_mismatch",
        unknown_code="unknown_registration_row",
        findings=findings,
    )
    _compare_inventory(
        expected_rows,
        observation_rows,
        missing_code="missing_observation_row",
        extra_code="extra_observation_row",
        mismatch_code="observation_row_mismatch",
        unknown_code="unknown_observation_row",
        findings=findings,
    )

    candidate_by_id = {candidate.candidate_id: candidate for candidate in raw_domain.candidates}
    findings.extend(
        RelationFinding("candidate_unknown", candidate.candidate_id)
        for candidate in raw_domain.candidates
        if candidate.state == "unknown"
    )

    projections_by_candidate: defaultdict[str, list[CandidateProjection]] = defaultdict(list)
    for projection in projection_ledger.projections:
        projections_by_candidate[projection.candidate_id].append(projection)
    for candidate_id in sorted(candidate_by_id):
        projections = projections_by_candidate.get(candidate_id, [])
        if not projections:
            findings.append(RelationFinding("candidate_unclassified", candidate_id))
        elif len(projections) != 1:
            findings.append(RelationFinding("candidate_multiply_classified", candidate_id))
    findings.extend(
        RelationFinding("projection_incomplete", candidate_id)
        for candidate_id in sorted(projections_by_candidate.keys() - candidate_by_id.keys())
    )

    projected_row_keys: set[TargetAuthorityKey] = set()
    for projection in projection_ledger.projections:
        key = projection.row_key
        projected_row_keys.add(key)
        candidate = candidate_by_id.get(projection.candidate_id)
        if candidate is not None and candidate.projected_family != key.family:
            findings.append(RelationFinding("projection_kind_mismatch", projection.candidate_id))
        row = observation_rows.get(key)
        if row is None:
            findings.append(
                RelationFinding(
                    "projection_row_missing",
                    _coordinate(projection.row_key),
                )
            )
        elif projection.row_digest != row.row_digest:
            findings.append(
                RelationFinding(
                    "projection_row_mismatch",
                    _coordinate(projection.row_key),
                    row.row_digest,
                    projection.row_digest,
                )
            )
    findings.extend(
        RelationFinding("unprojected_observation_row", _coordinate(key))
        for key in sorted(
            observation_rows.keys() - projected_row_keys,
            key=lambda item: item.sort_key,
        )
    )

    if findings:
        return RelationRejected(canonical_findings(findings))
    return UnactivatedRelationClosure(
        subject=expected.subject,
        epoch=expected.epoch,
        baseline_digest=expected.baseline_digest,
        delta_digest=expected.delta_digest,
        expected_relation_digest=expected.relation_digest,
        registration_inventory_digest=registration.inventory_digest,
        observation_inventory_digest=observation.inventory_digest,
        raw_candidate_domain_digest=raw_domain.domain_digest,
        projection_ledger_digest=projection_ledger.ledger_digest,
        registration_producer=registration.producer,
        observation_producer=observation.producer,
        row_count=len(expected.rows),
        candidate_count=len(raw_domain.candidates),
    )


def _compare_source_domain(
    inventory: TargetAuthorityInventory,
    expected_digest: str,
    expected_count: int,
    *,
    digest_code: RelationFindingCode,
    count_code: RelationFindingCode,
    findings: list[RelationFinding],
) -> None:
    if inventory.source_domain_digest != expected_digest:
        findings.append(
            RelationFinding(
                digest_code,
                expected_digest=expected_digest,
                observed_digest=inventory.source_domain_digest,
            )
        )
    if inventory.source_item_count != expected_count:
        findings.append(
            RelationFinding(
                count_code,
                f"expected={expected_count},observed={inventory.source_item_count}",
            )
        )


def _compare_inventory(
    expected: dict[TargetAuthorityKey, TargetAuthorityRow],
    observed: dict[TargetAuthorityKey, TargetAuthorityRow],
    *,
    missing_code: RelationFindingCode,
    extra_code: RelationFindingCode,
    mismatch_code: RelationFindingCode,
    unknown_code: RelationFindingCode,
    findings: list[RelationFinding],
) -> None:
    findings.extend(
        RelationFinding(missing_code, _coordinate(key))
        for key in sorted(expected.keys() - observed.keys(), key=lambda item: item.sort_key)
    )
    findings.extend(
        RelationFinding(extra_code, _coordinate(key))
        for key in sorted(observed.keys() - expected.keys(), key=lambda item: item.sort_key)
    )
    for key in sorted(expected.keys() & observed.keys(), key=lambda item: item.sort_key):
        expected_row = expected[key]
        observed_row = observed[key]
        if observed_row.has_unknown:
            findings.append(RelationFinding(unknown_code, _coordinate(observed_row.key)))
        if expected_row.row_digest != observed_row.row_digest:
            findings.append(
                RelationFinding(
                    mismatch_code,
                    _coordinate(expected_row.key),
                    expected_row.row_digest,
                    observed_row.row_digest,
                )
            )


def _rows_by_key(
    rows: tuple[TargetAuthorityRow, ...],
) -> dict[TargetAuthorityKey, TargetAuthorityRow]:
    return {row.key: row for row in rows}


def _coordinate(key: TargetAuthorityKey) -> str:
    return f"{key.family}:{key.member_id}"

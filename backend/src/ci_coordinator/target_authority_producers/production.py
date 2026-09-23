"""Independent registration and observation inventory production."""

from __future__ import annotations

from collections import defaultdict

from ci_coordinator.target_authority_relation import (
    CandidateProjection,
    ExpectedTargetAuthorityRelation,
    ProducerIdentity,
    ProjectionLedger,
    RawCandidateDomain,
    TargetAuthorityEpoch,
    TargetAuthorityInventory,
    TargetAuthorityKey,
    TargetAuthorityRow,
    TargetAuthoritySubject,
)

from .model import (
    ObservationCandidateSet,
    ObservationProduction,
    ObservedCandidate,
    OwnerProjectionPolicy,
    ProjectionRule,
    RegistrationCandidateSet,
    RegistrationProduction,
    TargetAuthorityProducerError,
)
from .registration_candidates import enumerate_registration_candidates
from .sources import TargetArtifactSources


def produce_registration(
    expected: ExpectedTargetAuthorityRelation,
    sources: TargetArtifactSources,
    policy: OwnerProjectionPolicy,
    producer: ProducerIdentity,
) -> RegistrationProduction:
    if type(expected) is not ExpectedTargetAuthorityRelation:
        raise TypeError("registration producer requires an exact expected relation")
    if (
        type(sources) is not TargetArtifactSources
        or type(policy) is not OwnerProjectionPolicy
        or type(producer) is not ProducerIdentity
    ):
        raise TypeError("registration producer requires exact sources, policy, and identity")
    candidates = enumerate_registration_candidates(sources)
    return project_registration(expected, candidates, policy, producer)


def project_registration(
    expected: ExpectedTargetAuthorityRelation,
    candidates: RegistrationCandidateSet,
    policy: OwnerProjectionPolicy,
    producer: ProducerIdentity,
) -> RegistrationProduction:
    if type(expected) is not ExpectedTargetAuthorityRelation:
        raise TypeError("registration projection requires an exact expected relation")
    if (
        type(candidates) is not RegistrationCandidateSet
        or type(policy) is not OwnerProjectionPolicy
        or type(producer) is not ProducerIdentity
    ):
        raise TypeError("registration projection requires exact candidates, policy, and identity")
    _require_policy_binding(
        policy,
        subject=expected.subject,
        epoch=expected.epoch,
        workflow_manifest_digest=_manifest_digest(expected.epoch),
        target_artifact_epoch_digest=candidates.target_artifact_epoch_digest,
        observation_authority_domain_digest=None,
        registration_declaration_domain_digest=candidates.candidate_set_digest,
    )
    _require_registration_epoch(candidates, expected)
    rules_by_key: defaultdict[TargetAuthorityKey, list[ProjectionRule]] = defaultdict(list)
    for rule in policy.rules:
        rules_by_key[rule.row_key].append(rule)
    expected_by_key = {row.key: row for row in expected.rows}
    if rules_by_key.keys() != expected_by_key.keys():
        raise TargetAuthorityProducerError(
            "registration_key_domain_mismatch",
            "owner projection policy does not cover the expected relation exactly",
        )
    for key, rules in rules_by_key.items():
        expected_row = expected_by_key[key]
        if any(
            (
                rule.disposition,
                rule.semantic_owner,
                rule.source_locator,
            )
            != (
                expected_row.disposition,
                expected_row.semantic_owner,
                expected_row.source_locator,
            )
            for rule in rules
        ):
            raise TargetAuthorityProducerError(
                "registration_owner_projection_mismatch",
                f"owner projection metadata disagrees with expected row {key.member_id}",
            )
    rule_by_id = {rule.candidate_id: rule for rule in policy.rules}
    for candidate in candidates.candidates:
        declaration_rule = rule_by_id.get(candidate.candidate_id)
        if declaration_rule is None:
            raise TargetAuthorityProducerError(
                "registration_declaration_unclassified",
                f"registration declaration lacks owner policy: {candidate.candidate_id}",
            )
        if declaration_rule.evidence_digest != candidate.evidence_digest:
            raise TargetAuthorityProducerError(
                "registration_declaration_evidence_mismatch",
                f"registration declaration evidence is stale: {candidate.candidate_id}",
            )
        if declaration_rule.row_key.family != candidate.projected_family:
            raise TargetAuthorityProducerError(
                "registration_declaration_family_mismatch",
                f"registration declaration crosses row families: {candidate.candidate_id}",
            )
        row = TargetAuthorityRow(
            key=declaration_rule.row_key,
            disposition=declaration_rule.disposition,
            semantic_owner=declaration_rule.semantic_owner,
            source_locator=declaration_rule.source_locator,
            fields=candidate.fields,
        )
        declared_expected_row = expected_by_key.get(row.key)
        if declared_expected_row is None or declared_expected_row.row_digest != row.row_digest:
            raise TargetAuthorityProducerError(
                "registration_declaration_row_mismatch",
                f"registration declaration disagrees with expected row: {candidate.candidate_id}",
            )
    inventory = TargetAuthorityInventory(
        kind="registration",
        producer=producer,
        subject=expected.subject,
        epoch=expected.epoch,
        workflow_manifest_digest=_manifest_digest(expected.epoch),
        source_domain_digest=expected.relation_digest,
        source_item_count=len(expected.rows),
        complete=True,
        rows=expected.rows,
    )
    return RegistrationProduction(
        inventory=inventory,
        declaration_domain_digest=candidates.candidate_set_digest,
        declaration_count=len(candidates.candidates),
        target_artifact_epoch_digest=candidates.target_artifact_epoch_digest,
    )


def produce_observation(
    candidates: ObservationCandidateSet,
    policy: OwnerProjectionPolicy,
    producer: ProducerIdentity,
    subject: TargetAuthoritySubject,
    epoch: TargetAuthorityEpoch,
) -> ObservationProduction:
    if type(candidates) is not ObservationCandidateSet:
        raise TypeError("observation producer requires an exact candidate set")
    if (
        type(policy) is not OwnerProjectionPolicy
        or type(producer) is not ProducerIdentity
        or type(subject) is not TargetAuthoritySubject
        or type(epoch) is not TargetAuthorityEpoch
    ):
        raise TypeError("observation producer requires exact policy, identity, subject, and epoch")
    if candidates.scope != subject.scope:
        raise TargetAuthorityProducerError(
            "observation_scope_mismatch",
            "observation candidates cross the target-authority subject scope",
        )
    _require_candidate_epoch(candidates, epoch)
    _require_policy_binding(
        policy,
        subject=subject,
        epoch=epoch,
        workflow_manifest_digest=candidates.workflow_manifest_digest,
        target_artifact_epoch_digest=candidates.target_artifact_epoch_digest,
        observation_authority_domain_digest=candidates.authority_domain_digest,
        registration_declaration_domain_digest=None,
    )
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates.candidates}
    rule_by_id = {rule.candidate_id: rule for rule in policy.rules}
    if candidate_by_id.keys() != rule_by_id.keys():
        raise TargetAuthorityProducerError(
            "candidate_classification_domain_mismatch",
            "owner projection rules do not classify the independently enumerated domain exactly",
        )

    projected: list[tuple[ObservedCandidate, TargetAuthorityRow]] = []
    rows_by_key: defaultdict[TargetAuthorityKey, list[TargetAuthorityRow]] = defaultdict(list)
    for candidate in candidates.candidates:
        rule = rule_by_id[candidate.candidate_id]
        if rule.evidence_digest != candidate.evidence_digest:
            raise TargetAuthorityProducerError(
                "candidate_evidence_mismatch",
                f"owner projection evidence is stale for {candidate.candidate_id}",
            )
        if rule.row_key.family != candidate.projected_family:
            raise TargetAuthorityProducerError(
                "candidate_family_mismatch",
                f"owner projection crosses row families for {candidate.candidate_id}",
            )
        row = TargetAuthorityRow(
            key=rule.row_key,
            disposition=rule.disposition,
            semantic_owner=rule.semantic_owner,
            source_locator=rule.source_locator,
            fields=candidate.fields,
        )
        projected.append((candidate, row))
        rows_by_key[row.key].append(row)

    rows: list[TargetAuthorityRow] = []
    for key in sorted(rows_by_key, key=lambda item: item.sort_key):
        candidates_for_row = rows_by_key[key]
        first = candidates_for_row[0]
        if any(row.row_digest != first.row_digest for row in candidates_for_row[1:]):
            raise TargetAuthorityProducerError(
                "correlated_projection_mismatch",
                f"independent candidates disagree on full row {key.member_id}",
            )
        rows.append(first)

    raw_domain = RawCandidateDomain(
        producer=producer,
        subject=subject,
        epoch=epoch,
        complete=True,
        candidates=tuple(candidate.raw_candidate for candidate in candidates.candidates),
    )
    inventory = TargetAuthorityInventory(
        kind="observation",
        producer=producer,
        subject=subject,
        epoch=epoch,
        workflow_manifest_digest=candidates.workflow_manifest_digest,
        source_domain_digest=raw_domain.domain_digest,
        source_item_count=len(candidates.candidates),
        complete=True,
        rows=tuple(rows),
    )
    projection_ledger = ProjectionLedger(
        producer=producer,
        subject=subject,
        epoch=epoch,
        complete=True,
        projections=tuple(
            sorted(
                (
                    CandidateProjection(
                        candidate.candidate_id,
                        row.key,
                        row.row_digest,
                    )
                    for candidate, row in projected
                ),
                key=lambda item: item.sort_key,
            )
        ),
    )
    return ObservationProduction(raw_domain, inventory, projection_ledger)


def _require_candidate_epoch(
    candidates: ObservationCandidateSet,
    epoch: TargetAuthorityEpoch,
) -> None:
    if epoch.phase != "adapted_target" or epoch.has_unknown:
        raise TargetAuthorityProducerError(
            "observation_epoch_incomplete",
            "observation production requires one complete adapted-target epoch",
        )
    components = (
        (epoch.source_manifest.digest, candidates.workflow_manifest_digest),
        (epoch.provider_governance.digest, candidates.provider_authority_digest),
        (epoch.policy.digest, candidates.target_policy_digest),
        (epoch.catalog.digest, candidates.validation_catalog_digest),
        (epoch.registry.digest, candidates.target_registry_digest),
    )
    if any(observed != expected for observed, expected in components):
        raise TargetAuthorityProducerError(
            "observation_epoch_mismatch",
            "observation candidate component digests cross the target epoch",
        )


def _require_registration_epoch(
    candidates: RegistrationCandidateSet,
    expected: ExpectedTargetAuthorityRelation,
) -> None:
    epoch = expected.epoch
    if candidates.scope != expected.subject.scope:
        raise TargetAuthorityProducerError(
            "registration_scope_mismatch",
            "registration declarations cross the target-authority subject scope",
        )
    components = (
        (epoch.policy.digest, candidates.target_policy_digest),
        (epoch.catalog.digest, candidates.validation_catalog_digest),
        (epoch.registry.digest, candidates.target_registry_digest),
    )
    if (
        epoch.phase != "adapted_target"
        or epoch.has_unknown
        or any(observed != declared for observed, declared in components)
    ):
        raise TargetAuthorityProducerError(
            "registration_epoch_mismatch",
            "registration declarations cross the expected target epoch",
        )


def _require_policy_binding(
    policy: OwnerProjectionPolicy,
    *,
    subject: TargetAuthoritySubject,
    epoch: TargetAuthorityEpoch,
    workflow_manifest_digest: str,
    target_artifact_epoch_digest: str,
    observation_authority_domain_digest: str | None,
    registration_declaration_domain_digest: str | None,
) -> None:
    if (
        policy.subject != subject
        or policy.workflow_manifest_digest != workflow_manifest_digest
        or policy.target_artifact_epoch_digest != target_artifact_epoch_digest
    ):
        raise TargetAuthorityProducerError(
            "owner_policy_source_mismatch",
            "owner projection policy crosses subject, workflow, or target-artifact identity",
        )
    if (
        observation_authority_domain_digest is not None
        and policy.observation_authority_domain_digest != observation_authority_domain_digest
    ) or (
        registration_declaration_domain_digest is not None
        and policy.registration_declaration_domain_digest != registration_declaration_domain_digest
    ):
        raise TargetAuthorityProducerError(
            "owner_policy_domain_mismatch",
            "owner projection policy does not bind the complete producer domain",
        )
    if epoch.owner.digest != policy.policy_digest:
        raise TargetAuthorityProducerError(
            "owner_policy_epoch_mismatch",
            "owner projection policy is not the owner component of the target epoch",
        )


def _manifest_digest(epoch: TargetAuthorityEpoch) -> str:
    digest = epoch.source_manifest.digest
    if digest is None:
        raise AssertionError("complete adapted-target epoch lost its source manifest")
    return digest

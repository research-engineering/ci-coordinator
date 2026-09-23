"""Strict construction of relation artifacts from admitted mappings."""

from __future__ import annotations

from typing import cast

from ._model_decoding import (
    decode_epoch_mapping,
    decode_key_mapping,
    decode_producer_mapping,
    decode_row_mapping,
    decode_subject_mapping,
    exact_boolean,
    exact_integer,
    exact_keys,
    exact_list,
    exact_string,
)
from .inventory import (
    AuthorityInventoryKind,
    CandidateProjection,
    ProjectionLedger,
    RawCandidate,
    RawCandidateDomain,
    RawCandidateKind,
    RawCandidateState,
    TargetAuthorityInventory,
)
from .model import TargetAuthorityRow
from .outcomes import UnactivatedRelationClosure
from .transition import (
    AuthorityIntroduction,
    AuthorityTransitionDelta,
    BaselineDisposition,
    BaselineDispositionKind,
    ExpectedTargetAuthorityRelation,
    PhaseZeroBaseline,
)


def decode_baseline_mapping(value: object) -> PhaseZeroBaseline:
    mapping = exact_keys(
        value,
        {"schemaVersion", "subject", "epoch", "producer", "ownerApprovalDigest", "rows"},
        "Phase-0 baseline",
    )
    return PhaseZeroBaseline(
        subject=decode_subject_mapping(mapping["subject"]),
        epoch=decode_epoch_mapping(mapping["epoch"]),
        producer=decode_producer_mapping(mapping["producer"]),
        owner_approval_digest=exact_string(mapping["ownerApprovalDigest"], "owner approval"),
        rows=_rows(mapping["rows"]),
    )


def decode_delta_mapping(value: object) -> AuthorityTransitionDelta:
    mapping = exact_keys(
        value,
        {
            "schemaVersion",
            "subject",
            "baselineDigest",
            "targetEpoch",
            "producer",
            "ownerApprovalDigest",
            "dispositions",
            "introductions",
        },
        "authority transition delta",
    )
    return AuthorityTransitionDelta(
        subject=decode_subject_mapping(mapping["subject"]),
        baseline_digest=exact_string(mapping["baselineDigest"], "baseline digest"),
        target_epoch=decode_epoch_mapping(mapping["targetEpoch"]),
        producer=decode_producer_mapping(mapping["producer"]),
        owner_approval_digest=exact_string(mapping["ownerApprovalDigest"], "owner approval"),
        dispositions=tuple(
            _disposition(item)
            for item in exact_list(mapping["dispositions"], "transition dispositions")
        ),
        introductions=tuple(
            _introduction(item)
            for item in exact_list(mapping["introductions"], "authority introductions")
        ),
    )


def decode_expected_mapping(value: object) -> ExpectedTargetAuthorityRelation:
    mapping = exact_keys(
        value,
        {"schemaVersion", "subject", "epoch", "baselineDigest", "deltaDigest", "rows"},
        "expected target-authority relation",
    )
    return ExpectedTargetAuthorityRelation(
        subject=decode_subject_mapping(mapping["subject"]),
        epoch=decode_epoch_mapping(mapping["epoch"]),
        baseline_digest=exact_string(mapping["baselineDigest"], "baseline digest"),
        delta_digest=exact_string(mapping["deltaDigest"], "delta digest"),
        rows=_rows(mapping["rows"]),
    )


def decode_inventory_mapping(value: object) -> TargetAuthorityInventory:
    mapping = exact_keys(
        value,
        {
            "schemaVersion",
            "kind",
            "producer",
            "subject",
            "epoch",
            "workflowManifestDigest",
            "sourceDomainDigest",
            "sourceItemCount",
            "complete",
            "rows",
        },
        "target-authority inventory",
    )
    return TargetAuthorityInventory(
        kind=cast(AuthorityInventoryKind, exact_string(mapping["kind"], "inventory kind")),
        producer=decode_producer_mapping(mapping["producer"]),
        subject=decode_subject_mapping(mapping["subject"]),
        epoch=decode_epoch_mapping(mapping["epoch"]),
        workflow_manifest_digest=exact_string(
            mapping["workflowManifestDigest"],
            "workflow manifest digest",
        ),
        source_domain_digest=exact_string(mapping["sourceDomainDigest"], "source domain digest"),
        source_item_count=exact_integer(mapping["sourceItemCount"], "source item count"),
        complete=exact_boolean(mapping["complete"], "inventory completeness"),
        rows=_rows(mapping["rows"]),
    )


def decode_raw_domain_mapping(value: object) -> RawCandidateDomain:
    mapping = exact_keys(
        value,
        {"schemaVersion", "producer", "subject", "epoch", "complete", "candidates"},
        "raw candidate domain",
    )
    return RawCandidateDomain(
        producer=decode_producer_mapping(mapping["producer"]),
        subject=decode_subject_mapping(mapping["subject"]),
        epoch=decode_epoch_mapping(mapping["epoch"]),
        complete=exact_boolean(mapping["complete"], "raw domain completeness"),
        candidates=tuple(
            _candidate(item) for item in exact_list(mapping["candidates"], "raw candidates")
        ),
    )


def decode_projection_ledger_mapping(value: object) -> ProjectionLedger:
    mapping = exact_keys(
        value,
        {"schemaVersion", "producer", "subject", "epoch", "complete", "projections"},
        "projection ledger",
    )
    return ProjectionLedger(
        producer=decode_producer_mapping(mapping["producer"]),
        subject=decode_subject_mapping(mapping["subject"]),
        epoch=decode_epoch_mapping(mapping["epoch"]),
        complete=exact_boolean(mapping["complete"], "projection completeness"),
        projections=tuple(
            _projection(item)
            for item in exact_list(mapping["projections"], "candidate projections")
        ),
    )


def decode_closure_mapping(value: object) -> UnactivatedRelationClosure:
    mapping = exact_keys(
        value,
        {
            "schemaVersion",
            "authorityState",
            "subject",
            "epoch",
            "baselineDigest",
            "deltaDigest",
            "expectedRelationDigest",
            "registrationInventoryDigest",
            "observationInventoryDigest",
            "rawCandidateDomainDigest",
            "projectionLedgerDigest",
            "registrationProducer",
            "observationProducer",
            "rowCount",
            "candidateCount",
        },
        "unactivated relation closure",
    )
    if mapping["authorityState"] != "unactivated":
        raise ValueError("relation closure cannot claim activation")
    return UnactivatedRelationClosure(
        subject=decode_subject_mapping(mapping["subject"]),
        epoch=decode_epoch_mapping(mapping["epoch"]),
        baseline_digest=exact_string(mapping["baselineDigest"], "baseline digest"),
        delta_digest=exact_string(mapping["deltaDigest"], "delta digest"),
        expected_relation_digest=exact_string(
            mapping["expectedRelationDigest"],
            "expected relation digest",
        ),
        registration_inventory_digest=exact_string(
            mapping["registrationInventoryDigest"],
            "registration inventory digest",
        ),
        observation_inventory_digest=exact_string(
            mapping["observationInventoryDigest"],
            "observation inventory digest",
        ),
        raw_candidate_domain_digest=exact_string(
            mapping["rawCandidateDomainDigest"],
            "raw candidate domain digest",
        ),
        projection_ledger_digest=exact_string(
            mapping["projectionLedgerDigest"],
            "projection ledger digest",
        ),
        registration_producer=decode_producer_mapping(mapping["registrationProducer"]),
        observation_producer=decode_producer_mapping(mapping["observationProducer"]),
        row_count=exact_integer(mapping["rowCount"], "closure row count"),
        candidate_count=exact_integer(mapping["candidateCount"], "closure candidate count"),
    )


def _rows(value: object) -> tuple[TargetAuthorityRow, ...]:
    return tuple(decode_row_mapping(item) for item in exact_list(value, "relation rows"))


def _disposition(value: object) -> BaselineDisposition:
    mapping = exact_keys(
        value,
        {"predecessor", "kind", "successors", "reason"},
        "baseline disposition",
    )
    return BaselineDisposition(
        predecessor=decode_key_mapping(mapping["predecessor"]),
        kind=cast(BaselineDispositionKind, exact_string(mapping["kind"], "disposition kind")),
        successors=_rows(mapping["successors"]),
        reason=exact_string(mapping["reason"], "disposition reason"),
    )


def _introduction(value: object) -> AuthorityIntroduction:
    mapping = exact_keys(value, {"row", "reason"}, "authority introduction")
    return AuthorityIntroduction(
        decode_row_mapping(mapping["row"]),
        exact_string(mapping["reason"], "introduction reason"),
    )


def _candidate(value: object) -> RawCandidate:
    mapping = exact_keys(
        value,
        {"candidateId", "kind", "sourceLocator", "state", "evidenceDigest", "reason"},
        "raw candidate",
    )
    return RawCandidate(
        candidate_id=exact_string(mapping["candidateId"], "candidate id"),
        kind=cast(RawCandidateKind, exact_string(mapping["kind"], "candidate kind")),
        source_locator=exact_string(mapping["sourceLocator"], "candidate source locator"),
        state=cast(RawCandidateState, exact_string(mapping["state"], "candidate state")),
        evidence_digest=(
            None
            if mapping["evidenceDigest"] is None
            else exact_string(mapping["evidenceDigest"], "candidate evidence digest")
        ),
        reason=(
            None
            if mapping["reason"] is None
            else exact_string(mapping["reason"], "candidate unknown reason")
        ),
    )


def _projection(value: object) -> CandidateProjection:
    mapping = exact_keys(
        value,
        {"candidateId", "rowKey", "rowDigest"},
        "candidate projection",
    )
    return CandidateProjection(
        candidate_id=exact_string(mapping["candidateId"], "candidate id"),
        row_key=decode_key_mapping(mapping["rowKey"]),
        row_digest=exact_string(mapping["rowDigest"], "projected row digest"),
    )

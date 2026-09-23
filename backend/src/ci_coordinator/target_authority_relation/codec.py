"""Canonical round-trip codecs for dormant relation artifacts."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, cast

from ._artifact_decoding import (
    decode_baseline_mapping,
    decode_closure_mapping,
    decode_delta_mapping,
    decode_expected_mapping,
    decode_inventory_mapping,
    decode_projection_ledger_mapping,
    decode_raw_domain_mapping,
)
from ._canonical import encode_mapping
from ._model_decoding import admit_document, decode_row_mapping
from .inventory import (
    PROJECTION_LEDGER_SCHEMA,
    RAW_CANDIDATE_DOMAIN_SCHEMA,
    TARGET_AUTHORITY_INVENTORY_SCHEMA,
    ProjectionLedger,
    RawCandidateDomain,
    TargetAuthorityInventory,
)
from .model import TARGET_AUTHORITY_ROW_SCHEMA, TargetAuthorityRow
from .outcomes import RELATION_CLOSURE_SCHEMA, UnactivatedRelationClosure
from .transition import (
    AUTHORITY_TRANSITION_DELTA_SCHEMA,
    EXPECTED_TARGET_RELATION_SCHEMA,
    PHASE_ZERO_BASELINE_SCHEMA,
    AuthorityTransitionDelta,
    ExpectedTargetAuthorityRelation,
    PhaseZeroBaseline,
)


class _CanonicalArtifact(Protocol):
    def to_mapping(self) -> dict[str, object]: ...


def encode_row(value: TargetAuthorityRow) -> bytes:
    return _encode_exact(value, TargetAuthorityRow)


def decode_row(body: bytes) -> TargetAuthorityRow:
    return decode_row_mapping(admit_document(body, TARGET_AUTHORITY_ROW_SCHEMA))


def encode_baseline(value: PhaseZeroBaseline) -> bytes:
    return _encode_exact(value, PhaseZeroBaseline)


def decode_baseline(body: bytes) -> PhaseZeroBaseline:
    return _decode(body, PHASE_ZERO_BASELINE_SCHEMA, decode_baseline_mapping)


def encode_transition_delta(value: AuthorityTransitionDelta) -> bytes:
    return _encode_exact(value, AuthorityTransitionDelta)


def decode_transition_delta(body: bytes) -> AuthorityTransitionDelta:
    return _decode(body, AUTHORITY_TRANSITION_DELTA_SCHEMA, decode_delta_mapping)


def encode_expected_relation(value: ExpectedTargetAuthorityRelation) -> bytes:
    return _encode_exact(value, ExpectedTargetAuthorityRelation)


def decode_expected_relation(body: bytes) -> ExpectedTargetAuthorityRelation:
    return _decode(body, EXPECTED_TARGET_RELATION_SCHEMA, decode_expected_mapping)


def encode_inventory(value: TargetAuthorityInventory) -> bytes:
    return _encode_exact(value, TargetAuthorityInventory)


def decode_inventory(body: bytes) -> TargetAuthorityInventory:
    return _decode(body, TARGET_AUTHORITY_INVENTORY_SCHEMA, decode_inventory_mapping)


def encode_raw_candidate_domain(value: RawCandidateDomain) -> bytes:
    return _encode_exact(value, RawCandidateDomain)


def decode_raw_candidate_domain(body: bytes) -> RawCandidateDomain:
    return _decode(body, RAW_CANDIDATE_DOMAIN_SCHEMA, decode_raw_domain_mapping)


def encode_projection_ledger(value: ProjectionLedger) -> bytes:
    return _encode_exact(value, ProjectionLedger)


def decode_projection_ledger(body: bytes) -> ProjectionLedger:
    return _decode(body, PROJECTION_LEDGER_SCHEMA, decode_projection_ledger_mapping)


def encode_unactivated_closure(value: UnactivatedRelationClosure) -> bytes:
    return _encode_exact(value, UnactivatedRelationClosure)


def decode_unactivated_closure(body: bytes) -> UnactivatedRelationClosure:
    return _decode(body, RELATION_CLOSURE_SCHEMA, decode_closure_mapping)


def _encode_exact(value: object, expected_type: type[object]) -> bytes:
    if type(value) is not expected_type:
        raise TypeError(f"canonical codec requires an exact {expected_type.__name__}")
    return encode_mapping(cast(_CanonicalArtifact, value).to_mapping())


def _decode[T](body: bytes, schema: str, decoder: Callable[[object], T]) -> T:
    return decoder(admit_document(body, schema))

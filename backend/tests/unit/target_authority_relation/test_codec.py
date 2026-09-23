from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import pytest

from ci_coordinator.target_authority_relation import (
    TargetAuthorityRow,
    UnactivatedRelationClosure,
    close_target_authority_relation,
    decode_baseline,
    decode_expected_relation,
    decode_inventory,
    decode_projection_ledger,
    decode_raw_candidate_domain,
    decode_row,
    decode_transition_delta,
    decode_unactivated_closure,
    encode_baseline,
    encode_expected_relation,
    encode_inventory,
    encode_projection_ledger,
    encode_raw_candidate_domain,
    encode_row,
    encode_transition_delta,
    encode_unactivated_closure,
)

from .factories import relation_fixture


class RowSubclass(TargetAuthorityRow):
    pass


def _assert_round_trip[T](
    value: T,
    encoder: Callable[[T], bytes],
    decoder: Callable[[bytes], T],
) -> None:
    encoded = encoder(value)
    assert decoder(encoded) == value
    assert encoder(decoder(encoded)) == encoded


def test_every_public_artifact_codec_is_an_exact_round_trip() -> None:
    fixture = relation_fixture()
    closure = close_target_authority_relation(
        fixture.baseline,
        fixture.delta,
        fixture.registration,
        fixture.observation,
        fixture.raw_domain,
        fixture.projection_ledger,
    )
    assert isinstance(closure, UnactivatedRelationClosure)
    _assert_round_trip(fixture.baseline, encode_baseline, decode_baseline)
    _assert_round_trip(fixture.delta, encode_transition_delta, decode_transition_delta)
    _assert_round_trip(fixture.expected, encode_expected_relation, decode_expected_relation)
    _assert_round_trip(fixture.registration, encode_inventory, decode_inventory)
    _assert_round_trip(
        fixture.raw_domain,
        encode_raw_candidate_domain,
        decode_raw_candidate_domain,
    )
    _assert_round_trip(
        fixture.projection_ledger,
        encode_projection_ledger,
        decode_projection_ledger,
    )
    _assert_round_trip(closure, encode_unactivated_closure, decode_unactivated_closure)
    _assert_round_trip(fixture.expected.rows[0], encode_row, decode_row)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda body: b" " + body,
        lambda body: body + b"\n",
        lambda body: body.replace(b'"schemaVersion"', b'"unknown":null,"schemaVersion"', 1),
        lambda body: body.replace(b'"schemaVersion"', b'"schemaVersion":"bad","schemaVersion"', 1),
    ),
)
def test_decoder_rejects_noncanonical_unknown_and_duplicate_input(
    mutate: Callable[[bytes], bytes],
) -> None:
    body = encode_row(relation_fixture().expected.rows[0])

    with pytest.raises((TypeError, ValueError)):
        decode_row(mutate(body))


def test_unactivated_closure_codec_rejects_authority_escalation() -> None:
    fixture = relation_fixture()
    closure = close_target_authority_relation(
        fixture.baseline,
        fixture.delta,
        fixture.registration,
        fixture.observation,
        fixture.raw_domain,
        fixture.projection_ledger,
    )
    assert isinstance(closure, UnactivatedRelationClosure)
    body = encode_unactivated_closure(closure)
    escalated = body.replace(b'"unactivated"', b'"activated"', 1)

    with pytest.raises(ValueError, match="activation"):
        decode_unactivated_closure(escalated)


def test_exact_type_boundary_rejects_dataclass_subclasses() -> None:
    fixture = relation_fixture()

    subclass = RowSubclass(
        fixture.expected.rows[0].key,
        fixture.expected.rows[0].disposition,
        fixture.expected.rows[0].semantic_owner,
        fixture.expected.rows[0].source_locator,
        fixture.expected.rows[0].fields,
    )

    with pytest.raises(TypeError, match="exact TargetAuthorityRow"):
        encode_row(subclass)


def test_digest_changes_when_full_row_bytes_change() -> None:
    value = relation_fixture().expected.rows[0]
    mutant = replace(value, semantic_owner="different-owner")

    assert encode_row(value) != encode_row(mutant)
    assert value.row_digest != mutant.row_digest

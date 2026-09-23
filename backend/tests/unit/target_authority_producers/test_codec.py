from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest

import ci_coordinator.target_authority_producers.codec as producer_codec
from ci_coordinator.kernel import canonical_json
from ci_coordinator.kernel.strict_json import load_strict_json
from ci_coordinator.target_authority_producers import (
    TargetAuthorityProducerError,
    decode_observation_candidates,
    decode_owner_projection_policy,
    decode_registration_candidates,
    encode_observation_candidates,
    encode_owner_projection_policy,
    encode_registration_candidates,
)

from .factories import producer_fixture


def test_producer_artifact_codecs_round_trip_exact_values() -> None:
    fixture = producer_fixture()

    assert (
        decode_observation_candidates(encode_observation_candidates(fixture.candidates))
        == fixture.candidates
    )
    assert (
        decode_registration_candidates(
            encode_registration_candidates(fixture.registration_candidates)
        )
        == fixture.registration_candidates
    )
    assert (
        decode_owner_projection_policy(encode_owner_projection_policy(fixture.policy))
        == fixture.policy
    )


def test_producer_artifact_codecs_reject_duplicate_and_extra_keys() -> None:
    fixture = producer_fixture()
    cases: tuple[tuple[bytes, Callable[[bytes], object]], ...] = (
        (encode_observation_candidates(fixture.candidates), decode_observation_candidates),
        (
            encode_registration_candidates(fixture.registration_candidates),
            decode_registration_candidates,
        ),
        (encode_owner_projection_policy(fixture.policy), decode_owner_projection_policy),
    )

    for content, decoder in cases:
        duplicate = b'{"schemaVersion":"duplicate",' + content[1:]
        with pytest.raises(TargetAuthorityProducerError, match="admitted JSON"):
            decoder(duplicate)

        mapping = _mapping(content)
        mapping["extra"] = True
        with pytest.raises(TargetAuthorityProducerError, match="keys are not exact"):
            decoder(canonical_json(mapping))


def test_semantic_field_changes_producer_artifact_identity() -> None:
    fixture = producer_fixture()

    registration_mapping = _mapping(encode_registration_candidates(fixture.registration_candidates))
    registration_mapping["targetRegistryDigest"] = "f" * 64
    registration = decode_registration_candidates(canonical_json(registration_mapping))
    assert registration.candidate_set_digest != fixture.registration_candidates.candidate_set_digest

    observation_mapping = _mapping(encode_observation_candidates(fixture.candidates))
    observation_mapping["sourceBindingDigest"] = "f" * 64
    observation = decode_observation_candidates(canonical_json(observation_mapping))
    assert observation.candidate_set_digest != fixture.candidates.candidate_set_digest

    policy_mapping = _mapping(encode_owner_projection_policy(fixture.policy))
    policy_mapping["workflowManifestDigest"] = "f" * 64
    policy = decode_owner_projection_policy(canonical_json(policy_mapping))
    assert policy.policy_digest != fixture.policy.policy_digest


@pytest.mark.parametrize(
    ("encoder", "decoder", "fixture_attribute"),
    (
        (encode_observation_candidates, decode_observation_candidates, "candidates"),
        (
            encode_registration_candidates,
            decode_registration_candidates,
            "registration_candidates",
        ),
        (encode_owner_projection_policy, decode_owner_projection_policy, "policy"),
    ),
)
def test_producer_codec_byte_bound_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    encoder: Callable[[object], bytes],
    decoder: Callable[[bytes], object],
    fixture_attribute: str,
) -> None:
    value = getattr(producer_fixture(), fixture_attribute)
    content = encoder(value)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        producer_codec,
        "_MAX_CANDIDATE_DOCUMENT_BYTES",
        len(content) - 1,
    )

    with pytest.raises(TargetAuthorityProducerError, match="admitted JSON"):
        decoder(content)


def _mapping(content: bytes) -> dict[str, object]:
    value = load_strict_json(content, max_bytes=len(content))
    if type(value) is not dict:
        raise AssertionError("producer fixture codec did not emit an object")
    return cast(dict[str, object], value)

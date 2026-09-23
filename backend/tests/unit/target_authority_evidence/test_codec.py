from __future__ import annotations

from dataclasses import replace
from typing import cast

import pytest

import ci_coordinator.target_authority_evidence.codec as evidence_codec
import ci_coordinator.target_authority_evidence.model as evidence_model
from ci_coordinator.kernel import canonical_json
from ci_coordinator.kernel.canonical_json import JsonResourceLimits
from ci_coordinator.target_authority_evidence import (
    EVIDENCE_ROLES,
    EvidenceArtifact,
    TargetAuthorityEvidenceError,
    UnactivatedEvidenceBundle,
    decode_target_authority_evidence,
    encode_target_authority_evidence,
    replay_target_authority_evidence,
)
from ci_coordinator.target_authority_producers import encode_registration_candidates
from ci_coordinator.target_authority_relation import (
    EpochComponent,
    ProducerIdentity,
    encode_expected_relation,
    encode_inventory,
    encode_transition_delta,
    encode_unactivated_closure,
)
from ci_coordinator.workflow_authority import encode_source_binding

from .factories import EvidenceFixture


def test_evidence_copy_has_independent_nested_owners_and_projections(
    fixture: EvidenceFixture,
    evidence_seed: EvidenceFixture,
) -> None:
    assert fixture == evidence_seed
    assert fixture is not evidence_seed
    assert fixture.values is not evidence_seed.values
    assert (
        fixture.producer.target_artifacts.policy
        is not evidence_seed.producer.target_artifacts.policy
    )
    original = evidence_seed.admitted.bundle.to_mapping()
    projection = fixture.admitted.bundle.to_mapping()
    _artifact_mappings(projection)[0]["role"] = "mutated"
    assert evidence_seed.admitted.bundle.to_mapping() == original
    assert fixture.admitted.bundle.to_mapping() == original


def test_evidence_bundle_round_trips_exact_owner_values(fixture: EvidenceFixture) -> None:
    content = encode_target_authority_evidence(fixture.admitted)

    decoded = decode_target_authority_evidence(content)

    assert decoded == fixture.admitted
    assert tuple(artifact.role for artifact in decoded.bundle.artifacts) == EVIDENCE_ROLES
    assert decoded.bundle.to_mapping()["authorityState"] == "unactivated"


@pytest.mark.parametrize(
    "mutation",
    (
        "missing",
        "duplicate",
        "extra",
        "swapped",
        "relabeled",
        "schema_crossed",
        "wrong_sha",
        "wrong_count",
        "wrong_digest",
    ),
)
def test_bundle_structure_mutations_are_rejected(fixture: EvidenceFixture, mutation: str) -> None:
    mapping = fixture.admitted.bundle.to_mapping()
    artifacts = _artifact_mappings(mapping)
    if mutation == "missing":
        del artifacts[-1]
    elif mutation == "duplicate":
        artifacts[-1] = artifacts[-2]
    elif mutation == "extra":
        artifacts.append({**artifacts[-1], "role": "unexpected"})
    elif mutation == "swapped":
        artifacts[0], artifacts[1] = artifacts[1], artifacts[0]
    elif mutation == "relabeled":
        artifacts[0]["role"] = "transition_delta"
    elif mutation == "schema_crossed":
        artifacts[0]["ownerSchema"] = artifacts[1]["ownerSchema"]
    elif mutation == "wrong_sha":
        artifacts[0]["sha256"] = "f" * 64
    elif mutation == "wrong_count":
        artifacts[0]["byteCount"] = cast(int, artifacts[0]["byteCount"]) + 1
    else:
        artifacts[0]["artifactDigest"] = "f" * 64

    with pytest.raises(TargetAuthorityEvidenceError):
        decode_target_authority_evidence(canonical_json(mapping))


def test_duplicate_json_key_and_activation_claim_are_rejected(fixture: EvidenceFixture) -> None:
    content = encode_target_authority_evidence(fixture.admitted)
    duplicate = b'{"authorityState":"unactivated",' + content[1:]
    with pytest.raises(TargetAuthorityEvidenceError) as duplicate_error:
        decode_target_authority_evidence(duplicate)
    assert duplicate_error.value.code == "bundle_json_rejected"

    mapping = fixture.admitted.bundle.to_mapping()
    mapping["authorityState"] = "active"
    with pytest.raises(TargetAuthorityEvidenceError) as activation_error:
        decode_target_authority_evidence(canonical_json(mapping))
    assert activation_error.value.code == "authority_state_rejected"


def test_noncanonical_bundle_bytes_are_rejected(fixture: EvidenceFixture) -> None:

    with pytest.raises(TargetAuthorityEvidenceError) as raised:
        decode_target_authority_evidence(encode_target_authority_evidence(fixture.admitted) + b"\n")

    assert raised.value.code == "bundle_not_canonical"


@pytest.mark.parametrize(
    "limits",
    (
        JsonResourceLimits(max_depth=1, max_nodes=8_388_608),
        JsonResourceLimits(max_depth=24, max_nodes=1),
    ),
)
def test_bundle_structural_bounds_are_fail_closed(
    fixture: EvidenceFixture,
    monkeypatch: pytest.MonkeyPatch,
    limits: JsonResourceLimits,
) -> None:
    content = encode_target_authority_evidence(fixture.admitted)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(evidence_codec, "EVIDENCE_JSON_LIMITS", limits)

    with pytest.raises(TargetAuthorityEvidenceError) as raised:
        decode_target_authority_evidence(content)

    assert raised.value.code == "bundle_json_rejected"


def test_bundle_byte_and_aggregate_bounds_are_fail_closed(
    fixture: EvidenceFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    content = encode_target_authority_evidence(fixture.admitted)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(evidence_codec, "MAX_EVIDENCE_BUNDLE_BYTES", len(content) - 1)
    with pytest.raises(TargetAuthorityEvidenceError) as bundle_error:
        decode_target_authority_evidence(content)
    assert bundle_error.value.code == "bundle_json_rejected"

    aggregate = sum(len(artifact.content) for artifact in fixture.admitted.bundle.artifacts)
    monkeypatch.setattr(
        evidence_model,
        "MAX_AGGREGATE_ARTIFACT_BYTES",
        aggregate - 1,
    )
    with pytest.raises(ValueError, match="aggregate bound"):
        UnactivatedEvidenceBundle(
            fixture.admitted.bundle.subject,
            fixture.admitted.bundle.epoch,
            fixture.admitted.bundle.artifacts,
        )


def test_source_binding_drift_is_rejected_after_exact_rebinding(fixture: EvidenceFixture) -> None:
    changed = replace(fixture.values.source_binding, manifest_digest="f" * 64)
    bundle = _replace_artifact(
        fixture.admitted.bundle,
        "source_binding",
        encode_source_binding(changed),
    )

    with pytest.raises(TargetAuthorityEvidenceError) as raised:
        replay_target_authority_evidence(bundle)

    assert raised.value.code == "source_or_epoch_mismatch"


def test_same_subject_with_different_epoch_is_rejected(fixture: EvidenceFixture) -> None:
    changed_epoch = replace(
        fixture.values.expected_relation.epoch,
        owner=EpochComponent.present("f" * 64),
    )
    changed = replace(fixture.values.expected_relation, epoch=changed_epoch)
    bundle = _replace_artifact(
        fixture.admitted.bundle,
        "expected_relation",
        encode_expected_relation(changed),
    )

    with pytest.raises(TargetAuthorityEvidenceError) as raised:
        replay_target_authority_evidence(bundle)

    assert raised.value.code == "bundle_identity_mismatch"


def test_same_relation_keys_with_different_full_row_bytes_are_rejected(
    fixture: EvidenceFixture,
) -> None:
    first, *remaining = fixture.values.registration_inventory.rows
    changed = replace(
        fixture.values.registration_inventory,
        rows=(replace(first, semantic_owner="different-owner"), *remaining),
    )
    bundle = _replace_artifact(
        fixture.admitted.bundle,
        "registration_inventory",
        encode_inventory(changed),
    )

    with pytest.raises(TargetAuthorityEvidenceError) as raised:
        replay_target_authority_evidence(bundle)

    assert raised.value.code == "producer_replay_mismatch"


def test_retained_candidate_domain_drift_is_rejected(fixture: EvidenceFixture) -> None:
    changed = replace(
        fixture.values.registration_candidates,
        target_registry_digest="f" * 64,
    )
    bundle = _replace_artifact(
        fixture.admitted.bundle,
        "registration_candidates",
        encode_registration_candidates(changed),
    )

    with pytest.raises(TargetAuthorityEvidenceError) as raised:
        replay_target_authority_evidence(bundle)

    assert raised.value.code == "source_or_epoch_mismatch"


def test_transition_drift_is_rejected_after_exact_rebinding(fixture: EvidenceFixture) -> None:
    first, *remaining = fixture.values.transition_delta.dispositions
    changed = replace(
        fixture.values.transition_delta,
        dispositions=(replace(first, reason="changed owner disposition"), *remaining),
    )
    bundle = _replace_artifact(
        fixture.admitted.bundle,
        "transition_delta",
        encode_transition_delta(changed),
    )

    with pytest.raises(TargetAuthorityEvidenceError) as raised:
        replay_target_authority_evidence(bundle)

    assert raised.value.code == "transition_replay_mismatch"


def test_registration_producer_drift_is_rejected_after_exact_rebinding(
    fixture: EvidenceFixture,
) -> None:
    changed = replace(
        fixture.values.registration_inventory,
        producer=ProducerIdentity("different-registration-producer", "1"),
    )
    bundle = _replace_artifact(
        fixture.admitted.bundle,
        "registration_inventory",
        encode_inventory(changed),
    )

    with pytest.raises(TargetAuthorityEvidenceError) as raised:
        replay_target_authority_evidence(bundle)

    assert raised.value.code == "producer_replay_mismatch"


def test_relation_closure_drift_is_rejected_after_exact_rebinding(fixture: EvidenceFixture) -> None:
    changed = replace(
        fixture.values.relation_closure,
        row_count=fixture.values.relation_closure.row_count + 1,
    )
    bundle = _replace_artifact(
        fixture.admitted.bundle,
        "relation_closure",
        encode_unactivated_closure(changed),
    )

    with pytest.raises(TargetAuthorityEvidenceError) as raised:
        replay_target_authority_evidence(bundle)

    assert raised.value.code == "relation_replay_mismatch"


def _replace_artifact(
    bundle: UnactivatedEvidenceBundle,
    role: str,
    content: bytes,
) -> UnactivatedEvidenceBundle:
    artifacts = tuple(
        (
            EvidenceArtifact(artifact.role, artifact.owner_schema, content)
            if artifact.role == role
            else artifact
        )
        for artifact in bundle.artifacts
    )
    return UnactivatedEvidenceBundle(bundle.subject, bundle.epoch, artifacts)


def _artifact_mappings(mapping: dict[str, object]) -> list[dict[str, object]]:
    value = mapping["artifacts"]
    if type(value) is not list or any(type(item) is not dict for item in value):
        raise AssertionError("evidence fixture artifacts are not objects")
    return cast(list[dict[str, object]], value)

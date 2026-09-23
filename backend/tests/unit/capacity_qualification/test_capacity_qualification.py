from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from ci_coordinator.capacity_qualification import (
    CAPACITY_QUALIFICATION_PROFILE,
    CapacityMeasurement,
    CapacityNotQualified,
    CapacityQualified,
    CapacityReceipt,
    admit_capacity_receipt,
    capacity_signature_payload,
    encode_capacity_envelope,
)
from ci_coordinator.kernel import FixedClock, sha256_hex

from ._support import (
    CAPACITY_KEY_ID,
    CAPACITY_PUBLIC_KEY_PEM,
    NOW,
    capacity_expectation,
    make_capacity_identity,
    make_capacity_measurements,
    make_capacity_receipt,
    sign_capacity_receipt,
)


def test_signed_complete_capacity_receipt_is_qualified_for_one_exact_epoch() -> None:
    receipt = make_capacity_receipt()
    content = sign_capacity_receipt(receipt)

    result = admit_capacity_receipt(
        content,
        public_key_pem=CAPACITY_PUBLIC_KEY_PEM,
        expected_key_id=CAPACITY_KEY_ID,
        expectation=capacity_expectation(receipt),
        clock=FixedClock(NOW),
    )

    assert isinstance(result, CapacityQualified)
    assert result.qualified is True
    assert result.receipt_digest == sha256_hex(content)
    assert result.identity == receipt.identity
    assert result.covers("audit-live-data-bytes", 0)


def test_missing_capacity_evidence_is_explicitly_not_qualified() -> None:
    receipt = make_capacity_receipt()

    result = admit_capacity_receipt(
        None,
        public_key_pem=CAPACITY_PUBLIC_KEY_PEM,
        expected_key_id=CAPACITY_KEY_ID,
        expectation=capacity_expectation(receipt),
        clock=FixedClock(NOW),
    )

    assert result == CapacityNotQualified("capacity_evidence_missing")


@pytest.mark.parametrize(
    ("content_factory", "expected_code"),
    (
        (
            lambda receipt: sign_capacity_receipt(
                receipt,
                private_key=Ed25519PrivateKey.from_private_bytes(bytes(range(33, 65))),
            ),
            "capacity_signature_invalid",
        ),
        (
            lambda receipt: sign_capacity_receipt(receipt) + b" ",
            "capacity_evidence_invalid",
        ),
    ),
)
def test_capacity_admission_rejects_unsigned_or_noncanonical_evidence(
    content_factory: Callable[[CapacityReceipt], bytes],
    expected_code: str,
) -> None:
    receipt = make_capacity_receipt()
    content = content_factory(receipt)

    result = admit_capacity_receipt(
        content,
        public_key_pem=CAPACITY_PUBLIC_KEY_PEM,
        expected_key_id=CAPACITY_KEY_ID,
        expectation=capacity_expectation(receipt),
        clock=FixedClock(NOW),
    )

    assert isinstance(result, CapacityNotQualified)
    assert result.code == expected_code


@pytest.mark.parametrize("public_key_pem", (b"", b"not a PEM key"))
def test_capacity_admission_rejects_invalid_public_key_configuration(
    public_key_pem: bytes,
) -> None:
    receipt = make_capacity_receipt()

    result = admit_capacity_receipt(
        sign_capacity_receipt(receipt),
        public_key_pem=public_key_pem,
        expected_key_id=CAPACITY_KEY_ID,
        expectation=capacity_expectation(receipt),
        clock=FixedClock(NOW),
    )

    assert result == CapacityNotQualified("capacity_signature_invalid")


def test_capacity_admission_rejects_an_unexpected_signing_key_id() -> None:
    receipt = make_capacity_receipt()

    result = admit_capacity_receipt(
        sign_capacity_receipt(receipt),
        public_key_pem=CAPACITY_PUBLIC_KEY_PEM,
        expected_key_id="rotated-capacity-owner",
        expectation=capacity_expectation(receipt),
        clock=FixedClock(NOW),
    )

    assert result == CapacityNotQualified("capacity_key_mismatch")


def test_capacity_receipt_for_another_environment_is_foreign() -> None:
    receipt = make_capacity_receipt()
    expectation = replace(
        capacity_expectation(receipt),
        identity=make_capacity_identity(environment_id="foreign-environment"),
    )

    result = admit_capacity_receipt(
        sign_capacity_receipt(receipt),
        public_key_pem=CAPACITY_PUBLIC_KEY_PEM,
        expected_key_id=CAPACITY_KEY_ID,
        expectation=expectation,
        clock=FixedClock(NOW),
    )

    assert result == CapacityNotQualified("capacity_evidence_foreign")


@pytest.mark.parametrize(
    ("length", "accepted"),
    [(39, False), (40, True), (41, False), (63, False), (64, True), (65, False)],
)
def test_capacity_source_commit_admits_only_exact_git_object_lengths(
    length: int,
    accepted: bool,
) -> None:
    if accepted:
        assert make_capacity_identity(source_commit="d" * length).source_commit == "d" * length
        return
    with pytest.raises(ValueError, match="source commit"):
        make_capacity_identity(source_commit="d" * length)


def test_capacity_receipt_requires_the_complete_metric_domain() -> None:
    measurements = make_capacity_measurements()[:-1]
    receipt = make_capacity_receipt(measurements=measurements)

    result = admit_capacity_receipt(
        sign_capacity_receipt(receipt),
        public_key_pem=CAPACITY_PUBLIC_KEY_PEM,
        expected_key_id=CAPACITY_KEY_ID,
        expectation=capacity_expectation(receipt),
        clock=FixedClock(NOW),
    )

    assert result == CapacityNotQualified("capacity_evidence_incomplete")


def test_capacity_metric_identity_enforces_its_byte_bound() -> None:
    with pytest.raises(ValueError, match="metric id"):
        CapacityMeasurement(
            metric_id="x" * 513,
            budget_value=1,
            observed_value=1,
            sample_count=1,
        )


def test_capacity_encoder_enforces_the_profile_byte_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = make_capacity_receipt()

    def oversized_mapping(_: CapacityReceipt) -> dict[str, object]:
        return {"padding": "x" * CAPACITY_QUALIFICATION_PROFILE.limits.maximum_envelope_bytes}

    monkeypatch.setattr(CapacityReceipt, "to_mapping", oversized_mapping)
    with pytest.raises(ValueError, match="maximum canonical JSON byte count"):
        encode_capacity_envelope(
            receipt,
            key_id=CAPACITY_KEY_ID,
            signature="A" * 86,
        )
    with pytest.raises(ValueError, match="maximum canonical JSON byte count"):
        capacity_signature_payload(receipt, key_id=CAPACITY_KEY_ID)


def test_capacity_receipt_enforces_its_measurement_count_bound() -> None:
    oversized_domain = tuple(
        CapacityMeasurement(
            metric_id=f"{index:03d}-" + "x" * 508,
            budget_value=1,
            observed_value=1,
            sample_count=1,
        )
        for index in range(256)
    )
    receipt = make_capacity_receipt(measurements=oversized_domain)
    with pytest.raises(ValueError, match="measurement count"):
        replace(receipt, measurements=(*oversized_domain, oversized_domain[-1]))


def test_capacity_receipt_derives_budget_failure_instead_of_trusting_a_status() -> None:
    measurements = make_capacity_measurements(
        overrides={"readiness-latency-p999-microseconds": (100, 101, 10)}
    )
    receipt = make_capacity_receipt(measurements=measurements)

    result = admit_capacity_receipt(
        sign_capacity_receipt(receipt),
        public_key_pem=CAPACITY_PUBLIC_KEY_PEM,
        expected_key_id=CAPACITY_KEY_ID,
        expectation=capacity_expectation(receipt),
        clock=FixedClock(NOW),
    )

    assert result == CapacityNotQualified("capacity_budget_exceeded")


def test_capacity_signer_cannot_weaken_profile_owned_zero_violation_budget() -> None:
    measurements = make_capacity_measurements(
        overrides={"safety-invariant-violation-count": (1, 0, 10)}
    )
    receipt = make_capacity_receipt(measurements=measurements)

    result = admit_capacity_receipt(
        sign_capacity_receipt(receipt),
        public_key_pem=CAPACITY_PUBLIC_KEY_PEM,
        expected_key_id=CAPACITY_KEY_ID,
        expectation=capacity_expectation(receipt),
        clock=FixedClock(NOW),
    )

    assert result == CapacityNotQualified("capacity_evidence_invalid")


def test_capacity_receipt_validity_is_checked_against_trusted_time() -> None:
    issued_at = NOW - timedelta(hours=2)
    receipt = make_capacity_receipt(
        issued_at=issued_at,
        valid_from=issued_at - timedelta(minutes=1),
        valid_until=NOW - timedelta(seconds=1),
    )

    result = admit_capacity_receipt(
        sign_capacity_receipt(receipt),
        public_key_pem=CAPACITY_PUBLIC_KEY_PEM,
        expected_key_id=CAPACITY_KEY_ID,
        expectation=capacity_expectation(receipt),
        clock=FixedClock(NOW),
    )

    assert result == CapacityNotQualified("capacity_evidence_expired")


def test_capacity_receipt_is_not_admitted_before_its_validity_interval() -> None:
    future = NOW + timedelta(minutes=1)
    receipt = make_capacity_receipt(
        issued_at=future,
        valid_from=future,
        valid_until=NOW + timedelta(hours=1),
    )

    result = admit_capacity_receipt(
        sign_capacity_receipt(receipt),
        public_key_pem=CAPACITY_PUBLIC_KEY_PEM,
        expected_key_id=CAPACITY_KEY_ID,
        expectation=capacity_expectation(receipt),
        clock=FixedClock(NOW),
    )

    assert result == CapacityNotQualified("capacity_evidence_not_yet_valid")


def test_capacity_receipt_rejects_stale_measurements_despite_current_validity() -> None:
    receipt = make_capacity_receipt(observed_at=NOW - timedelta(days=31))

    result = admit_capacity_receipt(
        sign_capacity_receipt(receipt),
        public_key_pem=CAPACITY_PUBLIC_KEY_PEM,
        expected_key_id=CAPACITY_KEY_ID,
        expectation=capacity_expectation(receipt),
        clock=FixedClock(NOW),
    )

    assert result == CapacityNotQualified("capacity_evidence_invalid")


def test_capacity_qualified_reuses_the_admitted_issuance_skew_boundary() -> None:
    skew = timedelta(seconds=CAPACITY_QUALIFICATION_PROFILE.limits.maximum_clock_skew_seconds)
    receipt = make_capacity_receipt(
        observed_at=NOW - timedelta(minutes=5),
        issued_at=NOW + skew,
        valid_from=NOW - timedelta(hours=1),
        valid_until=NOW + timedelta(hours=1),
    )

    result = admit_capacity_receipt(
        sign_capacity_receipt(receipt),
        public_key_pem=CAPACITY_PUBLIC_KEY_PEM,
        expected_key_id=CAPACITY_KEY_ID,
        expectation=capacity_expectation(receipt),
        clock=FixedClock(NOW),
    )

    assert isinstance(result, CapacityQualified)
    assert result.is_valid_at(NOW)
    assert not result.is_valid_at(NOW - timedelta(microseconds=1))


def test_capacity_qualified_expires_with_the_measurement_age_boundary() -> None:
    maximum_age = timedelta(
        seconds=CAPACITY_QUALIFICATION_PROFILE.limits.maximum_evidence_age_seconds
    )
    receipt = make_capacity_receipt(
        observed_at=NOW - maximum_age,
        valid_until=NOW + timedelta(hours=1),
    )

    result = admit_capacity_receipt(
        sign_capacity_receipt(receipt),
        public_key_pem=CAPACITY_PUBLIC_KEY_PEM,
        expected_key_id=CAPACITY_KEY_ID,
        expectation=capacity_expectation(receipt),
        clock=FixedClock(NOW),
    )

    assert isinstance(result, CapacityQualified)
    assert result.is_valid_at(NOW)
    assert not result.is_valid_at(NOW + timedelta(microseconds=1))


def test_capacity_qualified_cannot_be_constructed_by_a_caller() -> None:
    with pytest.raises(TypeError, match="cannot be constructed directly"):
        CapacityQualified(
            object(),
            receipt_digest="a" * 64,
            key_id=CAPACITY_KEY_ID,
            identity=make_capacity_identity(),
            valid_from=NOW,
            valid_until=NOW + timedelta(hours=1),
            evidence_valid_through=NOW + timedelta(hours=1),
            measurements=make_capacity_measurements(),
        )

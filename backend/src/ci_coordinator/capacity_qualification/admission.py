from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, NoReturn

from ci_coordinator.capacity_qualification.codec import (
    capacity_signature_payload,
    decode_capacity_envelope,
)
from ci_coordinator.capacity_qualification.model import (
    CapacityEvidenceIdentity,
    CapacityExpectation,
    CapacityMeasurement,
)
from ci_coordinator.capacity_qualification.profile import (
    CAPACITY_QUALIFICATION_PROFILE,
    CAPACITY_QUALIFICATION_PROFILE_DIGEST,
)
from ci_coordinator.kernel import (
    Clock,
    admit_ed25519_public_key,
    sha256_hex,
    verify_canonical_ed25519_signature,
)

type CapacityNotQualifiedCode = Literal[
    "capacity_budget_exceeded",
    "capacity_evidence_expired",
    "capacity_evidence_foreign",
    "capacity_evidence_incomplete",
    "capacity_evidence_invalid",
    "capacity_evidence_missing",
    "capacity_evidence_not_yet_valid",
    "capacity_key_mismatch",
    "capacity_signature_invalid",
]


@dataclass(frozen=True, slots=True)
class CapacityNotQualified:
    code: CapacityNotQualifiedCode
    qualified: Literal[False] = False


_QUALIFIED_TOKEN = object()


class CapacityQualified:
    __budgets: tuple[tuple[str, int], ...]
    __evidence_valid_through: datetime
    __identity: CapacityEvidenceIdentity
    __key_id: str
    __observations: tuple[tuple[str, int], ...]
    __receipt_digest: str
    __valid_from: datetime
    __valid_until: datetime

    __slots__ = (
        "__budgets",
        "__evidence_valid_through",
        "__identity",
        "__key_id",
        "__observations",
        "__receipt_digest",
        "__valid_from",
        "__valid_until",
    )

    def __init__(
        self,
        token: object,
        *,
        receipt_digest: str,
        key_id: str,
        identity: CapacityEvidenceIdentity,
        valid_from: datetime,
        valid_until: datetime,
        evidence_valid_through: datetime,
        measurements: tuple[CapacityMeasurement, ...],
    ) -> None:
        if token is not _QUALIFIED_TOKEN:
            raise TypeError("CapacityQualified cannot be constructed directly")
        object.__setattr__(self, "_CapacityQualified__receipt_digest", receipt_digest)
        object.__setattr__(self, "_CapacityQualified__key_id", key_id)
        object.__setattr__(self, "_CapacityQualified__identity", identity)
        object.__setattr__(self, "_CapacityQualified__valid_from", valid_from)
        object.__setattr__(self, "_CapacityQualified__valid_until", valid_until)
        object.__setattr__(
            self,
            "_CapacityQualified__evidence_valid_through",
            evidence_valid_through,
        )
        object.__setattr__(
            self,
            "_CapacityQualified__budgets",
            tuple((item.metric_id, item.budget_value) for item in measurements),
        )
        object.__setattr__(
            self,
            "_CapacityQualified__observations",
            tuple((item.metric_id, item.observed_value) for item in measurements),
        )

    def __setattr__(self, name: str, value: object) -> NoReturn:
        del name, value
        raise TypeError("CapacityQualified is immutable")

    def __reduce__(self) -> NoReturn:
        raise TypeError("CapacityQualified cannot be serialized")

    @property
    def qualified(self) -> Literal[True]:
        return True

    @property
    def receipt_digest(self) -> str:
        return self.__receipt_digest

    @property
    def key_id(self) -> str:
        return self.__key_id

    @property
    def identity(self) -> CapacityEvidenceIdentity:
        return self.__identity

    def is_valid_at(self, value: datetime) -> bool:
        if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
            return False
        canonical = value.astimezone(UTC)
        return (
            self.__valid_from <= canonical < self.__valid_until
            and canonical <= self.__evidence_valid_through
        )

    def covers(self, metric_id: str, value: int) -> bool:
        observed = _metric_value(self.__observations, metric_id)
        budget = _metric_value(self.__budgets, metric_id)
        return (
            observed is not None
            and budget is not None
            and type(value) is int
            and 0 <= value <= observed <= budget
        )


def _metric_value(values: tuple[tuple[str, int], ...], metric_id: str) -> int | None:
    return next((value for candidate, value in values if candidate == metric_id), None)


type CapacityQualificationResult = CapacityQualified | CapacityNotQualified


def admit_capacity_receipt(
    content: bytes | None,
    *,
    public_key_pem: bytes,
    expected_key_id: str,
    expectation: CapacityExpectation,
    clock: Clock,
) -> CapacityQualificationResult:
    if content is None:
        return CapacityNotQualified("capacity_evidence_missing")
    if type(expectation) is not CapacityExpectation:
        return CapacityNotQualified("capacity_evidence_invalid")
    try:
        envelope = decode_capacity_envelope(content)
    except (TypeError, ValueError):
        return CapacityNotQualified("capacity_evidence_invalid")
    if envelope.key_id != expected_key_id:
        return CapacityNotQualified("capacity_key_mismatch")
    public_key = admit_ed25519_public_key(public_key_pem)
    if public_key is None or not verify_canonical_ed25519_signature(
        public_key,
        signature=envelope.signature,
        payload=capacity_signature_payload(envelope.receipt, key_id=envelope.key_id),
    ):
        return CapacityNotQualified("capacity_signature_invalid")

    receipt = envelope.receipt
    now = clock.now()
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        return CapacityNotQualified("capacity_evidence_invalid")
    now = now.astimezone(UTC)
    limits = CAPACITY_QUALIFICATION_PROFILE.limits
    skew = timedelta(seconds=limits.maximum_clock_skew_seconds)
    if receipt.issued_at > now + skew or now < receipt.valid_from:
        return CapacityNotQualified("capacity_evidence_not_yet_valid")
    if now >= receipt.valid_until:
        return CapacityNotQualified("capacity_evidence_expired")
    if receipt.valid_until - receipt.valid_from > timedelta(
        seconds=limits.maximum_receipt_lifetime_seconds
    ) or now - receipt.observed_at > timedelta(seconds=limits.maximum_evidence_age_seconds):
        return CapacityNotQualified("capacity_evidence_invalid")
    if (
        receipt.capacity_profile_digest != CAPACITY_QUALIFICATION_PROFILE_DIGEST
        or receipt.identity != expectation.identity
        or receipt.budget_set_digest != expectation.budget_set_digest
    ):
        return CapacityNotQualified("capacity_evidence_foreign")
    if tuple(item.metric_id for item in receipt.measurements) != (
        CAPACITY_QUALIFICATION_PROFILE.metric_ids
    ):
        return CapacityNotQualified("capacity_evidence_incomplete")
    measurement_by_id = {item.metric_id: item for item in receipt.measurements}
    if any(
        measurement_by_id[metric_id].budget_value != required_budget
        for metric_id, required_budget in CAPACITY_QUALIFICATION_PROFILE.fixed_budgets
    ):
        return CapacityNotQualified("capacity_evidence_invalid")
    if any(item.observed_value > item.budget_value for item in receipt.measurements):
        return CapacityNotQualified("capacity_budget_exceeded")
    return CapacityQualified(
        _QUALIFIED_TOKEN,
        receipt_digest=sha256_hex(content),
        key_id=envelope.key_id,
        identity=receipt.identity,
        valid_from=max(receipt.valid_from, receipt.issued_at - skew),
        valid_until=receipt.valid_until,
        evidence_valid_through=receipt.observed_at
        + timedelta(seconds=limits.maximum_evidence_age_seconds),
        measurements=receipt.measurements,
    )

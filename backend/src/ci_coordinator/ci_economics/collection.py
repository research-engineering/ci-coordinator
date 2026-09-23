"""Pure durable collection lifecycle and lease authority."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

from ci_coordinator.ci_economics.sources import (
    CollectionSource,
    ProviderRunCollectionSource,
    ReconciliationCollectionSource,
)
from ci_coordinator.kernel import hash_object, is_safe_json_integer

MAX_COLLECTION_ATTEMPTS: Final = 100
MAX_COLLECTION_BACKOFF_SECONDS: Final = 86_400
MAX_COLLECTION_LEASE_SECONDS: Final = 3_600
MAX_COLLECTION_WINDOW_SECONDS: Final = 2_592_000
MAX_EVIDENCE_DAYS: Final = 3_650
MAX_TOMBSTONE_GRACE_SECONDS: Final = 2_592_000

type CollectionStatus = Literal[
    "pending",
    "leased",
    "deferred",
    "captured",
    "terminal_unavailable",
    "expired",
]
type ProviderCollectionFailureReason = Literal[
    "provider_unavailable",
    "provider_binding_mismatch",
    "provider_malformed",
    "provider_incomplete",
    "provider_not_terminal",
    "provider_unstable",
]
type RetryableCollectionFailureReason = (
    ProviderCollectionFailureReason | Literal["unexpected_error"]
)
type CollectionFailureReason = RetryableCollectionFailureReason | Literal["evidence_conflict"]
type CollectionTerminalReason = Literal[
    "deadline_exceeded",
    "attempts_exhausted",
    "evidence_conflict",
]
type CollectionFinalOutcome = Literal["captured", "terminal_unavailable"]


@dataclass(frozen=True, slots=True)
class CollectionPolicy:
    collection_window_seconds: int
    initial_backoff_seconds: int
    maximum_backoff_seconds: int
    maximum_attempts: int
    lease_seconds: int
    evidence_days: int
    tombstone_grace_seconds: int

    def __post_init__(self) -> None:
        _positive_bounded(
            self.collection_window_seconds,
            MAX_COLLECTION_WINDOW_SECONDS,
            "collection window",
        )
        _positive_bounded(
            self.initial_backoff_seconds,
            MAX_COLLECTION_BACKOFF_SECONDS,
            "initial collection backoff",
        )
        _positive_bounded(
            self.maximum_backoff_seconds,
            MAX_COLLECTION_BACKOFF_SECONDS,
            "maximum collection backoff",
        )
        _positive_bounded(
            self.maximum_attempts,
            MAX_COLLECTION_ATTEMPTS,
            "maximum collection attempts",
        )
        _positive_bounded(
            self.lease_seconds,
            MAX_COLLECTION_LEASE_SECONDS,
            "collection lease",
        )
        _positive_bounded(self.evidence_days, MAX_EVIDENCE_DAYS, "evidence days")
        _positive_bounded(
            self.tombstone_grace_seconds,
            MAX_TOMBSTONE_GRACE_SECONDS,
            "tombstone grace",
        )
        if self.initial_backoff_seconds > self.maximum_backoff_seconds:
            raise ValueError("initial collection backoff cannot exceed its maximum")
        if self.maximum_backoff_seconds >= self.collection_window_seconds:
            raise ValueError("maximum collection backoff must be shorter than the window")
        if self.lease_seconds >= self.collection_window_seconds:
            raise ValueError("collection lease must be shorter than the window")
        if timedelta(days=self.evidence_days) <= timedelta(seconds=self.collection_window_seconds):
            raise ValueError("evidence retention must outlive the collection window")

    @property
    def policy_hash(self) -> str:
        return hash_object(
            {
                "schemaVersion": "ci-economics-collection-policy/v1",
                "collectionWindowSeconds": self.collection_window_seconds,
                "initialBackoffSeconds": self.initial_backoff_seconds,
                "maximumBackoffSeconds": self.maximum_backoff_seconds,
                "maximumAttempts": self.maximum_attempts,
                "leaseSeconds": self.lease_seconds,
                "evidenceDays": self.evidence_days,
                "tombstoneGraceSeconds": self.tombstone_grace_seconds,
            }
        )


@dataclass(frozen=True, slots=True)
class CollectionState:
    subject_id: str
    policy_hash: str
    source_created_at: datetime
    deadline_at: datetime
    evidence_retain_until: datetime
    tombstone_retain_until: datetime
    status: CollectionStatus
    revision: int
    attempt_count: int
    max_attempts: int
    backoff_seconds: int
    max_backoff_seconds: int
    next_attempt_at: datetime | None
    claim_generation: int
    lease_owner_id: str | None
    lease_token: str | None
    lease_acquired_at: datetime | None
    lease_expires_at: datetime | None
    last_failure_reason: CollectionFailureReason | None
    final_outcome: CollectionFinalOutcome | None
    terminal_reason: CollectionTerminalReason | None
    completed_at: datetime | None
    expired_at: datetime | None

    def __post_init__(self) -> None:
        _require_digest(self.subject_id, "collection subject id")
        _require_digest(self.policy_hash, "collection policy hash")
        for name in (
            "source_created_at",
            "deadline_at",
            "evidence_retain_until",
            "tombstone_retain_until",
            "next_attempt_at",
            "lease_acquired_at",
            "lease_expires_at",
            "completed_at",
            "expired_at",
        ):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _utc(value, name))
        if not (
            self.source_created_at
            < self.deadline_at
            < self.evidence_retain_until
            < self.tombstone_retain_until
        ):
            raise ValueError("collection lifecycle times are not strictly ordered")
        for value, name in (
            (self.revision, "collection revision"),
            (self.attempt_count, "collection attempt count"),
            (self.claim_generation, "collection claim generation"),
        ):
            _non_negative_safe(value, name)
        _positive_bounded(self.max_attempts, MAX_COLLECTION_ATTEMPTS, "maximum attempts")
        _positive_bounded(
            self.backoff_seconds,
            MAX_COLLECTION_BACKOFF_SECONDS,
            "collection backoff",
        )
        _positive_bounded(
            self.max_backoff_seconds,
            MAX_COLLECTION_BACKOFF_SECONDS,
            "maximum collection backoff",
        )
        if self.attempt_count > self.max_attempts:
            raise ValueError("collection attempt count exceeds its bound")
        if self.backoff_seconds > self.max_backoff_seconds:
            raise ValueError("collection backoff exceeds its bound")
        if self.claim_generation != self.attempt_count:
            raise ValueError("collection claim generation must equal attempt count")
        if self.revision < self.claim_generation:
            raise ValueError("collection revision cannot precede its claim generation")
        _require_optional_reason(self.last_failure_reason, _FAILURE_REASONS, "failure")
        _require_optional_reason(self.terminal_reason, _TERMINAL_REASONS, "terminal")
        if self.final_outcome not in {None, "captured", "terminal_unavailable"}:
            raise ValueError("collection final outcome is invalid")
        self._validate_state_shape()

    def _validate_state_shape(self) -> None:
        conflict_failure = self.last_failure_reason == "evidence_conflict"
        conflict_terminal = self.terminal_reason == "evidence_conflict"
        if (
            conflict_failure != conflict_terminal
            or (
                self.terminal_reason == "attempts_exhausted"
                and self.attempt_count != self.max_attempts
            )
            or (self.final_outcome == "captured" and self.attempt_count == 0)
            or (conflict_terminal and self.attempt_count == 0)
        ):
            raise ValueError("collection state fields do not match its causal history")
        lease = (
            self.lease_owner_id,
            self.lease_token,
            self.lease_acquired_at,
            self.lease_expires_at,
        )
        has_lease = all(value is not None for value in lease)
        has_partial_lease = any(value is not None for value in lease) and not has_lease
        if has_partial_lease:
            raise ValueError("collection lease fields must be all present or all absent")
        if has_lease:
            lease_owner_id = self.lease_owner_id
            lease_token = self.lease_token
            lease_acquired_at = self.lease_acquired_at
            lease_expires_at = self.lease_expires_at
            if (
                lease_owner_id is None
                or lease_token is None
                or lease_acquired_at is None
                or lease_expires_at is None
            ):
                raise AssertionError("complete collection lease was not narrowed")
            _require_digest(lease_owner_id, "collection lease owner")
            _require_digest(lease_token, "collection lease token")
            if not (
                self.source_created_at <= lease_acquired_at < lease_expires_at < self.deadline_at
            ):
                raise ValueError("collection lease interval is invalid")
        if self.next_attempt_at is not None and not (
            self.source_created_at <= self.next_attempt_at < self.deadline_at
        ):
            raise ValueError("next collection attempt is outside the collection window")
        if self.completed_at is not None and self.completed_at < self.source_created_at:
            raise ValueError("collection completion precedes its source")

        if self.status == "pending":
            valid = (
                self.revision == 0
                and self.attempt_count == 0
                and self.next_attempt_at is not None
                and not has_lease
                and self.last_failure_reason is None
                and self.final_outcome is None
                and self.terminal_reason is None
                and self.completed_at is None
                and self.expired_at is None
            )
        elif self.status == "deferred":
            valid = (
                self.attempt_count > 0
                and self.next_attempt_at is not None
                and not has_lease
                and self.last_failure_reason is not None
                and self.final_outcome is None
                and self.terminal_reason is None
                and self.completed_at is None
                and self.expired_at is None
            )
        elif self.status == "leased":
            valid = (
                self.attempt_count > 0
                and self.next_attempt_at is None
                and has_lease
                and self.final_outcome is None
                and self.terminal_reason is None
                and self.completed_at is None
                and self.expired_at is None
            )
        elif self.status == "captured":
            valid = (
                self.next_attempt_at is None
                and not has_lease
                and self.final_outcome == "captured"
                and self.terminal_reason is None
                and self.completed_at is not None
                and self.completed_at < self.evidence_retain_until
                and self.expired_at is None
            )
        elif self.status == "terminal_unavailable":
            valid = (
                self.next_attempt_at is None
                and not has_lease
                and self.final_outcome == "terminal_unavailable"
                and self.terminal_reason is not None
                and self.completed_at is not None
                and self.expired_at is None
            )
        elif self.status == "expired":
            valid = (
                self.next_attempt_at is None
                and not has_lease
                and self.final_outcome is not None
                and self.completed_at is not None
                and self.expired_at is not None
                and self.expired_at >= self.evidence_retain_until
                and (
                    (
                        self.final_outcome == "captured"
                        and self.terminal_reason is None
                        and self.completed_at < self.evidence_retain_until
                    )
                    or (
                        self.final_outcome == "terminal_unavailable"
                        and self.terminal_reason is not None
                    )
                )
            )
        else:
            valid = False
        if not valid:
            raise ValueError("collection state fields do not match its status")


@dataclass(frozen=True, slots=True)
class CollectionClaim:
    source: CollectionSource
    revision: int
    generation: int
    attempt_count: int
    policy_hash: str
    worker_id: str
    token: str
    claimed_at: datetime
    lease_expires_at: datetime

    def __post_init__(self) -> None:
        if type(self.source) not in {ReconciliationCollectionSource, ProviderRunCollectionSource}:
            raise TypeError("collection claim requires an exact collection source")
        for numeric_value, name in (
            (self.revision, "collection claim revision"),
            (self.generation, "collection claim generation"),
            (self.attempt_count, "collection claim attempt"),
        ):
            _positive_safe(numeric_value, name)
        for digest_value, name in (
            (self.policy_hash, "collection policy hash"),
            (self.worker_id, "worker id"),
            (self.token, "claim token"),
        ):
            _require_digest(digest_value, name)
        object.__setattr__(self, "claimed_at", _utc(self.claimed_at, "claimed_at"))
        object.__setattr__(
            self,
            "lease_expires_at",
            _utc(self.lease_expires_at, "lease_expires_at"),
        )
        if not self.claimed_at < self.lease_expires_at:
            raise ValueError("collection claim lease must be non-empty")
        if self.generation != self.attempt_count or self.revision < self.generation:
            raise ValueError("collection claim counters are inconsistent")


@dataclass(frozen=True, slots=True)
class CollectionClaimAcquired:
    state: CollectionState
    claim: CollectionClaim

    def __post_init__(self) -> None:
        if type(self.state) is not CollectionState or type(self.claim) is not CollectionClaim:
            raise TypeError("collection acquisition requires exact state and claim values")
        if not (
            self.state.status == "leased"
            and self.state.subject_id == self.claim.source.source_id
            and self.state.policy_hash == self.claim.policy_hash
            and self.state.revision == self.claim.revision
            and self.state.claim_generation == self.claim.generation
            and self.state.attempt_count == self.claim.attempt_count
            and self.state.lease_owner_id == self.claim.worker_id
            and self.state.lease_token == self.claim.token
            and self.state.lease_acquired_at == self.claim.claimed_at
            and self.state.lease_expires_at == self.claim.lease_expires_at
        ):
            raise ValueError("collection acquisition state and claim diverge")


@dataclass(frozen=True, slots=True)
class CollectionTerminalized:
    state: CollectionState

    def __post_init__(self) -> None:
        if type(self.state) is not CollectionState:
            raise TypeError("collection terminalization requires an exact state")
        if self.state.status != "terminal_unavailable":
            raise ValueError("collection terminalization requires a terminal state")


@dataclass(frozen=True, slots=True)
class CollectionClaimLost:
    subject_id: str

    def __post_init__(self) -> None:
        _require_digest(self.subject_id, "lost collection subject id")


_FAILURE_REASONS: Final = frozenset(
    {
        "provider_unavailable",
        "provider_binding_mismatch",
        "provider_malformed",
        "provider_incomplete",
        "provider_not_terminal",
        "provider_unstable",
        "evidence_conflict",
        "unexpected_error",
    }
)
_RETRYABLE_FAILURE_REASONS: Final = _FAILURE_REASONS - {"evidence_conflict"}
_TERMINAL_REASONS: Final = frozenset(
    {"deadline_exceeded", "attempts_exhausted", "evidence_conflict"}
)


def initial_collection_state(
    subject_id: str,
    source_created_at: datetime,
    registered_at: datetime,
    policy: CollectionPolicy,
) -> CollectionState:
    if type(policy) is not CollectionPolicy:
        raise TypeError("initial collection state requires an exact policy")
    source = _utc(source_created_at, "source_created_at")
    now = _utc(registered_at, "registered_at")
    deadline = source + timedelta(seconds=policy.collection_window_seconds)
    if not source <= now < deadline:
        raise ValueError("collection registration is outside its eligibility window")
    evidence_retain_until = source + timedelta(days=policy.evidence_days)
    return CollectionState(
        subject_id=subject_id,
        policy_hash=policy.policy_hash,
        source_created_at=source,
        deadline_at=deadline,
        evidence_retain_until=evidence_retain_until,
        tombstone_retain_until=evidence_retain_until
        + timedelta(seconds=policy.tombstone_grace_seconds),
        status="pending",
        revision=0,
        attempt_count=0,
        max_attempts=policy.maximum_attempts,
        backoff_seconds=policy.initial_backoff_seconds,
        max_backoff_seconds=policy.maximum_backoff_seconds,
        next_attempt_at=now,
        claim_generation=0,
        lease_owner_id=None,
        lease_token=None,
        lease_acquired_at=None,
        lease_expires_at=None,
        last_failure_reason=None,
        final_outcome=None,
        terminal_reason=None,
        completed_at=None,
        expired_at=None,
    )


def acquire_collection_claim(
    state: CollectionState,
    source: CollectionSource,
    *,
    worker_id: str,
    now: datetime,
    policy: CollectionPolicy,
) -> CollectionClaimAcquired | CollectionTerminalized | None:
    _require_transition_inputs(state, source, policy)
    _require_digest(worker_id, "collection worker id")
    acquired_at = _utc(now, "collection claim time")
    if state.status not in {"pending", "deferred", "leased"}:
        return None
    if state.status == "leased":
        lease_expires_at = state.lease_expires_at
        if lease_expires_at is None:
            raise AssertionError("leased collection state has no expiry")
        if lease_expires_at > acquired_at:
            return None
    if state.next_attempt_at is not None and state.next_attempt_at > acquired_at:
        return None
    lease_expires_at = acquired_at + timedelta(seconds=policy.lease_seconds)
    if lease_expires_at >= state.deadline_at:
        return CollectionTerminalized(_terminal_state(state, acquired_at, "deadline_exceeded"))
    if state.attempt_count >= state.max_attempts:
        return CollectionTerminalized(_terminal_state(state, acquired_at, "attempts_exhausted"))

    generation = state.claim_generation + 1
    revision = state.revision + 1
    attempt_count = state.attempt_count + 1
    token = hash_object(
        {
            **_claim_source_mapping(source),
            "policyHash": policy.policy_hash,
            "workerId": worker_id,
            "generation": generation,
            "revision": revision,
            "claimedAt": _timestamp(acquired_at),
        }
    )
    successor = replace(
        state,
        status="leased",
        revision=revision,
        attempt_count=attempt_count,
        next_attempt_at=None,
        claim_generation=generation,
        lease_owner_id=worker_id,
        lease_token=token,
        lease_acquired_at=acquired_at,
        lease_expires_at=lease_expires_at,
    )
    return CollectionClaimAcquired(
        successor,
        CollectionClaim(
            source=source,
            revision=revision,
            generation=generation,
            attempt_count=attempt_count,
            policy_hash=policy.policy_hash,
            worker_id=worker_id,
            token=token,
            claimed_at=acquired_at,
            lease_expires_at=lease_expires_at,
        ),
    )


def defer_collection_claim(
    state: CollectionState,
    claim: CollectionClaim,
    now: datetime,
    reason: RetryableCollectionFailureReason,
    policy: CollectionPolicy,
) -> CollectionState | CollectionClaimLost:
    if type(policy) is not CollectionPolicy:
        raise TypeError("collection deferral requires an exact policy")
    checked_at = _utc(now, "collection defer time")
    _require_failure_reason(reason)
    if not collection_claim_is_active(state, claim, checked_at):
        return CollectionClaimLost(claim.source.source_id)
    delay = _stable_retry_delay(state.backoff_seconds, claim)
    next_attempt = checked_at + timedelta(seconds=delay)
    if state.attempt_count >= state.max_attempts:
        return _terminal_state(state, checked_at, "attempts_exhausted", reason)
    if next_attempt + timedelta(seconds=policy.lease_seconds) >= state.deadline_at:
        return _terminal_state(state, checked_at, "deadline_exceeded", reason)
    return replace(
        state,
        status="deferred",
        revision=state.revision + 1,
        backoff_seconds=min(state.backoff_seconds * 2, state.max_backoff_seconds),
        next_attempt_at=next_attempt,
        lease_owner_id=None,
        lease_token=None,
        lease_acquired_at=None,
        lease_expires_at=None,
        last_failure_reason=reason,
    )


def complete_collection_claim(
    state: CollectionState,
    claim: CollectionClaim,
    now: datetime,
) -> CollectionState | CollectionClaimLost:
    completed_at = _utc(now, "collection completion time")
    if not collection_claim_is_active(state, claim, completed_at):
        return CollectionClaimLost(claim.source.source_id)
    return replace(
        state,
        status="captured",
        revision=state.revision + 1,
        next_attempt_at=None,
        lease_owner_id=None,
        lease_token=None,
        lease_acquired_at=None,
        lease_expires_at=None,
        final_outcome="captured",
        terminal_reason=None,
        completed_at=completed_at,
    )


def reject_collection_claim(
    state: CollectionState,
    claim: CollectionClaim,
    now: datetime,
) -> CollectionState | CollectionClaimLost:
    rejected_at = _utc(now, "collection rejection time")
    if not collection_claim_is_active(state, claim, rejected_at):
        return CollectionClaimLost(claim.source.source_id)
    return _terminal_state(state, rejected_at, "evidence_conflict", "evidence_conflict")


def expire_collection_state(state: CollectionState, now: datetime) -> CollectionState | None:
    expired_at = _utc(now, "collection expiry time")
    if state.status not in {"captured", "terminal_unavailable"}:
        return None
    if expired_at < state.evidence_retain_until:
        return None
    return replace(
        state,
        status="expired",
        revision=state.revision + 1,
        expired_at=expired_at,
    )


def collection_claim_is_active(
    state: CollectionState,
    claim: CollectionClaim,
    now: datetime,
) -> bool:
    if type(state) is not CollectionState or type(claim) is not CollectionClaim:
        return False
    checked_at = _utc(now, "collection lease check time")
    return (
        state.subject_id == claim.source.source_id
        and state.policy_hash == claim.policy_hash
        and state.status == "leased"
        and state.revision == claim.revision
        and state.claim_generation == claim.generation
        and state.attempt_count == claim.attempt_count
        and state.lease_owner_id == claim.worker_id
        and state.lease_token == claim.token
        and state.lease_acquired_at == claim.claimed_at
        and state.lease_expires_at == claim.lease_expires_at
        and claim.claimed_at <= checked_at < claim.lease_expires_at
    )


def _terminal_state(
    state: CollectionState,
    completed_at: datetime,
    reason: CollectionTerminalReason,
    failure_reason: CollectionFailureReason | None = None,
) -> CollectionState:
    return replace(
        state,
        status="terminal_unavailable",
        revision=state.revision + 1,
        next_attempt_at=None,
        lease_owner_id=None,
        lease_token=None,
        lease_acquired_at=None,
        lease_expires_at=None,
        last_failure_reason=failure_reason or state.last_failure_reason,
        final_outcome="terminal_unavailable",
        terminal_reason=reason,
        completed_at=completed_at,
    )


def _stable_retry_delay(backoff_seconds: int, claim: CollectionClaim) -> int:
    minimum = (backoff_seconds + 1) // 2
    width = backoff_seconds - minimum + 1
    digest = hash_object(
        {
            "schemaVersion": "ci-economics-collection-retry-jitter/v1",
            "subjectId": claim.source.source_id,
            "attemptCount": claim.attempt_count,
        }
    )
    return minimum + int(digest[:16], 16) % width


def _require_transition_inputs(
    state: object,
    source: object,
    policy: object,
) -> None:
    if type(state) is not CollectionState:
        raise TypeError("collection transition requires an exact state")
    if (
        type(source) is not ReconciliationCollectionSource
        and type(source) is not ProviderRunCollectionSource
    ):
        raise TypeError("collection transition requires an exact collection source")
    if type(policy) is not CollectionPolicy:
        raise TypeError("collection transition requires an exact policy")
    if state.subject_id != source.source_id:
        raise ValueError("collection state belongs to another source")
    if isinstance(source, ProviderRunCollectionSource) and (
        state.source_created_at != source.run_created_at
    ):
        raise ValueError("collection source creation time changed")
    if state.policy_hash != policy.policy_hash:
        raise ValueError("collection state belongs to another policy")


def _claim_source_mapping(source: CollectionSource) -> dict[str, object]:
    if isinstance(source, ReconciliationCollectionSource):
        return {
            "schemaVersion": "ci-economics-collection-claim/v1",
            "subjectId": source.source_id,
            "contractHash": source.contract.contract_hash,
        }
    return {
        "schemaVersion": "ci-economics-provider-run-collection-claim/v1",
        "sourceId": source.source_id,
        "sourceKind": source.kind,
        "attempt": source.attempt.canonical_mapping(),
        "runCreatedAt": source.run_created_at.isoformat(timespec="microseconds"),
        "providerApiVersion": source.provider_api_version,
        "sourceEvidenceDigest": source.source_evidence_digest,
    }


def _require_failure_reason(value: object) -> None:
    if value not in _RETRYABLE_FAILURE_REASONS:
        raise ValueError("retryable collection failure reason is invalid")


def _require_optional_reason(value: object, allowed: frozenset[str], name: str) -> None:
    if value is not None and value not in allowed:
        raise ValueError(f"collection {name} reason is invalid")


def _require_digest(value: object, name: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _utc(value: object, name: str) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _positive_bounded(value: object, maximum: int, name: str) -> None:
    if type(value) is not int or not 1 <= value <= maximum or not is_safe_json_integer(value):
        raise ValueError(f"{name} is outside its admitted bound")


def _positive_safe(value: object, name: str) -> None:
    if type(value) is not int or value < 1 or not is_safe_json_integer(value):
        raise ValueError(f"{name} must be a positive safe integer")


def _non_negative_safe(value: object, name: str) -> None:
    if type(value) is not int or value < 0 or not is_safe_json_integer(value):
        raise ValueError(f"{name} must be a non-negative safe integer")

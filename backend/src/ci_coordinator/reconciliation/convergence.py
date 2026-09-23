"""Pure lifecycle algebra for durable reconciliation convergence."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Final, Literal

from ci_coordinator.kernel import hash_object, is_safe_json_integer
from ci_coordinator.reconciliation.contract import ReconciliationContract
from ci_coordinator.reconciliation.findings import ReconciliationFinding, ReconciliationResult
from ci_coordinator.reconciliation.subject import ReconciliationSubject

MAX_RECONCILIATION_ATTEMPTS: Final = 1_000
MAX_RECONCILIATION_BACKOFF_SECONDS: Final = 3_600
MAX_RECONCILIATION_DEADLINE_SECONDS: Final = 86_400
MAX_RECONCILIATION_LEASE_SECONDS: Final = 3_600
type ConvergenceTerminalReason = Literal["deadline_exceeded", "attempts_exhausted"]


def _require_aware(value: object, name: str) -> None:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _utc(value: datetime, name: str) -> datetime:
    _require_aware(value, name)
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _require_digest(value: object, name: str) -> None:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _bounded_positive(value: object, maximum: int, name: str) -> None:
    if type(value) is not int or value < 1 or value > maximum or not is_safe_json_integer(value):
        raise ValueError(f"{name} is outside its admitted bound")


def _bounded_non_negative(value: object, maximum: int, name: str) -> None:
    if type(value) is not int or value < 0 or value > maximum or not is_safe_json_integer(value):
        raise ValueError(f"{name} is outside its admitted bound")


@dataclass(frozen=True, slots=True)
class ReconciliationConvergencePolicy:
    deadline_seconds: int = 1_800
    initial_backoff_seconds: int = 5
    max_backoff_seconds: int = 60
    max_attempts: int = 20
    poll_timeout_seconds: int = 240
    lease_seconds: int = 300

    def __post_init__(self) -> None:
        _bounded_positive(
            self.deadline_seconds,
            MAX_RECONCILIATION_DEADLINE_SECONDS,
            "reconciliation deadline",
        )
        _bounded_positive(
            self.initial_backoff_seconds,
            MAX_RECONCILIATION_BACKOFF_SECONDS,
            "initial reconciliation backoff",
        )
        _bounded_positive(
            self.max_backoff_seconds,
            MAX_RECONCILIATION_BACKOFF_SECONDS,
            "maximum reconciliation backoff",
        )
        _bounded_positive(
            self.max_attempts,
            MAX_RECONCILIATION_ATTEMPTS,
            "maximum reconciliation attempts",
        )
        _bounded_positive(
            self.poll_timeout_seconds,
            MAX_RECONCILIATION_LEASE_SECONDS,
            "reconciliation poll timeout",
        )
        _bounded_positive(
            self.lease_seconds,
            MAX_RECONCILIATION_LEASE_SECONDS,
            "reconciliation lease",
        )
        if self.initial_backoff_seconds > self.max_backoff_seconds:
            raise ValueError("initial reconciliation backoff cannot exceed its maximum")
        if self.max_backoff_seconds > self.deadline_seconds:
            raise ValueError("maximum reconciliation backoff cannot exceed the deadline")
        if self.poll_timeout_seconds >= self.lease_seconds:
            raise ValueError("reconciliation poll timeout must be shorter than the lease")
        if self.lease_seconds > self.deadline_seconds:
            raise ValueError("reconciliation lease cannot exceed the deadline")


DEFAULT_RECONCILIATION_CONVERGENCE_POLICY: Final = ReconciliationConvergencePolicy()


@dataclass(frozen=True, slots=True)
class ReconciliationConvergenceState:
    created_at: datetime
    deadline_at: datetime
    next_attempt_at: datetime
    attempt_count: int
    max_attempts: int
    backoff_seconds: int
    max_backoff_seconds: int
    claim_generation: int
    lease_token: str | None
    lease_acquired_at: datetime | None
    lease_expires_at: datetime | None

    def __post_init__(self) -> None:
        for value, name in (
            (self.created_at, "created_at"),
            (self.deadline_at, "deadline_at"),
            (self.next_attempt_at, "next_attempt_at"),
        ):
            _require_aware(value, name)
        if not self.created_at < self.deadline_at:
            raise ValueError("reconciliation deadline must follow creation")
        if self.deadline_at > self.created_at + timedelta(
            seconds=MAX_RECONCILIATION_DEADLINE_SECONDS
        ):
            raise ValueError("reconciliation deadline duration exceeds its bound")
        if not self.created_at <= self.next_attempt_at <= self.deadline_at:
            raise ValueError("reconciliation due time must be within its lifetime")
        _bounded_non_negative(
            self.attempt_count,
            self.max_attempts,
            "reconciliation attempt count",
        )
        _bounded_positive(
            self.max_attempts,
            MAX_RECONCILIATION_ATTEMPTS,
            "maximum reconciliation attempts",
        )
        _bounded_positive(
            self.backoff_seconds,
            self.max_backoff_seconds,
            "reconciliation backoff",
        )
        _bounded_positive(
            self.max_backoff_seconds,
            MAX_RECONCILIATION_BACKOFF_SECONDS,
            "maximum reconciliation backoff",
        )
        _bounded_non_negative(
            self.claim_generation,
            9_007_199_254_740_991,
            "reconciliation claim generation",
        )
        if self.claim_generation < self.attempt_count:
            raise ValueError("reconciliation claim generation cannot trail attempts")
        lease_fields = (self.lease_token, self.lease_acquired_at, self.lease_expires_at)
        if any(value is None for value in lease_fields) and any(
            value is not None for value in lease_fields
        ):
            raise ValueError("reconciliation lease identity, acquisition, and expiry must pair")
        if self.lease_token is not None:
            _require_digest(self.lease_token, "reconciliation lease token")
            lease_acquired_at = self.lease_acquired_at
            lease_expires_at = self.lease_expires_at
            if lease_acquired_at is None or lease_expires_at is None:
                raise ValueError("reconciliation lease timestamps are missing")
            _require_aware(lease_acquired_at, "lease_acquired_at")
            _require_aware(lease_expires_at, "lease_expires_at")
            if lease_acquired_at < self.created_at:
                raise ValueError("reconciliation lease acquisition cannot precede creation")
            if not lease_acquired_at < lease_expires_at:
                raise ValueError("reconciliation lease expiry must follow acquisition")
            if lease_expires_at > lease_acquired_at + timedelta(
                seconds=MAX_RECONCILIATION_LEASE_SECONDS
            ):
                raise ValueError("reconciliation lease duration exceeds its bound")


@dataclass(frozen=True, slots=True)
class ReconciliationAttemptClaim:
    subject: ReconciliationSubject
    contract: ReconciliationContract
    revision: int
    token: str
    generation: int
    attempt_count: int
    max_attempts: int
    claimed_at: datetime
    lease_expires_at: datetime
    deadline_at: datetime
    terminal_reason: ConvergenceTerminalReason | None

    def __post_init__(self) -> None:
        if type(self.subject) is not ReconciliationSubject:
            raise TypeError("reconciliation claim requires an exact subject")
        if type(self.contract) is not ReconciliationContract:
            raise TypeError("reconciliation claim requires an exact contract")
        _bounded_non_negative(self.revision, 9_007_199_254_740_991, "reconciliation revision")
        _require_digest(self.token, "reconciliation claim token")
        _bounded_positive(
            self.generation,
            9_007_199_254_740_991,
            "reconciliation claim generation",
        )
        _bounded_non_negative(
            self.attempt_count,
            self.max_attempts,
            "reconciliation attempt count",
        )
        _bounded_positive(
            self.max_attempts,
            MAX_RECONCILIATION_ATTEMPTS,
            "maximum reconciliation attempts",
        )
        for value, name in (
            (self.claimed_at, "claimed_at"),
            (self.lease_expires_at, "lease_expires_at"),
            (self.deadline_at, "deadline_at"),
        ):
            _require_aware(value, name)
        if self.lease_expires_at <= self.claimed_at:
            raise ValueError("reconciliation claim lease must have positive duration")
        if self.lease_expires_at > self.claimed_at + timedelta(
            seconds=MAX_RECONCILIATION_LEASE_SECONDS
        ):
            raise ValueError("reconciliation claim lease exceeds its duration bound")
        if self.terminal_reason not in {None, "deadline_exceeded", "attempts_exhausted"}:
            raise ValueError("reconciliation claim terminal reason is unsupported")
        if self.terminal_reason == "deadline_exceeded" and self.claimed_at < self.deadline_at:
            raise ValueError("reconciliation deadline claim precedes its deadline")
        if self.terminal_reason == "attempts_exhausted":
            if self.attempt_count < self.max_attempts:
                raise ValueError("reconciliation attempt-limit claim has remaining attempts")
            if self.claimed_at >= self.deadline_at:
                raise ValueError("reconciliation deadline must dominate attempt exhaustion")
        if self.terminal_reason is None:
            if self.attempt_count < 1:
                raise ValueError("pollable reconciliation claim must consume an attempt")
            if self.claimed_at >= self.deadline_at:
                raise ValueError("pollable reconciliation claim cannot start at its deadline")
        if self.generation < self.attempt_count:
            raise ValueError("reconciliation claim generation cannot trail attempts")

    @property
    def may_poll(self) -> bool:
        return self.terminal_reason is None


@dataclass(frozen=True, slots=True)
class ReconciliationClaimAcquired:
    state: ReconciliationConvergenceState
    claim: ReconciliationAttemptClaim


@dataclass(frozen=True, slots=True)
class ReconciliationClaimLost:
    subject_id: str

    def __post_init__(self) -> None:
        _require_digest(self.subject_id, "lost reconciliation claim subject")


def initial_convergence_state(
    now: datetime,
    policy: ReconciliationConvergencePolicy = DEFAULT_RECONCILIATION_CONVERGENCE_POLICY,
) -> ReconciliationConvergenceState:
    """Create the immutable initial schedule for one newly registered subject."""
    _require_policy(policy)
    created_at = _utc(now, "reconciliation creation time")
    return ReconciliationConvergenceState(
        created_at=created_at,
        deadline_at=created_at + timedelta(seconds=policy.deadline_seconds),
        next_attempt_at=created_at,
        attempt_count=0,
        max_attempts=policy.max_attempts,
        backoff_seconds=policy.initial_backoff_seconds,
        max_backoff_seconds=policy.max_backoff_seconds,
        claim_generation=0,
        lease_token=None,
        lease_acquired_at=None,
        lease_expires_at=None,
    )


def acquire_reconciliation_claim(
    state: ReconciliationConvergenceState,
    subject: ReconciliationSubject,
    contract: ReconciliationContract,
    revision: int,
    *,
    worker_id: str,
    now: datetime,
    policy: ReconciliationConvergencePolicy = DEFAULT_RECONCILIATION_CONVERGENCE_POLICY,
) -> ReconciliationClaimAcquired | None:
    """Acquire one due, unleased subject and derive a fencing token."""
    if type(state) is not ReconciliationConvergenceState:
        raise TypeError("reconciliation claim requires exact convergence state")
    if type(subject) is not ReconciliationSubject or type(contract) is not ReconciliationContract:
        raise TypeError("reconciliation claim requires an exact subject and contract")
    _bounded_non_negative(revision, 9_007_199_254_740_991, "reconciliation revision")
    _require_digest(worker_id, "reconciliation worker id")
    _require_policy(policy)
    claimed_at = _utc(now, "reconciliation claim time")
    if state.next_attempt_at > claimed_at:
        return None
    if state.lease_expires_at is not None and state.lease_expires_at > claimed_at:
        return None

    generation = state.claim_generation + 1
    _bounded_positive(generation, 9_007_199_254_740_991, "reconciliation claim generation")
    if claimed_at >= state.deadline_at:
        terminal_reason: ConvergenceTerminalReason | None = "deadline_exceeded"
        attempt_count = state.attempt_count
    elif state.attempt_count >= state.max_attempts:
        terminal_reason = "attempts_exhausted"
        attempt_count = state.attempt_count
    else:
        terminal_reason = None
        attempt_count = state.attempt_count + 1
    lease_expires_at = claimed_at + timedelta(seconds=policy.lease_seconds)
    token = hash_object(
        {
            "schemaVersion": "ci-reconciliation-claim/v1",
            "subjectId": subject.subject_id,
            "workerId": worker_id,
            "generation": generation,
            "claimedAt": _timestamp(claimed_at),
        }
    )
    successor = replace(
        state,
        attempt_count=attempt_count,
        claim_generation=generation,
        lease_token=token,
        lease_acquired_at=claimed_at,
        lease_expires_at=lease_expires_at,
    )
    return ReconciliationClaimAcquired(
        successor,
        ReconciliationAttemptClaim(
            subject=subject,
            contract=contract,
            revision=revision,
            token=token,
            generation=generation,
            attempt_count=attempt_count,
            max_attempts=state.max_attempts,
            claimed_at=claimed_at,
            lease_expires_at=lease_expires_at,
            deadline_at=state.deadline_at,
            terminal_reason=terminal_reason,
        ),
    )


def defer_reconciliation_claim(
    state: ReconciliationConvergenceState,
    claim: ReconciliationAttemptClaim,
    now: datetime,
) -> ReconciliationConvergenceState | ReconciliationClaimLost:
    """Release an active nonterminal claim and advance its bounded backoff."""
    deferred_at = _utc(now, "reconciliation defer time")
    if not reconciliation_claim_is_active(state, claim, deferred_at):
        return ReconciliationClaimLost(claim.subject.subject_id)
    if deferred_at >= state.deadline_at or state.attempt_count >= state.max_attempts:
        raise ValueError("terminal reconciliation claim cannot be deferred")
    delay_seconds = _subject_stable_retry_delay(state.backoff_seconds, claim)
    next_attempt_at = min(
        state.deadline_at,
        deferred_at + timedelta(seconds=delay_seconds),
    )
    return replace(
        state,
        next_attempt_at=next_attempt_at,
        backoff_seconds=min(state.backoff_seconds * 2, state.max_backoff_seconds),
        lease_token=None,
        lease_acquired_at=None,
        lease_expires_at=None,
    )


def _subject_stable_retry_delay(
    backoff_seconds: int,
    claim: ReconciliationAttemptClaim,
) -> int:
    minimum = (backoff_seconds + 1) // 2
    width = backoff_seconds - minimum + 1
    digest = hash_object(
        {
            "schemaVersion": "ci-reconciliation-retry-jitter/v1",
            "subjectId": claim.subject.subject_id,
            "attemptCount": claim.attempt_count,
        }
    )
    return minimum + int(digest[:16], 16) % width


def release_terminal_claim(
    state: ReconciliationConvergenceState,
    claim: ReconciliationAttemptClaim,
    now: datetime,
) -> ReconciliationConvergenceState | ReconciliationClaimLost:
    """Clear a lease only when its token is still current and unexpired."""
    completed_at = _utc(now, "reconciliation completion time")
    if not reconciliation_claim_is_active(state, claim, completed_at):
        return ReconciliationClaimLost(claim.subject.subject_id)
    return replace(
        state,
        lease_token=None,
        lease_acquired_at=None,
        lease_expires_at=None,
    )


def reconciliation_claim_is_active(
    state: ReconciliationConvergenceState,
    claim: ReconciliationAttemptClaim,
    now: datetime,
) -> bool:
    checked_at = _utc(now, "reconciliation lease check time")
    return (
        type(state) is ReconciliationConvergenceState
        and type(claim) is ReconciliationAttemptClaim
        and state.lease_token == claim.token
        and state.claim_generation == claim.generation
        and state.lease_acquired_at == claim.claimed_at
        and state.lease_expires_at is not None
        and checked_at >= claim.claimed_at
        and state.lease_expires_at > checked_at
    )


def convergence_failure(
    claim: ReconciliationAttemptClaim,
    reason: ConvergenceTerminalReason,
) -> ReconciliationResult:
    """Create one redacted terminal failure from an exhausted lifecycle bound."""
    if type(claim) is not ReconciliationAttemptClaim:
        raise TypeError("convergence failure requires an exact claim")
    if reason == "deadline_exceeded":
        kind: Literal["reconciliation_timed_out", "reconciliation_attempts_exhausted"] = (
            "reconciliation_timed_out"
        )
        message = "reconciliation did not converge before its deadline"
    elif reason == "attempts_exhausted":
        kind = "reconciliation_attempts_exhausted"
        message = "reconciliation did not converge within its attempt bound"
    else:
        raise ValueError("reconciliation convergence failure reason is unsupported")
    return ReconciliationResult(
        subject_id=claim.subject.subject_id,
        state="failure",
        findings=(
            ReconciliationFinding(
                kind=kind,
                signal_id=None,
                observation_ids=(),
                message=message,
            ),
        ),
    )


def _require_policy(value: object) -> None:
    if type(value) is not ReconciliationConvergencePolicy:
        raise TypeError("reconciliation convergence policy must be exact")

"""Immutable owner-approved governance-baseline algebra."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_observation import GovernanceState
from ci_coordinator.governance_observation.codec import encode_governance_state
from ci_coordinator.kernel import canonical_json, sha256_hex
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

BASELINE_ID_PREFIX = "governance-baseline:"
GOVERNANCE_BASELINE_SCHEMA = "governance-baseline/v1"
MAX_BASELINE_OPERATION_ID_BYTES = 256
MAX_BASELINE_ACTOR_BYTES = 256
MAX_BASELINE_REASON_BYTES = 1_024
MAX_BASELINE_COMMAND_BYTES = 8_192

_BASELINE_ID = re.compile(r"governance-baseline:[0-9a-f]{64}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_AUDIT_EVENT_ID = re.compile(r"audit_[0-9a-f]{32}")
_NONCANONICAL_REASON_BOUNDARY = frozenset(
    {
        0x0020,
        0x00A0,
        0x1680,
        *range(0x2000, 0x200B),
        0x2028,
        0x2029,
        0x202F,
        0x205F,
        0x3000,
        0xFEFF,
    }
)


@dataclass(frozen=True, slots=True)
class GovernanceBaselinePointer:
    scope: RepositoryScope
    baseline_id: str
    version: int
    state_digest: str

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("governance baseline pointer requires an exact scope")
        if type(self.baseline_id) is not str or _BASELINE_ID.fullmatch(self.baseline_id) is None:
            raise ValueError("governance baseline identity is invalid")
        if type(self.version) is not int or not 1 <= self.version <= MAX_SAFE_JSON_INTEGER:
            raise ValueError("governance baseline version is invalid")
        _require_sha256(self.state_digest, "governance baseline state digest")

    def identity_mapping(self) -> dict[str, object]:
        return {
            "baselineId": self.baseline_id,
            "version": self.version,
            "stateDigest": self.state_digest,
        }


@dataclass(frozen=True, slots=True)
class GovernanceBaselineCommand:
    scope: RepositoryScope
    operation_id: str
    expected_state_digest: str
    expected_active: GovernanceBaselinePointer | None
    actor: str
    reason: str

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise TypeError("governance baseline command requires an exact scope")
        _require_bounded_text(
            self.operation_id,
            "operation id",
            MAX_BASELINE_OPERATION_ID_BYTES,
        )
        if "\0" in self.operation_id:
            raise ValueError("governance baseline operation id cannot contain NUL")
        _require_sha256(self.expected_state_digest, "expected governance state digest")
        if self.expected_active is not None and (
            type(self.expected_active) is not GovernanceBaselinePointer
            or self.expected_active.scope != self.scope
        ):
            raise ValueError("expected governance baseline must bind the command scope")
        _require_bounded_text(
            self.actor,
            "actor",
            MAX_BASELINE_ACTOR_BYTES,
        )
        if not governance_baseline_reason_is_admitted(self.reason):
            raise ValueError("governance baseline reason is outside its canonical text profile")


@dataclass(frozen=True, slots=True)
class GovernanceBaselineDraft:
    command: GovernanceBaselineCommand
    state: GovernanceState
    observed_at: datetime

    def __post_init__(self) -> None:
        if type(self.command) is not GovernanceBaselineCommand:
            raise TypeError("governance baseline draft requires an exact command")
        if type(self.state) is not GovernanceState:
            raise TypeError("governance baseline draft requires an exact state")
        _require_instant(self.observed_at, "governance observation time")
        if self.state.repository.scope != self.command.scope:
            raise ValueError("governance baseline draft crosses repository scope")
        if self.command.expected_state_digest != self.state_digest:
            raise ValueError("governance baseline draft contradicts the requested state")
        active = self.command.expected_active
        if active is not None and active.version == MAX_SAFE_JSON_INTEGER:
            raise ValueError("governance baseline version space is exhausted")

    @property
    def version(self) -> int:
        active = self.command.expected_active
        return 1 if active is None else active.version + 1

    @property
    def state_bytes(self) -> bytes:
        return encode_governance_state(self.state)

    @property
    def state_digest(self) -> str:
        return sha256_hex(self.state_bytes)

    def approval_identity(self, *, approved_at: datetime) -> dict[str, object]:
        _require_instant(approved_at, "governance approval time")
        if approved_at < self.observed_at:
            raise ValueError("governance approval cannot precede its observation")
        predecessor = self.command.expected_active
        return {
            "schemaVersion": GOVERNANCE_BASELINE_SCHEMA,
            "scope": {
                "installationId": self.command.scope.installation_id,
                "repositoryId": self.command.scope.repository_id,
            },
            "version": self.version,
            "supersedes": None if predecessor is None else predecessor.identity_mapping(),
            "state": self.state.identity_mapping(),
            "stateDigest": self.state_digest,
            "observedAt": canonical_instant(self.observed_at),
            "approvedAt": canonical_instant(approved_at),
            "operationId": self.command.operation_id,
            "actor": self.command.actor,
            "reason": self.command.reason,
        }


@dataclass(frozen=True, slots=True)
class GovernanceBaselineRecord:
    command: GovernanceBaselineCommand
    baseline_id: str
    version: int
    state: GovernanceState
    observed_at: datetime
    approved_at: datetime
    audit_event_id: str
    audit_input_hash: str

    @classmethod
    def from_draft(
        cls,
        draft: GovernanceBaselineDraft,
        *,
        approved_at: datetime,
        audit_event_id: str,
        audit_input_hash: str,
    ) -> GovernanceBaselineRecord:
        return cls(
            command=draft.command,
            baseline_id=baseline_id_for(draft, approved_at=approved_at),
            version=draft.version,
            state=draft.state,
            observed_at=draft.observed_at,
            approved_at=approved_at,
            audit_event_id=audit_event_id,
            audit_input_hash=audit_input_hash,
        )

    def __post_init__(self) -> None:
        draft = GovernanceBaselineDraft(
            command=self.command,
            state=self.state,
            observed_at=self.observed_at,
        )
        if type(self.baseline_id) is not str or _BASELINE_ID.fullmatch(self.baseline_id) is None:
            raise ValueError("governance baseline record identity is invalid")
        if self.baseline_id != baseline_id_for(draft, approved_at=self.approved_at):
            raise ValueError("governance baseline record identity is inconsistent")
        if self.version != draft.version:
            raise ValueError("governance baseline record version is inconsistent")
        if (
            type(self.audit_event_id) is not str
            or _AUDIT_EVENT_ID.fullmatch(self.audit_event_id) is None
        ):
            raise ValueError("governance baseline audit event identity is invalid")
        _require_sha256(self.audit_input_hash, "governance baseline audit input hash")

    @property
    def pointer(self) -> GovernanceBaselinePointer:
        return GovernanceBaselinePointer(
            scope=self.command.scope,
            baseline_id=self.baseline_id,
            version=self.version,
            state_digest=self.command.expected_state_digest,
        )

    @property
    def state_bytes(self) -> bytes:
        return encode_governance_state(self.state)


@dataclass(frozen=True, slots=True)
class GovernanceBaselineCreated:
    record: GovernanceBaselineRecord

    def __post_init__(self) -> None:
        _require_record(self.record)


@dataclass(frozen=True, slots=True)
class GovernanceBaselineDuplicate:
    record: GovernanceBaselineRecord

    def __post_init__(self) -> None:
        _require_record(self.record)


@dataclass(frozen=True, slots=True)
class GovernanceBaselineUnchanged:
    record: GovernanceBaselineRecord

    def __post_init__(self) -> None:
        _require_record(self.record)


@dataclass(frozen=True, slots=True)
class GovernanceBaselineConflict:
    active: GovernanceBaselinePointer | None

    def __post_init__(self) -> None:
        if self.active is not None and type(self.active) is not GovernanceBaselinePointer:
            raise TypeError("governance baseline conflict pointer must be exact")


@dataclass(frozen=True, slots=True)
class GovernanceBaselineOperationConflict:
    existing: GovernanceBaselineRecord

    def __post_init__(self) -> None:
        _require_record(self.existing)


type GovernanceBaselineResolution = (
    GovernanceBaselineDuplicate
    | GovernanceBaselineUnchanged
    | GovernanceBaselineOperationConflict
    | None
)
type GovernanceBaselineWriteResult = (
    GovernanceBaselineCreated
    | GovernanceBaselineDuplicate
    | GovernanceBaselineUnchanged
    | GovernanceBaselineConflict
    | GovernanceBaselineOperationConflict
)


def baseline_id_for(
    draft: GovernanceBaselineDraft,
    *,
    approved_at: datetime,
) -> str:
    if type(draft) is not GovernanceBaselineDraft:
        raise TypeError("governance baseline identity requires an exact draft")
    digest = sha256_hex(
        b"ci-governance-baseline/v1\0"
        + canonical_json(draft.approval_identity(approved_at=approved_at))
    )
    return f"{BASELINE_ID_PREFIX}{digest}"


def canonical_instant(value: datetime) -> str:
    return (
        normalize_baseline_instant(value)
        .isoformat(timespec="milliseconds")
        .replace(
            "+00:00",
            "Z",
        )
    )


def normalize_baseline_instant(value: datetime) -> datetime:
    _require_aware_instant(value, "governance baseline instant")
    normalized = value.astimezone(UTC)
    return normalized.replace(microsecond=(normalized.microsecond // 1_000) * 1_000)


def governance_baseline_reason_is_admitted(value: object) -> bool:
    if type(value) is not str or not value:
        return False
    code_points = tuple(ord(character) for character in value)
    return (
        len(value.encode("utf-8", errors="surrogatepass")) <= MAX_BASELINE_REASON_BYTES
        and not any(
            code_point <= 0x001F or 0x007F <= code_point <= 0x009F or 0xD800 <= code_point <= 0xDFFF
            for code_point in code_points
        )
        and code_points[0] not in _NONCANONICAL_REASON_BOUNDARY
        and code_points[-1] not in _NONCANONICAL_REASON_BOUNDARY
    )


def _require_bounded_text(
    value: object,
    name: str,
    maximum_bytes: int,
) -> None:
    if (
        type(value) is not str
        or not value
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError(f"governance baseline {name} must be bounded Unicode scalar text")


def _require_sha256(value: object, name: str) -> None:
    if type(value) is not str or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")


def _require_instant(value: object, name: str) -> None:
    _require_aware_instant(value, name)
    if not isinstance(value, datetime) or value.microsecond % 1_000 != 0:
        raise ValueError(f"{name} must use millisecond precision")


def _require_aware_instant(value: object, name: str) -> None:
    offset = value.utcoffset() if type(value) is datetime and value.tzinfo is not None else None
    if type(value) is not datetime or offset is None or offset.total_seconds() % 60 != 0:
        raise ValueError(f"{name} must be timezone-aware")


def _require_record(value: object) -> None:
    if type(value) is not GovernanceBaselineRecord:
        raise TypeError("governance baseline result requires an exact record")

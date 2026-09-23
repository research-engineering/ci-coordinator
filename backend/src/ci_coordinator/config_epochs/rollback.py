"""Coverage-monotonic rollback admission over retained immutable epochs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal, Protocol

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs.contracts import validate_rollback_reason

type CoverageRelation = Literal[
    "equal",
    "greater",
    "less",
    "incomparable",
    "unknown",
    "unavailable",
]

_MAX_ROLLBACK_ACTOR_UTF8_BYTES: Final = 512
_EPOCH_ID_LENGTH: Final = 64


@dataclass(frozen=True, slots=True)
class RollbackCommand:
    scope: RepositoryScope
    active_epoch_id: str
    target_epoch_id: str
    actor: str
    reason: str

    def __post_init__(self) -> None:
        if type(self.scope) is not RepositoryScope:
            raise ValueError("rollback scope must be exact")
        _require_epoch_id(self.active_epoch_id, "active epoch id")
        _require_epoch_id(self.target_epoch_id, "target epoch id")
        _require_bounded_text(self.actor, "actor", _MAX_ROLLBACK_ACTOR_UTF8_BYTES)
        validate_rollback_reason(self.reason)


class EpochCoverageComparator(Protocol):
    async def compare(
        self,
        *,
        scope: RepositoryScope,
        active_epoch_id: str,
        target_epoch_id: str,
    ) -> CoverageRelation: ...


@dataclass(frozen=True, slots=True)
class RollbackAllowed:
    relation: Literal["equal", "greater"]


@dataclass(frozen=True, slots=True)
class RollbackRejected:
    code: Literal[
        "coverage_reducing",
        "coverage_incomparable",
        "coverage_unknown",
        "coverage_unavailable",
    ]


type RollbackAdmission = RollbackAllowed | RollbackRejected


async def admit_rollback(
    command: RollbackCommand,
    comparator: EpochCoverageComparator,
) -> RollbackAdmission:
    relation = await comparator.compare(
        scope=command.scope,
        active_epoch_id=command.active_epoch_id,
        target_epoch_id=command.target_epoch_id,
    )
    if relation == "equal" or relation == "greater":
        return RollbackAllowed(relation)
    if relation == "less":
        return RollbackRejected("coverage_reducing")
    if relation == "incomparable":
        return RollbackRejected("coverage_incomparable")
    if relation == "unavailable":
        return RollbackRejected("coverage_unavailable")
    return RollbackRejected("coverage_unknown")


def _require_epoch_id(value: object, name: str) -> None:
    if (
        type(value) is not str
        or len(value) != _EPOCH_ID_LENGTH
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"rollback {name} must be lowercase SHA-256 hexadecimal")


def _require_bounded_text(value: object, name: str, maximum_bytes: int) -> None:
    if (
        type(value) is not str
        or not value
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
        or len(value.encode("utf-8")) > maximum_bytes
    ):
        raise ValueError(f"rollback {name} must be bounded non-empty text")

from __future__ import annotations

import asyncio

import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.config_epochs import (
    CoverageRelation,
    EpochCoverageComparator,
    RollbackCommand,
    RollbackRejected,
    admit_rollback,
)

SCOPE = RepositoryScope(1, 2)


class _Comparator(EpochCoverageComparator):
    def __init__(self, relation: CoverageRelation) -> None:
        self._relation = relation

    async def compare(
        self,
        *,
        scope: RepositoryScope,
        active_epoch_id: str,
        target_epoch_id: str,
    ) -> CoverageRelation:
        del scope, active_epoch_id, target_epoch_id
        return self._relation


def test_rollback_rejects_an_admitted_but_coverage_reducing_epoch() -> None:
    command = RollbackCommand(SCOPE, "a" * 64, "b" * 64, "operator", "rollback")

    result = asyncio.run(admit_rollback(command, _Comparator("less")))

    assert result == RollbackRejected("coverage_reducing")


def test_rollback_accepts_only_equal_or_greater_coverage() -> None:
    command = RollbackCommand(SCOPE, "a" * 64, "b" * 64, "operator", "rollback")
    equal = asyncio.run(admit_rollback(command, _Comparator("equal")))
    greater = asyncio.run(admit_rollback(command, _Comparator("greater")))

    assert not isinstance(equal, RollbackRejected)
    assert not isinstance(greater, RollbackRejected)
    assert equal.relation == "equal"
    assert greater.relation == "greater"


@pytest.mark.parametrize(
    ("relation", "code"),
    [
        ("incomparable", "coverage_incomparable"),
        ("unknown", "coverage_unknown"),
        ("unavailable", "coverage_unavailable"),
    ],
)
def test_rollback_fails_closed_without_a_positive_coverage_proof(
    relation: CoverageRelation,
    code: str,
) -> None:
    command = RollbackCommand(SCOPE, "a" * 64, "b" * 64, "operator", "rollback")

    result = asyncio.run(admit_rollback(command, _Comparator(relation)))

    assert isinstance(result, RollbackRejected)
    assert result.code == code


def test_rollback_reason_is_nonempty_and_utf8_bounded() -> None:
    with pytest.raises(ValueError, match="reason"):
        RollbackCommand(SCOPE, "a" * 64, "b" * 64, "operator", "")
    with pytest.raises(ValueError, match="reason"):
        RollbackCommand(SCOPE, "a" * 64, "b" * 64, "operator", "\u00e9" * 257)

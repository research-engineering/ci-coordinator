from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_observation import (
    EffectiveGovernanceRule,
    GovernanceObservation,
    GovernanceObservationForbidden,
    GovernanceObservationService,
    GovernanceObservationUnavailable,
    GovernanceRepository,
    GovernanceState,
)
from ci_coordinator.kernel import FixedClock, canonical_json

NOW = datetime(2026, 7, 26, 12, tzinfo=UTC)
SCOPE = RepositoryScope(7, 11)


def test_observation_digest_excludes_time_and_binds_additive_rule_fields() -> None:
    initial = _state(_rule(parameters={"required_status_checks": ["CI"]}))
    same_state_later = GovernanceObservation.from_state(
        initial,
        observed_at=NOW + timedelta(hours=1),
    )
    changed = GovernanceObservation.from_state(
        _state(_rule(parameters={"required_status_checks": ["CI", "Security"]})),
        observed_at=NOW,
    )

    first = GovernanceObservation.from_state(initial, observed_at=NOW)

    assert first.state_digest == same_state_later.state_digest
    assert first.state_digest != changed.state_digest
    assert first.baseline_state == "unbaselined"
    assert first.consistency == "best_effort"


def test_state_rejects_noncanonical_order_duplicates_and_aggregate_overflow() -> None:
    first = _rule(rule_type="a")
    second = _rule(rule_type="b")

    with pytest.raises(ValueError, match="canonical byte order"):
        GovernanceState(_repository(), "2026-03-10", (second, first))
    with pytest.raises(ValueError, match="unique"):
        GovernanceState(_repository(), "2026-03-10", (first, first))
    with pytest.raises(ValueError, match="rule-count bound"):
        GovernanceState(_repository(), "2026-03-10", (first,) * 1_001)

    oversized_aggregate = tuple(
        sorted(
            (
                _rule(rule_type="c", parameters={"payload": "x" * 700_000}),
                _rule(rule_type="d", parameters={"payload": "x" * 700_000}),
                _rule(rule_type="e", parameters={"payload": "x" * 700_000}),
            ),
            key=lambda rule: rule.canonical_json,
        )
    )
    with pytest.raises(ValueError, match="aggregate byte bound"):
        GovernanceState(_repository(), "2026-03-10", oversized_aggregate)


@pytest.mark.parametrize(
    ("default_branch", "admitted"),
    [
        ("a" * 512, True),
        ("a" * 513, False),
        ("\N{LATIN SMALL LETTER E WITH ACUTE}" * 256, True),
        ("\N{LATIN SMALL LETTER E WITH ACUTE}" * 257, False),
        ("@", True),
        ("feature/release", True),
        ("/main", False),
        ("main/", False),
        ("../main", False),
        ("main..next", False),
    ],
)
def test_repository_closes_the_bounded_git_branch_contract(
    default_branch: str,
    admitted: bool,
) -> None:
    if admitted:
        assert _repository(default_branch=default_branch).default_branch == default_branch
    else:
        with pytest.raises(ValueError, match="default branch"):
            _repository(default_branch=default_branch)


def test_authorization_precedes_reader_and_success_uses_injected_clock() -> None:
    events: list[str] = []
    authorizer = _Authorizer(True, events)
    reader = _Reader(_state(_rule()), events)
    service = GovernanceObservationService(
        authorizer=authorizer,
        reader=reader,
        clock=FixedClock(NOW),
    )

    result = asyncio.run(service(actor="operator", scope=SCOPE))

    assert isinstance(result, GovernanceObservation)
    assert result.observed_at == NOW
    assert events == ["authorize", "read"]


def test_denial_and_reader_failure_never_widen_authority() -> None:
    denied_events: list[str] = []
    denied = GovernanceObservationService(
        authorizer=_Authorizer(False, denied_events),
        reader=_UnexpectedReader(),
        clock=FixedClock(NOW),
    )
    unavailable_events: list[str] = []
    unavailable = GovernanceObservationService(
        authorizer=_Authorizer(True, unavailable_events),
        reader=_Reader(GovernanceObservationUnavailable("rate_limited", 30), unavailable_events),
        clock=FixedClock(NOW),
    )

    denied_result = asyncio.run(denied(actor="operator", scope=SCOPE))
    unavailable_result = asyncio.run(unavailable(actor="operator", scope=SCOPE))

    assert isinstance(denied_result, GovernanceObservationForbidden)
    assert denied_events == ["authorize"]
    assert unavailable_result == GovernanceObservationUnavailable("rate_limited", 30)
    assert unavailable_events == ["authorize", "read"]


@dataclass
class _Authorizer:
    result: bool
    events: list[str]

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        assert actor == "operator"
        assert scope == SCOPE
        self.events.append("authorize")
        return self.result


@dataclass
class _Reader:
    result: GovernanceState | GovernanceObservationUnavailable
    events: list[str] = field(default_factory=list)

    async def read(
        self,
        *,
        scope: RepositoryScope,
    ) -> GovernanceState | GovernanceObservationUnavailable:
        assert scope == SCOPE
        self.events.append("read")
        return self.result


class _UnexpectedReader:
    async def read(
        self,
        *,
        scope: RepositoryScope,
    ) -> GovernanceState | GovernanceObservationUnavailable:
        del scope
        raise AssertionError("denied observation must not perform provider I/O")


def _repository(*, default_branch: str = "master") -> GovernanceRepository:
    return GovernanceRepository(
        scope=SCOPE,
        owner_id=101,
        owner="example",
        name="repository",
        full_name="example/repository",
        default_branch=default_branch,
    )


def _state(*rules: EffectiveGovernanceRule) -> GovernanceState:
    return GovernanceState(
        repository=_repository(),
        api_version="2026-03-10",
        rules=tuple(sorted(rules, key=lambda item: item.canonical_json)),
    )


def _rule(
    *,
    rule_type: str = "required_status_checks",
    parameters: object | None = None,
) -> EffectiveGovernanceRule:
    value = {
        "parameters": {} if parameters is None else parameters,
        "ruleset_id": 41,
        "ruleset_source": "example/repository",
        "ruleset_source_type": "Repository",
        "type": rule_type,
    }
    return EffectiveGovernanceRule(
        rule_type=rule_type,
        ruleset_source_type="Repository",
        ruleset_source="example/repository",
        ruleset_id=41,
        canonical_json=canonical_json(value),
    )

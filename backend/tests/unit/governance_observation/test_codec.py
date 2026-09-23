from __future__ import annotations

import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_observation import (
    EffectiveGovernanceRule,
    GovernanceRepository,
    GovernanceState,
    decode_governance_state,
    encode_governance_state,
)
from ci_coordinator.kernel import canonical_json


def test_governance_state_round_trip_is_byte_exact_and_preserves_additive_fields() -> None:
    state = _state()

    encoded = encode_governance_state(state)
    decoded = decode_governance_state(encoded)

    assert decoded == state
    assert encode_governance_state(decoded) == encoded
    assert b'"future":{"mode":"strict"}' in encoded


@pytest.mark.parametrize(
    "mutated",
    [
        b'{"schemaVersion":"github-effective-governance-state/v1"}',
        (
            b'{"apiVersion":"2026-03-10","repository":{},"rules":[],"schemaVersion":'
            b'"github-effective-governance-state/v1","scope":{},"unknown":true}'
        ),
        (
            b'{"apiVersion":"2026-03-10","repository":{},"rules":[],"schemaVersion":'
            b'"unsupported","scope":{}}'
        ),
    ],
)
def test_governance_state_decoder_rejects_incomplete_additive_and_unsupported_shapes(
    mutated: bytes,
) -> None:
    with pytest.raises(ValueError, match="not admitted"):
        decode_governance_state(mutated)


def test_decoder_rejects_noncanonical_bytes() -> None:
    encoded = encode_governance_state(_state())

    with pytest.raises(ValueError, match="not admitted"):
        decode_governance_state(encoded + b" ")


def _state() -> GovernanceState:
    value = {
        "future": {"mode": "strict"},
        "parameters": {"required_status_checks": ["CI"]},
        "ruleset_id": 41,
        "ruleset_source": "example/repository",
        "ruleset_source_type": "Repository",
        "type": "required_status_checks",
    }
    rule = EffectiveGovernanceRule(
        rule_type="required_status_checks",
        ruleset_source_type="Repository",
        ruleset_source="example/repository",
        ruleset_id=41,
        canonical_json=canonical_json(value),
    )
    repository = GovernanceRepository(
        scope=RepositoryScope(7, 11),
        owner_id=101,
        owner="example",
        name="repository",
        full_name="example/repository",
        default_branch="master",
    )
    return GovernanceState(repository, "2026-03-10", (rule,))

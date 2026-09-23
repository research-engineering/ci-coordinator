from __future__ import annotations

import json
from copy import copy, deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from governance_state_support import governance_state as _state

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_baseline import (
    GOVERNANCE_BASELINE_APPROVED_EVENT_TYPE,
    GovernanceBaselineCommand,
    GovernanceBaselineDraft,
    GovernanceBaselinePointer,
    PreparedGovernanceBaseline,
    decode_governance_baseline_command,
    encode_governance_baseline_command,
    governance_baseline_reason_is_admitted,
    prepare_governance_baseline,
)
from ci_coordinator.governance_observation import (
    encode_governance_state,
)
from ci_coordinator.kernel import sha256_hex

SCOPE = RepositoryScope(7, 11)
NOW = datetime(2026, 7, 26, 10, tzinfo=UTC)


def test_prepared_baseline_binds_complete_identity_and_consumes_audit_once() -> None:
    draft = _draft()

    prepared = prepare_governance_baseline(draft, approved_at=NOW + timedelta(seconds=1))
    event = prepared._take_audit_event()
    payload = json.loads(event.payload_canonical_bytes)

    assert event.event_type == GOVERNANCE_BASELINE_APPROVED_EVENT_TYPE
    assert event.subject_id.startswith("governance-baseline:")
    assert event.actor == "owner:42"
    assert payload["stateDigest"] == draft.state_digest
    assert payload["reason"] == "Adopt repository governance"
    with pytest.raises(ValueError, match="already consumed"):
        prepared._take_audit_event()

    for operation in (copy, deepcopy):
        fresh = prepare_governance_baseline(draft, approved_at=NOW + timedelta(seconds=1))
        with pytest.raises(TypeError, match="cannot be copied"):
            operation(fresh)


def test_baseline_identity_binds_reason_actor_predecessor_and_state_bytes() -> None:
    draft = _draft()
    approved_at = NOW + timedelta(seconds=1)
    original = prepare_governance_baseline(draft, approved_at=approved_at)
    changed_state = _state(SCOPE, rule_type="pull_request")
    pointer = GovernanceBaselinePointer(
        SCOPE,
        "governance-baseline:" + "a" * 64,
        1,
        draft.state_digest,
    )
    variants = (
        replace(draft, command=replace(draft.command, actor="owner:43")),
        replace(draft, command=replace(draft.command, reason="A different decision")),
        replace(draft, command=replace(draft.command, expected_active=pointer)),
        replace(
            draft,
            command=replace(
                draft.command,
                expected_state_digest=sha256_hex(encode_governance_state(changed_state)),
            ),
            state=changed_state,
        ),
    )

    identities = {
        prepare_governance_baseline(candidate, approved_at=approved_at)
        ._take_audit_event()
        .subject_id
        for candidate in variants
    }

    assert original._take_audit_event().subject_id not in identities
    assert len(identities) == len(variants)


def test_command_and_prepared_capability_reject_unrepresentable_or_forged_inputs() -> None:
    with pytest.raises(ValueError, match="cannot contain NUL"):
        replace(_draft().command, operation_id="approve\0baseline")
    with pytest.raises(ValueError, match="reason"):
        replace(_draft().command, reason=" non-canonical ")
    valid_audit_event = prepare_governance_baseline(
        _draft(),
        approved_at=NOW,
    )._take_audit_event()
    with pytest.raises(TypeError, match="cannot be constructed"):
        PreparedGovernanceBaseline(
            object(),
            draft=_draft(),
            approved_at=NOW,
            audit_event=valid_audit_event,
        )


@pytest.mark.parametrize(
    "reason",
    [
        "",
        " padded",
        "padded\u00a0",
        "\ufeffhidden",
        "hidden\u3000",
        "\u001ccontrol",
        "control\u0085",
        "nul\0byte",
        "\ud800",
    ],
)
def test_reason_profile_rejects_every_noncanonical_boundary_class(reason: str) -> None:
    assert not governance_baseline_reason_is_admitted(reason)
    with pytest.raises(ValueError, match="reason"):
        replace(_draft().command, reason=reason)


@pytest.mark.parametrize("reason", ["\u00c4nderung genehmigt", "A\ufeffB", "exact reason"])
def test_reason_profile_admits_bounded_scalar_interior_text(reason: str) -> None:
    assert governance_baseline_reason_is_admitted(reason)
    assert replace(_draft().command, reason=reason).reason == reason


def test_command_codec_round_trips_exact_canonical_bytes() -> None:
    command = _draft().command

    encoded = encode_governance_baseline_command(command)

    assert decode_governance_baseline_command(encoded) == command
    with pytest.raises(ValueError, match="not admitted"):
        decode_governance_baseline_command(encoded + b" ")


def _draft() -> GovernanceBaselineDraft:
    state = _state(SCOPE)

    return GovernanceBaselineDraft(
        command=GovernanceBaselineCommand(
            scope=SCOPE,
            operation_id="approve-1",
            expected_state_digest=sha256_hex(encode_governance_state(state)),
            expected_active=None,
            actor="owner:42",
            reason="Adopt repository governance",
        ),
        state=state,
        observed_at=NOW,
    )

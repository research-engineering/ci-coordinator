from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import cast

import pytest
from control_plane_http_support import (
    ACTOR,
    StaticControlPlaneAuthenticator,
    StaticRoleAdmission,
    human_principal,
)
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    GovernanceComparisonRouteDependencies,
    HttpRouteDependencies,
    InvalidCredential,
)
from ci_coordinator.api.http.governance_comparison_contracts import (
    GovernanceComparisonErrorResponse,
    GovernanceComparisonResponse,
    governance_comparison_projection,
)
from ci_coordinator.api.http.routers.governance_comparisons import (
    GOVERNANCE_COMPARISON_PATH,
)
from ci_coordinator.app.governance_comparison import (
    GovernanceComparisonEvidence,
    GovernanceComparisonForbidden,
    GovernanceComparisonOutcome,
    GovernanceComparisonStale,
    GovernanceComparisonUseCase,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_baseline import (
    GovernanceBaselineCommand,
    GovernanceBaselineDraft,
    GovernanceBaselineRecord,
    prepare_governance_baseline,
)
from ci_coordinator.governance_comparison import compare_governance_states
from ci_coordinator.governance_observation import (
    EffectiveGovernanceRule,
    GovernanceObservation,
    GovernanceObservationUnavailable,
    GovernanceRepository,
    GovernanceState,
    encode_governance_state,
)
from ci_coordinator.kernel import canonical_json, sha256_hex

SCOPE = RepositoryScope(1, 2)
NOW = datetime(2026, 7, 26, 12, tzinfo=UTC)
PATH = GOVERNANCE_COMPARISON_PATH.format(installation_id=1, repository_id=2)
PUBLIC_ORIGIN = "https://ci.example.test"


@dataclass
class _UseCase:
    outcome: GovernanceComparisonOutcome
    calls: list[tuple[str, RepositoryScope]] = field(default_factory=list)

    async def __call__(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
    ) -> GovernanceComparisonOutcome:
        self.calls.append((actor, scope))
        return self.outcome


def test_browser_authentication_precedes_comparison_use_case() -> None:
    use_case = _UseCase(_evidence(None))

    response = _client(use_case, authenticated=False).get(PATH)

    assert response.status_code == 401
    assert response.json() == {
        "error": "unauthenticated",
        "ok": False,
        "retryAfterSeconds": None,
    }
    assert use_case.calls == []


@pytest.mark.parametrize("baseline", [None, "active"])
def test_route_projects_complete_noncacheable_evidence(baseline: str | None) -> None:
    record = None if baseline is None else _record(_state())
    use_case = _UseCase(_evidence(record))
    client = _client(use_case)

    response = client.get(PATH)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["scope"] == {"installationId": 1, "repositoryId": 2}
    assert body["observation"]["stateDigest"] == _digest(_state())
    if record is None:
        assert (body["state"], body["baseline"], body["comparison"]) == (
            "unbaselined",
            None,
            None,
        )
    else:
        assert body["state"] == "compared"
        assert body["baseline"]["pointer"]["baselineId"] == record.baseline_id
        assert body["comparison"] == {
            "addedRuleCount": 0,
            "baselineStateDigest": _digest(_state()),
            "changedCoordinates": [],
            "currentStateDigest": _digest(_state()),
            "relation": "matches",
            "removedRuleCount": 0,
        }
    assert use_case.calls[0][1] == SCOPE
    assert use_case.calls[0][0] == ACTOR


@pytest.mark.parametrize(
    ("outcome", "status_code", "error"),
    [
        (GovernanceComparisonForbidden(), 403, "forbidden"),
        (GovernanceComparisonStale(), 409, "stale"),
        (GovernanceObservationUnavailable("not_found"), 404, "not_found"),
        (GovernanceObservationUnavailable("rate_limited", 30), 429, "rate_limited"),
        (
            GovernanceObservationUnavailable("provider_binding_mismatch"),
            503,
            "provider_binding_mismatch",
        ),
        (
            GovernanceObservationUnavailable("unavailable"),
            503,
            "unavailable",
        ),
        (
            GovernanceObservationUnavailable("malformed_provider_response"),
            503,
            "malformed_provider_response",
        ),
        (
            GovernanceObservationUnavailable("observation_limit_exceeded"),
            503,
            "observation_limit_exceeded",
        ),
    ],
)
def test_failure_algebra_remains_distinct(
    outcome: GovernanceComparisonOutcome,
    status_code: int,
    error: str,
) -> None:
    client = _client(_UseCase(outcome))

    response = client.get(PATH)

    assert (response.status_code, response.json()["error"]) == (status_code, error)
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["retryAfterSeconds"] == (30 if error == "rate_limited" else None)


def test_error_contract_rejects_retry_delay_without_rate_limit() -> None:
    with pytest.raises(ValidationError, match="retry delay requires a rate limit"):
        GovernanceComparisonErrorResponse(
            ok=False,
            error="unavailable",
            retry_after_seconds=30,
        )


def test_response_model_rejects_cross_bound_digests_and_state() -> None:
    body = _response_body(_evidence(_record(_state())))
    candidates = []
    wrong_digest = deepcopy(body)
    cast(dict[str, object], wrong_digest["comparison"])["current_state_digest"] = "f" * 64
    candidates.append(wrong_digest)
    wrong_state = deepcopy(body)
    wrong_state["state"] = "unbaselined"
    candidates.append(wrong_state)
    empty_difference = deepcopy(body)
    cast(dict[str, object], empty_difference["comparison"])["relation"] = "differs"
    candidates.append(empty_difference)

    for candidate in candidates:
        with pytest.raises(ValidationError):
            GovernanceComparisonResponse.model_validate(candidate)


def _client(
    use_case: GovernanceComparisonUseCase,
    *,
    authenticated: bool = True,
) -> TestClient:
    return TestClient(
        create_app(
            HttpRouteDependencies(
                governance_comparison=GovernanceComparisonRouteDependencies(
                    authenticator=StaticControlPlaneAuthenticator(
                        human_principal() if authenticated else InvalidCredential()
                    ),
                    role_admission=StaticRoleAdmission(),
                    use_case=use_case,
                ),
            )
        ),
        base_url=PUBLIC_ORIGIN,
    )


def _response_body(evidence: GovernanceComparisonEvidence) -> dict[str, object]:
    return governance_comparison_projection(evidence).model_dump(mode="json")


def _evidence(record: GovernanceBaselineRecord | None) -> GovernanceComparisonEvidence:
    state = _state()
    observation = GovernanceObservation.from_state(state, observed_at=NOW)
    return GovernanceComparisonEvidence(
        observation=observation,
        baseline=record,
        comparison=None if record is None else compare_governance_states(record.state, state),
    )


def _record(state: GovernanceState) -> GovernanceBaselineRecord:
    command = GovernanceBaselineCommand(
        scope=SCOPE,
        operation_id="approve-1",
        expected_state_digest=_digest(state),
        expected_active=None,
        actor=ACTOR,
        reason="Adopt repository governance",
    )
    draft = GovernanceBaselineDraft(command, state, NOW)
    prepared = prepare_governance_baseline(draft, approved_at=NOW + timedelta(seconds=1))
    return GovernanceBaselineRecord.from_draft(
        draft,
        approved_at=prepared.approved_at,
        audit_event_id="audit_" + "a" * 32,
        audit_input_hash=prepared.audit_input_hash,
    )


def _digest(state: GovernanceState) -> str:
    return sha256_hex(encode_governance_state(state))


def _state() -> GovernanceState:
    value = {
        "ruleset_id": 41,
        "ruleset_source": "example/repository",
        "ruleset_source_type": "Repository",
        "type": "required_status_checks",
    }
    return GovernanceState(
        GovernanceRepository(SCOPE, 101, "example", "repository", "example/repository", "master"),
        "2026-03-10",
        (
            EffectiveGovernanceRule(
                "required_status_checks",
                "Repository",
                "example/repository",
                41,
                canonical_json(value),
            ),
        ),
    )

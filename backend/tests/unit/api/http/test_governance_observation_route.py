from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from control_plane_http_support import (
    ACTOR,
    StaticControlPlaneAuthenticator,
    StaticRoleAdmission,
    human_principal,
)
from fastapi.testclient import TestClient
from pydantic import BaseModel

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    GovernanceObservationRouteDependencies,
    HttpRouteDependencies,
    InvalidCredential,
)
from ci_coordinator.api.http.governance_observation_contracts import (
    GovernanceObservationErrorResponse,
    GovernanceObservationResponse,
)
from ci_coordinator.api.http.governance_state_contracts import (
    EffectiveGovernanceRuleResponse,
    GovernanceRepositoryResponse,
    GovernanceScopeResponse,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_observation import (
    EffectiveGovernanceRule,
    GovernanceObservation,
    GovernanceObservationForbidden,
    GovernanceObservationOutcome,
    GovernanceObservationUnavailable,
    GovernanceObservationUseCase,
    GovernanceRepository,
    GovernanceState,
)
from ci_coordinator.kernel import canonical_json

NOW = datetime(2026, 7, 26, 12, tzinfo=UTC)
SCOPE = RepositoryScope(1, 2)


@dataclass
class _UseCase:
    outcome: GovernanceObservationOutcome
    calls: list[tuple[str, RepositoryScope]] = field(default_factory=list)

    async def __call__(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
    ) -> GovernanceObservationOutcome:
        self.calls.append((actor, scope))
        return self.outcome


def test_route_projects_canonical_unbaselined_traversal_observation() -> None:
    use_case = _UseCase(_observation())

    response = _client("operator", use_case).get(
        "/api/v1/workbench/repositories/1/2/governance-observation"
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "apiVersion": "2026-03-10",
        "baselineState": "unbaselined",
        "consistency": "best_effort",
        "observedAt": "2026-07-26T12:00:00Z",
        "ok": True,
        "repository": {
            "defaultBranch": "master",
            "fullName": "example/repository",
            "name": "repository",
            "owner": "example",
            "ownerId": 101,
            "scope": {"installationId": 1, "repositoryId": 2},
        },
        "rules": [
            {
                "canonicalJson": (
                    '{"parameters":{"required_status_checks":["CI"]},"ruleset_id":41,'
                    '"ruleset_source":"example/repository","ruleset_source_type":"Repository",'
                    '"type":"required_status_checks"}'
                ),
                "ruleType": "required_status_checks",
                "rulesetId": 41,
                "rulesetSource": "example/repository",
                "rulesetSourceType": "Repository",
            }
        ],
        "stateDigest": _observation().state_digest,
    }
    assert use_case.calls == [(ACTOR, SCOPE)]


def test_authentication_and_path_admission_precede_use_case() -> None:
    unauthenticated = _UseCase(_observation())
    invalid = _UseCase(_observation())

    auth_response = _client(None, unauthenticated).get(
        "/api/v1/workbench/repositories/1/2/governance-observation"
    )
    invalid_response = _client("operator", invalid).get(
        "/api/v1/workbench/repositories/0/2/governance-observation"
    )

    assert (auth_response.status_code, auth_response.json()["error"]) == (
        401,
        "unauthenticated",
    )
    assert invalid_response.status_code == 422
    assert unauthenticated.calls == []
    assert invalid.calls == []


def test_domain_failures_keep_distinct_transport_semantics() -> None:
    cases = (
        (GovernanceObservationForbidden(), 403, "forbidden", None),
        (GovernanceObservationUnavailable("not_found"), 404, "not_found", None),
        (GovernanceObservationUnavailable("rate_limited", 30), 429, "rate_limited", 30),
        (
            GovernanceObservationUnavailable("provider_binding_mismatch"),
            503,
            "provider_binding_mismatch",
            None,
        ),
        (
            GovernanceObservationUnavailable("observation_limit_exceeded"),
            503,
            "observation_limit_exceeded",
            None,
        ),
    )
    for outcome, status_code, error, retry_after in cases:
        response = _client("operator", _UseCase(outcome)).get(
            "/api/v1/workbench/repositories/1/2/governance-observation"
        )
        assert response.status_code == status_code
        assert response.json() == {
            "error": error,
            "ok": False,
            "retryAfterSeconds": retry_after,
        }
        assert response.headers["cache-control"] == "no-store"


def test_unexpected_failure_is_a_redacted_internal_error() -> None:
    class _Failure:
        async def __call__(
            self,
            *,
            actor: str,
            scope: RepositoryScope,
        ) -> GovernanceObservationOutcome:
            del actor, scope
            raise RuntimeError("provider body")

    response = _client("operator", _Failure(), raise_server_exceptions=False).get(
        "/api/v1/workbench/repositories/1/2/governance-observation"
    )

    assert response.status_code == 500
    assert response.json() == {"code": "internal_error"}
    assert "provider body" not in response.text


def _observation() -> GovernanceObservation:
    value = {
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
    state = GovernanceState(
        GovernanceRepository(
            scope=SCOPE,
            owner_id=101,
            owner="example",
            name="repository",
            full_name="example/repository",
            default_branch="master",
        ),
        "2026-03-10",
        (rule,),
    )
    return GovernanceObservation.from_state(state, observed_at=NOW)


def _response_payload() -> dict[str, object]:
    observation = _observation()
    return {
        "apiVersion": observation.api_version,
        "baselineState": observation.baseline_state,
        "consistency": observation.consistency,
        "observedAt": observation.observed_at,
        "ok": True,
        "repository": {
            "defaultBranch": observation.repository.default_branch,
            "fullName": observation.repository.full_name,
            "name": observation.repository.name,
            "owner": observation.repository.owner,
            "ownerId": observation.repository.owner_id,
            "scope": {
                "installationId": observation.repository.scope.installation_id,
                "repositoryId": observation.repository.scope.repository_id,
            },
        },
        "rules": [
            {
                "canonicalJson": rule.canonical_text,
                "ruleType": rule.rule_type,
                "rulesetId": rule.ruleset_id,
                "rulesetSource": rule.ruleset_source,
                "rulesetSourceType": rule.ruleset_source_type,
            }
            for rule in observation.rules
        ],
        "stateDigest": observation.state_digest,
    }


@pytest.mark.parametrize(
    ("model", "candidate"),
    [
        (
            GovernanceScopeResponse,
            {"installationId": 0, "repositoryId": 2},
        ),
        (
            GovernanceRepositoryResponse,
            {
                "defaultBranch": "master",
                "fullName": "/repository",
                "name": "repository",
                "owner": "",
                "ownerId": 101,
                "scope": {"installationId": 1, "repositoryId": 2},
            },
        ),
        (
            GovernanceRepositoryResponse,
            {
                "defaultBranch": "../bad",
                "fullName": "example/repository",
                "name": "repository",
                "owner": "example",
                "ownerId": 101,
                "scope": {"installationId": 1, "repositoryId": 2},
            },
        ),
        (
            EffectiveGovernanceRuleResponse,
            {
                "canonicalJson": "{}",
                "ruleType": "",
                "rulesetId": 0,
                "rulesetSource": "example/repository",
                "rulesetSourceType": "Repository",
            },
        ),
        (
            GovernanceObservationResponse,
            {
                **_response_payload(),
                "apiVersion": "unsupported",
            },
        ),
        (
            GovernanceObservationResponse,
            {
                **_response_payload(),
                "stateDigest": "bad",
            },
        ),
        (
            GovernanceObservationErrorResponse,
            {"error": "rate_limited", "ok": False, "retryAfterSeconds": 3_601},
        ),
    ],
)
def test_response_dtos_reject_values_outside_the_published_contract(
    model: type[BaseModel],
    candidate: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        model.model_validate(candidate, by_alias=True, by_name=False)


def _client(
    actor: str | None,
    use_case: GovernanceObservationUseCase,
    *,
    raise_server_exceptions: bool = True,
) -> TestClient:
    return TestClient(
        create_app(
            HttpRouteDependencies(
                governance_observation=GovernanceObservationRouteDependencies(
                    authenticator=StaticControlPlaneAuthenticator(
                        InvalidCredential() if actor is None else human_principal()
                    ),
                    role_admission=StaticRoleAdmission(),
                    use_case=use_case,
                )
            )
        ),
        raise_server_exceptions=raise_server_exceptions,
    )

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

import pytest
from control_plane_http_support import (
    ACTOR,
    StaticControlPlaneAuthenticator,
    StaticRoleAdmission,
    human_principal,
)
from fastapi.testclient import TestClient

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    HttpRouteDependencies,
    InvalidCredential,
    WorkflowDiscoveryRouteDependencies,
)
from ci_coordinator.api.http.routers import workflow_discovery as workflow_discovery_router
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.workflow_discovery import (
    RepositoryIdentity,
    RepositoryWorkflowSnapshot,
    WorkflowDiscoveryCompleted,
    WorkflowDiscoveryForbidden,
    WorkflowDiscoveryInvalidRequest,
    WorkflowDiscoveryOutcome,
    WorkflowDiscoveryService,
    WorkflowDiscoveryUnavailable,
    WorkflowDiscoveryUseCase,
    WorkflowSource,
)
from ci_coordinator.workflow_discovery.source import git_blob_sha1

SCOPE = RepositoryScope(1, 2)
REVISION = "a" * 40
DISCOVERY_URL = "/api/v1/workbench/repositories/1/2/workflow-discovery"


@dataclass
class _UseCase:
    outcome: WorkflowDiscoveryOutcome
    calls: list[tuple[str, RepositoryScope, str | None]] = field(default_factory=list)

    async def __call__(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        revision: str | None,
    ) -> WorkflowDiscoveryOutcome:
        self.calls.append((actor, scope, revision))
        return self.outcome


def test_route_projects_exact_snapshot_unknowns_and_conservative_proposal() -> None:
    use_case = _UseCase(_completed())

    response = _client("operator", use_case).get(
        "/api/v1/workbench/repositories/1/2/workflow-discovery"
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["repository"] == {
        "scope": {"installationId": 1, "repositoryId": 2},
        "owner": "example",
        "name": "repo",
        "defaultBranch": "master",
    }
    assert body["revision"] == REVISION
    assert body["complete"] is True
    assert body["localGraphClosed"] is True
    assert body["proposal"]["state"] == "reviewable"
    assert body["proposal"]["selectedEvents"] == ["push"]
    assert '"dynamicCi":null' in body["proposal"]["policySource"]
    assert body["proposal"]["admittedEpochId"] is not None
    assert body["unknowns"]
    edge = body["callEdges"][0]
    assert edge["provenance"]["workflowPath"] == edge["callerWorkflowPath"]
    assert edge["provenance"]["location"]["path"] == "job.uses"
    graph_unknown = next(item for item in body["unknowns"] if item["field"] == "call.target")
    assert graph_unknown["provenance"]["location"]["path"] == "job.uses"
    passport = body["componentPassport"]
    assert passport["inventoryDigest"] == body["inventoryDigest"]
    assert passport["revision"] == REVISION
    assert passport["declarationsComplete"] is True
    assert passport["references"] == [
        {
            "referenceId": passport["references"][0]["referenceId"],
            "callerWorkflowPath": ".github/workflows/reuse.yml",
            "callerJobId": "call",
            "stepIndex": None,
            "uses": "example/shared/.github/workflows/python.yml@main",
            "kind": "reusable_workflow",
            "repository": "example/shared",
            "path": ".github/workflows/python.yml",
            "ref": "main",
            "digest": None,
            "immutable": False,
            "resolution": "unresolved",
            "unresolvedReason": "resolution_not_requested",
            "resolvedRevision": None,
            "resolvedBlobSha": None,
            "resolvedAt": None,
        }
    ]
    assert len(passport["nonClaims"]) == 6
    assert "pytest" not in response.text
    assert use_case.calls == [(ACTOR, SCOPE, None)]


def test_route_projects_provider_observed_component_time_exactly() -> None:
    completed = _completed()
    (reference,) = completed.component_passport.references
    observed = replace(
        reference,
        resolution="observed",
        unresolved_reason=None,
        resolved_revision="c" * 40,
        resolved_blob_sha="d" * 40,
        resolved_at=datetime(2026, 10, 11, 12, tzinfo=UTC),
    )
    use_case = _UseCase(
        replace(
            completed,
            component_passport=replace(completed.component_passport, references=(observed,)),
        )
    )

    response = _client("operator", use_case).get(
        "/api/v1/workbench/repositories/1/2/workflow-discovery"
    )

    assert response.status_code == 200
    (projected,) = response.json()["componentPassport"]["references"]
    assert projected["resolution"] == "observed"
    assert projected["resolvedRevision"] == "c" * 40
    assert projected["resolvedBlobSha"] == "d" * 40
    assert projected["resolvedAt"] == "2026-10-11T12:00:00Z"


def test_route_omits_passport_references_exactly_above_the_response_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _client("operator", _UseCase(_completed()))
    complete = client.get(DISCOVERY_URL)
    assert complete.json()["componentPassport"]["referencesOmitted"] is False
    budget = len(complete.content)

    monkeypatch.setattr(workflow_discovery_router, "WORKFLOW_DISCOVERY_RESPONSE_MAX_BYTES", budget)
    at_budget = client.get(DISCOVERY_URL)
    monkeypatch.setattr(
        workflow_discovery_router, "WORKFLOW_DISCOVERY_RESPONSE_MAX_BYTES", budget - 1
    )
    above_budget = client.get(DISCOVERY_URL)

    assert at_budget.content == complete.content
    assert above_budget.status_code == 200
    assert len(above_budget.content) <= budget - 1
    omitted = above_budget.json()
    assert omitted["componentPassport"]["referencesOmitted"] is True
    assert omitted["componentPassport"]["references"] == []
    expected = complete.json()
    expected["componentPassport"].update(references=[], referencesOmitted=True)
    assert omitted == expected


def test_large_action_inventory_fits_the_browser_response_budget() -> None:
    steps = "      - uses: actions/checkout@v4\n" * 1_000
    jobs = "".join(
        f"  job{index}:\n    runs-on: ubuntu-latest\n    steps:\n{steps}" for index in range(6)
    )
    content = f"name: CI\non: push\njobs:\n{jobs}".encode()
    outcome = _discover(
        WorkflowSource(".github/workflows/ci.yml", git_blob_sha1(content), len(content), content)
    )
    assert len(outcome.component_passport.references) == 6_000

    response = _client("operator", _UseCase(outcome)).get(DISCOVERY_URL)

    assert response.status_code == 200
    assert len(response.content) <= workflow_discovery_router.WORKFLOW_DISCOVERY_RESPONSE_MAX_BYTES
    body = response.json()
    assert body["componentPassport"]["referencesOmitted"] is True
    assert body["componentPassport"]["references"] == []
    assert body["componentPassport"]["declarationsComplete"] is True
    assert [len(job["steps"]) for job in body["workflows"][0]["jobs"]] == [1_000] * 6


def test_route_preserves_historical_inventory_without_reviewable_policy() -> None:
    use_case = _UseCase(_completed(revision=REVISION))

    response = _client("operator", use_case).get(
        f"/api/v1/workbench/repositories/1/2/workflow-discovery?revision={REVISION}"
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    proposal = response.json()["proposal"]
    assert proposal["state"] == "blocked"
    assert proposal["policySource"] is None
    assert proposal["blockers"] == ["default_branch_head_unproven"]
    assert use_case.calls == [(ACTOR, SCOPE, REVISION)]


def test_authentication_and_revision_admission_precede_use_case() -> None:
    unauthenticated = _UseCase(_completed())
    invalid = _UseCase(_completed())

    auth_response = _client(None, unauthenticated).get(
        "/api/v1/workbench/repositories/1/2/workflow-discovery"
    )
    invalid_response = _client("operator", invalid).get(
        "/api/v1/workbench/repositories/1/2/workflow-discovery?revision=main"
    )

    assert (auth_response.status_code, auth_response.json()["error"]) == (
        401,
        "unauthenticated",
    )
    assert auth_response.headers["cache-control"] == "no-store"
    assert invalid_response.status_code == 422
    assert invalid_response.headers["cache-control"] == "no-store"
    assert unauthenticated.calls == []
    assert invalid.calls == []


def test_domain_failures_keep_distinct_transport_semantics() -> None:
    cases = (
        (WorkflowDiscoveryForbidden(), 403, "forbidden"),
        (WorkflowDiscoveryInvalidRequest("invalid_revision"), 422, "invalid_revision"),
        (WorkflowDiscoveryUnavailable("not_found"), 404, "not_found"),
        (WorkflowDiscoveryUnavailable("rate_limited"), 429, "rate_limited"),
        (WorkflowDiscoveryUnavailable("overloaded"), 503, "overloaded"),
        (
            WorkflowDiscoveryUnavailable("provider_binding_mismatch"),
            503,
            "provider_binding_mismatch",
        ),
    )
    for outcome, status, error in cases:
        response = _client("operator", _UseCase(outcome)).get(
            "/api/v1/workbench/repositories/1/2/workflow-discovery"
        )
        assert (response.status_code, response.json()["error"]) == (status, error)
        assert response.headers["cache-control"] == "no-store"


def test_unexpected_failure_is_a_redacted_internal_error() -> None:
    class _Failure:
        async def __call__(
            self,
            *,
            actor: str,
            scope: RepositoryScope,
            revision: str | None,
        ) -> WorkflowDiscoveryOutcome:
            raise RuntimeError("provider body")

    response = _client(
        "operator",
        _Failure(),
        raise_server_exceptions=False,
    ).get("/api/v1/workbench/repositories/1/2/workflow-discovery")

    assert (response.status_code, response.json()) == (
        500,
        {"code": "internal_error"},
    )
    assert "provider body" not in response.text


def _client(
    actor: str | None,
    use_case: WorkflowDiscoveryUseCase,
    *,
    raise_server_exceptions: bool = True,
) -> TestClient:
    return TestClient(
        create_app(
            HttpRouteDependencies(
                workflow_discovery=WorkflowDiscoveryRouteDependencies(
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


def _completed(*, revision: str | None = None) -> WorkflowDiscoveryCompleted:
    ci_content = (
        b"name: CI\non:\n  push:\n  workflow_dispatch:\njobs:\n"
        b"  test:\n    name: Full CI\n    steps:\n      - run: pytest\n"
    )
    ci_source = WorkflowSource(
        ".github/workflows/ci.yml",
        git_blob_sha1(ci_content),
        len(ci_content),
        ci_content,
    )
    reusable_content = (
        b"name: Reuse\non: workflow_call\njobs:\n  call:\n"
        b"    uses: example/shared/.github/workflows/python.yml@main\n"
    )
    reusable_source = WorkflowSource(
        ".github/workflows/reuse.yml",
        git_blob_sha1(reusable_content),
        len(reusable_content),
        reusable_content,
    )
    return _discover(ci_source, reusable_source, revision=revision)


def _discover(
    *sources: WorkflowSource,
    revision: str | None = None,
) -> WorkflowDiscoveryCompleted:
    snapshot = RepositoryWorkflowSnapshot.create(
        repository=RepositoryIdentity(SCOPE, "example", "repo", "master"),
        revision=REVISION,
        sources=sources,
    )

    class _Allow:
        async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
            return True

    class _Read:
        async def read(
            self,
            *,
            scope: RepositoryScope,
            revision: str | None,
        ) -> RepositoryWorkflowSnapshot:
            return snapshot

    outcome = asyncio.run(
        WorkflowDiscoveryService(authorizer=_Allow(), reader=_Read())(
            actor="operator",
            scope=SCOPE,
            revision=revision,
        )
    )
    if not isinstance(outcome, WorkflowDiscoveryCompleted):
        raise AssertionError("fixture must produce completed workflow discovery")
    return outcome

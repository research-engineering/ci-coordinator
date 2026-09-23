from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

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
    assert "pytest" not in response.text
    assert use_case.calls == [(ACTOR, SCOPE, None)]


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
    snapshot = RepositoryWorkflowSnapshot.create(
        repository=RepositoryIdentity(SCOPE, "example", "repo", "master"),
        revision=REVISION,
        sources=(ci_source, reusable_source),
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

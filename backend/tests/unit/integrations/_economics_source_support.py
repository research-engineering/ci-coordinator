from __future__ import annotations

import json
from dataclasses import dataclass, field

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import (
    GitHubPaginationEvidence,
    GitHubRequest,
    GitHubResponse,
    GitHubTransportResult,
)

SCOPE = RepositoryScope(101, 202)
RUN_ID = 303
ATTEMPT = 2
PATH = "/repos/acme/service/actions/runs/303/attempts/2"


@dataclass
class Provider:
    responses: list[GitHubTransportResult | BaseException]
    requests: list[GitHubRequest] = field(default_factory=list)
    installations: list[int] = field(default_factory=list)

    def for_installation(self, installation_id: int) -> Provider:
        self.installations.append(installation_id)
        return self

    async def send(self, request: GitHubRequest) -> GitHubTransportResult:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def response(value: object) -> GitHubResponse:
    return GitHubResponse(
        200,
        GITHUB_API_VERSION,
        (),
        json.dumps(value).encode(),
        GitHubPaginationEvidence.not_paginated(),
    )


def repository() -> dict[str, object]:
    return {
        "id": SCOPE.repository_id,
        "name": "service",
        "full_name": "acme/service",
        "owner": {"login": "acme"},
    }


def run() -> dict[str, object]:
    return {
        "id": RUN_ID,
        "run_attempt": ATTEMPT,
        "repository": {"id": SCOPE.repository_id},
        "head_sha": "b" * 40,
        "created_at": "2026-09-07T12:00:00Z",
        "status": "completed",
        "conclusion": "failure",
    }

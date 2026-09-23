"""GitHub App recheck of one retained reviewer permission."""

from __future__ import annotations

import re

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import GitHubReviewerEvidence, ReviewerPermission
from ci_coordinator.integrations.github._client import GitHubProtocolClient
from ci_coordinator.integrations.github._response_decoding import (
    json_object_or_none,
    object_or_none,
    positive_safe_integer,
)
from ci_coordinator.integrations.github._reviewer_response import admit_reviewer_success
from ci_coordinator.integrations.github._routes import (
    GitHubRepository,
    path_value,
    repository_id_path,
)
from ci_coordinator.integrations.github.app_transport import GitHubAppTransportFactory
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import GitHubOutcome
from ci_coordinator.proposal_review import GitHubReviewerRejected, GitHubReviewerUnavailable

_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")


class _ReviewerPermissionClient(GitHubProtocolClient):
    async def get_repository(self, repository_id: int) -> GitHubOutcome:
        return await self._get(
            operation="repositories.get_by_id",
            path=repository_id_path(repository_id),
        )

    async def get_permission(
        self,
        repository: GitHubRepository,
        reviewer_login: str,
    ) -> GitHubOutcome:
        return await self._get(
            operation="reviewer_attestation.get_permission",
            path=f"{repository.path}/collaborators/{path_value(reviewer_login)}/permission",
        )


class GitHubReviewerPermissionReaderAdapter:
    def __init__(self, transport_factory: GitHubAppTransportFactory) -> None:
        self._transport_factory = transport_factory

    async def recheck(
        self,
        *,
        scope: RepositoryScope,
        reviewer_user_id: int,
        reviewer_login: str,
    ) -> GitHubReviewerEvidence:
        if type(scope) is not RepositoryScope:
            raise TypeError("GitHub reviewer recheck scope must be exact")
        if (
            type(reviewer_user_id) is not int
            or reviewer_user_id < 1
            or type(reviewer_login) is not str
            or _LOGIN.fullmatch(reviewer_login) is None
        ):
            raise GitHubReviewerRejected("GitHub reviewer identity is invalid")
        client = _ReviewerPermissionClient(
            self._transport_factory.for_installation(scope.installation_id),
            api_version=GITHUB_API_VERSION,
        )
        repository = await _resolve_repository(client, scope.repository_id)
        return await _resolve_reviewer(
            client,
            repository=repository,
            reviewer_user_id=reviewer_user_id,
            reviewer_login=reviewer_login,
        )


async def _resolve_repository(
    client: _ReviewerPermissionClient,
    repository_id: int,
) -> GitHubRepository:
    path = repository_id_path(repository_id)
    success = admit_reviewer_success(
        await client.get_repository(repository_id),
        operation="repositories.get_by_id",
        path=path,
    )
    value = json_object_or_none(success.response.body)
    owner = None if value is None else object_or_none(value.get("owner"))
    observed_id = None if value is None else positive_safe_integer(value.get("id"))
    name = None if value is None else value.get("name")
    owner_login = None if owner is None else owner.get("login")
    if (
        observed_id != repository_id
        or type(name) is not str
        or not name
        or type(owner_login) is not str
        or not owner_login
    ):
        raise GitHubReviewerUnavailable("GitHub reviewer repository identity is malformed")
    try:
        return GitHubRepository(owner_login, name)
    except ValueError as error:
        raise GitHubReviewerUnavailable(
            "GitHub reviewer repository identity is malformed"
        ) from error


async def _resolve_reviewer(
    client: _ReviewerPermissionClient,
    *,
    repository: GitHubRepository,
    reviewer_user_id: int,
    reviewer_login: str,
) -> GitHubReviewerEvidence:
    path = f"{repository.path}/collaborators/{path_value(reviewer_login)}/permission"
    success = admit_reviewer_success(
        await client.get_permission(repository, reviewer_login),
        operation="reviewer_attestation.get_permission",
        path=path,
    )
    value = json_object_or_none(success.response.body)
    user = None if value is None else object_or_none(value.get("user"))
    observed_id = None if user is None else positive_safe_integer(user.get("id"))
    observed_login = None if user is None else user.get("login")
    permission = (
        None if value is None else _permission(value.get("permission"), value.get("role_name"))
    )
    if observed_id != reviewer_user_id or observed_login != reviewer_login or permission is None:
        raise GitHubReviewerRejected("GitHub reviewer permission changed or is unavailable")
    return GitHubReviewerEvidence(
        user_id=observed_id,
        login=observed_login,
        permission=permission,
    )


def _permission(permission: object, role_name: object) -> ReviewerPermission | None:
    if role_name == "admin" and permission == "admin":
        return "admin"
    if role_name == "maintain" and permission == "write":
        return "maintain"
    return None

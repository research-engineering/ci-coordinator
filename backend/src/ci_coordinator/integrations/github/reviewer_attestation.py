"""Ephemeral GitHub reviewer identity and repository-permission proof."""

from __future__ import annotations

import asyncio
import re
from urllib.parse import urlencode, urlsplit

import httpx2 as httpx

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import GitHubReviewerEvidence, ReviewerPermission
from ci_coordinator.integrations.github._client import GitHubProtocolClient
from ci_coordinator.integrations.github._response_decoding import (
    json_object_or_none,
    object_or_none,
    positive_safe_integer,
)
from ci_coordinator.integrations.github._reviewer_response import admit_reviewer_success
from ci_coordinator.integrations.github._routes import repository_id_path
from ci_coordinator.integrations.github.app_http import (
    bounded_raw_response_body,
    response_admission_failure,
)
from ci_coordinator.integrations.github.app_transport import GitHubAppTransportFactory
from ci_coordinator.integrations.github.app_transport_profile import (
    GITHUB_API_USER_AGENT,
    GITHUB_API_VERSION,
    GITHUB_REQUEST_TIMEOUT_SECONDS,
)
from ci_coordinator.integrations.github.contracts import (
    GitHubOutcome,
)
from ci_coordinator.kernel import NoQueueAdmission, StrictJsonError, load_strict_json
from ci_coordinator.proposal_review import GitHubReviewerRejected, GitHubReviewerUnavailable

_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_OAUTH_BASE_URL = "https://github.com"
_TOKEN_PATH = "/login/oauth/access_token"  # noqa: S105 - provider route
_MAX_OAUTH_RESPONSE_BYTES = 64 * 1024
_MAX_TOKEN_LIFETIME_SECONDS = 86_400
_MAX_CONCURRENT_EXCHANGES = 4
_CLIENT_ID = re.compile(r"[A-Za-z0-9._-]{1,255}")
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?")


class _ReviewerClient(GitHubProtocolClient):
    async def get_user(self) -> GitHubOutcome:
        return await self._get(operation="reviewer_attestation.get_user", path="/user")

    async def get_repository(self, repository_id: int) -> GitHubOutcome:
        return await self._get(
            operation="reviewer_attestation.get_repository",
            path=repository_id_path(repository_id),
        )


class GitHubReviewerProviderAdapter:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        transport_factory: GitHubAppTransportFactory,
        oauth_transport: httpx.AsyncBaseTransport | None = None,
        outbound_proxy_url: str | None = None,
    ) -> None:
        if type(client_id) is not str or _CLIENT_ID.fullmatch(client_id) is None:
            raise ValueError("GitHub reviewer client id is invalid")
        if (
            type(client_secret) is not str
            or not client_secret
            or not client_secret.isascii()
            or len(client_secret) > 512
        ):
            raise ValueError("GitHub reviewer client secret is invalid")
        _require_redirect_uri(redirect_uri)
        if oauth_transport is not None and outbound_proxy_url is not None:
            raise ValueError("GitHub reviewer transport and proxy are mutually exclusive")
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._transport_factory = transport_factory
        self._oauth_transport = oauth_transport
        self._outbound_proxy_url = outbound_proxy_url
        self._admission = NoQueueAdmission(_MAX_CONCURRENT_EXCHANGES)

    def authorization_url(self, *, state: str, code_challenge: str) -> str:
        if not _base64url_256(state) or not _base64url_256(code_challenge):
            raise ValueError("GitHub reviewer OAuth values are not canonical")
        return f"{_AUTHORIZE_URL}?{
            urlencode(
                {
                    'allow_signup': 'false',
                    'client_id': self._client_id,
                    'code_challenge': code_challenge,
                    'code_challenge_method': 'S256',
                    'prompt': 'select_account',
                    'redirect_uri': self._redirect_uri,
                    'state': state,
                }
            )
        }"

    async def exchange_and_resolve(
        self,
        *,
        code: str,
        code_verifier: str,
        scope: RepositoryScope,
    ) -> GitHubReviewerEvidence:
        if type(scope) is not RepositoryScope:
            raise TypeError("GitHub reviewer scope must be exact")
        if not _bounded_ascii(code, maximum=512) or not _base64url_256(code_verifier):
            raise GitHubReviewerRejected("GitHub reviewer callback is invalid")
        lease = self._admission.try_acquire()
        if lease is None:
            raise GitHubReviewerUnavailable("GitHub reviewer admission is overloaded")
        try:
            token = await self._exchange_code(code=code, code_verifier=code_verifier)
            client = _ReviewerClient(
                self._transport_factory.for_reviewer(token),
                api_version=GITHUB_API_VERSION,
            )
            identity = await _resolve_identity(client)
            permission = await _resolve_permission(client, scope)
            return GitHubReviewerEvidence(
                user_id=identity[0],
                login=identity[1],
                permission=permission,
            )
        finally:
            lease.release()

    async def _exchange_code(self, *, code: str, code_verifier: str) -> str:
        body = urlencode(
            {
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
                "code_verifier": code_verifier,
                "redirect_uri": self._redirect_uri,
            }
        ).encode("ascii")
        try:
            async with (
                httpx.AsyncClient(
                    base_url=_OAUTH_BASE_URL,
                    follow_redirects=False,
                    timeout=GITHUB_REQUEST_TIMEOUT_SECONDS,
                    transport=self._oauth_transport,
                    proxy=self._outbound_proxy_url,
                    trust_env=False,
                    verify=True,
                ) as client,
                asyncio.timeout(GITHUB_REQUEST_TIMEOUT_SECONDS),
                client.stream(
                    "POST",
                    _TOKEN_PATH,
                    headers={
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                        "Content-Type": "application/x-www-form-urlencoded",
                        "User-Agent": GITHUB_API_USER_AGENT,
                    },
                    content=body,
                ) as response,
            ):
                framing_failure = response_admission_failure(
                    response,
                    maximum_bytes=_MAX_OAUTH_RESPONSE_BYTES,
                )
                if framing_failure is not None:
                    raise GitHubReviewerUnavailable(
                        "GitHub reviewer OAuth response framing is invalid"
                    )
                if (
                    300 <= response.status_code < 400
                    or response.status_code in {408, 429}
                    or response.status_code >= 500
                ):
                    raise GitHubReviewerUnavailable("GitHub reviewer OAuth exchange is unavailable")
                if response.status_code != 200:
                    raise GitHubReviewerRejected("GitHub reviewer OAuth exchange was rejected")
                content_types = tuple(
                    value
                    for name, value in response.headers.multi_items()
                    if name.lower() == "content-type"
                )
                if len(content_types) != 1 or not _is_json_media_type(content_types[0]):
                    raise GitHubReviewerUnavailable(
                        "GitHub reviewer OAuth response media type is invalid"
                    )
                response_body = await bounded_raw_response_body(
                    response,
                    maximum_bytes=_MAX_OAUTH_RESPONSE_BYTES,
                )
                if response_body is None:
                    raise GitHubReviewerUnavailable(
                        "GitHub reviewer OAuth response exceeded its bound"
                    )
                if self._client_secret.encode("ascii") in response_body:
                    raise GitHubReviewerUnavailable(
                        "GitHub reviewer OAuth response contained credential material"
                    )
                return _decode_token(response_body)
        except asyncio.CancelledError:
            raise
        except (GitHubReviewerRejected, GitHubReviewerUnavailable):
            raise
        except (TimeoutError, httpx.HTTPError) as error:
            raise GitHubReviewerUnavailable(
                "GitHub reviewer OAuth exchange is unavailable"
            ) from error


async def _resolve_identity(client: _ReviewerClient) -> tuple[int, str]:
    outcome = await client.get_user()
    success = admit_reviewer_success(
        outcome,
        operation="reviewer_attestation.get_user",
        path="/user",
    )
    value = json_object_or_none(success.response.body)
    user_id = None if value is None else positive_safe_integer(value.get("id"))
    login = None if value is None else value.get("login")
    if user_id is None or type(login) is not str or _LOGIN.fullmatch(login) is None:
        raise GitHubReviewerUnavailable("GitHub reviewer identity is malformed")
    return user_id, login


async def _resolve_permission(
    client: _ReviewerClient,
    scope: RepositoryScope,
) -> ReviewerPermission:
    expected_path = repository_id_path(scope.repository_id)
    outcome = await client.get_repository(scope.repository_id)
    success = admit_reviewer_success(
        outcome,
        operation="reviewer_attestation.get_repository",
        path=expected_path,
    )
    value = json_object_or_none(success.response.body)
    repository_id = None if value is None else positive_safe_integer(value.get("id"))
    permissions = None if value is None else object_or_none(value.get("permissions"))
    if repository_id != scope.repository_id or permissions is None:
        raise GitHubReviewerUnavailable("GitHub reviewer repository evidence is malformed")
    admin = permissions.get("admin")
    maintain = permissions.get("maintain")
    if type(admin) is not bool or type(maintain) is not bool:
        raise GitHubReviewerUnavailable("GitHub reviewer permission evidence is malformed")
    if admin:
        return "admin"
    if maintain:
        return "maintain"
    raise GitHubReviewerRejected("GitHub reviewer lacks repository manager permission")


def _decode_token(body: bytes) -> str:
    try:
        value = load_strict_json(body, max_bytes=_MAX_OAUTH_RESPONSE_BYTES)
    except StrictJsonError as error:
        raise GitHubReviewerUnavailable("GitHub reviewer OAuth response is malformed") from error
    if type(value) is not dict:
        raise GitHubReviewerUnavailable("GitHub reviewer OAuth response is malformed")
    if "error" in value:
        raise GitHubReviewerRejected("GitHub reviewer OAuth exchange was rejected")
    access_token = value.get("access_token")
    token_type = value.get("token_type")
    expires_in = value.get("expires_in")
    if (
        type(access_token) is not str
        or not _bounded_ascii(access_token, maximum=512)
        or token_type != "bearer"  # noqa: S105 - provider token-type discriminator
        or type(expires_in) is not int
        or not 1 <= expires_in <= _MAX_TOKEN_LIFETIME_SECONDS
    ):
        raise GitHubReviewerRejected("GitHub reviewer token is not bounded expiring evidence")
    return access_token


def _require_redirect_uri(value: object) -> None:
    if type(value) is not str or not 1 <= len(value) <= 2_048:
        raise ValueError("GitHub reviewer redirect URI is invalid")
    parsed = urlsplit(value)
    loopback_http = parsed.scheme == "http" and parsed.hostname in {
        "127.0.0.1",
        "::1",
        "localhost",
    }
    if (
        (parsed.scheme != "https" and not loopback_http)
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("GitHub reviewer redirect URI is invalid")


def _base64url_256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 43
        and all(
            character.isascii() and (character.isalnum() or character in "-_")
            for character in value
        )
    )


def _bounded_ascii(value: object, *, maximum: int) -> bool:
    return type(value) is str and bool(value) and value.isascii() and len(value) <= maximum


def _is_json_media_type(value: str | None) -> bool:
    if value is None:
        return False
    media_type, separator, parameters = value.partition(";")
    return media_type.strip().lower() == "application/json" and (
        not separator or parameters.strip().lower() == "charset=utf-8"
    )

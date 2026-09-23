"""GitHub App JWT issuance and per-installation token cache semantics."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from joserfc import jwt as jose_jwt
from joserfc.errors import JoseError, SecurityWarning
from joserfc.jwk import RSAKey
from joserfc.jws import JWSRegistry

from ci_coordinator.integrations.github.app_http import _GitHubAppHttpClient, _HttpFailure
from ci_coordinator.integrations.github.app_transport_profile import (
    GITHUB_API_ACCEPT,
    GITHUB_API_USER_AGENT,
    GITHUB_API_VERSION,
    GITHUB_APP_JWT_BACKDATE_SECONDS,
    GITHUB_APP_JWT_LIFETIME_SECONDS,
    GITHUB_INSTALLATION_TOKEN_REFRESH_SKEW_SECONDS,
    GITHUB_MAXIMUM_CACHED_INSTALLATIONS,
    GITHUB_MAXIMUM_CONCURRENT_REFRESHES,
)
from ci_coordinator.kernel import Clock, StrictJsonError, load_strict_json

_APP_JWT_ALGORITHM = "RS256"
_APP_JWT_REGISTRY = JWSRegistry(algorithms=[_APP_JWT_ALGORITHM], strict_check_header=True)


@dataclass(frozen=True, slots=True)
class _InstallationToken:
    value: str = field(repr=False)
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class _AppToken:
    value: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class _CredentialUnavailable:
    kind: str


def _validate_github_app_identity(app_id: object, private_key_pem: object) -> None:
    if type(app_id) is not str or not app_id or app_id != app_id.strip() or len(app_id) > 255:
        raise ValueError("GitHub App id must be bounded non-empty text")
    if type(private_key_pem) is not str or not private_key_pem:
        raise ValueError("GitHub App private key must be non-empty text")


class _GitHubAppCredentialProvider:
    """Issue App JWTs and single-flight installation-token refreshes."""

    def __init__(
        self,
        *,
        app_id: str,
        private_key_pem: str,
        clock: Clock,
        http_client: _GitHubAppHttpClient,
    ) -> None:
        _validate_github_app_identity(app_id, private_key_pem)
        self._app_id = app_id
        self._private_key_pem = private_key_pem
        self._clock = clock
        self._http_client = http_client
        self._cache: dict[int, _InstallationToken] = {}
        self._refresh_tasks: dict[
            int, asyncio.Task[_InstallationToken | _CredentialUnavailable]
        ] = {}
        self._refresh_waiters: dict[int, int] = {}
        self._refresh_slots = asyncio.BoundedSemaphore(GITHUB_MAXIMUM_CONCURRENT_REFRESHES)

    def get_app(self) -> _AppToken | _CredentialUnavailable:
        app_jwt = _issue_app_jwt(
            self._app_id,
            self._private_key_pem,
            _utc_now(self._clock),
        )
        return _AppToken(app_jwt) if app_jwt is not None else _CredentialUnavailable("jwt_invalid")

    async def get(self, installation_id: int) -> _InstallationToken | _CredentialUnavailable:
        now = _utc_now(self._clock)
        self._discard_expired(now)
        cached = self._cache.get(installation_id)
        if cached is not None and _token_is_fresh(cached, now):
            return cached
        refresh = self._live_refresh(installation_id)
        if refresh is None:
            await self._refresh_slots.acquire()
            slot_owned = True
            try:
                now = _utc_now(self._clock)
                self._discard_expired(now)
                cached = self._cache.get(installation_id)
                if cached is not None and _token_is_fresh(cached, now):
                    return cached
                refresh = self._live_refresh(installation_id)
                if refresh is None:
                    refresh = asyncio.create_task(self._refresh(installation_id, now))
                    self._refresh_tasks[installation_id] = refresh
                    slot_owned = False
                    refresh.add_done_callback(self._refresh_done_callback(installation_id))
            finally:
                if slot_owned:
                    self._refresh_slots.release()
        return await self._await_refresh(installation_id, refresh)

    def _live_refresh(
        self,
        installation_id: int,
    ) -> asyncio.Task[_InstallationToken | _CredentialUnavailable] | None:
        refresh = self._refresh_tasks.get(installation_id)
        if refresh is None or refresh.done() or refresh.cancelling():
            return None
        return refresh

    async def _await_refresh(
        self,
        installation_id: int,
        refresh: asyncio.Task[_InstallationToken | _CredentialUnavailable],
    ) -> _InstallationToken | _CredentialUnavailable:
        self._refresh_waiters[installation_id] = self._refresh_waiters.get(installation_id, 0) + 1
        try:
            # A caller owns only its wait.  The refresh belongs to all waiters.
            return await asyncio.shield(refresh)
        finally:
            remaining = self._refresh_waiters[installation_id] - 1
            if remaining > 0:
                self._refresh_waiters[installation_id] = remaining
            else:
                del self._refresh_waiters[installation_id]
                if self._refresh_tasks.get(installation_id) is refresh and not refresh.done():
                    refresh.cancel()

    async def _refresh(
        self,
        installation_id: int,
        now: datetime,
    ) -> _InstallationToken | _CredentialUnavailable:
        refreshed = await self._request(installation_id, now)
        if isinstance(refreshed, _InstallationToken):
            now = _utc_now(self._clock)
            if _token_is_fresh(refreshed, now):
                self._discard_expired(now)
                if len(self._cache) >= GITHUB_MAXIMUM_CACHED_INSTALLATIONS:
                    self._cache.pop(
                        min(self._cache, key=lambda key: self._cache[key].expires_at),
                        None,
                    )
                self._cache[installation_id] = refreshed
            else:
                return _CredentialUnavailable("token_near_expiry")
        return refreshed

    def _finish_refresh(
        self,
        installation_id: int,
        completed: asyncio.Future[_InstallationToken | _CredentialUnavailable],
    ) -> None:
        if self._refresh_tasks.get(installation_id) is completed:
            del self._refresh_tasks[installation_id]
        self._refresh_slots.release()

    def _refresh_done_callback(
        self,
        installation_id: int,
    ) -> Callable[[asyncio.Future[_InstallationToken | _CredentialUnavailable]], None]:
        def callback(
            completed: asyncio.Future[_InstallationToken | _CredentialUnavailable],
        ) -> None:
            self._finish_refresh(installation_id, completed)

        return callback

    def _discard_expired(self, now: datetime) -> None:
        for installation_id, credential in tuple(self._cache.items()):
            if not _token_is_fresh(credential, now):
                del self._cache[installation_id]

    async def _request(
        self,
        installation_id: int,
        now: datetime,
    ) -> _InstallationToken | _CredentialUnavailable:
        app_jwt = _issue_app_jwt(self._app_id, self._private_key_pem, now)
        if app_jwt is None:
            return _CredentialUnavailable("jwt_invalid")
        exchange = await self._http_client.exchange(
            method="POST",
            path=f"/app/installations/{installation_id}/access_tokens",
            headers=(
                ("Accept", GITHUB_API_ACCEPT),
                ("Authorization", f"Bearer {app_jwt}"),
                ("User-Agent", GITHUB_API_USER_AGENT),
                ("X-GitHub-Api-Version", GITHUB_API_VERSION),
            ),
            body=None,
        )
        if isinstance(exchange, _HttpFailure):
            return _CredentialUnavailable(f"token_{exchange.kind}")
        if not 200 <= exchange.status < 300:
            return _CredentialUnavailable("token_status")
        if _single_header(exchange.headers, "x-github-api-version-selected") != GITHUB_API_VERSION:
            return _CredentialUnavailable("token_api_version")
        content_type = _single_header(exchange.headers, "content-type")
        if content_type is None or not content_type.casefold().startswith("application/json"):
            return _CredentialUnavailable("token_content_type")
        return _parse_installation_token(exchange.body, _utc_now(self._clock))


def _issue_app_jwt(app_id: str, private_key_pem: str, now: datetime) -> str | None:
    timestamp = int(now.timestamp())
    try:
        signing_key = RSAKey.import_key(private_key_pem)
        result = jose_jwt.encode(
            {"alg": _APP_JWT_ALGORITHM},
            {
                "iat": timestamp - GITHUB_APP_JWT_BACKDATE_SECONDS,
                "exp": timestamp + GITHUB_APP_JWT_LIFETIME_SECONDS,
                "iss": app_id,
            },
            signing_key,
            algorithms=[_APP_JWT_ALGORITHM],
            registry=_APP_JWT_REGISTRY,
        )
    except (JoseError, SecurityWarning, TypeError, ValueError):
        return None
    return result if type(result) is str else None


def _parse_installation_token(
    body: bytes,
    now: datetime,
) -> _InstallationToken | _CredentialUnavailable:
    try:
        document = load_strict_json(body)
    except StrictJsonError:
        return _CredentialUnavailable("token_malformed")
    if type(document) is not dict:
        return _CredentialUnavailable("token_malformed")
    token = document.get("token")
    expires_at = document.get("expires_at")
    if type(token) is not str or not token or type(expires_at) is not str:
        return _CredentialUnavailable("token_malformed")
    try:
        parsed_expiry = datetime.fromisoformat(expires_at)
    except ValueError:
        return _CredentialUnavailable("token_malformed")
    if parsed_expiry.tzinfo is None:
        return _CredentialUnavailable("token_malformed")
    credential = _InstallationToken(token, parsed_expiry.astimezone(UTC))
    if not _token_is_fresh(credential, now):
        return _CredentialUnavailable("token_near_expiry")
    return credential


def _token_is_fresh(token: _InstallationToken, now: datetime) -> bool:
    return token.expires_at > now + timedelta(
        seconds=GITHUB_INSTALLATION_TOKEN_REFRESH_SKEW_SECONDS
    )


def _utc_now(clock: Clock) -> datetime:
    now = clock.now()
    if now.tzinfo is None:
        raise ValueError("GitHub App clock must produce an aware instant")
    return now.astimezone(UTC)


def _single_header(headers: tuple[tuple[str, str], ...], name: str) -> str | None:
    values = tuple(
        value for header_name, value in headers if header_name.casefold() == name.casefold()
    )
    return values[0] if len(values) == 1 else None

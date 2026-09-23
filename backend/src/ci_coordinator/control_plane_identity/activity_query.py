from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal, Protocol

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity.activity import (
    ActivityForbidden,
    ActivityPrincipal,
    IdentityActivityObserver,
    observe_principal,
)
from ci_coordinator.control_plane_identity.model import (
    BreakGlassPrincipal,
    ControlPlanePrincipal,
    KeycloakHumanPrincipal,
    KeycloakWorkloadPrincipal,
    RoleAdmissionGranted,
    admit_control_plane_roles,
    is_administrator_actor_id,
    is_canonical_oidc_issuer,
)

SECURITY_RETENTION_SECONDS = 30 * 86400
MAX_ACTIVITY_PAGE_SIZE = 100
BUSINESS_ACTIONS = (
    "config-epoch-registration/v1",
    "config-epoch-activation/v1",
    "config-epoch-rollback/v1",
    "ci-economics-history-configured/v1",
    "ci-economics-history-gaps-requeued/v1",
    "ci-economics-history-retention-applied/v1",
    "operator_override_applied",
    "governance-baseline-approved/v1",
)
SECURITY_ACTIONS = ("login", "logout", "expired", "revoked", "replaced", "role_denied", "export")
type ActivitySource = Literal["security", "business"]
type ActivityOutcome = Literal["committed", "denied", "attempted"]


@dataclass(frozen=True, slots=True)
class ActivityQuery:
    source: ActivitySource
    since: datetime
    until: datetime
    issuer: str | None = None
    scope: RepositoryScope | None = None
    actor: str | None = None
    action: str | None = None
    limit: int = 50

    def __post_init__(self) -> None:
        if self.source not in ("security", "business"):
            raise ValueError("invalid activity source")
        for field in ("since", "until"):
            value = getattr(self, field)
            if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("activity time must be aware")
            object.__setattr__(self, field, value.astimezone(UTC))
            if value.microsecond % 1000:
                raise ValueError("activity time requires millisecond precision")
        if not timedelta() < self.until - self.since <= timedelta(days=31):
            raise ValueError("activity window must be positive and at most 31 days")
        if self.source == "security":
            if not is_canonical_oidc_issuer(self.issuer) or self.scope is not None:
                raise ValueError("security activity requires exactly one issuer scope")
        elif type(self.scope) is not RepositoryScope or self.issuer is not None:
            raise ValueError("business activity requires exactly one repository scope")
        if self.actor is not None and not is_activity_actor(self.actor):
            raise ValueError("invalid activity actor filter")
        actions = SECURITY_ACTIONS if self.source == "security" else BUSINESS_ACTIONS
        if self.action is not None and self.action not in actions:
            raise ValueError("invalid activity action filter")
        if type(self.limit) is not int or not 1 <= self.limit <= MAX_ACTIVITY_PAGE_SIZE:
            raise ValueError("invalid activity page limit")

    def binding(self) -> dict[str, object]:
        return {
            "source": self.source,
            "since": self.since.isoformat(),
            "until": self.until.isoformat(),
            "issuer": self.issuer,
            "installation": None if self.scope is None else self.scope.installation_id,
            "repository": None if self.scope is None else self.scope.repository_id,
            "actor": self.actor,
            "action": self.action,
            "limit": self.limit,
        }


@dataclass(frozen=True, slots=True)
class ActivityEntry:
    sequence: int
    source: ActivitySource
    action: str
    outcome: ActivityOutcome
    occurred_at: datetime
    actor: str | None
    issuer: str | None
    subject: str | None
    operation_ref: str
    audit_event_id: str | None = None
    event_hash: str | None = None

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or not 1 <= self.sequence <= 9007199254740991:
            raise ValueError("invalid activity sequence")
        if type(self.occurred_at) is not datetime or self.occurred_at.tzinfo is None:
            raise ValueError("invalid activity timestamp")
        if self.actor is not None and not is_activity_actor(self.actor):
            raise ValueError("invalid retained activity actor")
        if self.source == "business":
            if self.action not in BUSINESS_ACTIONS or self.outcome != "committed":
                raise ValueError("invalid business activity action")
            if (
                self.issuer is not None
                or self.subject is not None
                or re.fullmatch(r"sha256:[0-9a-f]{64}", self.operation_ref) is None
                or self.audit_event_id is None
                or re.fullmatch(r"audit_[0-9a-f]{32}", self.audit_event_id) is None
                or self.event_hash is None
                or re.fullmatch(r"[0-9a-f]{64}", self.event_hash) is None
            ):
                raise ValueError("invalid business activity reference")
        elif self.source == "security":
            expected = (
                "denied"
                if self.action == "role_denied"
                else "attempted"
                if self.action == "export"
                else "committed"
            )
            if (
                self.action not in SECURITY_ACTIONS
                or self.outcome != expected
                or not is_administrator_actor_id(self.actor)
                or not is_canonical_oidc_issuer(self.issuer)
                or type(self.subject) is not str
                or not 1 <= len(self.subject.encode("utf-8")) <= 512
                or re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", self.operation_ref)
                is None
                or self.audit_event_id is not None
                or self.event_hash is not None
            ):
                raise ValueError("invalid security activity")
        else:
            raise ValueError("invalid activity source")


@dataclass(frozen=True, slots=True)
class ActivityPage:
    items: tuple[ActivityEntry, ...]
    next_cursor: str | None
    observed_at: datetime
    retention_seconds: int | None
    integrity: Literal["journal_transaction", "audit_reference_only"]


class ActivityRepositoryAuthorizer(Protocol):
    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool: ...


class ActivityScopeAuthorizer(ActivityRepositoryAuthorizer, Protocol):
    async def allows_issuer(self, *, actor: str, issuer: str) -> bool: ...


class TrustedIssuerActivityAuthorization:
    def __init__(
        self, repository: ActivityRepositoryAuthorizer, trusted_issuer: str | None
    ) -> None:
        if trusted_issuer is not None and not is_canonical_oidc_issuer(trusted_issuer):
            raise ValueError("activity issuer authority must be canonical")
        self._repository = repository
        self._trusted_issuer = trusted_issuer

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        return await self._repository.allows_scope(actor=actor, scope=scope)

    async def allows_issuer(self, *, actor: str, issuer: str) -> bool:
        return is_administrator_actor_id(actor) and issuer == self._trusted_issuer


class ActivityReader(Protocol):
    async def page(
        self, query: ActivityQuery, principal: ActivityPrincipal, cursor: str | None
    ) -> ActivityPage: ...


class ActivityReadUseCase(Protocol):
    async def page(
        self,
        query: ActivityQuery,
        principal: ControlPlanePrincipal,
        *,
        at: datetime,
        cursor: str | None = None,
        export: bool = False,
    ) -> ActivityPage | ActivityForbidden: ...


class ActivityReadService:
    def __init__(
        self,
        reader: ActivityReader,
        authorizer: ActivityScopeAuthorizer,
        observer: IdentityActivityObserver | None = None,
    ) -> None:
        self._reader = reader
        self._authorizer = authorizer
        self._observer = observer

    async def page(
        self,
        query: ActivityQuery,
        principal: ControlPlanePrincipal,
        *,
        at: datetime,
        cursor: str | None = None,
        export: bool = False,
    ) -> ActivityPage | ActivityForbidden:
        admission = admit_control_plane_roles(principal, frozenset({"audit"}), at=at)
        if not isinstance(admission, RoleAdmissionGranted) or not isinstance(
            principal, KeycloakHumanPrincipal | KeycloakWorkloadPrincipal
        ):
            return ActivityForbidden()
        if query.scope is not None:
            allowed = await self._authorizer.allows_scope(
                actor=principal.actor_id, scope=query.scope
            )
        else:
            if query.issuer is None:
                raise ValueError("missing issuer scope")
            if query.issuer != principal.issuer:
                return ActivityForbidden()
            allowed = await self._authorizer.allows_issuer(
                actor=principal.actor_id, issuer=query.issuer
            )
        if allowed is not True:
            return ActivityForbidden()
        page = await self._reader.page(query, principal, cursor)
        if export:
            await observe_principal(self._observer, principal, "export")
        return page


def parse_activity_time(value: str) -> datetime:
    if (
        type(value) is not str
        or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value) is None
    ):
        raise ValueError("activity time must be UTC RFC3339")
    return datetime.fromisoformat(value)


def is_activity_actor(value: object) -> bool:
    if is_administrator_actor_id(value):
        return True
    if type(value) is not str or not value.startswith("break-glass:v1:"):
        return False
    try:
        return BreakGlassPrincipal(value.removeprefix("break-glass:v1:")).actor_id == value
    except ValueError:
        return False

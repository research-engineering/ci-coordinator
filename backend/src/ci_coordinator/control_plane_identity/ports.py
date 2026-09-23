"""Narrow effects required by control-plane identity transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from ci_coordinator.control_plane_identity.activity import SessionEndReason
from ci_coordinator.control_plane_identity.model import (
    BackChannelLogoutEvidence,
    ControlPlanePrincipal,
    ControlPlaneRole,
    ControlPlaneSessionRecord,
    KeycloakBrowserEvidence,
    KeycloakMachineTokenEvidence,
    RoleAdmission,
)


class KeycloakEvidenceRejected(RuntimeError):
    pass


class KeycloakUnavailable(RuntimeError):
    pass


class ControlPlaneSessionStoreRejected(RuntimeError):
    pass


class ControlPlaneSessionStoreUnavailable(RuntimeError):
    pass


class BackChannelLogoutStoreUnavailable(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BackChannelLogoutApplied:
    deleted_session_count: int

    def __post_init__(self) -> None:
        if type(self.deleted_session_count) is not int or self.deleted_session_count < 0:
            raise ValueError("deleted session count must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class BackChannelLogoutReplay:
    pass


type BackChannelLogoutMutation = BackChannelLogoutApplied | BackChannelLogoutReplay


class KeycloakBrowserPort(Protocol):
    def authorization_url(
        self,
        *,
        state: str,
        nonce: str,
        code_challenge: str,
    ) -> str: ...

    async def exchange_code(
        self,
        *,
        code: str,
        code_verifier: str,
        expected_nonce: str,
    ) -> KeycloakBrowserEvidence: ...

    async def logout_url(self) -> str | None: ...


class ControlPlaneSessionStore(Protocol):
    async def current_time(self) -> datetime: ...

    async def replace(
        self,
        *,
        previous_handle_digest: bytes | None,
        record: ControlPlaneSessionRecord,
    ) -> None: ...

    async def load(self, handle_digest: bytes) -> ControlPlaneSessionRecord | None: ...

    async def delete(
        self, handle_digest: bytes, *, reason: SessionEndReason = "logout"
    ) -> None: ...


class BackChannelLogoutStore(Protocol):
    async def consume_and_delete(
        self,
        *,
        evidence: BackChannelLogoutEvidence,
        replay_retained_until: datetime,
    ) -> BackChannelLogoutMutation: ...


class ControlPlaneRoleAdmission(Protocol):
    def admit(
        self,
        principal: ControlPlanePrincipal,
        required_roles: frozenset[ControlPlaneRole],
    ) -> RoleAdmission: ...


class BackChannelLogoutTokenVerifier(Protocol):
    async def verify_back_channel_logout(
        self,
        token: str,
    ) -> BackChannelLogoutEvidence: ...


class KeycloakMachineTokenVerifier(Protocol):
    async def verify(self, token: str) -> KeycloakMachineTokenEvidence: ...

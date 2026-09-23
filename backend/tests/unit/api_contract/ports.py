from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cached_property
from typing import Never

from config_epoch_support import admitted_config_epoch
from fastapi import Request

from ci_coordinator.api.http.control_plane_authentication import ControlPlaneRequestAuthenticator
from ci_coordinator.api.http.dependencies import ControlPlaneAuthenticationResult
from ci_coordinator.app import ActivateConfigEpoch, RegisterConfigEpoch, RollbackConfigEpoch
from ci_coordinator.app.config_queries import ConfigStatusAvailable
from ci_coordinator.config_control import (
    PolicyAdmissionResult,
    PolicySourceFormat,
    RepositoryScope,
    ValidatedEpochDraft,
    admit_policy_document,
)
from ci_coordinator.config_epochs import (
    ActiveConfigEpoch,
    ConfigEpochPage,
    ConfigEpochStatus,
    ConfigEpochSummary,
)
from ci_coordinator.control_plane_identity import IdentityRejected, KeycloakWorkloadPrincipal
from ci_coordinator.workbench_read_models import (
    ConfigEpochView,
    ReplayView,
    RepositoryDataSnapshot,
    RepositoryWorkbenchSnapshot,
    TruncationView,
)

from .profile import TOKEN

NOW = datetime(2026, 9, 19, 12, tzinfo=UTC)


class FixedClock:
    def now(self) -> datetime:
        return NOW


def machine_principal() -> KeycloakWorkloadPrincipal:
    return KeycloakWorkloadPrincipal(
        issuer="https://identity.example/realms/contract",
        authorized_party="contract-tests",
        subject="synthetic-machine",
        roles=frozenset({"read", "configure"}),
        issued_at=NOW - timedelta(minutes=1),
        expires_at=NOW + timedelta(hours=1),
        authority_profile_digest="a" * 64,
    )


@dataclass
class Ports:
    principal: KeycloakWorkloadPrincipal = field(default_factory=machine_principal)
    authentications: list[str | None] = field(default_factory=list)
    machine_tokens: list[str | None] = field(default_factory=list)
    workbench_calls: list[tuple[str, RepositoryScope, int]] = field(default_factory=list)
    status_calls: list[tuple[str, RepositoryScope, str | None, int]] = field(default_factory=list)
    policy_calls: list[tuple[bytes, PolicySourceFormat]] = field(default_factory=list)
    scope_calls: list[tuple[str, RepositoryScope]] = field(default_factory=list)
    forbidden_calls: list[str] = field(default_factory=list)
    starts: int = 0
    stops: int = 0

    @cached_property
    def draft(self) -> ValidatedEpochDraft:
        return admitted_config_epoch()

    def reset(self) -> None:
        self.principal = machine_principal()
        self.authentications.clear()
        self.machine_tokens.clear()
        self.workbench_calls.clear()
        self.status_calls.clear()
        self.policy_calls.clear()
        self.scope_calls.clear()
        self.forbidden_calls.clear()

    async def authenticate(self, token: str | None) -> KeycloakWorkloadPrincipal | IdentityRejected:
        self.machine_tokens.append(token)
        return self.principal if token == TOKEN else IdentityRejected("unauthenticated")

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        self.scope_calls.append((actor, scope))
        return actor == self.principal.actor_id

    async def admit_policy(
        self, source: bytes, source_format: PolicySourceFormat
    ) -> PolicyAdmissionResult:
        self.policy_calls.append((source, source_format))
        return admit_policy_document(source, source_format)

    async def __call__(
        self, *, actor: str, scope: RepositoryScope, limit: int
    ) -> RepositoryWorkbenchSnapshot:
        self.workbench_calls.append((actor, scope, limit))
        draft = self.draft
        epoch = ConfigEpochView(
            draft.epoch_id,
            draft.source_format,
            draft.source_hash,
            draft.document_hash,
            draft.epoch_hash,
            draft.document_schema_id,
            draft.document_profile_id,
            draft.semantic_profile_id,
            draft.compiled_schema_id,
            True,
            1,
        )
        return RepositoryWorkbenchSnapshot(
            RepositoryDataSnapshot(
                scope,
                NOW,
                1,
                (),
                (),
                (),
                (epoch,),
                (),
                TruncationView(False, False, False, False, False),
            ),
            ReplayView("valid", 1, 1, None),
        )

    async def status(
        self, *, actor: str, scope: RepositoryScope, after_epoch_id: str | None, limit: int
    ) -> ConfigStatusAvailable:
        self.status_calls.append((actor, scope, after_epoch_id, limit))
        draft = self.draft
        summary = ConfigEpochSummary(
            draft.epoch_id,
            draft.source_format,
            draft.source_hash,
            draft.document_hash,
            draft.epoch_hash,
            len(draft.source_bytes),
        )
        items = (summary,) if after_epoch_id is None or draft.epoch_id > after_epoch_id else ()
        return ConfigStatusAvailable(
            ConfigEpochStatus(
                scope, ActiveConfigEpoch(scope, draft.epoch_id, 1), ConfigEpochPage(items, None)
            )
        )

    def forbidden(self, operation: str) -> Never:
        self.forbidden_calls.append(operation)
        raise AssertionError(f"Unexpected effect port: {operation}")

    async def source(self, *, actor: str, scope: RepositoryScope, epoch_id: str) -> Never:
        self.forbidden("source")

    async def register(self, command: RegisterConfigEpoch) -> Never:
        self.forbidden("register")

    async def activate(self, command: ActivateConfigEpoch) -> Never:
        self.forbidden("activate")

    async def rollback(self, command: RollbackConfigEpoch) -> Never:
        self.forbidden("rollback")


class RecordingAuthenticator(ControlPlaneRequestAuthenticator):
    ports: Ports

    async def authenticate(self, request: Request) -> ControlPlaneAuthenticationResult:
        self.ports.authentications.append(request.headers.get("authorization"))
        return await super().authenticate(request)

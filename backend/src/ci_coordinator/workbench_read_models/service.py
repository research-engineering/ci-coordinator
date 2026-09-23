"""Authorization and replay-status composition for repository workbench reads."""

from __future__ import annotations

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.workbench_read_models.model import (
    ReplayView,
    RepositoryWorkbenchSnapshot,
    WorkbenchForbidden,
    WorkbenchResult,
    WorkbenchUnavailable,
)
from ci_coordinator.workbench_read_models.ports import (
    ReplayVerification,
    ReplayVerificationProbe,
    WorkbenchAuthorizer,
    WorkbenchReadError,
    WorkbenchRepository,
)


class RepositoryWorkbenchService:
    def __init__(
        self,
        *,
        authorizer: WorkbenchAuthorizer,
        repository: WorkbenchRepository,
        replay_probe: ReplayVerificationProbe,
    ) -> None:
        self._authorizer = authorizer
        self._repository = repository
        self._replay_probe = replay_probe

    async def __call__(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
        limit: int,
    ) -> WorkbenchResult:
        if not await self._authorizer.allows_scope(actor=actor, scope=scope):
            return WorkbenchForbidden()
        try:
            data = await self._repository.load(scope, limit=limit)
            verification = await self._replay_probe.check()
        except WorkbenchReadError:
            return WorkbenchUnavailable()
        return RepositoryWorkbenchSnapshot(
            data=data,
            replay=_replay_view(data.ledger_revision, verification),
        )


def _replay_view(snapshot_revision: int, verification: ReplayVerification) -> ReplayView:
    verified = verification.verified_revision
    if (
        verified is not None
        and verified >= snapshot_revision
        and verification.reason
        in {
            "ready",
            "audit_verification_in_progress",
        }
    ):
        return ReplayView("valid", snapshot_revision, verified, None)
    if verification.reason == "audit_verification_in_progress":
        return ReplayView("in_progress", snapshot_revision, verified, verification.reason)
    if verification.reason in {"audit_chain_invalid", "audit_head_invalid"}:
        return ReplayView("invalid", snapshot_revision, verified, verification.reason)
    return ReplayView("unavailable", snapshot_revision, verified, verification.reason)

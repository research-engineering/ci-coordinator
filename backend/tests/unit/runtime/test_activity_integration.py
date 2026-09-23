import asyncio
from dataclasses import dataclass
from datetime import timedelta

import pytest
from control_plane_http_support import NOW, human_principal
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity import ControlPlaneActorAuthority
from ci_coordinator.control_plane_identity.activity import ActivityUnavailable
from ci_coordinator.control_plane_identity.activity_cursor import (
    ActivityCursorCodec,
    ActivityPosition,
)
from ci_coordinator.control_plane_identity.activity_query import (
    ActivityQuery,
    TrustedIssuerActivityAuthorization,
)
from ci_coordinator.operator_controls.auth import (
    ControlPlaneScopeAuthorizer,
    RepositoryAccessUnavailable,
)
from ci_coordinator.persistence.activity_repository import PostgresActivityStore
from ci_coordinator.runtime.activity import ActivityCleanup, RuntimeActivityRepositoryAccess
from ci_coordinator.runtime.cursor_key import derive_runtime_cursor_key
from ci_coordinator.runtime.history_cursor_key import derive_history_cursor_key


def test_activity_cursor_key_is_stable_separated_and_preserves_archive_key() -> None:
    raw = bytes(range(32))
    private = ed25519.Ed25519PrivateKey.from_private_bytes(raw)
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    activity = derive_runtime_cursor_key(pem, purpose="activity")
    history = derive_history_cursor_key(pem)
    assert activity == derive_runtime_cursor_key(pem.replace("\n", "\r\n"), purpose="activity")
    assert activity != history and activity != raw and len(activity) == 32
    assert history == HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"ci-coordinator/runtime-subkeys/v1",
        info=b"ci-coordinator/history-cursor/v1",
    ).derive(raw)
    principal = human_principal()
    query = ActivityQuery("security", NOW - timedelta(days=1), NOW, issuer=principal.issuer)
    token = ActivityCursorCodec(history).encode(
        query, principal, ActivityPosition(3, 2, NOW + timedelta(minutes=1))
    )
    with pytest.raises(ValueError):
        ActivityCursorCodec(activity).decode(token, query, principal, now=NOW)


@dataclass
class _Access:
    available: bool = True

    async def allows_repository(self, scope: RepositoryScope) -> bool:
        if not self.available:
            raise RepositoryAccessUnavailable()
        return scope == RepositoryScope(1, 2)


def test_runtime_repository_access_never_implies_trusted_issuer_authority() -> None:
    async def scenario() -> None:
        access = _Access()
        repository = ControlPlaneScopeAuthorizer(
            actor_authority=ControlPlaneActorAuthority(),
            allowed_scopes=frozenset(),
            scope_mode="app",
            repository_access=access,
        )
        actor = human_principal().actor_id
        bridge = RuntimeActivityRepositoryAccess(repository)
        no_issuer = TrustedIssuerActivityAuthorization(bridge, None)
        assert await no_issuer.allows_scope(actor=actor, scope=RepositoryScope(1, 2))
        assert not await no_issuer.allows_issuer(actor=actor, issuer=human_principal().issuer)
        authorizer = TrustedIssuerActivityAuthorization(bridge, human_principal().issuer)
        assert await authorizer.allows_issuer(actor=actor, issuer=human_principal().issuer)
        assert not await authorizer.allows_issuer(
            actor=actor, issuer="https://foreign.example/realm"
        )
        assert not await authorizer.allows_scope(actor=actor, scope=RepositoryScope(1, 3))
        access.available = False
        with pytest.raises(ActivityUnavailable):
            await authorizer.allows_scope(actor=actor, scope=RepositoryScope(1, 2))

    asyncio.run(scenario())


class _CleanupStore(PostgresActivityStore):
    def __init__(self) -> None:
        self.calls = 0
        self.failure: BaseException | None = None

    async def cleanup(self) -> int:
        self.calls += 1
        if self.failure is not None:
            raise self.failure
        return 128


def test_cleanup_respects_abort_and_preserves_failure_or_cancellation() -> None:
    async def scenario() -> None:
        store = _CleanupStore()
        cleanup = ActivityCleanup(store)
        abort = asyncio.Event()
        abort.set()
        await cleanup(abort)
        assert store.calls == 0
        abort.clear()
        await cleanup(abort)
        assert store.calls == 1
        store.failure = ActivityUnavailable()
        with pytest.raises(ActivityUnavailable):
            await cleanup(abort)
        store.failure = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await cleanup(abort)

    asyncio.run(scenario())

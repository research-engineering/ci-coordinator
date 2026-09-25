from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

import pytest
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from ci_coordinator.control_plane_identity import (
    ControlPlaneSessionRecord,
    ControlPlaneSessionStoreRejected,
    ControlPlaneSessionStoreUnavailable,
    DisplayMetadata,
    derive_human_actor_id,
)
from ci_coordinator.persistence import (
    compatibility_admission,
    compatibility_repository,
    control_plane_identity_schema_attestation,
    control_plane_session_repository,
)
from ci_coordinator.persistence.compatibility_contracts import (
    CapabilityDeclaration,
    build_declaration,
)
from ci_coordinator.persistence.compatibility_fence import CompatibilityFenceMode
from ci_coordinator.persistence.compatibility_profile import (
    CompatibilityProfile,
    load_bundled_profile,
)
from ci_coordinator.persistence.compatibility_repository import CurrentCompatibility
from ci_coordinator.persistence.errors import DatabaseCapabilityUnavailable
from ci_coordinator.persistence.schema_capabilities import (
    ADMINISTRATOR_ACTIVITY,
    AUDIT_LEDGER,
    CONTROL_PLANE_IDENTITY_STATE,
    control_plane_identity_state_requirements,
    proposal_review_registration_requirements,
)


class _Connection:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.identity_matches = True
        self.identity_error: BaseException | None = None
        self.provided = tuple(
            sorted(
                {
                    *proposal_review_registration_requirements(load_bundled_profile()),
                    CONTROL_PLANE_IDENTITY_STATE.declaration(),
                    ADMINISTRATOR_ACTIVITY.declaration(),
                }
            )
        )

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        self.events.append("begin")
        try:
            yield
        finally:
            self.events.append("end")

    async def run_sync(self, callback: object) -> bool:
        assert callback is (
            control_plane_identity_schema_attestation.control_plane_identity_schema_matches_contract_sync
        )
        self.events.append("identity")
        if self.identity_error is not None:
            raise self.identity_error
        return self.identity_matches


class _Engine:
    def __init__(self, connection: _Connection) -> None:
        self.connection = connection

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[_Connection]:
        yield self.connection


@pytest.fixture
def admitted_connection(monkeypatch: pytest.MonkeyPatch) -> _Connection:
    connection = _Connection()

    async def current(
        actual: AsyncConnection, profile: CompatibilityProfile
    ) -> CurrentCompatibility:
        assert actual is cast(AsyncConnection, connection)
        connection.events.append("declaration")
        return CurrentCompatibility(
            declaration=build_declaration(
                profile,
                generation=1,
                revision_id="20260716_0001",
                parent_revision_id=None,
                transition_kind="bootstrap",
                capabilities=connection.provided,
            ),
            parent=None,
        )

    def guard(name: str) -> Callable[..., Awaitable[bool]]:
        async def matches(*_args: object) -> bool:
            connection.events.append(name)
            return True

        return matches

    for name in (
        "activity_schema_matches_contract",
        "schema_matches_contract",
        "audit_ledger_bridge_constraints_are_validated",
        "audit_ledger_seed_is_valid",
        "compatibility_protocol_schema_matches_contract",
        "config_epoch_lifecycle_schema_matches_contract",
        "proposal_review_registration_schema_matches_contract",
        "repository_attestation_schema_matches_contract",
        "public_access_is_restricted",
        "application_routines_are_security_invoker",
        "application_schema_is_empty_of_public_default_privileges",
        "runtime_principal_is_restricted",
    ):
        monkeypatch.setattr(compatibility_admission, name, guard(name))
    monkeypatch.setattr(compatibility_repository, "load_current_compatibility", current)

    async def configure(actual: AsyncConnection, _profile: CompatibilityProfile) -> AsyncConnection:
        assert actual is cast(AsyncConnection, connection)
        connection.events.append("configure")
        return actual

    async def verify(actual: AsyncConnection, _profile: CompatibilityProfile) -> None:
        assert actual is cast(AsyncConnection, connection)
        connection.events.append("verify")

    async def fence(
        actual: AsyncConnection, _profile: CompatibilityProfile, mode: CompatibilityFenceMode
    ) -> None:
        assert actual is cast(AsyncConnection, connection)
        assert mode is CompatibilityFenceMode.PARTICIPANT
        connection.events.append("fence")

    monkeypatch.setattr(control_plane_session_repository, "configure_read_committed", configure)
    monkeypatch.setattr(control_plane_session_repository, "verify_read_committed", verify)
    monkeypatch.setattr(control_plane_session_repository, "acquire_compatibility_fence", fence)
    return connection


def test_missing_only_identity_declaration_rejects_before_fact_collection(
    admitted_connection: _Connection,
) -> None:
    connection = admitted_connection
    connection.provided = tuple(
        item
        for item in connection.provided
        if item.capability_id != "control-plane-identity-state/v1"
    )
    with pytest.raises(DatabaseCapabilityUnavailable, match="does not provide"):
        asyncio.run(_admit(connection, proposal_review_registration_requirements))
    assert connection.events == ["declaration"]


def test_identity_fact_mismatch_rejects_after_existing_common_guards(
    admitted_connection: _Connection,
) -> None:
    connection = admitted_connection
    connection.identity_matches = False
    with pytest.raises(DatabaseCapabilityUnavailable, match="control-plane identity schema facts"):
        asyncio.run(_admit(connection, proposal_review_registration_requirements))
    assert connection.events[-2:] == ["runtime_principal_is_restricted", "identity"]
    assert connection.events.count("identity") == 1


def test_unrequired_identity_does_not_add_catalog_work(admitted_connection: _Connection) -> None:
    def without_identity(profile: CompatibilityProfile) -> tuple[CapabilityDeclaration, ...]:
        return tuple(
            item
            for item in proposal_review_registration_requirements(profile)
            if item.capability_id != "control-plane-identity-state/v1"
        )

    asyncio.run(_admit(admitted_connection, without_identity))
    assert "identity" not in admitted_connection.events


@pytest.mark.parametrize("business", [False, True])
def test_identity_wrapper_uses_one_attestor_after_the_same_fence(
    admitted_connection: _Connection, business: bool
) -> None:
    connection = admitted_connection

    async def scenario() -> None:
        profile = load_bundled_profile()
        required = control_plane_identity_state_requirements(profile)
        if business:
            required = tuple(sorted({*required, AUDIT_LEDGER.declaration()}))
        async with control_plane_session_repository._identity_transaction(
            cast(AsyncEngine, _Engine(connection)), profile, required
        ) as actual:
            assert actual is cast(AsyncConnection, connection)
            assert connection.events[-1] == "identity"
            connection.events.append("body")

    asyncio.run(scenario())
    assert connection.events[:5] == ["configure", "begin", "verify", "fence", "declaration"]
    assert connection.events[-3:] == ["identity", "body", "end"]
    assert connection.events.count("identity") == 1


@pytest.mark.parametrize("operation", ["clock", "replace"])
@pytest.mark.parametrize("failure", ["facts", "sql", "integrity", "cancelled"])
def test_identity_move_preserves_public_failures_before_body_sql(
    admitted_connection: _Connection,
    operation: Literal["clock", "replace"],
    failure: Literal["facts", "sql", "integrity", "cancelled"],
) -> None:
    connection = admitted_connection
    error: BaseException | None
    if failure == "facts":
        error = None
        connection.identity_matches = False
    elif failure == "sql":
        error = SQLAlchemyError("identity catalog unavailable")
    elif failure == "integrity":
        error = IntegrityError("identity catalog", None, Exception("catalog failure"))
    else:
        error = asyncio.CancelledError("identity catalog cancelled")
    connection.identity_error = error
    expected: type[BaseException] = ControlPlaneSessionStoreUnavailable
    if failure == "cancelled":
        expected = asyncio.CancelledError
    elif failure == "integrity" and operation == "replace":
        expected = ControlPlaneSessionStoreRejected

    async def scenario() -> None:
        store = control_plane_session_repository.PostgresControlPlaneSessionStore(
            cast(AsyncEngine, _Engine(connection))
        )
        with pytest.raises(expected) as rejected:
            if operation == "clock":
                await store.current_time()
            else:
                now = datetime(2026, 9, 25, tzinfo=UTC)
                issuer = "https://identity.example/realms/control-plane"
                await store.replace(
                    previous_handle_digest=None,
                    record=ControlPlaneSessionRecord(
                        handle_digest=b"s" * 32,
                        issuer=issuer,
                        subject="reviewer",
                        keycloak_session_id="session",
                        actor_id=derive_human_actor_id(issuer, "reviewer"),
                        roles=frozenset({"configure"}),
                        authority_profile_digest="a" * 64,
                        issued_at=now,
                        expires_at=now + timedelta(minutes=5),
                        display=DisplayMetadata(None, None),
                    ),
                )
        if failure == "cancelled":
            assert rejected.value is error
        elif error is not None:
            assert rejected.value.__cause__ is error
        else:
            assert isinstance(rejected.value.__cause__, DatabaseCapabilityUnavailable)

    asyncio.run(scenario())
    assert connection.events.count("identity") == 1
    assert connection.events[-1] == "end"


async def _admit(
    connection: _Connection,
    requirements: Callable[[CompatibilityProfile], tuple[CapabilityDeclaration, ...]],
) -> None:
    profile = load_bundled_profile()
    await compatibility_admission.admit_schema_dependent_operation(
        cast(AsyncConnection, connection), profile, requirements(profile)
    )

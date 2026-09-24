from __future__ import annotations

import asyncio
import traceback
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
)
from jwt.algorithms import RSAAlgorithm
from production_admission_support import make_production_admission_fixture

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import ForbiddenIdentity, HttpRouteDependencies
from ci_coordinator.api.http.plan_authentication import RequestBoundActionsOidcAuthenticator
from ci_coordinator.app.ci_history_administration import CiHistoryAdministrationService
from ci_coordinator.app.ci_history_analytics import CiHistoryAnalyticsService
from ci_coordinator.app.ci_history_collection import CiHistoryCollectionService
from ci_coordinator.app.ci_history_delivery import CiHistoryDeliveryService
from ci_coordinator.app.ci_history_read import CiHistoryReadService
from ci_coordinator.app.ci_observation_scanning import CiObservationScanningService
from ci_coordinator.ci_economics.observation_ports import ObservationClaimStore, ObservationProvider
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.control_plane_identity.activity_query import ActivityReadService
from ci_coordinator.identity_admission import (
    ActionsOidcJwkSet,
    JwksProvider,
    TrustedActionsRun,
)
from ci_coordinator.identity_admission import (
    is_workflow_path_identity as identity_workflow_path_is_admitted,
)
from ci_coordinator.integrations.github.ci_economics_sources import GitHubCiEconomicsSources
from ci_coordinator.integrations.github.ci_history_provider import GitHubHistoryProvider
from ci_coordinator.integrations.github.repository_membership import GitHubRepositoryAccess
from ci_coordinator.kernel import Clock, SystemClock
from ci_coordinator.observability import (
    MaintenanceOperationName,
    RuntimeDiagnosticObserver,
    RuntimeMetrics,
)
from ci_coordinator.operator_controls.auth import RepositoryAccessReader
from ci_coordinator.persistence.activity_repository import PostgresActivityStore
from ci_coordinator.persistence.ci_history_adapters import TransactionalHistoryStore
from ci_coordinator.persistence.ci_observation_adapters import TransactionalObservationStore
from ci_coordinator.persistence.errors import (
    CommitCancelledOutcomeUnknown,
    CommitOutcomeUnknown,
    CommittedButCleanupFailed,
    DatabaseCompatibilityError,
    PersistenceError,
)
from ci_coordinator.plan_issuance import PlanRequest, SignedPlanSigner
from ci_coordinator.production_admission import ProductionAdmissionGrant, ProductionScopeSubject
from ci_coordinator.runtime import composition as runtime_composition
from ci_coordinator.runtime import control_plane_composition as runtime_control_plane_composition
from ci_coordinator.runtime.activity import ActivityCleanup
from ci_coordinator.runtime.application import (
    RuntimeCompositionRejection,
    compose_runtime_application,
)
from ci_coordinator.runtime.background_services import RuntimeBackgroundGroup
from ci_coordinator.runtime.history_collection_worker import HistoryCollectionWorker
from ci_coordinator.runtime.history_detail_cleanup import (
    HISTORY_DETAIL_CLEANUP_TIMEOUT_SECONDS,
    HistoryDetailCleanup,
)
from ci_coordinator.runtime.maintenance_round import (
    BoundedMaintenanceOperation,
    MaintenanceOperation,
)
from ci_coordinator.runtime.reconciliation_service import PeriodicReconciliationService
from ci_coordinator.runtime_settings import NonEnforcingRuntimeSettings, admit_runtime_settings
from ci_coordinator.runtime_settings.contracts import (
    SecretValue,
)
from ci_coordinator.runtime_settings.contracts import (
    is_workflow_path_identity as settings_workflow_path_is_admitted,
)

EXAMPLE_KEYCLOAK_ISSUER = "https://auth.example.test/realms/coordinator"
GITHUB_REVIEWER_CLIENT_ID = "Iv1SyntheticClient01"


@pytest.mark.parametrize(
    ("identity", "expected"),
    [
        ("example/ci-coordinator/.github/workflows/ci.yml", True),
        ("example-platform/repository.name/.github/workflows/full-check.yaml", True),
        ("", False),
        ("-example/repository/.github/workflows/ci.yml", False),
        ("example/repository/.github/workflows/nested/ci.yml", False),
        (r"example/repository/.github/workflows\\ci.yml", False),
        ("example/repository/.github/workflows/ci.json", False),
        ("example/repository/.github/workflows/" + "a" * 480 + ".yml", False),
    ],
)
def test_workflow_path_identity_admission_remains_semantically_equal_across_boundaries(
    identity: str,
    expected: bool,
) -> None:
    assert identity_workflow_path_is_admitted(identity) is expected
    assert settings_workflow_path_is_admitted(identity) is expected


def test_ci_economics_collection_uses_the_single_composed_metrics_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_metrics: list[RuntimeMetrics] = []
    collection_metrics: list[RuntimeMetrics] = []

    def create_metrics() -> RuntimeMetrics:
        metrics = RuntimeMetrics()
        created_metrics.append(metrics)
        return metrics

    class CollectionOperation:
        async def __call__(self, _: asyncio.Event, /) -> None:
            return None

    def capture_collection(
        *_: object,
        runtime_metrics: RuntimeMetrics,
        **__: object,
    ) -> CollectionOperation:
        collection_metrics.append(runtime_metrics)
        return CollectionOperation()

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "RuntimeMetrics", create_metrics)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_composition,
        "CiEconomicsCollectionService",
        capture_collection,
    )

    _, resources = runtime_composition.compose_non_enforcing_dependencies(_non_enforcing_settings())
    asyncio.run(resources.aclose())

    assert len(created_metrics) == 1
    assert collection_metrics == created_metrics


def test_observation_composes_background_membership_and_independent_bounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    bounded: dict[str, tuple[int, object]] = {}
    native_scanning = CiObservationScanningService
    native_bounded = BoundedMaintenanceOperation
    worker_factory = Mock(wraps=HistoryCollectionWorker)

    def capture_scanning(
        *,
        store: ObservationClaimStore,
        provider: ObservationProvider,
        repository_access: RepositoryAccessReader,
        worker_id: str,
        metrics: RuntimeMetrics,
        diagnostics: RuntimeDiagnosticObserver | None = None,
    ) -> CiObservationScanningService:
        observed.update(
            store=store,
            provider=provider,
            repository_access=repository_access,
            worker_id=worker_id,
            metrics=metrics,
        )
        return native_scanning(
            store=store,
            provider=provider,
            repository_access=repository_access,
            worker_id=worker_id,
            metrics=metrics,
            diagnostics=diagnostics,
        )

    def capture_bounded(
        name: MaintenanceOperationName, deadline: int, operation: MaintenanceOperation
    ) -> BoundedMaintenanceOperation:
        bounded[name] = (deadline, operation)
        return native_bounded(name, deadline, operation)

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "CiObservationScanningService", capture_scanning)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "BoundedMaintenanceOperation", capture_bounded)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "HistoryCollectionWorker", worker_factory)
    factory = Mock(wraps=create_app)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "create_app", factory)
    _, resources = runtime_composition.compose_non_enforcing_dependencies(_non_enforcing_settings())
    asyncio.run(resources.aclose())
    assert factory.call_count == 1
    routes = factory.call_args.args[0]
    assert type(routes) is HttpRouteDependencies
    assert type(observed["store"]) is TransactionalObservationStore
    assert type(observed["provider"]) is GitHubCiEconomicsSources
    assert type(observed["repository_access"]) is GitHubRepositoryAccess
    assert type(observed["metrics"]) is RuntimeMetrics
    assert len(str(observed["worker_id"])) == 64
    assert bounded["ci_observation_discovery"][0] == 55
    assert bounded["ci_observation_gap_cleanup"][0] == 10
    assert "ci_history_collection" not in bounded
    assert bounded["ci_history_delivery"][0] == 55
    cleanup_timeout, cleanup = bounded["ci_history_detail_cleanup"]
    assert cleanup_timeout == HISTORY_DETAIL_CLEANUP_TIMEOUT_SECONDS
    assert type(cleanup) is HistoryDetailCleanup
    assert worker_factory.call_count == 1
    history_collection = worker_factory.call_args.args[0].__self__
    assert worker_factory.call_args.args[0] == history_collection.collect_next
    assert type(resources.background) is RuntimeBackgroundGroup
    assert type(resources.background._services[1]) is HistoryCollectionWorker
    primary = resources.background._primary
    assert type(primary) is PeriodicReconciliationService
    assert worker_factory.call_args.kwargs["idle_seconds"] == primary._interval_seconds
    assert worker_factory.call_args.kwargs["drain_seconds"] == primary._drain_timeout_seconds
    assert worker_factory.call_args.kwargs["metrics"] is observed["metrics"]
    history_delivery = bounded["ci_history_delivery"][1]
    assert type(history_collection) is CiHistoryCollectionService
    assert type(history_delivery) is CiHistoryDeliveryService
    assert type(history_collection._store) is TransactionalHistoryStore
    assert history_collection._store is history_delivery._store
    assert routes.ci_history is not None
    administration = routes.ci_history.use_case
    assert type(administration) is CiHistoryAdministrationService
    assert administration._store is history_collection._store
    assert routes.ci_observation is not None
    assert routes.ci_history.authenticator is routes.ci_observation.authenticator
    assert routes.ci_history.role_admission is routes.ci_observation.role_admission
    assert routes.ci_history.mutation_admission is routes.ci_observation.mutation_admission
    assert routes.ci_history_read is not None and routes.ci_history_analytics is not None
    assert type(routes.ci_history_read.use_case) is CiHistoryReadService
    assert type(routes.ci_history_analytics.use_case) is CiHistoryAnalyticsService
    assert routes.ci_history_read.authenticator is routes.ci_history.authenticator
    assert routes.ci_history_read.role_admission is routes.ci_history.role_admission
    assert routes.ci_history_read.mutation_admission is routes.ci_history.mutation_admission
    assert routes.ci_history_analytics.authenticator is routes.ci_history.authenticator
    assert routes.ci_history_analytics.role_admission is routes.ci_history.role_admission
    assert routes.ci_history_read.use_case._authorizer is administration._authorizer
    assert routes.ci_history_analytics.use_case._authorizer is administration._authorizer
    assert type(history_collection._attempts) is GitHubHistoryProvider
    assert history_collection._access is observed["repository_access"]
    assert history_collection._metrics is observed["metrics"]
    assert set(bounded) == {
        "activity_cleanup",
        "ci_economics_collection",
        "ci_economics_expiry",
        "ci_economics_observation_cleanup",
        "ci_economics_tombstone_purge",
        "ci_observation_discovery",
        "ci_observation_gap_cleanup",
        "ci_history_delivery",
        "ci_history_detail_cleanup",
    }
    cleanup = bounded["activity_cleanup"][1]
    assert type(cleanup) is ActivityCleanup
    assert bounded["activity_cleanup"][0] == 3
    assert routes.activity is not None
    assert isinstance(routes.activity.service, ActivityReadService)
    assert type(cleanup._store) is PostgresActivityStore
    assert routes.activity.service._reader is cleanup._store
    assert routes.activity.service._observer is cleanup._store
    assert routes.activity.authenticator is routes.ci_history.authenticator
    assert routes.activity.role_admission is routes.ci_history.role_admission


def test_connected_composition_uses_the_github_runner_snapshot_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[tuple[object, Clock]] = []

    class SnapshotProvider:
        pass

    def capture_provider(github: object, *, clock: Clock) -> SnapshotProvider:
        captured.append((github, clock))
        return SnapshotProvider()

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_composition,
        "GitHubRunnerSnapshotProvider",
        capture_provider,
    )

    _, resources = runtime_composition.compose_non_enforcing_dependencies(_non_enforcing_settings())
    asyncio.run(resources.aclose())

    assert len(captured) == 1
    github, clock = captured[0]
    assert github is resources.github
    assert type(clock) is SystemClock


def test_oidc_claim_namespaces_flow_from_settings_through_composition_to_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class Jwks:
        async def get_key_set(self, _: str) -> ActionsOidcJwkSet:
            return _oidc_key_set(private_key)

        async def probe(self) -> None:
            return None

        async def aclose(self) -> None:
            return None

    authenticators: list[RequestBoundActionsOidcAuthenticator] = []
    authenticator_type = RequestBoundActionsOidcAuthenticator

    def capture_authenticator(
        *,
        jwks_provider: JwksProvider,
        clock: Clock,
        audience: str,
        allowed_workflow_refs: tuple[str, ...],
        allowed_job_workflow_refs: tuple[str, ...] = (),
        allowed_workflow_paths: tuple[str, ...] = (),
        allowed_job_workflow_paths: tuple[str, ...] = (),
        runtime_metrics: RuntimeMetrics | None = None,
    ) -> RequestBoundActionsOidcAuthenticator:
        authenticator = authenticator_type(
            jwks_provider=jwks_provider,
            clock=clock,
            audience=audience,
            allowed_workflow_refs=allowed_workflow_refs,
            allowed_job_workflow_refs=allowed_job_workflow_refs,
            allowed_workflow_paths=allowed_workflow_paths,
            allowed_job_workflow_paths=allowed_job_workflow_paths,
            runtime_metrics=runtime_metrics,
        )
        authenticators.append(authenticator)
        return authenticator

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_composition,
        "GitHubActionsJwksProvider",
        lambda _, **__: Jwks(),
    )
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_composition,
        "RequestBoundActionsOidcAuthenticator",
        capture_authenticator,
    )
    _, resources = runtime_composition.compose_non_enforcing_dependencies(_non_enforcing_settings())
    request = _oidc_plan_request()

    async def exercise() -> tuple[object, object]:
        try:
            admitted = await authenticators[0].authenticate(
                f"Bearer {_oidc_token(private_key, request, job_workflow_ref='job-workflow-a')}",
                request,
            )
            cross_namespace = await authenticators[0].authenticate(
                f"Bearer {_oidc_token(private_key, request, job_workflow_ref='workflow-a')}",
                request,
            )
            return admitted, cross_namespace
        finally:
            await resources.aclose()

    admitted, cross_namespace = asyncio.run(exercise())

    assert isinstance(admitted, TrustedActionsRun)
    assert admitted.job_workflow_ref == "job-workflow-a"
    assert isinstance(cross_namespace, ForbiddenIdentity)


def test_non_enforcing_runtime_rejects_invalid_cryptographic_material_without_leaking_it() -> None:
    settings = _non_enforcing_settings(github_private_key="github-private-key")

    result = compose_runtime_application(settings)

    assert result == RuntimeCompositionRejection(
        "runtime_dependencies_unavailable",
        ("runtime_configuration",),
    )
    assert "github-private-key" not in repr(result)


def test_invalid_database_dsn_is_rejected_before_network_clients_are_allocated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = replace(
        _non_enforcing_settings(),
        database_dsn=SecretValue("invalid-database-dsn"),
    )

    def unexpected_client(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("network client was allocated before pure preflight completed")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "GitHubAppTransportFactory", unexpected_client)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "GitHubActionsJwksProvider", unexpected_client)

    assert compose_runtime_application(settings) == RuntimeCompositionRejection(
        "runtime_dependencies_unavailable",
        ("runtime_configuration",),
    )


def test_connected_composition_routes_github_and_jwks_through_one_proxy_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class Engine:
        async def dispose(self) -> None:
            return None

    class GitHub:
        async def aclose(self) -> None:
            return None

    class Jwks:
        async def aclose(self) -> None:
            return None

    def create_github(**kwargs: object) -> GitHub:
        observed["github"] = kwargs["outbound_proxy_url"]
        return GitHub()

    def create_jwks(_: object, **kwargs: object) -> Jwks:
        observed["jwks"] = kwargs["outbound_proxy_url"]
        return Jwks()

    def stop_after_network_construction(**_: object) -> None:
        raise ValueError("stop after network construction")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_composition,
        "create_postgres_engine",
        lambda _, **__: Engine(),
    )
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "GitHubAppTransportFactory", create_github)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "GitHubActionsJwksProvider", create_jwks)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_composition,
        "compose_control_plane_runtime_dependencies",
        stop_after_network_construction,
    )

    result = compose_runtime_application(
        _non_enforcing_settings(
            outbound_proxy_url="http://proxy.example.test:3128",
        )
    )

    assert result == RuntimeCompositionRejection(
        "runtime_dependencies_unavailable",
        ("runtime_configuration",),
    )
    assert observed == {
        "github": "http://proxy.example.test:3128",
        "jwks": "http://proxy.example.test:3128",
    }


def test_control_plane_composition_routes_keycloak_through_the_connected_proxy_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}

    class StopAfterProviderCapture(Exception):
        pass

    def capture_provider(**kwargs: object) -> None:
        observed.update(kwargs)
        raise StopAfterProviderCapture

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_control_plane_composition,
        "KeycloakIntegration",
        capture_provider,
    )

    with pytest.raises(StopAfterProviderCapture):
        runtime_control_plane_composition.compose_control_plane_runtime_dependencies(
            settings=_non_enforcing_settings(
                outbound_proxy_url="http://proxy.example.test:3128",
                control_plane_identity=True,
            ),
            engine=object(),  # type: ignore[arg-type]
            clock=SystemClock(),
        )

    assert observed["outbound_proxy_url"] == "http://proxy.example.test:3128"


@pytest.mark.parametrize(
    ("failure_stage", "expected_closed"),
    [
        ("github", ["engine"]),
        ("jwks", ["github", "engine"]),
        ("downstream", ["jwks", "github", "engine"]),
    ],
)
def test_non_enforcing_composition_rolls_back_every_constructed_resource(
    failure_stage: str,
    expected_closed: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []

    class Engine:
        async def dispose(self) -> None:
            closed.append("engine")

    class GitHub:
        async def aclose(self) -> None:
            closed.append("github")

    class Jwks:
        async def aclose(self) -> None:
            closed.append("jwks")

    def create_engine(_: str, **__: object) -> Engine:
        return Engine()

    def create_github(**_: object) -> GitHub:
        if failure_stage == "github":
            raise ValueError("GitHub construction failed")
        return GitHub()

    def create_jwks(_: object, **__: object) -> Jwks:
        if failure_stage == "jwks":
            raise ValueError("JWKS construction failed")
        return Jwks()

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "create_postgres_engine", create_engine)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "GitHubAppTransportFactory", create_github)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "GitHubActionsJwksProvider", create_jwks)
    if failure_stage == "downstream":

        def reject_authenticator(**_: object) -> None:
            raise ValueError("operator authentication construction failed")

        # noinspection PyUnresolvedReferences
        monkeypatch.setattr(
            runtime_composition,
            "compose_control_plane_runtime_dependencies",
            reject_authenticator,
        )

    assert compose_runtime_application(_non_enforcing_settings()) == RuntimeCompositionRejection(
        "runtime_dependencies_unavailable",
        ("runtime_configuration",),
    )
    assert closed == expected_closed


def test_non_enforcing_composition_preserves_primary_failure_and_continues_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []

    class Engine:
        async def dispose(self) -> None:
            closed.append("engine")
            raise RuntimeError("engine cleanup failed")

    class GitHub:
        async def aclose(self) -> None:
            closed.append("github")
            raise RuntimeError("GitHub cleanup failed")

    class Jwks:
        async def aclose(self) -> None:
            closed.append("jwks")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_composition,
        "create_postgres_engine",
        lambda _, **__: Engine(),
    )
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_composition,
        "GitHubAppTransportFactory",
        lambda **_: GitHub(),
    )
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_composition,
        "GitHubActionsJwksProvider",
        lambda _, **__: Jwks(),
    )

    def reject_authenticator(**_: object) -> None:
        raise ValueError("primary construction failed")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_composition,
        "compose_control_plane_runtime_dependencies",
        reject_authenticator,
    )

    with pytest.raises(runtime_composition.RuntimeDependencyConfigurationError) as captured:
        runtime_composition.compose_non_enforcing_dependencies(_non_enforcing_settings())

    primary = captured.value.__cause__
    assert isinstance(primary, ValueError)
    assert str(primary) == "primary construction failed"
    assert primary.__notes__ == ["runtime composition rollback also failed"]
    assert closed == ["jwks", "github", "engine"]


@pytest.mark.parametrize(
    "error_type",
    [DatabaseCompatibilityError, CommitOutcomeUnknown, CommittedButCleanupFailed],
)
def test_production_registration_persistence_failures_precede_runtime_resource_allocation(
    error_type: type[PersistenceError],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []

    class Engine:
        async def dispose(self) -> None:
            closed.append("engine")

    async def reject_registration(*_: object) -> None:
        raise error_type("sensitive persistence detail")

    monkeypatch.setattr(
        runtime_composition,
        "create_postgres_engine",
        lambda _, **__: Engine(),
    )
    monkeypatch.setattr(
        runtime_composition,
        "_register_production_admission",
        reject_registration,
    )

    with pytest.raises(runtime_composition.RuntimeDependencyConfigurationError) as captured:
        runtime_composition._compose_connected_dependencies(
            _non_enforcing_settings(),
            _production_grant(),
            SystemClock(),
            production_verifier=lambda _: _production_grant(),
            drain_verifier=lambda _: None,
        )

    assert str(captured.value) == "runtime dependency configuration is invalid"
    assert "sensitive" not in str(captured.value)
    assert isinstance(captured.value.__cause__, error_type)
    assert closed == []


def test_production_registration_cancellation_is_preserved_after_resource_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []

    class Engine:
        async def dispose(self) -> None:
            closed.append("engine")

    async def cancel_registration(*_: object) -> None:
        raise CommitCancelledOutcomeUnknown("registration commit was cancelled")

    monkeypatch.setattr(
        runtime_composition,
        "create_postgres_engine",
        lambda _, **__: Engine(),
    )
    monkeypatch.setattr(
        runtime_composition,
        "_register_production_admission",
        cancel_registration,
    )

    with pytest.raises(CommitCancelledOutcomeUnknown):
        runtime_composition._compose_connected_dependencies(
            _non_enforcing_settings(),
            _production_grant(),
            SystemClock(),
            production_verifier=lambda _: _production_grant(),
            drain_verifier=lambda _: None,
        )

    assert closed == []


def test_production_registration_engine_is_created_used_and_disposed_in_one_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, object]] = []
    grant = _production_grant()

    class RegistrationEngine:
        async def dispose(self) -> None:
            events.append(("dispose", id(asyncio.get_running_loop())))

    engine = RegistrationEngine()

    class Registrations:
        async def register(self, registration: object) -> None:
            events.append(("register", id(asyncio.get_running_loop())))
            assert registration is grant.registration

    class UnitOfWork:
        def __init__(self, candidate: object) -> None:
            assert candidate is engine
            self.production_admissions = Registrations()

        async def __aenter__(self) -> UnitOfWork:
            events.append(("enter", id(asyncio.get_running_loop())))
            return self

        async def __aexit__(self, *_: object) -> None:
            events.append(("exit", id(asyncio.get_running_loop())))

        async def commit(self) -> None:
            events.append(("commit", id(asyncio.get_running_loop())))

    monkeypatch.setattr(
        runtime_composition,
        "create_postgres_engine",
        lambda _, **__: engine,
    )
    monkeypatch.setattr(runtime_composition, "PostgresIngressIssuanceUnitOfWork", UnitOfWork)

    asyncio.run(
        runtime_composition._register_production_admission(
            "postgresql+psycopg://authority.invalid/database",
            grant.registration,
        )
    )

    assert [name for name, _loop in events] == ["enter", "register", "commit", "exit", "dispose"]
    assert len({loop for _name, loop in events}) == 1


def test_production_registration_cleanup_failure_does_not_retain_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Engine:
        async def dispose(self) -> None:
            raise RuntimeError("postgresql://operator:cleanup-secret@example.invalid/database")

    class Registrations:
        async def register(self, _: object) -> None:
            raise ValueError("registration failed")

    class UnitOfWork:
        def __init__(self, _: object) -> None:
            self.production_admissions = Registrations()

        async def __aenter__(self) -> UnitOfWork:
            return self

        async def __aexit__(self, *_: object) -> None:
            return None

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_composition,
        "create_postgres_engine",
        lambda _, **__: Engine(),
    )
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "PostgresIngressIssuanceUnitOfWork", UnitOfWork)

    with pytest.raises(ValueError, match="registration failed") as captured:
        asyncio.run(
            runtime_composition._register_production_admission(
                "postgresql+psycopg://authority.invalid/database",
                _production_grant().registration,
            )
        )

    rendered = "".join(traceback.format_exception(captured.value))
    assert "cleanup-secret" not in rendered
    assert captured.value.__notes__ == [
        "production admission registration engine cleanup also failed"
    ]


def test_production_registration_precedes_runtime_engine_and_provider_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class Engine:
        async def dispose(self) -> None:
            events.append("runtime-engine-dispose")

    class GitHub:
        async def aclose(self) -> None:
            events.append("github-close")

    class Jwks:
        async def aclose(self) -> None:
            events.append("jwks-close")

    async def register(*_: object) -> None:
        events.append("register")

    def create_engine(_: str, **__: object) -> Engine:
        events.append("runtime-engine")
        return Engine()

    def create_github(**_: object) -> GitHub:
        events.append("github")
        return GitHub()

    def create_jwks(_: object, **__: object) -> Jwks:
        events.append("jwks")
        return Jwks()

    def stop_after_allocation(**_: object) -> None:
        raise ValueError("stop after provider allocation")

    monkeypatch.setattr(runtime_composition, "_register_production_admission", register)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "create_postgres_engine", create_engine)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "GitHubAppTransportFactory", create_github)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "GitHubActionsJwksProvider", create_jwks)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        runtime_composition,
        "compose_control_plane_runtime_dependencies",
        stop_after_allocation,
    )

    with pytest.raises(runtime_composition.RuntimeDependencyConfigurationError):
        runtime_composition._compose_connected_dependencies(
            _non_enforcing_settings(),
            _production_grant(),
            SystemClock(),
            production_verifier=lambda _: _production_grant(),
            drain_verifier=lambda _: None,
        )

    assert events == [
        "register",
        "runtime-engine",
        "github",
        "jwks",
        "jwks-close",
        "github-close",
        "runtime-engine-dispose",
    ]


def test_non_enforcing_composition_rejects_an_active_event_loop_before_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_engine(_: str, **__: object) -> None:
        raise AssertionError("resource allocated inside an active event loop")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "create_postgres_engine", unexpected_engine)

    async def compose_inside_loop() -> None:
        runtime_composition.compose_non_enforcing_dependencies(_non_enforcing_settings())

    with pytest.raises(RuntimeError, match="before the event loop starts"):
        asyncio.run(compose_inside_loop())


@pytest.mark.parametrize("ttl_seconds", [1, 300])
def test_runtime_passes_admitted_plan_lifetime_to_the_real_signer(
    monkeypatch: pytest.MonkeyPatch,
    ttl_seconds: int,
) -> None:
    signer = Mock(wraps=SignedPlanSigner)

    def stop_before_provider_allocation(*_: object, **__: object) -> None:
        raise ValueError("composition stopped after signer construction")

    monkeypatch.setattr(runtime_composition, "SignedPlanSigner", signer)
    monkeypatch.setattr(
        runtime_composition, "create_postgres_engine", stop_before_provider_allocation
    )
    settings = replace(_non_enforcing_settings(), plan_ttl_seconds=ttl_seconds)

    with pytest.raises(runtime_composition.RuntimeDependencyConfigurationError):
        runtime_composition._compose_connected_dependencies(settings, None, SystemClock())

    signer.assert_called_once()
    assert signer.call_args.kwargs["ttl_seconds"] == ttl_seconds


@pytest.mark.parametrize(
    ("bootstrap", "receipt_verifier", "drain_verifier"),
    [
        (False, False, True),
        (False, True, False),
        (False, True, True),
        (True, False, False),
        (True, False, True),
        (True, True, False),
    ],
)
def test_production_bootstrap_and_both_verifiers_are_one_composition_requirement(
    bootstrap: bool,
    receipt_verifier: bool,
    drain_verifier: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def reject_allocation(*_: object, **__: object) -> None:
        raise AssertionError("incomplete production composition allocated resources")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(runtime_composition, "create_postgres_engine", reject_allocation)
    monkeypatch.setattr(runtime_composition, "_register_production_admission", reject_allocation)
    with pytest.raises(
        runtime_composition.ProductionAdmissionConfigurationError,
        match="production bootstrap and verifier must agree",
    ):
        runtime_composition._compose_connected_dependencies(
            _non_enforcing_settings(),
            _production_grant() if bootstrap else None,
            SystemClock(),
            production_verifier=(lambda _: _production_grant()) if receipt_verifier else None,
            drain_verifier=(lambda _: None) if drain_verifier else None,
        )


def _non_enforcing_settings(
    *,
    github_private_key: str | None = None,
    outbound_proxy_url: str | None = None,
    control_plane_identity: bool = False,
) -> NonEnforcingRuntimeSettings:
    github_key = github_private_key or _private_key_pem(rsa.generate_private_key(65537, 2048))
    signing_key = _private_key_pem(ed25519.Ed25519PrivateKey.generate())
    mapping = {
        "CI_COORDINATOR_RUNTIME_MODE": "non_enforcing",
        "CI_COORDINATOR_BIND_HOST": "127.0.0.1",
        "CI_COORDINATOR_BIND_PORT": "8080",
        "CI_COORDINATOR_SHUTDOWN_TIMEOUT_SECONDS": "30",
        "CI_COORDINATOR_REQUEST_TIMEOUT_SECONDS": "20",
        "CI_COORDINATOR_MAXIMUM_RETAINED_BODY_BYTES": "33554432",
        "CI_COORDINATOR_DATABASE_POOL_SIZE": "16",
        "CI_COORDINATOR_DATABASE_POOL_TIMEOUT_SECONDS": "5",
        "CI_COORDINATOR_PLAN_TTL_SECONDS": "60",
        "CI_COORDINATOR_RECONCILIATION_INTERVAL_SECONDS": "30",
        "CI_COORDINATOR_RECONCILIATION_STARTUP_TIMEOUT_SECONDS": "30",
        "CI_COORDINATOR_RECONCILIATION_SCAN_LIMIT": "100",
        "CI_COORDINATOR_SHADOW_ROLLOUT_PROFILE_ID": "a" * 64,
        "CI_COORDINATOR_DATABASE_DSN": (
            "postgresql+psycopg://ci_coordinator:secret@127.0.0.1:5432/ci_coordinator"
        ),
        "CI_COORDINATOR_WEBHOOK_SECRET": "w" * 32,
        "CI_COORDINATOR_GITHUB_APP_ID": "1234",
        "CI_COORDINATOR_GITHUB_PRIVATE_KEY": github_key,
        "CI_COORDINATOR_PLAN_SIGNING_KEY_ID": "plan-key",
        "CI_COORDINATOR_PLAN_SIGNING_PRIVATE_KEY": signing_key,
        "CI_COORDINATOR_OIDC_AUDIENCE": "ci-coordinator",
        "CI_COORDINATOR_OIDC_ALLOWED_WORKFLOW_REFS": "workflow-a,workflow-b",
        "CI_COORDINATOR_OIDC_ALLOWED_JOB_WORKFLOW_REFS": "job-workflow-a",
        "CI_COORDINATOR_BREAK_GLASS_ACTOR_ID": "break-glass:v1:local-development",
        "CI_COORDINATOR_BREAK_GLASS_BEARER_TOKEN": "b" * 32,
        "CI_COORDINATOR_METRICS_BEARER_TOKEN": "m" * 32,
        "CI_COORDINATOR_CONTROL_PLANE_SCOPE_ALLOWLIST": "100:200",
    }
    if outbound_proxy_url is not None:
        mapping["CI_COORDINATOR_OUTBOUND_PROXY_URL"] = outbound_proxy_url
    if control_plane_identity:
        mapping.update(
            {
                "CI_COORDINATOR_CONTROL_PLANE_AUTH_MODE": "keycloak",
                "CI_COORDINATOR_KEYCLOAK_ISSUER": EXAMPLE_KEYCLOAK_ISSUER,
                "CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_ID": "ci-coordinator-admin-ui",
                "CI_COORDINATOR_KEYCLOAK_BROWSER_CLIENT_SECRET": "k" * 32,
                "CI_COORDINATOR_KEYCLOAK_API_CLIENT_ID": "ci-coordinator-admin-api",
                "CI_COORDINATOR_PUBLIC_ORIGIN": "https://coordinator.example.test",
                "CI_COORDINATOR_CONTROL_PLANE_SESSION_KEY": "A" * 43,
                "CI_COORDINATOR_CONTROL_PLANE_SESSION_MAXIMUM_SECONDS": "900",
                "CI_COORDINATOR_GITHUB_APP_CLIENT_ID": GITHUB_REVIEWER_CLIENT_ID,
                "CI_COORDINATOR_GITHUB_APP_CLIENT_SECRET": "r" * 32,
            }
        )
    result = admit_runtime_settings(mapping)
    assert isinstance(result, NonEnforcingRuntimeSettings)
    return result


def _production_grant() -> ProductionAdmissionGrant:
    subject = ProductionScopeSubject(
        scope=RepositoryScope(100, 200),
        config_epoch_id="1" * 64,
        compiled_policy_hash="2" * 64,
        policy_hash="3" * 64,
        catalog_hash="4" * 64,
        target_registry_hash="5" * 64,
        workflow_refs=("workflow-a",),
        job_workflow_refs=(),
    )
    return make_production_admission_fixture(
        subject,
        now=datetime(2026, 7, 17, 12, tzinfo=UTC),
    ).grant


def _private_key_pem(key: rsa.RSAPrivateKey | ed25519.Ed25519PrivateKey) -> str:
    return key.private_bytes(
        Encoding.PEM,
        PrivateFormat.PKCS8,
        NoEncryption(),
    ).decode("ascii")


def _oidc_plan_request() -> PlanRequest:
    return PlanRequest(
        schema_version="dynamic-ci-plan-request/v2",
        request_id="settings-composition-authenticator-1",
        installation_id=100,
        repository_id=200,
        owner="example",
        repository="ci",
        event_name="pull_request",
        ref="refs/pull/42/merge",
        base_sha="a" * 40,
        head_sha="b" * 40,
        workflow_run_id=7001,
        run_attempt=1,
        pull_request_number=42,
        execution_sha="c" * 40,
    )


def _oidc_token(
    private_key: rsa.RSAPrivateKey,
    request: PlanRequest,
    *,
    job_workflow_ref: str,
) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "iss": "https://token.actions.githubusercontent.com",
            "aud": "ci-coordinator",
            "exp": int((now + timedelta(minutes=5)).timestamp()),
            "nbf": int((now - timedelta(minutes=1)).timestamp()),
            "repository": f"{request.owner}/{request.repository}",
            "repository_id": str(request.repository_id),
            "ref": request.ref,
            "run_id": str(request.workflow_run_id),
            "run_attempt": str(request.run_attempt),
            "event_name": request.event_name,
            "sha": request.execution_sha,
            "workflow_ref": "workflow-a",
            "job_workflow_ref": job_workflow_ref,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "settings-composition-key"},
    )


def _oidc_key_set(private_key: rsa.RSAPrivateKey) -> ActionsOidcJwkSet:
    public = RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    assert type(public) is dict
    return ActionsOidcJwkSet(
        (
            {
                **public,
                "kid": "settings-composition-key",
                "use": "sig",
                "alg": "RS256",
            },
        )
    )

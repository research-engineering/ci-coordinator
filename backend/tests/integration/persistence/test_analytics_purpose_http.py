import asyncio
from unittest.mock import AsyncMock

import pytest
from ci_economics.purpose_configuration_factories import purpose_command
from control_plane_http_support import (
    StaticControlPlaneAuthenticator,
    StaticMutationAdmission,
    StaticRoleAdmission,
    human_principal,
)
from httpx2 import ASGITransport, AsyncClient

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    CiHistoryAnalyticsRouteDependencies,
    HttpRouteDependencies,
    PurposeSettingsRouteDependencies,
)
from ci_coordinator.app.analytics_configuration import PurposeSettingsService
from ci_coordinator.app.ci_economics import CiEconomicsAuthorizer
from ci_coordinator.app.ci_history_analytics import CiHistoryAnalyticsService
from ci_coordinator.ci_economics.archive_analytics_models import PurposeEntry
from ci_coordinator.persistence.analytics_purpose_store import TransactionalPurposeSettingsStore
from ci_coordinator.persistence.ci_history_analytics import TransactionalHistoryAnalyticsStore
from ci_coordinator.persistence.ci_history_unit_of_work import PostgresPurposeUnitOfWork
from ci_coordinator.persistence.connection import create_postgres_engine

from .test_history_analytics import _query, _record, _seed

pytestmark = pytest.mark.persistence
PATH = "/api/v2/economics/repositories/101/202/history/analytics"


def test_http_configuration_hot_applies_without_callback_and_reauthenticates_replay(
    runtime_postgres_database_url: str,
) -> None:
    async def scenario() -> None:
        engine = create_postgres_engine(runtime_postgres_database_url)
        authorizer = AsyncMock(spec=CiEconomicsAuthorizer)
        authorizer.allows_scope.return_value = True
        authenticator = StaticControlPlaneAuthenticator(human_principal())
        roles = StaticRoleAdmission()
        app = create_app(
            HttpRouteDependencies(
                purpose_settings=PurposeSettingsRouteDependencies(
                    authenticator=authenticator,
                    role_admission=roles,
                    mutation_admission=StaticMutationAdmission(),
                    use_case=PurposeSettingsService(
                        authorizer=authorizer,
                        store=TransactionalPurposeSettingsStore(
                            lambda: PostgresPurposeUnitOfWork(engine)
                        ),
                    ),
                ),
                ci_history_analytics=CiHistoryAnalyticsRouteDependencies(
                    authenticator=authenticator,
                    role_admission=roles,
                    use_case=CiHistoryAnalyticsService(
                        authorizer=authorizer,
                        store=TransactionalHistoryAnalyticsStore(
                            lambda: PostgresPurposeUnitOfWork(engine)
                        ),
                    ),
                ),
            ),
            include_operator_ui=False,
            request_timeout_seconds=30,
        )
        query = {
            key: value
            for key, value in _query(purpose="lint").model_dump(mode="json").items()
            if key not in {"installationId", "repositoryId"} and value is not None
        }
        command = purpose_command(
            entries=(PurposeEntry(workflow_id=404, job_name="Lint", purposes=("lint", "test")),)
        )
        body = command.model_dump(mode="json", exclude={"actor"})
        try:
            await _seed(engine, (_record(1),))
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="https://example.test"
            ) as client:
                missing = await client.get(PATH, params=query)
                assert missing.json()["unavailable"]["reason"] == "purpose_mapping_unavailable"
                saved = await client.put(PATH + "/settings", json=body)
                assert saved.status_code == 200 and saved.json()["outcome"] == "committed"
                for purpose, jobs, mixed in (("lint", 1, 1), ("mixed", 1, 1), ("unknown", 0, 0)):
                    response = await client.get(PATH, params={**query, "purpose": purpose})
                    assert response.status_code == 200
                    report = response.json()["report"]
                    assert report["mapping"]["version"] == "repository-settings:1"
                    assert report["buckets"][0]["selected"]["jobs"] == jobs
                    assert report["buckets"][0]["selected"]["mixedJobs"] == mixed
                authorizer.allows_scope.return_value = False
                denied = await client.put(PATH + "/settings", json=body)
                assert denied.status_code == 403
                authorizer.allows_scope.return_value = True
                replay = await client.put(PATH + "/settings", json=body)
                assert replay.status_code == 200 and replay.json()["outcome"] == "replayed"
                assert replay.json()["snapshot"] == saved.json()["snapshot"]
        finally:
            await engine.dispose()

    asyncio.run(scenario())

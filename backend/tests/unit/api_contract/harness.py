from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import cast

import schemathesis
from fastapi import FastAPI
from schemathesis.config import SchemathesisConfig
from schemathesis.python import asgi

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.control_plane_authentication import (
    BreakGlassBearerAuthenticator,
    ControlPlaneRoleAuthorizer,
)
from ci_coordinator.api.http.control_plane_security import ControlPlaneMutationGuard
from ci_coordinator.api.http.dependencies import (
    ConfigManagementRouteDependencies,
    HttpRouteDependencies,
    WorkbenchRouteDependencies,
)
from ci_coordinator.app.config_admission import ConfigAdmissionService

from .ports import FixedClock, Ports, RecordingAuthenticator
from .profile import HEADERS, OPERATIONS, Campaign, campaign_from_environment


def schemathesis_config(campaign: Campaign) -> SchemathesisConfig:
    return SchemathesisConfig.from_dict(
        {
            "seed": campaign.seed,
            "headers": dict(HEADERS),
            "generation": {
                "max-examples": campaign.max_examples,
                "mode": "all",
                "database": "none",
                "with-security-parameters": False,
            },
            "phases": {
                "coverage": {"unexpected-methods": []},
                "stateful": {"enabled": False},
            },
        }
    )


class Harness:
    def __init__(self) -> None:
        self.ports = Ports()
        ports = self.ports

        @asynccontextmanager
        async def lifespan(app: FastAPI) -> AsyncIterator[None]:
            ports.starts += 1
            try:
                yield
            finally:
                ports.stops += 1

        authenticator = RecordingAuthenticator(
            session_cookie_name=None,
            human=None,
            machine=ports,
            break_glass=BreakGlassBearerAuthenticator(
                actor_id="break-glass:v1:" + "b" * 64,
                bearer_token="synthetic-unused-break-glass-credential-20260919",
            ),
        )
        authenticator.ports = ports
        roles = ControlPlaneRoleAuthorizer(FixedClock())
        self.app = create_app(
            HttpRouteDependencies(
                workbench=WorkbenchRouteDependencies(authenticator, roles, ports),
                config_management=ConfigManagementRouteDependencies(
                    authenticator=authenticator,
                    role_admission=roles,
                    mutation_admission=ControlPlaneMutationGuard(human=None, public_origin=None),
                    admission=ConfigAdmissionService(
                        authorizer=ports, policy_admission=ports.admit_policy
                    ),
                    queries=ports,
                    use_case=ports,
                ),
            ),
            lifespan=lifespan,
            include_operator_ui=False,
        )
        self.campaign = campaign_from_environment()
        raw = self.app.openapi()
        self.schema_digest = hashlib.sha256(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        schema = schemathesis.openapi.from_dict(raw, config=schemathesis_config(self.campaign))
        # from_dict is unbound; auth checks create fresh sessions through schema.transport.
        schema.app = self.app
        self.schema = schema.include(name=[f"{method} {path}" for method, path in OPERATIONS])
        self.schema.app = self.app

    @contextmanager
    def example(self) -> Iterator[asgi.ASGIClient]:
        self.ports.reset()
        try:
            with asgi.get_client(cast(asgi.ASGIApp, self.app)) as client:
                client.trust_env = False
                yield client
        finally:
            # Closing a session does not end its process-scoped native lifespan.
            asgi.shutdown_lifespans()
            assert self.ports.starts == self.ports.stops
            assert not self.ports.forbidden_calls

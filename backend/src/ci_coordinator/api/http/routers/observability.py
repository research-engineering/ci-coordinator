"""Read-only health, readiness, and metrics HTTP projections."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse, Response

from ci_coordinator.api.http.dependencies import ObservabilityRouteDependencies
from ci_coordinator.api.http.model_contracts import ResponseModel
from ci_coordinator.observability import health

HEALTH_PATH = "/healthz"
READINESS_PATH = "/readyz"
METRICS_PATH = "/metrics"
VERSIONED_HEALTH_PATH = "/api/v1/health"
VERSIONED_READINESS_PATH = "/api/v1/ready"


class HealthResponse(ResponseModel):
    ok: bool
    status: str


class ReadinessResponse(HealthResponse):
    pass


def build_observability_router(dependencies: ObservabilityRouteDependencies) -> APIRouter:
    router = APIRouter()

    @router.get(
        VERSIONED_HEALTH_PATH,
        response_model=HealthResponse,
        operation_id="get_versioned_liveness",
    )
    @router.get(HEALTH_PATH, response_model=HealthResponse, operation_id="get_liveness")
    async def get_liveness() -> HealthResponse:
        return HealthResponse(ok=True, status=health().state)

    @router.get(
        VERSIONED_READINESS_PATH,
        response_model=ReadinessResponse,
        operation_id="get_versioned_readiness",
    )
    @router.get(READINESS_PATH, response_model=ReadinessResponse, operation_id="get_readiness")
    async def get_readiness() -> JSONResponse:
        readiness = await dependencies.readiness()
        response = ReadinessResponse(
            ok=readiness.ready,
            status="ready" if readiness.ready else "not_ready",
        )
        response_status = (
            status.HTTP_200_OK if readiness.ready else status.HTTP_503_SERVICE_UNAVAILABLE
        )
        return JSONResponse(
            status_code=response_status,
            content=response.to_wire_mapping(),
            headers={"Cache-Control": "no-store"},
        )

    @router.get(
        METRICS_PATH,
        response_class=Response,
        operation_id="get_metrics",
        responses={
            200: {
                "content": {"text/plain": {}},
                "description": "Prometheus text exposition.",
            }
        },
    )
    async def get_metrics(request: Request) -> Response:
        authenticator = dependencies.metrics_authenticator
        if authenticator is not None and not authenticator.authenticate(request):
            return Response(
                status_code=status.HTTP_401_UNAUTHORIZED,
                headers={
                    "Cache-Control": "no-store",
                    "WWW-Authenticate": "Bearer",
                },
            )
        snapshot = dependencies.metrics.snapshot()
        return Response(
            content=snapshot.content,
            headers={"Cache-Control": "no-store", "Content-Type": snapshot.content_type},
        )

    return router

from collections.abc import Callable, Coroutine

from fastapi import Request
from fastapi.responses import Response
from fastapi.routing import APIRoute

from ci_coordinator.kernel import StrictJsonError, load_strict_json
from ci_coordinator.kernel.canonical_json import JsonResourceLimits


def strict_json_route(
    *,
    methods: frozenset[str] | None,
    maximum_bytes: int,
    resource_limits: JsonResourceLimits,
    invalid_response: Callable[[], Response],
) -> type[APIRoute]:
    class StrictJsonRoute(APIRoute):
        def get_route_handler(self) -> Callable[[Request], Coroutine[object, object, Response]]:
            native = super().get_route_handler()

            async def admitted(request: Request) -> Response:
                if methods is None or request.method in methods:
                    headers = request.scope["headers"]
                    if tuple(
                        value for name, value in headers if name.lower() == b"content-type"
                    ) != (b"application/json",) or any(
                        name.lower() == b"content-encoding" for name, _ in headers
                    ):
                        return invalid_response()
                    try:
                        load_strict_json(
                            await request.body(),
                            max_bytes=maximum_bytes,
                            resource_limits=resource_limits,
                        )
                    except StrictJsonError:
                        return invalid_response()
                return await native(request)

            return admitted

    return StrictJsonRoute

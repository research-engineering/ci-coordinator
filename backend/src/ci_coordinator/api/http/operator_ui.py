"""Same-origin delivery of a startup-verified operator UI snapshot."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Final
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.datastructures import Headers
from starlette.routing import BaseRoute
from starlette.staticfiles import StaticFiles
from starlette.types import Scope

from ci_coordinator.api.http.dependencies import ControlPlaneIdentityRouteDependencies
from ci_coordinator.api.http.operator_ui_access import admit_operator_page
from ci_coordinator.api.http.operator_ui_bundle import (
    ASSET_BUNDLE_NAMESPACE,
    VerifiedOperatorUiAsset,
    admit_operator_ui_bundle,
)

OPERATOR_UI_PATH: Final = "/workbench"
OPERATOR_UI_ASSETS_PATH: Final = "/assets"
_CONTENT_SECURITY_POLICY: Final = (
    "default-src 'self'; base-uri 'none'; connect-src 'self'; form-action 'self'; "
    "frame-ancestors 'none'; img-src 'self' data:; object-src 'none'; script-src 'self'; "
    "style-src 'self'; worker-src 'none'"
)
_COMMON_HEADERS: Final = {
    "Cross-Origin-Opener-Policy": "same-origin",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=()",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


def bundled_operator_ui_directory() -> Path:
    return Path(__file__).with_name("static")


def mount_operator_ui(
    app: FastAPI,
    directory: Path,
    *,
    identity: ControlPlaneIdentityRouteDependencies | None = None,
) -> tuple[BaseRoute, ...]:
    """Mount one immutable in-memory snapshot and return its observable routes."""
    snapshot = admit_operator_ui_bundle(directory)
    first_route = len(app.routes)

    async def operator_root() -> Response:
        return Response(
            status_code=307,
            headers={**_COMMON_HEADERS, "Cache-Control": "no-store", "Location": OPERATOR_UI_PATH},
        )

    app.add_api_route("/", operator_root, methods=["GET", "HEAD"], include_in_schema=False)

    async def operator_ui(request: Request) -> Response:
        if identity is not None:
            rejection = await admit_operator_page(request, identity)
            if rejection is not None:
                rejection.headers.update({**_COMMON_HEADERS, "Cache-Control": "no-store"})
                return rejection
        headers = {
            **_COMMON_HEADERS,
            "Cache-Control": "no-store",
            "Content-Security-Policy": _CONTENT_SECURITY_POLICY,
            "Content-Length": str(len(snapshot.index)),
        }
        return Response(
            content=b"" if request.method == "HEAD" else snapshot.index,
            media_type="text/html",
            headers=headers,
        )

    app.add_api_route(
        OPERATOR_UI_PATH,
        operator_ui,
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    app.add_api_route(
        OPERATOR_UI_PATH + "/",
        operator_ui,
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    app.mount(
        OPERATOR_UI_ASSETS_PATH,
        _ImmutableOperatorAssets(dict(snapshot.assets), bundle_id=snapshot.bundle_id),
        name="operator-ui-assets",
    )
    return tuple(app.routes[first_route:])


class _ImmutableOperatorAssets(StaticFiles):
    def __init__(
        self,
        assets: Mapping[str, VerifiedOperatorUiAsset],
        *,
        bundle_id: str,
    ) -> None:
        super().__init__(directory=None, check_dir=False, follow_symlink=False)
        self._assets = dict(assets)
        self._bundle_id = bundle_id

    async def get_response(self, path: str, scope: Scope) -> Response:
        if scope["method"] not in {"GET", "HEAD"}:
            return Response(
                status_code=405,
                headers={**_COMMON_HEADERS, "Allow": "GET, HEAD", "Cache-Control": "no-store"},
            )
        normalized_path = PurePosixPath(path).as_posix()
        version_prefix = f"{ASSET_BUNDLE_NAMESPACE}/{self._bundle_id}/"
        if normalized_path in self._assets:
            location = (
                f"{OPERATOR_UI_ASSETS_PATH}/{version_prefix}{quote(normalized_path, safe='/-._~')}"
            )
            return Response(
                status_code=307,
                headers={**_COMMON_HEADERS, "Cache-Control": "no-store", "Location": location},
            )
        asset_path = (
            normalized_path.removeprefix(version_prefix)
            if normalized_path.startswith(version_prefix)
            else ""
        )
        asset = self._assets.get(asset_path)
        if asset is None:
            return Response(
                status_code=404,
                headers={**_COMMON_HEADERS, "Cache-Control": "no-store"},
            )
        headers = {
            **_COMMON_HEADERS,
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Type": asset.content_type,
            "Content-Length": str(len(asset.content)),
            "ETag": asset.etag,
        }
        if _etag_matches(scope, asset.etag):
            return Response(status_code=304, headers=headers)
        content = b"" if scope["method"] == "HEAD" else asset.content
        return Response(content=content, headers=headers)


def _etag_matches(scope: Scope, expected: str) -> bool:
    supplied = Headers(scope=scope).getlist("if-none-match")
    if not supplied:
        return False
    candidates = (candidate.strip() for line in supplied for candidate in line.split(","))
    return any(
        candidate == "*" or candidate.removeprefix("W/") == expected for candidate in candidates
    )

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
from typing import cast

import pytest
from fastapi.testclient import TestClient
from starlette.types import Scope

import ci_coordinator.api.http.operator_ui as operator_ui_module
import ci_coordinator.api.http.operator_ui_bundle as operator_ui_bundle_module
from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    HttpRouteDependencies,
    ObservabilityRouteDependencies,
)
from ci_coordinator.observability import ReadinessStatus, RuntimeMetrics

_CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
}


def _write_manifest(root: Path) -> None:
    entries: list[dict[str, object]] = []
    for path in sorted(
        candidate for candidate in root.rglob("*") if candidate.is_file() or candidate.is_symlink()
    ):
        relative = path.relative_to(root).as_posix()
        content = path.read_bytes()
        entries.append(
            {
                "contentType": (
                    "text/html; charset=utf-8"
                    if relative == "index.html"
                    else _CONTENT_TYPES[path.suffix]
                ),
                "path": relative,
                "sha256": hashlib.sha256(content).hexdigest(),
                "sizeBytes": len(content),
            }
        )
    manifest = {
        "files": entries,
        "schemaVersion": "ci-coordinator-operator-ui-assets/v1",
    }
    (root / "asset-manifest.v1.json").write_text(
        json.dumps(manifest, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="ascii",
    )


async def _ready() -> ReadinessStatus:
    return ReadinessStatus(True, ())


def _dependencies() -> HttpRouteDependencies:
    return HttpRouteDependencies(
        observability=ObservabilityRouteDependencies(
            readiness=_ready,
            metrics=RuntimeMetrics(),
        )
    )


def test_production_bundle_is_served_from_the_backend_origin(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<main>operator shell</main><script src="/assets/app-01234567.js"></script>',
        encoding="utf-8",
    )
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app-01234567.js").write_text("export {}", encoding="utf-8")
    (assets / "runtime.js").write_text("export {}", encoding="utf-8")
    _write_manifest(tmp_path)
    client = TestClient(create_app(_dependencies(), operator_ui_directory=tmp_path))

    for path in ("/workbench", "/workbench/"):
        shell = client.get(path)
        assert shell.status_code == 200
        assert "operator shell" in shell.text
        assert shell.headers["cache-control"] == "no-store"
        assert shell.headers["content-security-policy"].startswith("default-src 'self'")
        assert shell.headers["x-content-type-options"] == "nosniff"
        shell_head = client.head(path)
        assert shell_head.status_code == 200
        assert shell_head.content == b""
        assert shell_head.headers["content-length"] == str(len(shell.content))

    asset = client.get("/assets/app-01234567.js")
    assert asset.status_code == 200
    assert asset.text == "export {}"
    assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert asset.headers["x-content-type-options"] == "nosniff"
    asset_head = client.head("/assets/app-01234567.js")
    assert asset_head.status_code == 200
    assert asset_head.content == b""
    assert asset_head.headers["content-length"] == str(len(asset.content))
    for validator in ("*", f"W/{asset.headers['etag']}"):
        not_modified = client.get(
            "/assets/app-01234567.js",
            headers={"If-None-Match": validator},
        )
        assert not_modified.status_code == 304
        assert not_modified.content == b""
    assert client.get("/assets/runtime.js").headers["cache-control"] == (
        "public, max-age=31536000, immutable"
    )
    redirect = client.get("/assets/runtime.js", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["cache-control"] == "no-store"
    versioned_path = redirect.headers["location"]
    assert versioned_path.startswith("/assets/_bundle/")
    assert client.get(versioned_path).text == "export {}"
    assert client.get("/assets/asset-manifest.v1.json").status_code == 404
    assert client.get("/assets/missing.js").status_code == 404

    (assets / "app-01234567.js").write_text("caller mutation", encoding="utf-8")
    assert client.get("/assets/app-01234567.js").text == "export {}"


def test_etag_admission_reads_every_repeated_header_line() -> None:
    scope = cast(
        "Scope",
        {
            "headers": [
                (b"if-none-match", b'"different"'),
                (b"if-none-match", b'W/"expected"'),
            ]
        },
    )

    assert operator_ui_module._etag_matches(scope, '"expected"') is True


@pytest.mark.parametrize("prefix", ("./assets/", "/assets/"))
def test_shell_projects_only_resource_attributes_after_raw_verification(
    tmp_path: Path, prefix: str
) -> None:
    raw_index = (
        "<!doctype html>\n<html><head><title>./assets/app.js &amp; unchanged</title>\n"
        f'<script type="module" crossorigin src="{prefix}app.js"></script>\n'
        f'<link rel="modulepreload" href="{prefix}lazy.js">\n'
        f'<link rel="stylesheet" href="{prefix}app.css"></head>\n'
        f'<body><img alt="&quot;logo&quot;" src="{prefix}logo.svg" />'
        "<p>./assets/app.js</p></body></html>"
    ).encode()
    payloads = {
        "app.js": b'import "./lazy.js"; export {};',
        "lazy.js": b'import "./app.js"; export {};',
        "app.css": b"body { color: black; }",
        "logo.svg": b'<svg xmlns="http://www.w3.org/2000/svg"></svg>',
    }
    (tmp_path / "index.html").write_bytes(raw_index)
    (tmp_path / "assets").mkdir()
    for name, content in payloads.items():
        (tmp_path / "assets" / name).write_bytes(content)
    _write_manifest(tmp_path)
    manifest = (tmp_path / "asset-manifest.v1.json").read_bytes()
    bundle_id = hashlib.sha256(manifest).hexdigest()
    expected_prefix = f"/assets/_bundle/{bundle_id}/"

    with TestClient(create_app(_dependencies(), operator_ui_directory=tmp_path)) as client:
        assert (tmp_path / "index.html").read_bytes() == raw_index
        for path in ("/workbench", "/workbench/"):
            shell = client.get(path)
            expected = raw_index.decode()
            for name in payloads:
                expected = expected.replace(f'="{prefix}{name}"', f'="{expected_prefix}{name}"')
            assert shell.text == expected
            assert shell.headers["cache-control"] == "no-store"
            assert "base-uri 'none'" in shell.headers["content-security-policy"]
            assert "unsafe-inline" not in shell.headers["content-security-policy"]
            head = client.head(path)
            assert head.content == b""
            for header in (
                "content-type",
                "content-length",
                "cache-control",
                "content-security-policy",
            ):
                assert head.headers[header] == shell.headers[header]
            assert int(head.headers["content-length"]) == len(shell.content)
        for name, content in payloads.items():
            response = client.get(expected_prefix + name, follow_redirects=False)
            assert response.status_code == 200
            assert response.content == content
            assert response.headers["etag"] == f'"{hashlib.sha256(content).hexdigest()}"'
            assert response.headers["cache-control"] == "public, max-age=31536000, immutable"
            head = client.head(expected_prefix + name, follow_redirects=False)
            assert head.content == b""
            for header in ("content-type", "content-length", "cache-control", "etag"):
                assert head.headers[header] == response.headers[header]
        old = client.get(f"/assets/_bundle/{'0' * 64}/app.js", follow_redirects=False)
        assert old.status_code == 404
        assert old.headers["cache-control"] == "no-store"
        (tmp_path / "index.html").write_text("changed after admission", encoding="utf-8")
        assert client.get("/workbench").text == expected

    assert (tmp_path / "asset-manifest.v1.json").read_bytes() == manifest
    for name, content in payloads.items():
        assert (tmp_path / "assets" / name).read_bytes() == content


@pytest.mark.parametrize(
    "index",
    (
        '<script src="./assets/missing.js"></script>',
        '<script src="https://outside.invalid/assets/app.js"></script>',
        '<script src="//outside.invalid/assets/app.js"></script>',
        '<script src="./assets/../app.js"></script>',
        '<script src="./assets/%61pp.js"></script>',
        '<script src="./assets/app.js?alias=1"></script>',
        '<script src="./assets/app.js#alias"></script>',
        '<script src="/assets/_bundle/old/app.js"></script>',
        '<script src="./assets/app.js" SRC="./assets/app.js"></script>',
        '<script type="module">import "./assets/app.js";</script>',
        '<link rel="modulepreload" href="./assets/missing.js">',
        '<base href="/assets/"><script src="./assets/app.js"></script>',
    ),
)
def test_shell_resource_admission_rejects_literal_nonmember_and_alias_inputs(
    tmp_path: Path, index: str
) -> None:
    (tmp_path / "index.html").write_text(index, encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("export {};", encoding="utf-8")
    _write_manifest(tmp_path)

    with pytest.raises(ValueError, match="operator UI index"):
        create_app(_dependencies(), operator_ui_directory=tmp_path)


@pytest.mark.parametrize("bound", ("_MAXIMUM_INDEX_BYTES", "_MAXIMUM_BUNDLE_BYTES"))
def test_projected_shell_and_final_snapshot_still_obey_byte_bounds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bound: str
) -> None:
    raw = b'<script src="./assets/app.js"></script>'
    asset = b"export {};"
    (tmp_path / "index.html").write_bytes(raw)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_bytes(asset)
    _write_manifest(tmp_path)
    maximum = len(raw) + (len(asset) if bound == "_MAXIMUM_BUNDLE_BYTES" else 0)
    monkeypatch.setattr(operator_ui_bundle_module, bound, maximum)

    with pytest.raises(ValueError, match=r"projected .*byte bound"):
        create_app(_dependencies(), operator_ui_directory=tmp_path)


def test_projection_cannot_hide_raw_index_manifest_mismatch(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text(
        '<script src="./assets/app.js"></script>', encoding="utf-8"
    )
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("export {};", encoding="utf-8")
    _write_manifest(tmp_path)
    (tmp_path / "index.html").write_text(
        '<script src="./assets/bad.js"></script>', encoding="utf-8"
    )

    with pytest.raises(ValueError, match="identity does not match the manifest"):
        create_app(_dependencies(), operator_ui_directory=tmp_path)


@pytest.mark.parametrize("method", ("GET", "HEAD"))
@pytest.mark.parametrize("query", ("", "?next=https://outside.invalid/&state=opaque"))
def test_root_redirect_is_fixed_noncacheable_and_has_no_auth_effect(
    tmp_path: Path,
    method: str,
    query: str,
) -> None:
    (tmp_path / "index.html").write_text("<main>operator shell</main>", encoding="utf-8")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "runtime.js").write_text("export {}", encoding="utf-8")
    _write_manifest(tmp_path)
    app = create_app(_dependencies(), operator_ui_directory=tmp_path)
    with TestClient(app) as client:
        response = client.request(method, "/" + query, follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/workbench"
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.content == b""
        assert "set-cookie" not in response.headers
        assert client.get(response.headers["location"]).status_code == 200
        assert client.post("/", follow_redirects=False).status_code == 405
        assert client.get("/unknown").status_code == 404
    assert "/" not in app.openapi()["paths"]
    with TestClient(create_app(_dependencies(), include_operator_ui=False)) as client:
        assert client.request(method, "/", follow_redirects=False).status_code == 404


def test_explicit_missing_bundle_fails_during_composition(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match=r"^operator UI directory is unavailable$"):
        create_app(_dependencies(), operator_ui_directory=tmp_path / "missing")


@pytest.mark.parametrize(
    ("index", "message"),
    [(b"", "byte bound"), (b"\xff", "readable UTF-8")],
)
def test_invalid_index_is_rejected_during_composition(
    tmp_path: Path,
    index: bytes,
    message: str,
) -> None:
    (tmp_path / "index.html").write_bytes(index)
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("export {}", encoding="utf-8")
    _write_manifest(tmp_path)

    with pytest.raises(ValueError, match=message):
        create_app(_dependencies(), operator_ui_directory=tmp_path)


def test_index_symlink_is_rejected_during_composition(tmp_path: Path) -> None:
    target = tmp_path.parent / f"{tmp_path.name}-shell.html"
    target.write_text("<main></main>", encoding="utf-8")
    (tmp_path / "index.html").symlink_to(target)
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("export {}", encoding="utf-8")
    _write_manifest(tmp_path)

    with pytest.raises(ValueError, match="symbolic link"):
        create_app(_dependencies(), operator_ui_directory=tmp_path)


def test_manifest_fails_closed_for_unlisted_suffix_shaped_asset(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<main></main>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("export {}", encoding="utf-8")
    _write_manifest(tmp_path)
    (assets / "app-01234567.js").write_text("unlisted", encoding="utf-8")

    with pytest.raises(ValueError, match="exact bundle"):
        create_app(_dependencies(), operator_ui_directory=tmp_path)


def test_bundle_inventory_is_bounded_before_complete_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "index.html").write_text("<main></main>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("export {}", encoding="utf-8")
    _write_manifest(tmp_path)
    monkeypatch.setattr(operator_ui_bundle_module, "_MAXIMUM_FILESYSTEM_ENTRIES", 1)

    with pytest.raises(ValueError, match="shape exceeded"):
        create_app(_dependencies(), operator_ui_directory=tmp_path)


def test_bundle_aggregate_is_admitted_before_payload_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "index.html").write_text("<main></main>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("export {}", encoding="utf-8")
    _write_manifest(tmp_path)
    original_read = operator_ui_bundle_module._read_regular_file
    payload_reads: list[PurePosixPath] = []

    def tracked_read(
        root_fd: int,
        relative_path: PurePosixPath,
        maximum_bytes: int,
        label: str,
    ) -> bytes:
        if relative_path.name != "asset-manifest.v1.json":
            payload_reads.append(relative_path)
        return original_read(root_fd, relative_path, maximum_bytes, label)

    monkeypatch.setattr(operator_ui_bundle_module, "_MAXIMUM_BUNDLE_BYTES", 1)
    monkeypatch.setattr(operator_ui_bundle_module, "_read_regular_file", tracked_read)

    with pytest.raises(ValueError, match="shape exceeded"):
        create_app(_dependencies(), operator_ui_directory=tmp_path)
    assert payload_reads == []


def test_bundle_file_mutation_during_read_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asset = tmp_path / "asset.js"
    asset.write_bytes(b"original")
    original_read = os.read
    mutated = False

    def read_and_mutate(descriptor: int, maximum: int) -> bytes:
        nonlocal mutated
        chunk = original_read(descriptor, maximum)
        if not mutated:
            mutated = True
            asset.write_bytes(b"changed-content")
        return chunk

    monkeypatch.setattr(os, "read", read_and_mutate)

    root_fd = operator_ui_bundle_module._open_bundle_root(tmp_path)
    try:
        with pytest.raises(ValueError, match="changed while"):
            operator_ui_bundle_module._read_regular_file(
                root_fd,
                PurePosixPath(asset.name),
                1_024,
                "asset",
            )
    finally:
        os.close(root_fd)


def test_bundle_rejects_ancestor_replaced_by_symlink_after_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "index.html").write_text("<main></main>", encoding="utf-8")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("export {}", encoding="utf-8")
    _write_manifest(tmp_path)
    replacement = tmp_path.parent / f"{tmp_path.name}-replacement"
    replacement.mkdir()
    (replacement / "app.js").write_text("export {}", encoding="utf-8")
    original_inventory = operator_ui_bundle_module._actual_payload_inventory
    inventory_calls = 0

    def swap_after_inventory(root_fd: int) -> tuple[tuple[str, int], ...]:
        nonlocal inventory_calls
        inventory = original_inventory(root_fd)
        inventory_calls += 1
        if inventory_calls == 1:
            assets.rename(tmp_path / "original-assets")
            assets.symlink_to(replacement, target_is_directory=True)
        return inventory

    monkeypatch.setattr(
        operator_ui_bundle_module,
        "_actual_payload_inventory",
        swap_after_inventory,
    )

    with pytest.raises(ValueError, match="symbolic link"):
        create_app(_dependencies(), operator_ui_directory=tmp_path)

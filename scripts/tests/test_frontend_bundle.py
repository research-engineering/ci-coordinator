from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

import pytest
import scripts.frontend_bundle as frontend_bundle_module
from scripts.frontend_bundle import (
    ASSET_MANIFEST_NAME,
    BundleAdmissionError,
    main,
    verify_bundle,
    write_bundle_manifest,
)


def _write_minimal_bundle(root: Path) -> None:
    (root / "index.html").write_text('<script src="/assets/app-01234567.js"></script>')
    assets = root / "assets"
    assets.mkdir()
    (assets / "app-01234567.js").write_text("console.info('ready')")


def test_bundle_admission_accepts_a_minimal_production_projection(
    tmp_path: Path,
) -> None:
    _write_minimal_bundle(tmp_path)

    write_bundle_manifest(tmp_path)
    verify_bundle(tmp_path)


def test_cli_accepts_an_explicit_bundle_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _write_minimal_bundle(tmp_path)

    assert main(["--write-manifest", str(tmp_path)]) == 0
    assert main([str(tmp_path)]) == 0
    assert '"state": "passed"' in capsys.readouterr().out


@pytest.mark.parametrize(
    ("filename", "content", "message"),
    [
        (
            "assets/app-01234567.js",
            "CI_COORDINATOR_BREAK_GLASS_BEARER_TOKEN",
            "forbidden marker",
        ),
        ("assets/app-01234567.js", "proxyRequestIsAdmitted", "forbidden marker"),
        ("assets/app-01234567.js.map", "{}", "source map"),
    ],
)
def test_bundle_admission_rejects_non_production_evidence(
    tmp_path: Path,
    filename: str,
    content: str,
    message: str,
) -> None:
    (tmp_path / "index.html").write_text("<main></main>")
    path = tmp_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    if path.suffix != ".js":
        (path.parent / "app-01234567.js").write_text("export {}")

    with pytest.raises(BundleAdmissionError, match=message):
        write_bundle_manifest(tmp_path)


def test_bundle_admission_rejects_symlinks(tmp_path: Path) -> None:
    (tmp_path / "index.html").write_text("<main></main>")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app-01234567.js").symlink_to("missing.js")

    with pytest.raises(BundleAdmissionError, match="symbolic link"):
        write_bundle_manifest(tmp_path)


@pytest.mark.parametrize("filename", ["assets/_bundle/app.js", "assets/unsafe name.js"])
def test_bundle_admission_rejects_reserved_or_unsafe_asset_paths(
    tmp_path: Path,
    filename: str,
) -> None:
    (tmp_path / "index.html").write_text("<main></main>")
    path = tmp_path / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("export {}")

    with pytest.raises(BundleAdmissionError, match="unsupported path"):
        write_bundle_manifest(tmp_path)


def test_manifest_rejects_changed_or_extra_assets_regardless_of_filename_shape(
    tmp_path: Path,
) -> None:
    _write_minimal_bundle(tmp_path)
    write_bundle_manifest(tmp_path)
    versioned = tmp_path / "assets" / "app-01234567.js"
    versioned.write_text("export {}")

    with pytest.raises(BundleAdmissionError, match="identity"):
        verify_bundle(tmp_path)

    versioned.write_text("console.info('ready')")
    (tmp_path / "assets" / "app.js").write_text("export {}")
    with pytest.raises(BundleAdmissionError, match="exact bundle"):
        verify_bundle(tmp_path)


def test_manifest_itself_is_required_and_canonical(tmp_path: Path) -> None:
    _write_minimal_bundle(tmp_path)

    with pytest.raises(BundleAdmissionError, match="unreadable file"):
        verify_bundle(tmp_path)

    write_bundle_manifest(tmp_path)
    manifest = tmp_path / ASSET_MANIFEST_NAME
    manifest.write_bytes(manifest.read_bytes().rstrip())
    with pytest.raises(BundleAdmissionError, match="not canonical"):
        verify_bundle(tmp_path)


def test_bundle_inventory_is_bounded_before_complete_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_minimal_bundle(tmp_path)
    monkeypatch.setattr(frontend_bundle_module, "_MAX_FILESYSTEM_ENTRIES", 1)

    with pytest.raises(BundleAdmissionError, match="shape exceeded"):
        write_bundle_manifest(tmp_path)


def test_bundle_aggregate_is_admitted_before_payload_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_minimal_bundle(tmp_path)
    reads: list[PurePosixPath] = []
    original_read = frontend_bundle_module._read_regular_file

    def tracked_read(
        root_fd: int,
        relative_path: PurePosixPath,
        maximum_bytes: int,
    ) -> bytes:
        reads.append(relative_path)
        return original_read(root_fd, relative_path, maximum_bytes)

    monkeypatch.setattr(frontend_bundle_module, "_MAX_BUNDLE_BYTES", 1)
    monkeypatch.setattr(frontend_bundle_module, "_read_regular_file", tracked_read)

    with pytest.raises(BundleAdmissionError, match="shape exceeded"):
        write_bundle_manifest(tmp_path)
    assert reads == []


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

    root_fd = frontend_bundle_module._open_bundle_root(tmp_path)
    try:
        with pytest.raises(BundleAdmissionError, match="changed while"):
            frontend_bundle_module._read_regular_file(
                root_fd,
                PurePosixPath(asset.name),
                1_024,
            )
    finally:
        os.close(root_fd)


def test_bundle_rejects_ancestor_replaced_by_symlink_after_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_minimal_bundle(tmp_path)
    assets = tmp_path / "assets"
    replacement = tmp_path.parent / f"{tmp_path.name}-replacement"
    replacement.mkdir()
    (replacement / "app-01234567.js").write_text("console.info('ready')")
    original_inventory = frontend_bundle_module._payload_inventory
    inventory_calls = 0

    def swap_after_inventory(root_fd: int) -> tuple[tuple[str, int], ...]:
        nonlocal inventory_calls
        inventory = original_inventory(root_fd)
        inventory_calls += 1
        if inventory_calls == 1:
            assets.rename(tmp_path / "original-assets")
            assets.symlink_to(replacement, target_is_directory=True)
        return inventory

    monkeypatch.setattr(frontend_bundle_module, "_payload_inventory", swap_after_inventory)

    with pytest.raises(BundleAdmissionError, match="symbolic link"):
        write_bundle_manifest(tmp_path)

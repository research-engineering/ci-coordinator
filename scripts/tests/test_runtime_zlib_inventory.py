from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
REPAIRED = "usr/lib/x86_64-linux-gnu/libz.so.1.3.2"
INDEPENDENT_LIB64 = "usr/lib64/libz.so"
RENAMED_WHEEL_COPY = (
    "app/backend/.venv/lib/python3.13/site-packages/pkg.libs/libz-0a1b2c3d.so.1.2.13"
)


@pytest.fixture
def verify() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "runtime_verify", ROOT / "docker/runtime/verify.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _image(tmp_path: Path, *, lib64_version: str = "1.3.1") -> tuple[Path, dict[Path, str]]:
    image = tmp_path.resolve() / "image"
    libraries = image / "usr/lib/x86_64-linux-gnu"
    libraries.mkdir(parents=True)
    (libraries / "libz.so.1.3.2").write_bytes(b"repaired zlib")
    (libraries / "libz.so.1").symlink_to("libz.so.1.3.2")
    (libraries / "libz.so").symlink_to("libz.so.1")
    (libraries / "libzstd.so.1").write_bytes(b"not zlib")
    (image / "lib").symlink_to("usr/lib")
    (image / "usr/lib64").mkdir()
    (image / INDEPENDENT_LIB64).write_bytes(b"independent unversioned zlib")
    (image / "lib64").mkdir()
    (image / "usr/local/lib").mkdir(parents=True)
    wheel_copy = image / RENAMED_WHEEL_COPY
    wheel_copy.parent.mkdir(parents=True)
    wheel_copy.write_bytes(b"renamed wheel zlib")
    versions = {
        image / REPAIRED: "1.3.2",
        image / INDEPENDENT_LIB64: lib64_version,
        wheel_copy: "1.2.13",
    }
    return image, versions


def _inventory(verify: ModuleType, image: Path, versions: dict[Path, str]) -> list[dict[str, str]]:
    loaded: list[Path] = []

    def read_version(path: Path) -> str:
        loaded.append(path)
        return versions[path]

    copies: list[dict[str, str]] = verify.zlib_library_copies(
        tuple(image / root.relative_to("/") for root in verify.ZLIB_INVENTORY_ROOTS),
        repaired=image / REPAIRED,
        read_version=read_version,
    )
    assert loaded == sorted(set(loaded))
    return copies


def test_zlib_inventory_declares_independent_and_aliased_scan_roots(verify: ModuleType) -> None:
    admitted_roots = (
        "/lib",
        "/lib64",
        "/usr/lib",
        "/usr/lib64",
        "/usr/local",
        "/app/backend/.venv",
    )
    assert tuple(Path(root) for root in admitted_roots) == verify.ZLIB_INVENTORY_ROOTS
    assert Path("/" + REPAIRED) == verify.REPAIRED_ZLIB


def test_zlib_inventory_counts_each_resolved_copy_once(verify: ModuleType, tmp_path: Path) -> None:
    image, versions = _image(tmp_path)
    copies = _inventory(verify, image, versions)
    assert copies == [
        {
            "path": str(image / relative),
            "version": version,
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for relative, version, content in (
            (RENAMED_WHEEL_COPY, "1.2.13", b"renamed wheel zlib"),
            (REPAIRED, "1.3.2", b"repaired zlib"),
            (INDEPENDENT_LIB64, "1.3.1", b"independent unversioned zlib"),
        )
    ]


def test_zlib_inventory_rejects_an_unrepaired_copy_of_the_repaired_version(
    verify: ModuleType, tmp_path: Path
) -> None:
    image, versions = _image(tmp_path, lib64_version="1.3.2")
    with pytest.raises(RuntimeError, match=r"additional zlib 1\.3\.2 copy"):
        _inventory(verify, image, versions)


def test_zlib_inventory_rejects_omission_of_the_repaired_copy(
    verify: ModuleType, tmp_path: Path
) -> None:
    image, versions = _image(tmp_path)
    for alias in ("libz.so", "libz.so.1", "libz.so.1.3.2"):
        (image / "usr/lib/x86_64-linux-gnu" / alias).unlink()
    del versions[image / REPAIRED]
    with pytest.raises(RuntimeError, match="repaired zlib copy is absent"):
        _inventory(verify, image, versions)

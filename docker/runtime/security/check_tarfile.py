from __future__ import annotations

import io
import json
import tarfile
import tempfile
from pathlib import Path
from typing import Literal


def check_filter(filter_name: Literal["data", "tar"]) -> bool:
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as stream:
        regular = tarfile.TarInfo("a/escape")
        regular.size = len(b"internal")
        stream.addfile(regular, io.BytesIO(b"internal"))
        symlink = tarfile.TarInfo("a/b/s")
        symlink.type = tarfile.SYMTYPE
        symlink.linkname = "../escape"
        stream.addfile(symlink)
        hardlink = tarfile.TarInfo("s")
        hardlink.type = tarfile.LNKTYPE
        hardlink.linkname = "a/b/s"
        stream.addfile(hardlink)
    archive.seek(0)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "escape").write_bytes(b"outside-fixture")
        destination = root / "extracted"
        with tarfile.open(fileobj=archive) as stream:
            stream.extractall(destination, filter=filter_name)  # noqa: S202 - owned adversarial fixture
        result = destination / "s"
        assert (destination / "a/escape").read_bytes() == b"internal"
        assert (root / "escape").read_bytes() == b"outside-fixture"
        if result.is_symlink() and result.read_bytes() == b"outside-fixture":
            return False
        assert not result.is_symlink() and result.read_bytes() == b"internal"
        return True


results = {name: check_filter(name) for name in ("data", "tar")}
print(json.dumps({"cve": "CVE-2026-82049", "safeFilters": results}))
if all(results.values()):
    raise SystemExit(0)
if not any(results.values()):
    raise SystemExit(10)
raise SystemExit(2)

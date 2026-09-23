from __future__ import annotations

import gzip
import hashlib
import os
import tempfile
import zlib
from pathlib import Path

MAX_CASE_BYTES = 16 * 1024 * 1024
MAX_RETAINED_BYTES = 64 * 1024 * 1024
MAX_CASE_FILES = 32


def retain_case(record: bytes, directory: Path) -> str:
    if len(record) > MAX_CASE_BYTES:
        return "Exact case exceeded 16 MiB; reproduction evidence incomplete"
    try:
        directory.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(record).hexdigest()
        target = directory / f"case-{digest}.json.gz"
        if target.exists():
            try:
                with gzip.open(target, "rb") as stream:
                    if stream.read(MAX_CASE_BYTES + 1) == record:
                        return f"Exact synthetic case SHA-256={digest}; artifact={target}"
            except (OSError, EOFError, zlib.error):
                pass
        retained = tuple(path for path in directory.glob("case-*.json.gz") if path != target)
        if len(retained) >= MAX_CASE_FILES:
            return "Exact-case count limit reached; reproduction evidence incomplete"
        compressed = gzip.compress(record, mtime=0)
        if sum(path.stat().st_size for path in retained) + len(compressed) > MAX_RETAINED_BYTES:
            return "Exact-case byte limit reached; reproduction evidence incomplete"
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=directory, prefix=".case-", delete=False
            ) as stream:
                temporary = Path(stream.name)
                stream.write(compressed)
            os.replace(temporary, target)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return f"Exact synthetic case SHA-256={digest}; artifact={target}"
    except OSError:
        return "Exact-case storage unavailable; reproduction evidence incomplete"

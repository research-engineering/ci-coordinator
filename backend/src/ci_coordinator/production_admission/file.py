"""Bounded no-symlink receipt loading before JSON materialization."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from ci_coordinator.production_admission.codec import MAX_PRODUCTION_ADMISSION_BYTES


class ProductionAdmissionFileError(OSError):
    """The configured receipt path is absent, unstable, or outside its bound."""


def read_production_admission_file(path: Path) -> bytes:
    if not isinstance(path, Path):
        raise TypeError("production admission path must be exact")
    descriptor: int | None = None
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK)
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_size < 1
            or before.st_size > MAX_PRODUCTION_ADMISSION_BYTES
        ):
            raise ProductionAdmissionFileError("production admission file is outside its bound")
        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = os.read(descriptor, min(65_536, MAX_PRODUCTION_ADMISSION_BYTES + 1 - observed))
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
            if observed > MAX_PRODUCTION_ADMISSION_BYTES:
                raise ProductionAdmissionFileError(
                    "production admission file changed beyond its bound"
                )
        after = os.fstat(descriptor)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or observed != before.st_size
        ):
            raise ProductionAdmissionFileError("production admission file changed while reading")
        return b"".join(chunks)
    except (OSError, ValueError) as error:
        if isinstance(error, ProductionAdmissionFileError):
            raise
        raise ProductionAdmissionFileError("production admission file is unavailable") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)

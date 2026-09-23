from __future__ import annotations

from hashlib import sha256

from ci_coordinator.kernel.canonical_json import canonical_json, try_canonical_json
from ci_coordinator.kernel.result import Err, Ok, ResultValue


def sha256_hex(value: bytes) -> str:
    return sha256(value).hexdigest()


def hash_object(value: object) -> str:
    return sha256_hex(canonical_json(value))


def try_hash_object(value: object) -> ResultValue[str]:
    canonical = try_canonical_json(value)
    if isinstance(canonical, Err):
        return canonical
    return Ok(sha256_hex(canonical.value))

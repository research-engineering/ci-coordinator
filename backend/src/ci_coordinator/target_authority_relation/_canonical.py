"""Domain-separated canonical encoding shared by relation artifacts."""

from __future__ import annotations

from ci_coordinator.kernel.canonical_json import bounded_canonical_json
from ci_coordinator.kernel.hashing import sha256_hex

from .limits import MAX_RELATION_DOCUMENT_BYTES, RELATION_JSON_LIMITS


def encode_mapping(
    value: dict[str, object],
    *,
    max_bytes: int = MAX_RELATION_DOCUMENT_BYTES,
) -> bytes:
    return bounded_canonical_json(
        value,
        max_bytes=max_bytes,
        resource_limits=RELATION_JSON_LIMITS,
    )


def domain_digest(
    domain: str,
    value: dict[str, object],
    *,
    max_bytes: int = MAX_RELATION_DOCUMENT_BYTES,
) -> str:
    if type(domain) is not str or not domain.isascii() or not domain:
        raise ValueError("digest domain must be non-empty ASCII text")
    return sha256_hex(domain.encode("ascii") + b"\0" + encode_mapping(value, max_bytes=max_bytes))

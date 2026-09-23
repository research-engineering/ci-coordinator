"""Strict canonical-JSON and scalar guards shared by durable row codecs."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from hashlib import sha256
from typing import cast

from ci_coordinator.kernel import canonical_json

_DIGEST = re.compile(r"^[0-9a-f]{64}$")


class CanonicalRowCodecError(ValueError):
    pass


def encode_canonical_object(value: object, *, maximum_bytes: int, context: str) -> bytes:
    encoded = canonical_json(value)
    if not 1 <= len(encoded) <= maximum_bytes:
        raise CanonicalRowCodecError(f"{context} violates its admitted byte bound")
    return encoded


def decode_canonical_object(
    value: object, *, maximum_bytes: int, context: str
) -> dict[str, object]:
    raw = require_bytes(value, context)
    if not 1 <= len(raw) <= maximum_bytes:
        raise CanonicalRowCodecError(f"stored {context} violates its admitted byte bound")
    try:
        decoded = json.loads(raw, object_pairs_hook=_no_duplicate_object)
    except (UnicodeDecodeError, json.JSONDecodeError, CanonicalRowCodecError) as error:
        raise CanonicalRowCodecError(f"stored {context} is not valid JSON") from error
    if type(decoded) is not dict or any(type(key) is not str for key in decoded):
        raise CanonicalRowCodecError(f"stored {context} must be an object")
    mapping = cast(dict[str, object], decoded)
    if canonical_json(mapping) != raw:
        raise CanonicalRowCodecError(f"stored {context} is not canonical JSON")
    return mapping


def require_bounded_text(value: object, *, maximum_bytes: int, context: str) -> str:
    if type(value) is not str or not value or len(value.encode("utf-8")) > maximum_bytes:
        raise CanonicalRowCodecError(f"{context} violates its admitted byte bound")
    return value


def require_digest(value: object, context: str) -> str:
    if type(value) is not str or _DIGEST.fullmatch(value) is None:
        raise CanonicalRowCodecError(f"{context} must be a lowercase SHA-256 digest")
    return value


def require_bytes(value: object, context: str) -> bytes:
    if isinstance(value, memoryview):
        value = value.tobytes()
    if type(value) is not bytes:
        raise CanonicalRowCodecError(f"{context} must be bytes")
    return value


def require_exact_keys(mapping: Mapping[str, object], expected: set[str], context: str) -> None:
    if set(mapping) != expected:
        raise CanonicalRowCodecError(f"{context} has unexpected or missing fields")


def semantic_hash(value: object) -> str:
    return sha256(canonical_json(value)).hexdigest()


def _no_duplicate_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise CanonicalRowCodecError("stored canonical JSON has duplicate keys")
        result[key] = value
    return result

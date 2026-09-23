from __future__ import annotations


def utf16_sort_key(value: str) -> bytes:
    """Return the ECMAScript-compatible UTF-16 code-unit ordering key."""
    return value.encode("utf-16-be")

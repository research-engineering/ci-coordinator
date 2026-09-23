"""Canonical primitives shared by GitHub credential-plane request policies."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import quote, unquote_to_bytes

from ci_coordinator.integrations.github.contracts import GitHubQueryParameter, GitHubRequest

_MAXIMUM_PATH_BYTES = 4_096
_MAXIMUM_PATH_SEGMENTS = 64
_MAXIMUM_COMPONENT_BYTES = 512


def get_request_matches(
    request: GitHubRequest,
    *,
    operation: str,
    path: str,
    api_version: str,
    query: tuple[GitHubQueryParameter, ...] = (),
) -> bool:
    return (
        request.operation == operation
        and request.method == "GET"
        and request.path == path
        and request.api_version == api_version
        and request.query == query
        and request.body is None
    )


def bounded_ascii_path_is_admitted(path: str) -> bool:
    return (
        path.isascii()
        and len(path) <= _MAXIMUM_PATH_BYTES
        and len(path.split("/")) <= _MAXIMUM_PATH_SEGMENTS
    )


def canonical_path_segment_is_admitted(raw: str) -> bool:
    decoded = _decode_canonical_path_value(raw)
    return decoded is not None and "/" not in decoded and "\\" not in decoded


def canonical_reference_is_admitted(raw: str) -> bool:
    return _decode_canonical_path_value(raw) is not None


def _decode_canonical_path_value(raw: str) -> str | None:
    if not raw:
        return None
    try:
        decoded = unquote_to_bytes(raw).decode("utf-8", errors="strict")
    except (UnicodeDecodeError, UnicodeEncodeError):
        return None
    admitted = (
        decoded not in {".", ".."}
        and len(decoded.encode("utf-8")) <= _MAXIMUM_COMPONENT_BYTES
        and not any(ord(character) < 32 or ord(character) == 127 for character in decoded)
        and quote(decoded, safe="") == raw
    )
    return decoded if admitted else None


def parse_canonical_positive_integer(value: str, *, maximum: int) -> int | None:
    if (
        type(value) is not str
        or type(maximum) is not int
        or maximum < 1
        or not value.isascii()
        or not value.isdecimal()
        or value.startswith("0")
        or len(value) > 16
    ):
        return None
    parsed = int(value)
    return parsed if 1 <= parsed <= maximum else None


def parse_canonical_page_query(
    query: Sequence[GitHubQueryParameter],
) -> tuple[int, int] | None:
    if tuple(parameter.name for parameter in query) != ("page", "per_page"):
        return None
    page = parse_canonical_positive_integer(query[0].value, maximum=10_000)
    per_page = parse_canonical_positive_integer(query[1].value, maximum=100)
    if page is None or per_page is None:
        return None
    return page, per_page

"""Bounded decoding of GitHub provider payloads for repository context."""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass

from ci_coordinator.integrations.github._response_decoding import (
    non_negative_safe_integer as _non_negative_integer,
)
from ci_coordinator.integrations.github._response_decoding import (
    object_or_none as _object,
)
from ci_coordinator.integrations.github._response_decoding import (
    positive_safe_integer as _positive_integer,
)
from ci_coordinator.kernel import StrictJsonError, load_strict_json
from ci_coordinator.repo_context.diff_builder import DiffFileChangeInput


@dataclass(frozen=True, slots=True)
class PullRequestSnapshot:
    number: int
    base_sha: str
    head_sha: str


def parse_pull_request_snapshot(
    body: bytes,
    *,
    max_json_bytes: int,
) -> PullRequestSnapshot | None:
    value = _json_object(body, max_json_bytes)
    if value is None:
        return None
    base = _object(value.get("base"))
    head = _object(value.get("head"))
    number = _positive_integer(value.get("number"))
    base_sha = None if base is None else _git_object_id(base.get("sha"))
    head_sha = None if head is None else _git_object_id(head.get("sha"))
    if number is None or base_sha is None or head_sha is None:
        return None
    return PullRequestSnapshot(number=number, base_sha=base_sha, head_sha=head_sha)


def parse_pull_request_files(
    body: bytes,
    *,
    max_json_bytes: int,
) -> tuple[DiffFileChangeInput, ...] | None:
    value = _json_value(body, max_json_bytes)
    if not isinstance(value, list):
        return None
    files: list[DiffFileChangeInput] = []
    for item in value:
        file = _diff_file(item)
        if file is None:
            return None
        files.append(file)
    return tuple(files)


def parse_compared_files(
    body: bytes,
    *,
    max_json_bytes: int,
) -> tuple[DiffFileChangeInput, ...] | None:
    value = _json_object(body, max_json_bytes)
    if value is None:
        return None
    raw_files = value.get("files")
    if not isinstance(raw_files, list):
        return None
    files: list[DiffFileChangeInput] = []
    for item in raw_files:
        file = _diff_file(item)
        if file is None:
            return None
        files.append(file)
    return tuple(files)


def decode_contents_file(
    body: bytes,
    *,
    expected_path: str,
    max_json_bytes: int,
    max_content_bytes: int,
) -> bytes | None:
    value = _json_object(body, max_json_bytes)
    if value is None:
        return None
    content = _string(value.get("content"))
    size = _non_negative_integer(value.get("size"))
    if (
        _string(value.get("type")) != "file"
        or _string(value.get("encoding")) != "base64"
        or _string(value.get("path")) != expected_path
        or content is None
        or size is None
    ):
        return None
    return _decode_base64(content, expected_size=size, max_content_bytes=max_content_bytes)


def _diff_file(value: object) -> DiffFileChangeInput | None:
    item = _object(value)
    if item is None:
        return None
    path = _string(item.get("filename"))
    status = _string(item.get("status"))
    additions = _non_negative_integer(item.get("additions"))
    deletions = _non_negative_integer(item.get("deletions"))
    previous_path = _optional_string(item.get("previous_filename"))
    patch = _optional_string(item.get("patch"))
    if (
        path is None
        or status is None
        or additions is None
        or deletions is None
        or previous_path is _INVALID
        or patch is _INVALID
    ):
        return None
    return DiffFileChangeInput(
        path=path,
        status=status,
        additions=additions,
        deletions=deletions,
        previous_path=previous_path,
        patch=patch,
    )


_INVALID = object()


def _optional_string(value: object) -> str | object | None:
    if value is None:
        return None
    text = _string(value)
    return _INVALID if text is None else text


def _json_object(body: bytes, max_json_bytes: int) -> dict[str, object] | None:
    return _object(_json_value(body, max_json_bytes))


def _json_value(body: bytes, max_json_bytes: int) -> object | None:
    try:
        return load_strict_json(body, max_bytes=max_json_bytes)
    except StrictJsonError:
        return None


def _string(value: object) -> str | None:
    if type(value) is not str or any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        return None
    return value


def _git_object_id(value: object) -> str | None:
    text = _string(value)
    if text is None or not 40 <= len(text) <= 64:
        return None
    return text if all(character in "0123456789abcdef" for character in text) else None


def _decode_base64(value: str, *, expected_size: int, max_content_bytes: int) -> bytes | None:
    if type(max_content_bytes) is not int or max_content_bytes < 0 or not value.isascii():
        return None
    if any(character.isspace() and character not in "\r\n" for character in value):
        return None
    compact = value.replace("\r", "").replace("\n", "")
    if len(compact) > 4 * ((max_content_bytes + 2) // 3):
        return None
    try:
        decoded = base64.b64decode(compact, validate=True)
    except (ValueError, binascii.Error):
        return None
    if len(decoded) != expected_size or len(decoded) > max_content_bytes:
        return None
    return decoded

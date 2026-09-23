"""Bounded semantic comparison of admitted repository policies."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final, Self, cast

from ci_coordinator.config_control import RepositoryScope, ValidatedEpochDraft
from ci_coordinator.config_control.epoch_integrity import assert_admitted_epoch_draft
from ci_coordinator.kernel import canonical_json, sha256_hex, utf16_sort_key

SEMANTIC_DIFF_VERSION: Final = "policy-semantic-diff/v1"
MAX_CHANGED_POINTERS: Final = 1_024
MAX_SEMANTIC_DIFF_BYTES: Final = 65_536
_DIFF_HASH_DOMAIN: Final = b"ci-policy-semantic-diff/v1\0"
_MISSING = object()


class SemanticDiffLimitExceeded(ValueError):
    """The exact diff cannot fit the review evidence contract."""


@dataclass(frozen=True, slots=True)
class PolicySemanticDiff:
    version: str
    scope: RepositoryScope
    base_epoch_id: str | None
    target_epoch_id: str
    changed_pointers: tuple[str, ...]
    canonical_bytes: bytes
    diff_hash: str

    @classmethod
    def create(
        cls,
        *,
        scope: RepositoryScope,
        base_epoch_id: str | None,
        target_epoch_id: str,
        changed_pointers: tuple[str, ...],
    ) -> Self:
        if type(changed_pointers) is not tuple:
            raise TypeError("semantic diff pointers must be an exact tuple")
        if len(changed_pointers) > MAX_CHANGED_POINTERS:
            raise SemanticDiffLimitExceeded("semantic diff pointer limit exceeded")
        unique_pointers: set[str] = set()
        pointer_bytes = 0
        for pointer in changed_pointers:
            _require_json_pointer(pointer)
            if pointer in unique_pointers:
                continue
            if len(pointer) > MAX_SEMANTIC_DIFF_BYTES:
                raise SemanticDiffLimitExceeded("semantic diff byte limit exceeded")
            pointer_bytes += len(pointer.encode("utf-8"))
            if pointer_bytes > MAX_SEMANTIC_DIFF_BYTES:
                raise SemanticDiffLimitExceeded("semantic diff byte limit exceeded")
            unique_pointers.add(pointer)
        pointers = tuple(sorted(unique_pointers, key=utf16_sort_key))
        projection = _projection(scope, base_epoch_id, target_epoch_id, pointers)
        encoded = canonical_json(projection)
        if len(encoded) > MAX_SEMANTIC_DIFF_BYTES:
            raise SemanticDiffLimitExceeded("semantic diff byte limit exceeded")
        return cls(
            version=SEMANTIC_DIFF_VERSION,
            scope=scope,
            base_epoch_id=base_epoch_id,
            target_epoch_id=target_epoch_id,
            changed_pointers=pointers,
            canonical_bytes=encoded,
            diff_hash=sha256_hex(_DIFF_HASH_DOMAIN + encoded),
        )

    def __post_init__(self) -> None:
        if self.version != SEMANTIC_DIFF_VERSION:
            raise ValueError("semantic diff version is not admitted")
        if type(self.scope) is not RepositoryScope:
            raise TypeError("semantic diff scope must be exact")
        _require_epoch_id(self.base_epoch_id, allow_none=True)
        _require_epoch_id(self.target_epoch_id, allow_none=False)
        if type(self.changed_pointers) is not tuple:
            raise TypeError("semantic diff pointers must be an exact tuple")
        if len(self.changed_pointers) > MAX_CHANGED_POINTERS:
            raise SemanticDiffLimitExceeded("semantic diff pointer limit exceeded")
        if self.changed_pointers != tuple(sorted(set(self.changed_pointers), key=utf16_sort_key)):
            raise ValueError("semantic diff pointers must be canonical and unique")
        for pointer in self.changed_pointers:
            _require_json_pointer(pointer)
        expected = canonical_json(
            _projection(
                self.scope,
                self.base_epoch_id,
                self.target_epoch_id,
                self.changed_pointers,
            )
        )
        if (
            type(self.canonical_bytes) is not bytes
            or not 1 <= len(self.canonical_bytes) <= MAX_SEMANTIC_DIFF_BYTES
            or self.canonical_bytes != expected
        ):
            raise ValueError("semantic diff bytes do not match the canonical projection")
        if self.diff_hash != sha256_hex(_DIFF_HASH_DOMAIN + expected):
            raise ValueError("semantic diff hash does not bind its canonical bytes")


def compare_policy_drafts(
    base: ValidatedEpochDraft | None,
    target: ValidatedEpochDraft,
) -> PolicySemanticDiff:
    admitted_target = assert_admitted_epoch_draft(target)
    admitted_base = None if base is None else assert_admitted_epoch_draft(base)
    if admitted_base is not None and admitted_base.scope != admitted_target.scope:
        raise ValueError("semantic diff drafts must belong to one repository scope")
    changed = (
        ("",)
        if admitted_base is None
        else tuple(
            _changed_pointers(
                _decode_document(admitted_base.normalized_document_bytes),
                _decode_document(admitted_target.normalized_document_bytes),
                "",
            )
        )
    )
    return PolicySemanticDiff.create(
        scope=admitted_target.scope,
        base_epoch_id=None if admitted_base is None else admitted_base.epoch_id,
        target_epoch_id=admitted_target.epoch_id,
        changed_pointers=changed,
    )


def decode_policy_semantic_diff(value: bytes) -> PolicySemanticDiff:
    if type(value) is not bytes or not 1 <= len(value) <= MAX_SEMANTIC_DIFF_BYTES:
        raise ValueError("stored semantic diff bytes are invalid")
    try:
        decoded = json.loads(value)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("stored semantic diff is not canonical JSON") from error
    if type(decoded) is not dict or set(decoded) != {
        "baseEpochId",
        "changedPointers",
        "scope",
        "targetEpochId",
        "version",
    }:
        raise ValueError("stored semantic diff shape is invalid")
    scope_value = decoded["scope"]
    pointers = decoded["changedPointers"]
    if type(scope_value) is not dict or set(scope_value) != {"installationId", "repositoryId"}:
        raise ValueError("stored semantic diff scope is invalid")
    if type(pointers) is not list or any(type(pointer) is not str for pointer in pointers):
        raise ValueError("stored semantic diff pointers are invalid")
    diff = PolicySemanticDiff.create(
        scope=RepositoryScope(scope_value["installationId"], scope_value["repositoryId"]),
        base_epoch_id=decoded["baseEpochId"],
        target_epoch_id=decoded["targetEpochId"],
        changed_pointers=tuple(pointers),
    )
    if decoded["version"] != SEMANTIC_DIFF_VERSION or diff.canonical_bytes != value:
        raise ValueError("stored semantic diff is not the canonical versioned projection")
    return diff


def _changed_pointers(left: object, right: object, pointer: str) -> list[str]:
    if type(left) is not type(right):
        return [pointer]
    if type(left) is dict:
        left_object = cast(dict[str, object], left)
        right_object = cast(dict[str, object], right)
        pointers: list[str] = []
        keys = sorted(set(left_object) | set(right_object), key=utf16_sort_key)
        for key in keys:
            left_value = left_object.get(key, _MISSING)
            right_value = right_object.get(key, _MISSING)
            child = f"{pointer}/{_escape_pointer_token(key)}"
            if left_value is _MISSING or right_value is _MISSING:
                pointers.append(child)
            else:
                pointers.extend(_changed_pointers(left_value, right_value, child))
        return pointers
    if type(left) is list:
        return [] if left == right else [pointer]
    return [] if left == right else [pointer]


def _decode_document(value: bytes) -> object:
    return json.loads(value)


def _projection(
    scope: RepositoryScope,
    base_epoch_id: str | None,
    target_epoch_id: str,
    pointers: tuple[str, ...],
) -> dict[str, object]:
    return {
        "version": SEMANTIC_DIFF_VERSION,
        "scope": {
            "installationId": scope.installation_id,
            "repositoryId": scope.repository_id,
        },
        "baseEpochId": base_epoch_id,
        "targetEpochId": target_epoch_id,
        "changedPointers": list(pointers),
    }


def _escape_pointer_token(value: str) -> str:
    return value.replace("~", "~0").replace("/", "~1")


def _require_json_pointer(value: object) -> None:
    if type(value) is not str or any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError("semantic diff pointer must contain Unicode scalar text")
    if not value:
        return
    if not value.startswith("/"):
        raise ValueError("semantic diff pointer must be an RFC 6901 JSON Pointer")
    index = 0
    while index < len(value):
        if value[index] == "~":
            if index + 1 >= len(value) or value[index + 1] not in {"0", "1"}:
                raise ValueError("semantic diff pointer has an invalid escape")
            index += 1
        index += 1


def _require_epoch_id(value: object, *, allow_none: bool) -> None:
    if value is None and allow_none:
        return
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("semantic diff epoch identity is invalid")

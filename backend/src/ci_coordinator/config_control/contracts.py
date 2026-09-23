from __future__ import annotations

import math
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from typing import Final, Literal, Self, cast

from ci_coordinator.config_control.limits import (
    MAX_CONFIG_CONTRACT_ID_UTF8_BYTES,
    MAX_POLICY_SOURCE_BYTES,
)
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

type PolicySourceFormat = Literal["json", "yaml-1.2"]
type PolicyPhase = Literal[
    "source",
    "decode",
    "parse",
    "structure",
    "semantics",
    "feasibility",
    "compile",
]

SOURCE_FORMATS: Final = ("json", "yaml-1.2")
_PHASES: Final = frozenset(
    {"source", "decode", "parse", "structure", "semantics", "feasibility", "compile"}
)
_SHA256_PATTERN: Final = re.compile(r"^[0-9a-f]{64}$")
_MAX_OUTPUT_BYTES: Final = 4_194_304

type _FrozenJsonScalar = bool | int | float | str | None


@dataclass(frozen=True, slots=True)
class _FrozenJsonObject(Mapping[str, object]):
    _items: tuple[tuple[str, _FrozenJsonValue], ...]

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> Self:
        items: list[tuple[str, _FrozenJsonValue]] = []
        for key in sorted(value):
            _require_unicode_scalar_string(key, field_name="JSON object key", allow_empty=True)
            items.append((key, _freeze_json(value[key])))
        return cls(tuple(items))

    def __getitem__(self, key: str) -> _FrozenJsonValue:
        for candidate, value in self._items:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)


type _FrozenJsonValue = _FrozenJsonScalar | tuple["_FrozenJsonValue", ...] | _FrozenJsonObject


def _freeze_json(value: object) -> _FrozenJsonValue:
    if value is None or type(value) is bool:
        return value
    if type(value) is str:
        _require_unicode_scalar_string(value, field_name="JSON string", allow_empty=True)
        return value
    if type(value) is int:
        if abs(value) > MAX_SAFE_JSON_INTEGER:
            raise ValueError("integer exceeds the JSON safe-integer range")
        return value
    if type(value) is float:
        if not math.isfinite(value) or abs(value) > MAX_SAFE_JSON_INTEGER:
            raise ValueError("number must be finite and within the JSON safe-integer magnitude")
        return value
    if type(value) is list or type(value) is tuple:
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError("JSON object keys must be strings")
        return _FrozenJsonObject.from_mapping(cast(Mapping[str, object], value))
    raise ValueError(f"{type(value).__name__} is not a JSON-domain value")


@dataclass(frozen=True, slots=True)
class RepositoryScope:
    installation_id: int
    repository_id: int

    def __post_init__(self) -> None:
        for field_name, value in (
            ("installation_id", self.installation_id),
            ("repository_id", self.repository_id),
        ):
            if type(value) is not int or not 1 <= value <= MAX_SAFE_JSON_INTEGER:
                raise ValueError(f"{field_name} must be a positive JSON safe integer")


@dataclass(frozen=True, slots=True)
class PolicyDiagnostic:
    code: str
    phase: PolicyPhase
    rule_id: str
    instance_pointer: str
    parameters: Mapping[str, object]

    def __post_init__(self) -> None:
        _require_unicode_scalar_string(self.code, field_name="code")
        if self.phase not in _PHASES:
            raise ValueError("phase must be an admitted policy phase")
        _require_unicode_scalar_string(self.rule_id, field_name="rule_id")
        _require_json_pointer(self.instance_pointer)
        if not isinstance(self.parameters, Mapping):
            raise ValueError("parameters must be a JSON object")
        object.__setattr__(self, "parameters", _FrozenJsonObject.from_mapping(self.parameters))


@dataclass(frozen=True, slots=True)
class ValidatedEpochDraft:
    source_format: PolicySourceFormat
    source_bytes: bytes
    scope: RepositoryScope
    normalized_document_bytes: bytes
    compiled_policy_bytes: bytes
    document_schema_id: str
    document_profile_id: str
    semantic_profile_id: str
    compiled_schema_id: str
    producer_resource_profile_id: str
    producer_byte_profile_id: str
    producer_feasibility_profile_id: str
    source_hash: str
    document_hash: str
    epoch_hash: str
    epoch_id: str

    def __post_init__(self) -> None:
        if self.source_format not in SOURCE_FORMATS:
            raise ValueError("source_format must be json or yaml-1.2")
        _require_bytes(
            self.source_bytes,
            field_name="source_bytes",
            minimum=0,
            maximum=MAX_POLICY_SOURCE_BYTES,
        )
        if not isinstance(self.scope, RepositoryScope):
            raise ValueError("scope must be a RepositoryScope")
        _require_bytes(
            self.normalized_document_bytes,
            field_name="normalized_document_bytes",
            minimum=1,
            maximum=_MAX_OUTPUT_BYTES,
        )
        _require_bytes(
            self.compiled_policy_bytes,
            field_name="compiled_policy_bytes",
            minimum=1,
            maximum=_MAX_OUTPUT_BYTES,
        )
        for field_name in (
            "document_schema_id",
            "document_profile_id",
            "semantic_profile_id",
            "compiled_schema_id",
            "producer_resource_profile_id",
            "producer_byte_profile_id",
            "producer_feasibility_profile_id",
        ):
            _require_unicode_scalar_string(
                getattr(self, field_name),
                field_name=field_name,
                maximum_utf8_bytes=MAX_CONFIG_CONTRACT_ID_UTF8_BYTES,
            )
        for field_name in ("source_hash", "document_hash", "epoch_hash", "epoch_id"):
            value = getattr(self, field_name)
            if type(value) is not str or _SHA256_PATTERN.fullmatch(value) is None:
                raise ValueError(f"{field_name} must be lowercase SHA-256 hexadecimal")


type PolicyAdmissionResult = ValidatedEpochDraft | tuple[PolicyDiagnostic]


def _require_bytes(value: object, *, field_name: str, minimum: int, maximum: int) -> None:
    if type(value) is not bytes or not minimum <= len(value) <= maximum:
        raise ValueError(f"{field_name} must be exact bytes with length {minimum}..{maximum}")


def _require_unicode_scalar_string(
    value: object,
    *,
    field_name: str,
    allow_empty: bool = False,
    maximum_utf8_bytes: int | None = None,
) -> str:
    if type(value) is not str or (not allow_empty and not value):
        qualification = "Unicode scalar" if allow_empty else "non-empty Unicode scalar"
        raise ValueError(f"{field_name} must be a {qualification} string")
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ValueError(f"{field_name} must contain only Unicode scalar values")
    if maximum_utf8_bytes is not None and len(value.encode("utf-8")) > maximum_utf8_bytes:
        raise ValueError(f"{field_name} exceeds its UTF-8 byte limit")
    return value


def _require_json_pointer(value: object) -> None:
    pointer = _require_unicode_scalar_string(
        value,
        field_name="instance_pointer",
        allow_empty=True,
    )
    if not pointer:
        return
    if not pointer.startswith("/"):
        raise ValueError("instance_pointer must be an RFC 6901 JSON Pointer")
    index = 0
    while index < len(pointer):
        if pointer[index] == "~":
            if index + 1 >= len(pointer) or pointer[index + 1] not in {"0", "1"}:
                raise ValueError("instance_pointer contains an invalid RFC 6901 escape")
            index += 1
        index += 1

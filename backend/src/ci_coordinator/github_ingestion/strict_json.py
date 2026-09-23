from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal, Self

from ci_coordinator.github_ingestion.payload_limits import WebhookPayloadLimits
from ci_coordinator.kernel import is_safe_json_integer


class JsonPayloadError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class FrozenJsonObject(Mapping[str, object]):
    _items: tuple[tuple[str, FrozenJsonValue], ...]

    @classmethod
    def from_pairs(cls, pairs: tuple[tuple[str, FrozenJsonValue], ...]) -> Self:
        return cls(pairs)

    def __getitem__(self, key: str) -> FrozenJsonValue:
        for candidate, value in self._items:
            if candidate == key:
                return value
        raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        return (key for key, _ in self._items)

    def __len__(self) -> int:
        return len(self._items)


@dataclass(frozen=True, slots=True)
class _ObjectPairs:
    pairs: tuple[tuple[str, object], ...]


type FrozenJsonScalar = bool | int | Decimal | str | None
type FrozenJsonValue = FrozenJsonScalar | tuple["FrozenJsonValue", ...] | FrozenJsonObject


def parse_json_object(raw_body: bytes, limits: WebhookPayloadLimits) -> FrozenJsonObject:
    if type(raw_body) is not bytes:
        raise JsonPayloadError("invalid_webhook_body")
    if len(raw_body) > limits.maximum_body_bytes:
        raise JsonPayloadError("webhook_payload_too_large")
    try:
        decoded_body = raw_body.decode("utf-8")
    except UnicodeDecodeError as error:
        raise JsonPayloadError("invalid_json") from error

    _StructuralPreflight(decoded_body, limits).run()
    try:
        parsed: object = json.loads(
            decoded_body,
            object_pairs_hook=_object_pairs,
            parse_float=Decimal,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError, ValueError) as error:
        raise JsonPayloadError("invalid_json") from error

    frozen = _FreezeState(limits).freeze(parsed, depth=1)
    if not isinstance(frozen, FrozenJsonObject):
        raise JsonPayloadError("webhook_payload_root_not_object")
    return frozen


def _object_pairs(pairs: list[tuple[str, object]]) -> _ObjectPairs:
    return _ObjectPairs(tuple(pairs))


def _reject_json_constant(_: str) -> object:
    raise ValueError("non-finite JSON constant")


@dataclass(slots=True)
class _Frame:
    kind: Literal["array", "object"]
    member_count: int = 0


class _StructuralPreflight:
    def __init__(self, source: str, limits: WebhookPayloadLimits) -> None:
        self._source = source
        self._limits = limits
        self._index = 0
        self._node_count = 0

    def run(self) -> None:
        self._skip_whitespace()
        self._parse_value(depth=1, parent=None)
        self._skip_whitespace()
        if self._index != len(self._source):
            raise JsonPayloadError("invalid_json")

    def _parse_value(self, *, depth: int, parent: _Frame | None) -> None:
        if depth > self._limits.maximum_nesting_depth:
            raise JsonPayloadError("webhook_payload_nesting_limit_exceeded")
        self._add_member(parent)
        self._add_node()
        if self._index >= len(self._source):
            raise JsonPayloadError("invalid_json")
        marker = self._source[self._index]
        if marker == "{":
            self._index += 1
            self._parse_object(depth)
            return
        if marker == "[":
            self._index += 1
            self._parse_array(depth)
            return
        if marker == '"':
            self._parse_string()
            return
        self._parse_scalar()

    def _parse_object(self, depth: int) -> None:
        frame = _Frame(kind="object")
        self._skip_whitespace()
        if self._consume("}"):
            return
        while True:
            if self._index >= len(self._source) or self._source[self._index] != '"':
                raise JsonPayloadError("invalid_json")
            self._parse_string()
            self._skip_whitespace()
            self._expect(":")
            self._skip_whitespace()
            self._parse_value(depth=depth + 1, parent=frame)
            self._skip_whitespace()
            if self._consume("}"):
                return
            self._expect(",")
            self._skip_whitespace()

    def _parse_array(self, depth: int) -> None:
        frame = _Frame(kind="array")
        self._skip_whitespace()
        if self._consume("]"):
            return
        while True:
            self._parse_value(depth=depth + 1, parent=frame)
            self._skip_whitespace()
            if self._consume("]"):
                return
            self._expect(",")
            self._skip_whitespace()

    def _parse_string(self) -> None:
        self._expect('"')
        code_point_count = 0
        while self._index < len(self._source):
            character = self._source[self._index]
            self._index += 1
            if character == '"':
                return
            if ord(character) < 0x20:
                raise JsonPayloadError("invalid_json")
            if character != "\\":
                code_point_count = self._add_string_code_point(code_point_count)
                continue
            if self._index >= len(self._source):
                raise JsonPayloadError("invalid_json")
            escape = self._source[self._index]
            self._index += 1
            if escape in {'"', "\\", "/", "b", "f", "n", "r", "t"}:
                code_point_count = self._add_string_code_point(code_point_count)
                continue
            if escape != "u":
                raise JsonPayloadError("invalid_json")
            code_unit = self._parse_unicode_escape()
            if 0xD800 <= code_unit <= 0xDBFF:
                if not self._source.startswith("\\u", self._index):
                    raise JsonPayloadError("invalid_json")
                self._index += 2
                low_surrogate = self._parse_unicode_escape()
                if not 0xDC00 <= low_surrogate <= 0xDFFF:
                    raise JsonPayloadError("invalid_json")
            elif 0xDC00 <= code_unit <= 0xDFFF:
                raise JsonPayloadError("invalid_json")
            code_point_count = self._add_string_code_point(code_point_count)
        raise JsonPayloadError("invalid_json")

    def _parse_unicode_escape(self) -> int:
        if self._index + 4 > len(self._source):
            raise JsonPayloadError("invalid_json")
        hex_digits = self._source[self._index : self._index + 4]
        if any(character not in "0123456789abcdefABCDEF" for character in hex_digits):
            raise JsonPayloadError("invalid_json")
        self._index += 4
        return int(hex_digits, 16)

    def _add_string_code_point(self, count: int) -> int:
        next_count = count + 1
        if next_count > self._limits.maximum_string_code_points:
            raise JsonPayloadError("webhook_payload_string_limit_exceeded")
        return next_count

    def _parse_scalar(self) -> None:
        start = self._index
        while self._index < len(self._source):
            character = self._source[self._index]
            if character in ",]}" or character.isspace():
                break
            self._index += 1
        if self._index == start:
            raise JsonPayloadError("invalid_json")

    def _add_member(self, parent: _Frame | None) -> None:
        if parent is None:
            return
        parent.member_count += 1
        maximum = (
            self._limits.maximum_array_items
            if parent.kind == "array"
            else self._limits.maximum_object_members
        )
        if parent.member_count > maximum:
            code = (
                "webhook_payload_array_limit_exceeded"
                if parent.kind == "array"
                else "webhook_payload_object_limit_exceeded"
            )
            raise JsonPayloadError(code)

    def _add_node(self) -> None:
        self._node_count += 1
        if self._node_count > self._limits.maximum_json_nodes:
            raise JsonPayloadError("webhook_payload_node_limit_exceeded")

    def _expect(self, marker: str) -> None:
        if not self._consume(marker):
            raise JsonPayloadError("invalid_json")

    def _consume(self, marker: str) -> bool:
        if self._index < len(self._source) and self._source[self._index] == marker:
            self._index += 1
            return True
        return False

    def _skip_whitespace(self) -> None:
        while self._index < len(self._source) and self._source[self._index] in " \t\r\n":
            self._index += 1


class _FreezeState:
    def __init__(self, limits: WebhookPayloadLimits) -> None:
        self._limits = limits
        self._node_count = 0

    def freeze(self, value: object, *, depth: int) -> FrozenJsonValue:
        if depth > self._limits.maximum_nesting_depth:
            raise JsonPayloadError("webhook_payload_nesting_limit_exceeded")
        self._add_node()
        if value is None or type(value) is bool:
            return value
        if type(value) is str:
            self._admit_string(value)
            return value
        if type(value) is int:
            if not is_safe_json_integer(value):
                raise JsonPayloadError("invalid_json")
            return value
        if isinstance(value, Decimal):
            if not value.is_finite():
                raise JsonPayloadError("invalid_json")
            return value
        if type(value) is list:
            items = value
            if len(items) > self._limits.maximum_array_items:
                raise JsonPayloadError("webhook_payload_array_limit_exceeded")
            return tuple(self.freeze(item, depth=depth + 1) for item in items)
        if isinstance(value, _ObjectPairs):
            if len(value.pairs) > self._limits.maximum_object_members:
                raise JsonPayloadError("webhook_payload_object_limit_exceeded")
            return self._freeze_object(value, depth=depth)
        raise JsonPayloadError("invalid_json")

    def _freeze_object(self, value: _ObjectPairs, *, depth: int) -> FrozenJsonObject:
        frozen_pairs: list[tuple[str, FrozenJsonValue]] = []
        seen_keys: set[str] = set()
        for key, item in value.pairs:
            self._admit_string(key)
            if key in seen_keys:
                raise JsonPayloadError("invalid_json")
            seen_keys.add(key)
            frozen_pairs.append((key, self.freeze(item, depth=depth + 1)))
        return FrozenJsonObject.from_pairs(tuple(frozen_pairs))

    def _admit_string(self, value: str) -> None:
        if len(value) > self._limits.maximum_string_code_points:
            raise JsonPayloadError("webhook_payload_string_limit_exceeded")
        if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise JsonPayloadError("invalid_json")

    def _add_node(self) -> None:
        self._node_count += 1
        if self._node_count > self._limits.maximum_json_nodes:
            raise JsonPayloadError("webhook_payload_node_limit_exceeded")

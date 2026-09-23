"""Strict JSON admission for exact untrusted bytes."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from string import hexdigits
from typing import NoReturn, cast

from ci_coordinator.kernel.canonical_json import JsonResourceLimits


class StrictJsonError(ValueError):
    """The input is not admitted strict JSON bytes."""


def load_strict_json(
    body: bytes,
    *,
    max_bytes: int | None = None,
    resource_limits: JsonResourceLimits | None = None,
) -> object:
    """Decode exact bounded UTF-8 bytes without widening JSON semantics."""
    if type(body) is not bytes:
        raise StrictJsonError("strict JSON input must be exact bytes")
    if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 0):
        raise StrictJsonError("strict JSON byte bound must be a non-negative integer")
    if max_bytes is not None and len(body) > max_bytes:
        raise StrictJsonError("strict JSON input exceeds its byte bound")
    if resource_limits is not None and type(resource_limits) is not JsonResourceLimits:
        raise StrictJsonError("strict JSON resource limits must be exact")

    try:
        text = body.decode("utf-8", errors="strict")
        if resource_limits is not None:
            _JsonResourcePreflight(text, resource_limits).run()
        value = cast(
            object,
            json.loads(
                text,
                object_pairs_hook=_unique_scalar_key_object,
                parse_constant=_reject_non_finite_constant,
                parse_float=_finite_float,
            ),
        )
        _admit_scalar_strings(value)
        return value
    except (RecursionError, UnicodeError, ValueError) as error:
        raise StrictJsonError("invalid strict JSON bytes") from error


def _unique_scalar_key_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, item in pairs:
        if key in value or not _unicode_scalar_string(key):
            raise ValueError("duplicate or unsafe JSON object key")
        value[key] = item
    return value


def _reject_non_finite_constant(value: str) -> NoReturn:
    raise ValueError(f"non-finite JSON constant: {value}")


def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("JSON number exceeds the finite float domain")
    return parsed


def _unicode_scalar_string(value: object) -> bool:
    return type(value) is str and (
        value.isascii() or not any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    )


def _admit_scalar_strings(value: object) -> None:
    pending = [value]
    while pending:
        current = pending.pop()
        if type(current) is str:
            if not _unicode_scalar_string(current):
                raise ValueError("unsafe JSON string value")
        elif type(current) is list:
            pending.extend(current)
        elif type(current) is dict:
            pending.extend(current.values())


@dataclass(slots=True)
class _JsonResourcePreflight:
    text: str
    limits: JsonResourceLimits
    index: int = 0
    nodes: int = 0

    def run(self) -> None:
        self._skip_whitespace()
        self._parse_value(depth=0)
        self._skip_whitespace()
        if self.index != len(self.text):
            self._fail_syntax()

    def _parse_value(self, *, depth: int) -> None:
        if self.index >= len(self.text):
            self._fail_syntax()
        self._visit(depth)
        character = self.text[self.index]
        if character == "{":
            self._parse_object(depth=depth)
            return
        if character == "[":
            self._parse_array(depth=depth)
            return
        if character == '"':
            self._parse_string()
            return
        for token in ("true", "false", "null"):
            if self.text.startswith(token, self.index):
                self.index += len(token)
                return
        if character == "-" or self._is_ascii_digit(character):
            self._parse_number()
            return
        self._fail_syntax()

    def _parse_object(self, *, depth: int) -> None:
        self.index += 1
        self._skip_whitespace()
        if self._consume("}"):
            return
        while True:
            if self.index >= len(self.text) or self.text[self.index] != '"':
                self._fail_syntax()
            self._parse_string()
            self._skip_whitespace()
            if not self._consume(":"):
                self._fail_syntax()
            self._skip_whitespace()
            self._parse_value(depth=depth + 1)
            self._skip_whitespace()
            if self._consume("}"):
                return
            if not self._consume(","):
                self._fail_syntax()
            self._skip_whitespace()

    def _parse_array(self, *, depth: int) -> None:
        self.index += 1
        self._skip_whitespace()
        if self._consume("]"):
            return
        while True:
            self._parse_value(depth=depth + 1)
            self._skip_whitespace()
            if self._consume("]"):
                return
            if not self._consume(","):
                self._fail_syntax()
            self._skip_whitespace()

    def _parse_string(self) -> None:
        self.index += 1
        while self.index < len(self.text):
            character = self.text[self.index]
            if character == '"':
                self.index += 1
                return
            if ord(character) < 0x20:
                self._fail_syntax()
            if character != "\\":
                self.index += 1
                continue
            self.index += 1
            if self.index >= len(self.text):
                self._fail_syntax()
            escaped = self.text[self.index]
            if escaped in '"\\/bfnrt':
                self.index += 1
                continue
            if escaped != "u" or self.index + 4 >= len(self.text):
                self._fail_syntax()
            digits = self.text[self.index + 1 : self.index + 5]
            if len(digits) != 4 or any(digit not in hexdigits for digit in digits):
                self._fail_syntax()
            self.index += 5
        self._fail_syntax()

    def _parse_number(self) -> None:
        if self._consume("-") and self.index >= len(self.text):
            self._fail_syntax()
        if self._consume("0"):
            pass
        elif self.index < len(self.text) and self.text[self.index] in "123456789":
            self.index += 1
            self._consume_digits()
        else:
            self._fail_syntax()
        if self._consume("."):
            if self.index >= len(self.text) or not self._is_ascii_digit(self.text[self.index]):
                self._fail_syntax()
            self._consume_digits()
        if self.index < len(self.text) and self.text[self.index] in "eE":
            self.index += 1
            if self.index < len(self.text) and self.text[self.index] in "+-":
                self.index += 1
            if self.index >= len(self.text) or not self._is_ascii_digit(self.text[self.index]):
                self._fail_syntax()
            self._consume_digits()

    def _consume_digits(self) -> None:
        while self.index < len(self.text) and self._is_ascii_digit(self.text[self.index]):
            self.index += 1

    def _visit(self, depth: int) -> None:
        if depth > self.limits.max_depth:
            raise StrictJsonError("strict JSON input exceeds its depth bound")
        self.nodes += 1
        if self.nodes > self.limits.max_nodes:
            raise StrictJsonError("strict JSON input exceeds its node bound")

    def _skip_whitespace(self) -> None:
        while self.index < len(self.text) and self.text[self.index] in " \t\r\n":
            self.index += 1

    def _consume(self, token: str) -> bool:
        if not self.text.startswith(token, self.index):
            return False
        self.index += len(token)
        return True

    @staticmethod
    def _is_ascii_digit(character: str) -> bool:
        return "0" <= character <= "9"

    @staticmethod
    def _fail_syntax() -> NoReturn:
        raise StrictJsonError("invalid strict JSON bytes")

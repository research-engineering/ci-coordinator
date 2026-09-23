from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Final, Never, cast

from ci_coordinator.config_control._parser_support import (
    MAX_DEPTH,
    MAX_NODES,
    DocumentParseResult,
    ParsedDocument,
    ParseFailure,
    SchemaCursor,
    array_item_schema,
    diagnostic,
    document_schema,
    integer_token_is_unsafe,
    is_ascii_digit,
    line_column,
    object_child_location,
    schema_for_kind,
)
from ci_coordinator.config_control.contracts import PolicyDiagnostic
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

_JSON_WHITESPACE: Final = frozenset(" \t\r\n")
_HEX_DIGITS: Final = frozenset("0123456789abcdefABCDEF")


def parse_json_document(text: str) -> DocumentParseResult:
    failure = _JsonPreflight(text).run()
    if failure is not None:
        return failure
    try:
        value = _construct_json_document(text)
    except (json.JSONDecodeError, ValueError) as error:
        raise AssertionError("JSON construction diverged from deterministic preflight") from error
    return ParsedDocument(value=value, source_format="json")


def _construct_json_document(text: str) -> object:
    return json.loads(
        text,
        object_pairs_hook=_defensive_object,
        parse_constant=_reject_nonstandard_constant,
    )


@dataclass(slots=True)
class _JsonPreflight:
    text: str
    index: int = 0
    nodes: int = 0

    def run(self) -> PolicyDiagnostic | None:
        try:
            self._skip_whitespace()
            self._parse_value(depth=0, pointer="", schema=document_schema())
            self._skip_whitespace()
            if self.index != len(self.text):
                self._fail_syntax()
            return None
        except ParseFailure as failure:
            return failure.diagnostic

    def _parse_value(self, *, depth: int, pointer: str, schema: SchemaCursor) -> None:
        if self.index >= len(self.text):
            self._fail_syntax()
        character = self.text[self.index]
        if character == "{":
            self._visit(depth=depth, pointer=pointer)
            self._parse_object(
                depth=depth,
                pointer=pointer,
                schema=schema_for_kind(schema, "object"),
            )
            return
        if character == "[":
            self._visit(depth=depth, pointer=pointer)
            self._parse_array(
                depth=depth,
                pointer=pointer,
                schema=schema_for_kind(schema, "array"),
            )
            return
        if character == '"':
            try:
                self._parse_string(pointer=pointer)
            except ParseFailure as failure:
                if failure.diagnostic.code == "parse.invalid_syntax":
                    self._visit(depth=depth, pointer=pointer)
                raise
            self._visit(depth=depth, pointer=pointer)
            return
        for token in ("true", "false", "null"):
            if self.text.startswith(token, self.index):
                self._visit(depth=depth, pointer=pointer)
                self.index += len(token)
                return
        for token in ("-Infinity", "Infinity", "NaN"):
            if self.text.startswith(token, self.index) and self._token_ends_after(token):
                raise ParseFailure(diagnostic("parse.non_finite_number", pointer=pointer))
        if character == "-" or is_ascii_digit(character):
            try:
                self._parse_number(pointer=pointer)
            except ParseFailure as failure:
                if failure.diagnostic.code == "parse.invalid_syntax":
                    self._visit(depth=depth, pointer=pointer)
                raise
            self._visit(depth=depth, pointer=pointer)
            return
        self._fail_syntax()

    def _parse_object(self, *, depth: int, pointer: str, schema: SchemaCursor) -> None:
        self.index += 1
        keys: set[str] = set()
        self._skip_whitespace()
        if self._consume("}"):
            return
        while True:
            if self.index >= len(self.text) or self.text[self.index] != '"':
                self._fail_syntax()
            key = self._parse_string(pointer=pointer)
            if key in keys:
                raise ParseFailure(diagnostic("parse.duplicate_key", pointer=pointer))
            keys.add(key)
            self._skip_whitespace()
            if not self._consume(":"):
                self._fail_syntax()
            self._skip_whitespace()
            child_pointer, child_schema = object_child_location(pointer, schema, key)
            self._parse_value(
                depth=depth + 1,
                pointer=child_pointer,
                schema=child_schema,
            )
            self._skip_whitespace()
            if self._consume("}"):
                return
            if not self._consume(","):
                self._fail_syntax()
            self._skip_whitespace()

    def _parse_array(self, *, depth: int, pointer: str, schema: SchemaCursor) -> None:
        self.index += 1
        index = 0
        self._skip_whitespace()
        if self._consume("]"):
            return
        while True:
            child_pointer = f"{pointer}/{index}"
            self._parse_value(
                depth=depth + 1,
                pointer=child_pointer,
                schema=array_item_schema(schema),
            )
            index += 1
            self._skip_whitespace()
            if self._consume("]"):
                return
            if not self._consume(","):
                self._fail_syntax()
            self._skip_whitespace()

    def _parse_string(self, *, pointer: str) -> str:
        start = self.index
        self.index += 1
        while self.index < len(self.text):
            character = self.text[self.index]
            if character == '"':
                self.index += 1
                return cast(str, json.loads(self.text[start : self.index]))
            if ord(character) < 0x20:
                self._fail_syntax()
            if character != "\\":
                self.index += 1
                continue
            self.index += 1
            if self.index >= len(self.text):
                self._fail_syntax()
            escape = self.text[self.index]
            if escape != "u":
                if escape not in '"\\/bfnrt':
                    self._fail_syntax()
                self.index += 1
                continue
            code_point = self._unicode_escape()
            if 0xD800 <= code_point <= 0xDBFF:
                if not self.text.startswith("\\u", self.index):
                    raise ParseFailure(diagnostic("parse.unpaired_surrogate", pointer=pointer))
                self.index += 1
                low = self._unicode_escape()
                if not 0xDC00 <= low <= 0xDFFF:
                    raise ParseFailure(diagnostic("parse.unpaired_surrogate", pointer=pointer))
            elif 0xDC00 <= code_point <= 0xDFFF:
                raise ParseFailure(diagnostic("parse.unpaired_surrogate", pointer=pointer))
        self._fail_syntax()

    def _unicode_escape(self) -> int:
        if self.text[self.index] != "u" or self.index + 5 > len(self.text):
            self._fail_syntax()
        digits = self.text[self.index + 1 : self.index + 5]
        if len(digits) != 4 or any(character not in _HEX_DIGITS for character in digits):
            self._fail_syntax()
        self.index += 5
        return int(digits, 16)

    def _parse_number(self, *, pointer: str) -> None:
        start = self.index
        self._consume("-")
        if self._consume("0"):
            if self.index < len(self.text) and is_ascii_digit(self.text[self.index]):
                self._fail_syntax()
        else:
            if self.index >= len(self.text) or self.text[self.index] not in "123456789":
                self._fail_syntax()
            while self.index < len(self.text) and is_ascii_digit(self.text[self.index]):
                self.index += 1
        is_integer = True
        if self._consume("."):
            is_integer = False
            if self.index >= len(self.text) or not is_ascii_digit(self.text[self.index]):
                self._fail_syntax()
            while self.index < len(self.text) and is_ascii_digit(self.text[self.index]):
                self.index += 1
        if self.index < len(self.text) and self.text[self.index] in "eE":
            is_integer = False
            self.index += 1
            if self.index < len(self.text) and self.text[self.index] in "+-":
                self.index += 1
            if self.index >= len(self.text) or not is_ascii_digit(self.text[self.index]):
                self._fail_syntax()
            while self.index < len(self.text) and is_ascii_digit(self.text[self.index]):
                self.index += 1
        token = self.text[start : self.index]
        if is_integer:
            if integer_token_is_unsafe(token):
                raise ParseFailure(diagnostic("parse.unsafe_integer", pointer=pointer))
            return
        value = float(token)
        if not math.isfinite(value):
            raise ParseFailure(diagnostic("parse.non_finite_number", pointer=pointer))
        if abs(value) > MAX_SAFE_JSON_INTEGER:
            raise ParseFailure(diagnostic("parse.unsafe_integer", pointer=pointer))

    def _visit(self, *, depth: int, pointer: str) -> None:
        if depth > MAX_DEPTH:
            raise ParseFailure(
                diagnostic(
                    "resource.max_depth_exceeded",
                    pointer=pointer,
                    parameters={"limit": MAX_DEPTH, "observed": depth},
                )
            )
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise ParseFailure(
                diagnostic(
                    "resource.max_nodes_exceeded",
                    pointer=pointer,
                    parameters={"limit": MAX_NODES, "observed": self.nodes},
                )
            )

    def _skip_whitespace(self) -> None:
        while self.index < len(self.text) and self.text[self.index] in _JSON_WHITESPACE:
            self.index += 1

    def _consume(self, token: str) -> bool:
        if self.text.startswith(token, self.index):
            self.index += len(token)
            return True
        return False

    def _token_ends_after(self, token: str) -> bool:
        end = self.index + len(token)
        return (
            end == len(self.text) or self.text[end] in _JSON_WHITESPACE or self.text[end] in ",]}"
        )

    def _fail_syntax(self) -> Never:
        line, column = line_column(self.text, self.index)
        raise ParseFailure(
            diagnostic(
                "parse.invalid_syntax",
                parameters={"format": "json", "line": line, "column": column},
            )
        )


def _defensive_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise AssertionError("JSON construction observed a duplicate key after preflight")
        result[key] = value
    return result


def _reject_nonstandard_constant(token: str) -> Never:
    raise ValueError(f"non-standard JSON constant escaped preflight: {token}")

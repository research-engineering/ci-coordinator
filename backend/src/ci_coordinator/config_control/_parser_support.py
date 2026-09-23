from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from typing import Final, cast

from ci_coordinator.config_control._resources import ContractResource, contract_document
from ci_coordinator.config_control.contracts import (
    PolicyDiagnostic,
    PolicyPhase,
    PolicySourceFormat,
)
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

MAX_DEPTH: Final = 64
MAX_NODES: Final = 32_768


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    value: object
    source_format: PolicySourceFormat


type DocumentParseResult = ParsedDocument | PolicyDiagnostic
type SchemaCursor = dict[str, object] | None


class ParseFailure(Exception):
    def __init__(self, failure: PolicyDiagnostic) -> None:
        super().__init__(failure.code)
        self.diagnostic = failure


def diagnostic(
    code: str,
    *,
    pointer: str = "",
    parameters: dict[str, object] | None = None,
) -> PolicyDiagnostic:
    phase, rule_id = _diagnostic_identity(code)
    return PolicyDiagnostic(
        code=code,
        phase=phase,
        rule_id=rule_id,
        instance_pointer=pointer,
        parameters={} if parameters is None else parameters,
    )


def select_parse_failure(
    *candidates: PolicyDiagnostic | None,
) -> PolicyDiagnostic | None:
    failures = tuple(candidate for candidate in candidates if candidate is not None)
    if not failures:
        return None
    order = _diagnostic_code_order()
    return min(failures, key=lambda failure: order[failure.code])


@cache
def _diagnostic_identity(code: str) -> tuple[PolicyPhase, str]:
    profile = contract_document(ContractResource.DOCUMENT_PROFILE)
    diagnostics = profile.get("diagnostics")
    if not isinstance(diagnostics, dict):
        raise RuntimeError("document profile diagnostics must be an object")
    phases = diagnostics.get("codePhase")
    projection = diagnostics.get("ruleIdProjection")
    if not isinstance(phases, dict) or not isinstance(projection, dict):
        raise RuntimeError("document profile diagnostic identity maps are missing")
    fixed_rules = projection.get("fixedByCode")
    phase = phases.get(code)
    rule_id = fixed_rules.get(code) if isinstance(fixed_rules, dict) else None
    if not isinstance(phase, str) or not isinstance(rule_id, str):
        raise RuntimeError(f"document profile has no fixed diagnostic identity for {code}")
    return cast(PolicyPhase, phase), rule_id


@cache
def _diagnostic_code_order() -> dict[str, int]:
    profile = contract_document(ContractResource.DOCUMENT_PROFILE)
    diagnostics = profile.get("diagnostics")
    codes = diagnostics.get("codes") if isinstance(diagnostics, dict) else None
    if not isinstance(codes, list) or not all(isinstance(code, str) for code in codes):
        raise RuntimeError("document profile diagnostic codes must be a string array")
    if len(set(codes)) != len(codes):
        raise RuntimeError("document profile diagnostic codes must be unique")
    return {code: index for index, code in enumerate(codes)}


@cache
def document_schema() -> dict[str, object]:
    return contract_document(ContractResource.DOCUMENT_SCHEMA)


def object_child_location(
    pointer: str,
    schema: SchemaCursor,
    key: str,
) -> tuple[str, SchemaCursor]:
    effective = schema_for_kind(schema, "object")
    if effective is None:
        return pointer, None
    properties = effective.get("properties")
    if not isinstance(properties, dict):
        return pointer, None
    child = properties.get(key)
    if not isinstance(child, dict):
        return pointer, None
    token = key.replace("~", "~0").replace("/", "~1")
    return f"{pointer}/{token}", cast(dict[str, object], child)


def array_item_schema(schema: SchemaCursor) -> SchemaCursor:
    effective = schema_for_kind(schema, "array")
    if effective is None:
        return None
    items = effective.get("items")
    return cast(dict[str, object], items) if isinstance(items, dict) else None


def schema_for_kind(schema: SchemaCursor, kind: str) -> SchemaCursor:
    effective = _resolve_schema(schema)
    if effective is None:
        return None
    alternatives = effective.get("oneOf")
    if isinstance(alternatives, list):
        matching: list[SchemaCursor] = []
        for candidate in alternatives:
            if not isinstance(candidate, dict):
                continue
            admitted = cast(dict[str, object], candidate)
            if _schema_accepts_kind(admitted, kind):
                matching.append(_resolve_schema(admitted))
        if len(matching) != 1:
            return None
        return matching[0]
    return effective if _schema_accepts_kind(effective, kind) else None


def _schema_accepts_kind(schema: dict[str, object], kind: str) -> bool:
    effective = _resolve_schema(schema)
    if effective is None:
        return False
    declared = effective.get("type")
    return declared == kind or (isinstance(declared, list) and kind in declared)


def _resolve_schema(schema: SchemaCursor) -> SchemaCursor:
    effective = schema
    seen: set[str] = set()
    while effective is not None:
        reference = effective.get("$ref")
        if not isinstance(reference, str):
            return effective
        if not reference.startswith("#/") or reference in seen:
            return None
        seen.add(reference)
        candidate: object = document_schema()
        for token in reference[2:].split("/"):
            decoded = token.replace("~1", "/").replace("~0", "~")
            if not isinstance(candidate, dict):
                return None
            admitted = cast(dict[str, object], candidate)
            if decoded not in admitted:
                return None
            candidate = admitted[decoded]
        effective = cast(dict[str, object], candidate) if isinstance(candidate, dict) else None
    return None


def line_column(text: str, offset: int) -> tuple[int, int]:
    bounded = min(offset, len(text))
    line = 1
    column = 1
    index = 0
    while index < bounded:
        character = text[index]
        if character == "\r":
            line += 1
            column = 1
            index += 2 if index + 1 < bounded and text[index + 1] == "\n" else 1
        elif character == "\n":
            line += 1
            column = 1
            index += 1
        else:
            column += 1
            index += 1
    return line, column


def fold_surrogate_pairs(value: str) -> str:
    result: list[str] = []
    index = 0
    while index < len(value):
        code_point = ord(value[index])
        if 0xD800 <= code_point <= 0xDBFF:
            if index + 1 >= len(value):
                raise ValueError("unpaired high surrogate")
            low = ord(value[index + 1])
            if not 0xDC00 <= low <= 0xDFFF:
                raise ValueError("unpaired high surrogate")
            result.append(chr(0x10000 + ((code_point - 0xD800) << 10) + low - 0xDC00))
            index += 2
            continue
        if 0xDC00 <= code_point <= 0xDFFF:
            raise ValueError("unpaired low surrogate")
        result.append(value[index])
        index += 1
    return "".join(result)


def is_ascii_digit(character: str) -> bool:
    return "0" <= character <= "9"


def integer_token_is_unsafe(token: str) -> bool:
    digits = token.removeprefix("-").lstrip("0") or "0"
    limit = str(MAX_SAFE_JSON_INTEGER)
    return len(digits) > len(limit) or (len(digits) == len(limit) and digits > limit)

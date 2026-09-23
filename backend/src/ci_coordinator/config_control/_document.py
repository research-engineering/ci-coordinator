from __future__ import annotations

from typing import Final, cast

from ci_coordinator.config_control._json_document import parse_json_document
from ci_coordinator.config_control._parser_support import (
    DocumentParseResult,
    ParsedDocument,
    diagnostic,
)
from ci_coordinator.config_control._yaml_document import parse_yaml_document
from ci_coordinator.config_control.contracts import SOURCE_FORMATS, PolicySourceFormat
from ci_coordinator.config_control.limits import MAX_POLICY_SOURCE_BYTES

_UTF8_BOM: Final = b"\xef\xbb\xbf"


def parse_policy_document(raw_source: bytes, source_format: str) -> DocumentParseResult:
    if type(raw_source) is not bytes:
        raise TypeError("raw_source must be exact bytes")
    if type(source_format) is not str:
        raise TypeError("source_format must be an exact string")
    if len(raw_source) > MAX_POLICY_SOURCE_BYTES:
        return diagnostic(
            "source.too_large",
            parameters={"limit": MAX_POLICY_SOURCE_BYTES, "observed": len(raw_source)},
        )
    if source_format not in SOURCE_FORMATS:
        return diagnostic(
            "source.unsupported_format",
            parameters={"accepted": list(SOURCE_FORMATS)},
        )
    try:
        text = raw_source.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        return diagnostic("decode.invalid_utf8", parameters={"offset": error.start})
    if raw_source.startswith(_UTF8_BOM):
        return diagnostic("decode.byte_order_mark_forbidden")

    admitted_format = cast(PolicySourceFormat, source_format)
    value_or_diagnostic = (
        parse_json_document(text) if admitted_format == "json" else parse_yaml_document(text)
    )
    if not isinstance(value_or_diagnostic, ParsedDocument):
        return value_or_diagnostic
    return ParsedDocument(value=value_or_diagnostic.value, source_format=admitted_format)

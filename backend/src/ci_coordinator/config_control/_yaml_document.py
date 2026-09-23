from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Final

from ruamel.yaml import YAML
from ruamel.yaml.constructor import SafeConstructor
from ruamel.yaml.error import MarkedYAMLError, YAMLError
from ruamel.yaml.events import (
    AliasEvent,
    DocumentStartEvent,
    MappingEndEvent,
    MappingStartEvent,
    ScalarEvent,
    SequenceEndEvent,
    SequenceStartEvent,
)
from ruamel.yaml.nodes import ScalarNode

from ci_coordinator.config_control._parser_support import (
    MAX_DEPTH,
    MAX_NODES,
    DocumentParseResult,
    ParsedDocument,
    SchemaCursor,
    array_item_schema,
    diagnostic,
    document_schema,
    fold_surrogate_pairs,
    line_column,
    object_child_location,
    schema_for_kind,
    select_parse_failure,
)
from ci_coordinator.config_control.contracts import PolicyDiagnostic
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

_YAML_JSON_TAGS: Final = frozenset(
    {
        "tag:yaml.org,2002:bool",
        "tag:yaml.org,2002:float",
        "tag:yaml.org,2002:int",
        "tag:yaml.org,2002:map",
        "tag:yaml.org,2002:null",
        "tag:yaml.org,2002:seq",
        "tag:yaml.org,2002:str",
    }
)


class _PolicySafeConstructor(SafeConstructor):
    yaml_constructors = SafeConstructor.yaml_constructors.copy()


@dataclass(slots=True)
class _YamlFrame:
    kind: str
    pointer: str
    depth: int
    schema: SchemaCursor
    expecting_key: bool = True
    pending_key: str | None = None
    seen_keys: set[str] = field(default_factory=set)
    next_index: int = 0


def parse_yaml_document(text: str) -> DocumentParseResult:
    yaml = _new_yaml_loader()
    frames: list[_YamlFrame] = []
    nodes = 0
    documents = 0
    try:
        for event in yaml.parse(text):
            if isinstance(event, DocumentStartEvent):
                documents += 1
                if documents > 1:
                    return diagnostic("parse.multiple_documents_forbidden")
                if event.version is not None and event.version != (1, 2):
                    return _syntax_diagnostic(0, 0)
                continue
            if isinstance(event, AliasEvent):
                return diagnostic("parse.graph_feature_forbidden", pointer=_pointer(frames))
            if isinstance(event, (MappingEndEvent, SequenceEndEvent)):
                if frames:
                    frames.pop()
                    _complete_value(frames)
                continue
            if not isinstance(event, (ScalarEvent, MappingStartEvent, SequenceStartEvent)):
                continue

            pointer, depth, is_key, schema = _location(frames)
            tag = _resolved_tag(yaml, event)
            key = None
            if is_key:
                key, key_failure = _preflight_key(
                    event,
                    tag=tag,
                    frames=frames,
                    pointer=pointer,
                )
            else:
                key_failure = None
            failure = select_parse_failure(
                _tag_kind_failure(event, tag=tag),
                (
                    diagnostic("parse.graph_feature_forbidden", pointer=pointer)
                    if getattr(event, "anchor", None) is not None
                    or tag == "tag:yaml.org,2002:merge"
                    else None
                ),
                (
                    diagnostic("parse.custom_tag_forbidden", pointer=pointer)
                    if tag is not None and tag not in _YAML_JSON_TAGS
                    else None
                ),
                key_failure,
                (
                    _preflight_scalar(event, tag=tag, pointer=pointer)
                    if isinstance(event, ScalarEvent) and not is_key
                    else None
                ),
            )
            if failure is not None:
                return failure
            if is_key:
                if key is None:
                    raise AssertionError("YAML key preflight admitted no key")
                _commit_key(frames, key)
                continue

            if depth > MAX_DEPTH:
                return diagnostic(
                    "resource.max_depth_exceeded",
                    pointer=pointer,
                    parameters={"limit": MAX_DEPTH, "observed": depth},
                )
            nodes += 1
            if nodes > MAX_NODES:
                return diagnostic(
                    "resource.max_nodes_exceeded",
                    pointer=pointer,
                    parameters={"limit": MAX_NODES, "observed": nodes},
                )
            if isinstance(event, ScalarEvent):
                try:
                    scalar = _construct_scalar(yaml, event, tag)
                except (IndexError, KeyError, ValueError):
                    return _syntax_diagnostic(event.start_mark.line, event.start_mark.column)
                failure = _validate_scalar(scalar, pointer=pointer)
                if failure is not None:
                    return failure
                _complete_value(frames)
            elif isinstance(event, MappingStartEvent):
                frames.append(
                    _YamlFrame(
                        kind="mapping",
                        pointer=pointer,
                        depth=depth,
                        schema=schema_for_kind(schema, "object"),
                    )
                )
            else:
                frames.append(
                    _YamlFrame(
                        kind="sequence",
                        pointer=pointer,
                        depth=depth,
                        schema=schema_for_kind(schema, "array"),
                        expecting_key=False,
                    )
                )
    except YAMLError as error:
        return _yaml_error_diagnostic(error, text)

    try:
        value = _construct_yaml_document(text)
    except YAMLError as error:
        return _yaml_error_diagnostic(error, text)
    if nodes == 0:
        value = None
    return ParsedDocument(value=_normalize_value(value), source_format="yaml-1.2")


def _construct_yaml_document(text: str) -> object:
    return _new_yaml_loader().load(text)


def _preflight_key(
    event: ScalarEvent | MappingStartEvent | SequenceStartEvent,
    *,
    tag: str | None,
    frames: list[_YamlFrame],
    pointer: str,
) -> tuple[str | None, PolicyDiagnostic | None]:
    if not isinstance(event, ScalarEvent) or tag != "tag:yaml.org,2002:str":
        return None, diagnostic("parse.non_string_key", pointer=pointer)
    try:
        key = fold_surrogate_pairs(event.value)
    except ValueError:
        return None, diagnostic("parse.unpaired_surrogate", pointer=pointer)
    frame = frames[-1]
    if key in frame.seen_keys:
        return None, diagnostic("parse.duplicate_key", pointer=frame.pointer)
    return key, None


def _commit_key(frames: list[_YamlFrame], key: str) -> None:
    frame = frames[-1]
    frame.seen_keys.add(key)
    frame.pending_key = key
    frame.expecting_key = False


def _tag_kind_failure(
    event: ScalarEvent | MappingStartEvent | SequenceStartEvent,
    *,
    tag: str | None,
) -> PolicyDiagnostic | None:
    if tag not in _YAML_JSON_TAGS:
        return None
    allowed_tags = (
        frozenset(
            {
                "tag:yaml.org,2002:bool",
                "tag:yaml.org,2002:float",
                "tag:yaml.org,2002:int",
                "tag:yaml.org,2002:null",
                "tag:yaml.org,2002:str",
            }
        )
        if isinstance(event, ScalarEvent)
        else frozenset(
            {
                (
                    "tag:yaml.org,2002:map"
                    if isinstance(event, MappingStartEvent)
                    else "tag:yaml.org,2002:seq"
                )
            }
        )
    )
    if tag in allowed_tags:
        return None
    return _syntax_diagnostic(event.start_mark.line, event.start_mark.column)


def _resolved_tag(
    yaml: YAML,
    event: ScalarEvent | MappingStartEvent | SequenceStartEvent,
) -> str | None:
    if event.tag is not None:
        return str(event.tag)
    if isinstance(event, ScalarEvent):
        return str(yaml.resolver.resolve(ScalarNode, event.value, event.implicit))
    return (
        "tag:yaml.org,2002:map" if isinstance(event, MappingStartEvent) else "tag:yaml.org,2002:seq"
    )


def _construct_scalar(yaml: YAML, event: ScalarEvent, tag: str | None) -> object:
    if tag is None:
        raise AssertionError("YAML scalar must have a resolved tag")
    node = ScalarNode(
        tag=tag,
        value=event.value,
        start_mark=event.start_mark,
        end_mark=event.end_mark,
        style=event.style,
    )
    return yaml.constructor.construct_object(node, deep=False)


def _validate_scalar(value: object, *, pointer: str) -> PolicyDiagnostic | None:
    if type(value) is str:
        try:
            fold_surrogate_pairs(value)
        except ValueError:
            return diagnostic("parse.unpaired_surrogate", pointer=pointer)
        return None
    if type(value) is int:
        if abs(value) > MAX_SAFE_JSON_INTEGER:
            return diagnostic("parse.unsafe_integer", pointer=pointer)
        return None
    if type(value) is float:
        if not math.isfinite(value):
            return diagnostic("parse.non_finite_number", pointer=pointer)
        if abs(value) > MAX_SAFE_JSON_INTEGER:
            return diagnostic("parse.unsafe_integer", pointer=pointer)
        return None
    if value is None or type(value) is bool:
        return None
    raise AssertionError("YAML preflight admitted a non-JSON scalar")


def _preflight_scalar(
    event: ScalarEvent,
    *,
    tag: str | None,
    pointer: str,
) -> PolicyDiagnostic | None:
    if tag == "tag:yaml.org,2002:str":
        try:
            fold_surrogate_pairs(event.value)
        except ValueError:
            return diagnostic("parse.unpaired_surrogate", pointer=pointer)
        return None
    if tag == "tag:yaml.org,2002:int":
        if not _integer_token_is_valid(event.value):
            return _syntax_diagnostic(event.start_mark.line, event.start_mark.column)
        if _integer_token_is_unsafe(event.value):
            return diagnostic("parse.unsafe_integer", pointer=pointer)
        return None
    if tag == "tag:yaml.org,2002:float":
        normalized = event.value.replace("_", "").lower()
        if normalized in {".inf", "+.inf", "-.inf", ".nan"}:
            return diagnostic("parse.non_finite_number", pointer=pointer)
        try:
            number = float(normalized)
        except ValueError:
            return _syntax_diagnostic(event.start_mark.line, event.start_mark.column)
        if not math.isfinite(number):
            return diagnostic("parse.non_finite_number", pointer=pointer)
        if abs(number) > MAX_SAFE_JSON_INTEGER:
            return diagnostic("parse.unsafe_integer", pointer=pointer)
        return None
    if tag == "tag:yaml.org,2002:bool":
        if event.value.lower() not in {"false", "true"}:
            return _syntax_diagnostic(event.start_mark.line, event.start_mark.column)
        return None
    if tag == "tag:yaml.org,2002:null":
        if event.value not in {"", "~"} and event.value.lower() != "null":
            return _syntax_diagnostic(event.start_mark.line, event.start_mark.column)
        return None
    return None


def _integer_token_is_valid(token: str) -> bool:
    _, base, magnitude = _integer_parts(token)
    if not magnitude:
        return False
    allowed = {
        2: frozenset("01"),
        8: frozenset("01234567"),
        10: frozenset("0123456789"),
        16: frozenset("0123456789abcdefABCDEF"),
    }[base]
    return all(character in allowed for character in magnitude)


def _integer_token_is_unsafe(token: str) -> bool:
    _, base, magnitude = _integer_parts(token)
    digits = magnitude.lstrip("0").lower() or "0"
    limit = {
        2: format(MAX_SAFE_JSON_INTEGER, "b"),
        8: format(MAX_SAFE_JSON_INTEGER, "o"),
        10: str(MAX_SAFE_JSON_INTEGER),
        16: format(MAX_SAFE_JSON_INTEGER, "x"),
    }[base]
    return len(digits) > len(limit) or (len(digits) == len(limit) and digits > limit)


def _integer_parts(token: str) -> tuple[int, int, str]:
    magnitude = token.replace("_", "")
    sign = -1 if magnitude.startswith("-") else 1
    magnitude = magnitude.removeprefix("+").removeprefix("-")
    base = 10
    if magnitude.startswith(("0b", "0B")):
        base, magnitude = 2, magnitude[2:]
    elif magnitude.startswith(("0o", "0O")):
        base, magnitude = 8, magnitude[2:]
    elif magnitude.startswith(("0x", "0X")):
        base, magnitude = 16, magnitude[2:]
    return sign, base, magnitude


def _construct_policy_integer(_: SafeConstructor, node: ScalarNode) -> int:
    sign, base, magnitude = _integer_parts(node.value)
    significant = magnitude.lstrip("0") or "0"
    return sign * int(significant, base)


def _location(frames: list[_YamlFrame]) -> tuple[str, int, bool, SchemaCursor]:
    if not frames:
        return "", 0, False, document_schema()
    parent = frames[-1]
    if parent.kind == "mapping":
        if parent.expecting_key:
            return parent.pointer, parent.depth + 1, True, None
        if parent.pending_key is None:
            raise AssertionError("YAML mapping value has no key")
        child_pointer, child_schema = object_child_location(
            parent.pointer,
            parent.schema,
            parent.pending_key,
        )
        return child_pointer, parent.depth + 1, False, child_schema
    return (
        f"{parent.pointer}/{parent.next_index}",
        parent.depth + 1,
        False,
        array_item_schema(parent.schema),
    )


def _complete_value(frames: list[_YamlFrame]) -> None:
    if not frames:
        return
    parent = frames[-1]
    if parent.kind == "mapping":
        parent.expecting_key = True
        parent.pending_key = None
    else:
        parent.next_index += 1


def _pointer(frames: list[_YamlFrame]) -> str:
    return _location(frames)[0]


def _new_yaml_loader() -> YAML:
    yaml = YAML(typ="safe", pure=True)
    yaml.Constructor = _PolicySafeConstructor
    yaml.version = (1, 2)
    yaml.allow_duplicate_keys = False
    return yaml


def _normalize_value(value: object) -> object:
    if type(value) is str:
        return fold_surrogate_pairs(value)
    if type(value) is list:
        return [_normalize_value(item) for item in value]
    if type(value) is dict:
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if type(key) is not str:
                raise AssertionError("YAML construction escaped the admitted string-key algebra")
            normalized[fold_surrogate_pairs(key)] = _normalize_value(item)
        return normalized
    if value is None or type(value) in {bool, int, float}:
        return value
    raise AssertionError("YAML construction escaped the admitted JSON algebra")


def _yaml_error_diagnostic(error: YAMLError, text: str) -> PolicyDiagnostic:
    if isinstance(error, MarkedYAMLError):
        mark = error.problem_mark or error.context_mark
        if mark is not None:
            return _syntax_diagnostic(mark.line, mark.column)
    position = getattr(error, "position", 0)
    line, column = line_column(text, position if isinstance(position, int) else 0)
    return _syntax_diagnostic(line - 1, column - 1)


def _syntax_diagnostic(zero_based_line: int, zero_based_column: int) -> PolicyDiagnostic:
    return diagnostic(
        "parse.invalid_syntax",
        parameters={
            "format": "yaml-1.2",
            "line": zero_based_line + 1,
            "column": zero_based_column + 1,
        },
    )


_PolicySafeConstructor.add_constructor("tag:yaml.org,2002:int", _construct_policy_integer)

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

import tree_sitter_javascript
from tree_sitter import Language, Node, Parser

COMMONJS_SUFFIX = ".cjs"
ECMASCRIPT_SUFFIXES = frozenset(
    {COMMONJS_SUFFIX, ".cts", ".js", ".jsx", ".mjs", ".mts", ".ts", ".tsx"}
)
_COMMONJS_LANGUAGE = Language(tree_sitter_javascript.language())
_LINE_LEADING_EXPORT = re.compile(r"(?m)^[\t ]*export\b")
_FROM_SPECIFIER = re.compile(
    r"""(?mx)
    ^[\t ]*(?:import|export)\b
    [^\n;]*?
    \bfrom[\t ]*
    ["']([^"'\r\n]+)["']
    """
)
_SIDE_EFFECT_IMPORT = re.compile(
    r"""(?mx)
    ^[\t ]*import[\t ]*
    ["']([^"'\r\n]+)["']
    """
)


@dataclass(frozen=True, slots=True)
class EcmaScriptSignals:
    first_party_import_contexts: int | None
    recognized_public_declarations: int | None


def ecmascript_signals(path: str, source: str) -> EcmaScriptSignals:
    if path.endswith(COMMONJS_SUFFIX):
        return _commonjs_signals(path, source)
    admitted_source = _mask_comments(source)
    if admitted_source is None:
        return EcmaScriptSignals(
            first_party_import_contexts=None,
            recognized_public_declarations=None,
        )
    specifiers = {
        *(_FROM_SPECIFIER.findall(admitted_source)),
        *(_SIDE_EFFECT_IMPORT.findall(admitted_source)),
    }
    contexts = {
        context for specifier in specifiers if (context := _context(path, specifier)) is not None
    }
    return EcmaScriptSignals(
        first_party_import_contexts=len(contexts),
        recognized_public_declarations=len(_LINE_LEADING_EXPORT.findall(admitted_source)),
    )


class _Unknown:
    pass


_UNKNOWN = _Unknown()


def _commonjs_signals(path: str, source: str) -> EcmaScriptSignals:
    payload = source.encode("utf-8")
    root = Parser(_COMMONJS_LANGUAGE).parse(payload).root_node
    if root.has_error:
        return _unknown_signals()

    contexts: set[str] = set()
    admitted_special_identifiers: set[tuple[int, int]] = set()
    export_count = 0
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "call_expression":
            specifier = _static_require_specifier(node, payload)
            if specifier is _UNKNOWN:
                return _unknown_signals()
            if isinstance(specifier, str):
                function = node.child_by_field_name("function")
                if function is None:
                    return _unknown_signals()
                admitted_special_identifiers.add(_node_key(function))
                context = _context(path, specifier)
                if context is not None:
                    contexts.add(context)
            if _mutates_commonjs_exports(node, payload):
                return _unknown_signals()
        elif node.type == "assignment_expression":
            count = _commonjs_assignment_export_count(node, payload)
            if count is _UNKNOWN:
                return _unknown_signals()
            if isinstance(count, int):
                left = node.child_by_field_name("left")
                if left is None:
                    return _unknown_signals()
                admitted_special_identifiers.update(_special_identifier_keys(left, payload))
                export_count += count
        stack.extend(reversed(node.children))

    if _special_identifier_keys(root, payload) != admitted_special_identifiers:
        return _unknown_signals()

    return EcmaScriptSignals(
        first_party_import_contexts=len(contexts),
        recognized_public_declarations=export_count,
    )


def _unknown_signals() -> EcmaScriptSignals:
    return EcmaScriptSignals(
        first_party_import_contexts=None,
        recognized_public_declarations=None,
    )


def _static_require_specifier(node: Node, payload: bytes) -> str | _Unknown | None:
    function = node.child_by_field_name("function")
    if function is None or _node_text(function, payload) != "require":
        return None
    arguments = node.child_by_field_name("arguments")
    if arguments is None:
        return _UNKNOWN
    named_arguments = arguments.named_children
    if len(named_arguments) != 1 or named_arguments[0].type != "string":
        return _UNKNOWN
    return _plain_string(named_arguments[0], payload)


def _plain_string(node: Node, payload: bytes) -> str | _Unknown:
    literal = _node_text(node, payload)
    if (
        len(literal) < 2
        or literal[0] not in {'"', "'"}
        or literal[-1] != literal[0]
        or "\\" in literal[1:-1]
        or "\n" in literal
        or "\r" in literal
    ):
        return _UNKNOWN
    return literal[1:-1]


def _commonjs_assignment_export_count(node: Node, payload: bytes) -> int | _Unknown | None:
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    if left is None or right is None:
        return _UNKNOWN
    target = _commonjs_export_target(left, payload)
    if target is None:
        return None
    if target is _UNKNOWN:
        return _UNKNOWN
    if target == "member":
        return 1
    return _commonjs_module_export_count(right, payload)


def _commonjs_export_target(node: Node, payload: bytes) -> str | _Unknown | None:
    if node.type != "member_expression":
        return None
    target_text = _node_text(node, payload)
    if target_text.startswith(
        ("exports[", "module.exports[", 'module["exports"]', "module['exports']")
    ):
        return _UNKNOWN
    object_node = node.child_by_field_name("object")
    property_node = node.child_by_field_name("property")
    if object_node is None or property_node is None:
        return _UNKNOWN
    object_text = _node_text(object_node, payload)
    property_text = _node_text(property_node, payload)
    if object_text == "module" and property_text == "exports":
        return "module"
    if object_text == "exports":
        return "member" if property_node.type == "property_identifier" else _UNKNOWN
    if object_text == "module.exports":
        return "member" if property_node.type == "property_identifier" else _UNKNOWN
    return None


def _commonjs_module_export_count(node: Node, payload: bytes) -> int | _Unknown:
    exported = node
    if node.type == "call_expression":
        function = node.child_by_field_name("function")
        arguments = node.child_by_field_name("arguments")
        if (
            function is None
            or _node_text(function, payload) != "Object.freeze"
            or arguments is None
            or len(arguments.named_children) != 1
        ):
            return _UNKNOWN
        exported = arguments.named_children[0]
    if exported.type != "object":
        return _UNKNOWN
    count = 0
    for child in exported.named_children:
        if child.type in {"shorthand_property_identifier", "method_definition"}:
            count += 1
        elif child.type == "pair":
            key = child.child_by_field_name("key")
            if key is None or key.type == "computed_property_name":
                return _UNKNOWN
            count += 1
        else:
            return _UNKNOWN
    return count


def _mutates_commonjs_exports(node: Node, payload: bytes) -> bool:
    function = node.child_by_field_name("function")
    arguments = node.child_by_field_name("arguments")
    if function is None or arguments is None or not arguments.named_children:
        return False
    if _node_text(function, payload) not in {
        "Object.assign",
        "Object.defineProperties",
        "Object.defineProperty",
        "Reflect.defineProperty",
    }:
        return False
    target = _node_text(arguments.named_children[0], payload)
    return target in {"exports", "module.exports"}


def _node_text(node: Node, payload: bytes) -> str:
    return payload[node.start_byte : node.end_byte].decode("utf-8")


def _node_key(node: Node) -> tuple[int, int]:
    return node.start_byte, node.end_byte


def _special_identifier_keys(node: Node, payload: bytes) -> set[tuple[int, int]]:
    keys: set[tuple[int, int]] = set()
    stack = [node]
    while stack:
        current = stack.pop()
        if (
            current.is_named
            and current.type != "property_identifier"
            and _node_text(current, payload) in {"exports", "module", "require"}
        ):
            keys.add(_node_key(current))
        stack.extend(reversed(current.children))
    return keys


def _context(path: str, specifier: str) -> str | None:
    if not specifier.startswith("."):
        return None
    resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), specifier))
    parts = resolved.split("/")
    if parts[:3] == ["frontend", "src", "api"] and len(parts) >= 4:
        return f"frontend-api.{parts[3]}"
    if parts[:2] == ["frontend", "src"] and len(parts) >= 3:
        return f"frontend.{parts[2]}"
    if parts[:2] == ["frontend", "tools"]:
        return "frontend-tools"
    if parts[:3] == ["backend", "src", "ci_coordinator"] and len(parts) >= 4:
        return f"ci-coordinator.{parts[3]}"
    return None


def _mask_comments(source: str) -> str | None:
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    admitted = list(source)
    index = 0
    while index < len(source):
        current = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if current == "/" and following == "/":
            index = _mask_until_line_end(source, admitted, index)
            continue
        if current == "/" and following == "*":
            terminator = source.find("*" + "/", index + 2)
            if terminator < 0:
                return None
            _mask_range_preserving_newlines(admitted, index, terminator + 2)
            index = terminator + 2
            continue
        if current == "/":
            return None
        if current in {"'", '"'}:
            end = _quoted_literal_end(source, index, current)
            if end is None:
                return None
            index = end
            continue
        if current == "`":
            return None
        index += 1
    return "".join(admitted)


def _mask_until_line_end(source: str, admitted: list[str], start: int) -> int:
    end = start
    while end < len(source) and source[end] not in {"\r", "\n"}:
        end += 1
    _mask_range_preserving_newlines(admitted, start, end)
    return end


def _mask_range_preserving_newlines(
    admitted: list[str],
    start: int,
    end: int,
) -> None:
    for index in range(start, end):
        if admitted[index] not in {"\r", "\n"}:
            admitted[index] = " "


def _quoted_literal_end(source: str, start: int, quote: str) -> int | None:
    index = start + 1
    while index < len(source):
        current = source[index]
        if current in {"\r", "\n"}:
            return None
        if current == "\\":
            if index + 1 >= len(source) or source[index + 1] in {"\r", "\n"}:
                return None
            index += 2
            continue
        if current == quote:
            return index + 1
        index += 1
    return None

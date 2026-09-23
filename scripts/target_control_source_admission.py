from __future__ import annotations

from collections.abc import Mapping, Set
from dataclasses import dataclass
from typing import Final, NoReturn

import tree_sitter_javascript
from tree_sitter import Language, Node, Parser

_LANGUAGE: Final = Language(tree_sitter_javascript.language())
_ALLOWED_PROCESS_PROPERTIES: Final = frozenset({"argv", "env", "exitCode", "pid"})
_FORBIDDEN_AMBIENT_IDENTIFIERS: Final = frozenset(
    {"Function", "arguments", "eval", "global", "globalThis"}
)


class TargetControlSourceAdmissionError(ValueError):
    """The target-control source is outside the admitted CommonJS grammar."""


@dataclass(frozen=True, slots=True)
class SourceModuleDependencies:
    path: str
    internal: tuple[str, ...]
    external: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceDependencyGraph:
    modules: tuple[SourceModuleDependencies, ...]

    def module(self, path: str) -> SourceModuleDependencies:
        for module in self.modules:
            if module.path == path:
                return module
        raise KeyError(path)

    @property
    def external_occurrences(self) -> tuple[str, ...]:
        return tuple(sorted(specifier for module in self.modules for specifier in module.external))


def admit_source_dependency_graph(
    sources: Mapping[str, bytes],
    *,
    allowed_external_imports: Set[str],
) -> SourceDependencyGraph:
    """Parse exact source bytes and admit their complete lexical loading graph."""
    source_paths = frozenset(sources)
    if not source_paths:
        raise TargetControlSourceAdmissionError("target-control source set is empty")
    modules = tuple(
        _admit_module(
            source_path,
            sources[source_path],
            source_paths=source_paths,
            allowed_external_imports=allowed_external_imports,
        )
        for source_path in sorted(source_paths)
    )
    return SourceDependencyGraph(modules=modules)


def _admit_module(
    source_path: str,
    payload: bytes,
    *,
    source_paths: Set[str],
    allowed_external_imports: Set[str],
) -> SourceModuleDependencies:
    root = Parser(_LANGUAGE).parse(payload).root_node
    if root.has_error:
        _reject(source_path, root, "source contains a parse error")

    admitted_require_identifiers: set[tuple[int, int]] = set()
    internal: list[str] = []
    external: list[str] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type in {"export_statement", "import", "import_statement"}:
            _reject(source_path, node, "ESM or dynamic import syntax is forbidden")
        if node.type == "call_expression":
            function = node.child_by_field_name("function")
            if function is None:
                _reject(source_path, node, "call expression has no exact function")
            if function.type == "identifier" and _node_text(function, payload) == "require":
                specifier = _literal_require_specifier(source_path, node, payload)
                admitted_require_identifiers.add(_node_key(function))
                if specifier.startswith("./"):
                    target = specifier[2:]
                    if target not in source_paths or specifier != f"./{target}":
                        _reject(source_path, node, "relative require is outside the source set")
                    internal.append(target)
                elif specifier in allowed_external_imports:
                    external.append(specifier)
                else:
                    _reject(source_path, node, "external require is not admitted")
        stack.extend(reversed(node.children))

    _admit_ambient_identifiers(
        source_path,
        root,
        payload,
        admitted_require_identifiers=admitted_require_identifiers,
    )
    if len(internal) != len(set(internal)) or len(external) != len(set(external)):
        _reject(source_path, root, "duplicate source dependency is forbidden")
    return SourceModuleDependencies(
        path=source_path,
        internal=tuple(sorted(internal)),
        external=tuple(sorted(external)),
    )


def _literal_require_specifier(source_path: str, node: Node, payload: bytes) -> str:
    arguments = node.child_by_field_name("arguments")
    if arguments is None or len(arguments.named_children) != 1:
        _reject(source_path, node, "require must have exactly one argument")
    argument = arguments.named_children[0]
    if argument.type != "string":
        _reject(source_path, argument, "require specifier must be a string literal")
    literal = _node_text(argument, payload)
    if (
        len(literal) < 2
        or literal[0] not in {'"', "'"}
        or literal[-1] != literal[0]
        or "\\" in literal[1:-1]
        or "\n" in literal
        or "\r" in literal
    ):
        _reject(source_path, argument, "require specifier must be a plain literal")
    if _node_text(node, payload) != f"require({literal})":
        _reject(source_path, node, "require call syntax is not exact")
    return literal[1:-1]


def _admit_ambient_identifiers(
    source_path: str,
    root: Node,
    payload: bytes,
    *,
    admitted_require_identifiers: Set[tuple[int, int]],
) -> None:
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "identifier":
            name = _node_text(node, payload)
            if "\\" in name:
                _reject(source_path, node, "escaped identifier is forbidden")
            if name == "require" and _node_key(node) not in admitted_require_identifiers:
                _reject(source_path, node, "indirect or alternate require is forbidden")
            if name == "module" and not _is_module_exports_assignment(node, payload):
                _reject(source_path, node, "module may only publish exact exports")
            if name == "process" and not _is_admitted_process_access(node, payload):
                _reject(source_path, node, "process capability is not admitted")
            if name in _FORBIDDEN_AMBIENT_IDENTIFIERS:
                _reject(source_path, node, "ambient loader or dynamic-code capability is forbidden")
        stack.extend(reversed(node.children))


def _is_module_exports_assignment(node: Node, payload: bytes) -> bool:
    member = node.parent
    if (
        member is None
        or member.type != "member_expression"
        or member.child_by_field_name("object") != node
        or _node_text(member, payload) != "module.exports"
    ):
        return False
    assignment = member.parent
    return (
        assignment is not None
        and assignment.type == "assignment_expression"
        and assignment.child_by_field_name("left") == member
    )


def _is_admitted_process_access(node: Node, payload: bytes) -> bool:
    member = node.parent
    if (
        member is None
        or member.type != "member_expression"
        or member.child_by_field_name("object") != node
    ):
        return False
    property_node = member.child_by_field_name("property")
    if property_node is None or property_node.type != "property_identifier":
        return False
    property_name = _node_text(property_node, payload)
    return (
        property_name in _ALLOWED_PROCESS_PROPERTIES
        and _node_text(member, payload) == f"process.{property_name}"
    )


def _node_text(node: Node, payload: bytes) -> str:
    return payload[node.start_byte : node.end_byte].decode("utf-8")


def _node_key(node: Node) -> tuple[int, int]:
    return node.start_byte, node.end_byte


def _reject(source_path: str, node: Node, reason: str) -> NoReturn:
    line = node.start_point.row + 1
    raise TargetControlSourceAdmissionError(f"{source_path}:{line}: {reason}")

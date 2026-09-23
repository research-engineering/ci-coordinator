"""Small immutable YAML 1.2 projection with source coordinates."""

from __future__ import annotations

import math
from collections.abc import Iterator
from dataclasses import dataclass

from ruamel.yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER
from ci_coordinator.workflow_discovery._validation import require_text

type YamlScalarValue = bool | int | float | str | None
type YamlNode = YamlScalar | YamlSequence | YamlMapping


@dataclass(frozen=True, slots=True)
class YamlScalar:
    value: YamlScalarValue
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class YamlSequence:
    items: tuple[YamlNode, ...]
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class YamlMapping:
    entries: tuple[tuple[str, YamlNode], ...]
    line: int
    column: int

    def get(self, key: str) -> YamlNode | None:
        for candidate, value in self.entries:
            if candidate == key:
                return value
        return None


def project_yaml_node(node: Node) -> YamlNode:
    if isinstance(node, ScalarNode):
        return YamlScalar(_scalar_value(node), node.start_mark.line + 1, node.start_mark.column + 1)
    if isinstance(node, SequenceNode):
        return YamlSequence(
            tuple(project_yaml_node(item) for item in node.value),
            node.start_mark.line + 1,
            node.start_mark.column + 1,
        )
    if isinstance(node, MappingNode):
        entries: list[tuple[str, YamlNode]] = []
        seen: set[str] = set()
        for raw_key, raw_value in node.value:
            if not isinstance(raw_key, ScalarNode) or raw_key.tag != "tag:yaml.org,2002:str":
                raise ValueError("YAML mapping keys must be strings")
            key = raw_key.value
            require_text(key, "YAML mapping key", maximum_bytes=4_096, allow_empty=True)
            if key in seen:
                raise ValueError("YAML mapping keys must be unique")
            seen.add(key)
            entries.append((key, project_yaml_node(raw_value)))
        return YamlMapping(
            tuple(entries),
            node.start_mark.line + 1,
            node.start_mark.column + 1,
        )
    raise ValueError("YAML node kind is not admitted")


def node_location(node: YamlNode | None, parent: YamlMapping) -> tuple[int, int]:
    return (parent.line, parent.column) if node is None else (node.line, node.column)


def scalar_text(node: YamlNode | None) -> str | None:
    return node.value if isinstance(node, YamlScalar) and type(node.value) is str else None


def scalar_int(node: YamlNode | None) -> int | None:
    return node.value if isinstance(node, YamlScalar) and type(node.value) is int else None


def contains_expression(value: str) -> bool:
    return "${{" in value


def walk_scalars(
    node: YamlNode,
    *,
    skip_mapping_keys: frozenset[str] = frozenset(),
) -> Iterator[YamlScalar]:
    if isinstance(node, YamlScalar):
        yield node
        return
    if isinstance(node, YamlSequence):
        for item in node.items:
            yield from walk_scalars(item, skip_mapping_keys=skip_mapping_keys)
        return
    for key, value in node.entries:
        if key not in skip_mapping_keys:
            yield from walk_scalars(value, skip_mapping_keys=skip_mapping_keys)


def _scalar_value(node: ScalarNode) -> YamlScalarValue:
    if type(node.value) is not str:
        raise TypeError("YAML scalar value must be exact text")
    value = node.value
    if node.tag == "tag:yaml.org,2002:str":
        return value
    if node.tag == "tag:yaml.org,2002:null":
        return None
    if node.tag == "tag:yaml.org,2002:bool":
        normalized = value.casefold()
        if normalized not in {"true", "false"}:
            raise ValueError("YAML boolean is not canonical YAML 1.2")
        return normalized == "true"
    if node.tag == "tag:yaml.org,2002:int":
        integer = _parse_integer(value)
        if abs(integer) > MAX_SAFE_JSON_INTEGER:
            raise ValueError("YAML integer exceeds the JSON safe-integer range")
        return integer
    if node.tag == "tag:yaml.org,2002:float":
        number = float(value.replace("_", ""))
        if not math.isfinite(number) or abs(number) > MAX_SAFE_JSON_INTEGER:
            raise ValueError("YAML number is not finite and safely bounded")
        return number
    raise ValueError("YAML scalar tag is not admitted")


def _parse_integer(value: str) -> int:
    normalized = value.replace("_", "")
    sign = -1 if normalized.startswith("-") else 1
    unsigned = normalized.lstrip("+-")
    if unsigned.startswith(("0x", "0X")):
        return sign * int(unsigned, 0)
    if unsigned.startswith(("0o", "0O")):
        return sign * int(unsigned, 0)
    return sign * int(unsigned, 10)

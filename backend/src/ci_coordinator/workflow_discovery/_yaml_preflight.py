"""Bounded event-stream preflight before YAML node composition."""

from __future__ import annotations

from dataclasses import dataclass, field

from ruamel.yaml import YAML
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

_JSON_TAGS = frozenset(
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


@dataclass(frozen=True, slots=True)
class YamlPreflightFailure:
    code: str
    line: int
    column: int


@dataclass(slots=True)
class _Frame:
    kind: str
    expecting_key: bool
    seen_keys: set[str] = field(default_factory=set)


def preflight_yaml_events(
    text: str,
    *,
    max_events: int,
    max_nodes: int,
    max_depth: int,
    max_scalar_bytes: int,
) -> YamlPreflightFailure | None:
    yaml = _new_yaml()
    frames: list[_Frame] = []
    events = 0
    nodes = 0
    documents = 0
    try:
        for event in yaml.parse(text):
            events += 1
            if events > max_events:
                return _failure("event_limit_exceeded", event)
            if isinstance(event, DocumentStartEvent):
                documents += 1
                if documents > 1:
                    return _failure("multiple_documents", event)
                if event.version is not None and event.version != (1, 2):
                    return _failure("invalid_yaml_version", event)
                continue
            if isinstance(event, AliasEvent):
                return _failure("alias_forbidden", event)
            if isinstance(event, (MappingEndEvent, SequenceEndEvent)):
                failure = _close_collection(event, frames)
                if failure is not None:
                    return failure
                continue
            if not isinstance(event, (ScalarEvent, MappingStartEvent, SequenceStartEvent)):
                continue
            if getattr(event, "anchor", None) is not None:
                return _failure("anchor_forbidden", event)
            tag = _resolved_tag(yaml, event)
            if tag not in _JSON_TAGS:
                return _failure("custom_tag_forbidden", event)
            nodes += 1
            if nodes > max_nodes:
                return _failure("node_limit_exceeded", event)
            if len(frames) > max_depth:
                return _failure("depth_limit_exceeded", event)
            if isinstance(event, ScalarEvent):
                if len(event.value.encode("utf-8")) > max_scalar_bytes:
                    return _failure("scalar_limit_exceeded", event)
                failure = _admit_scalar(event, tag, frames)
                if failure is not None:
                    return failure
            else:
                if frames and frames[-1].kind == "mapping" and frames[-1].expecting_key:
                    return _failure("mapping_key_invalid", event)
                frames.append(
                    _Frame(
                        kind="mapping" if isinstance(event, MappingStartEvent) else "sequence",
                        expecting_key=isinstance(event, MappingStartEvent),
                    )
                )
    except (RecursionError, MemoryError):
        return YamlPreflightFailure("resource_exhausted", 1, 1)
    except YAMLError as error:
        return _yaml_failure(error)
    return None


def new_yaml() -> YAML:
    return _new_yaml()


def _new_yaml() -> YAML:
    yaml = YAML(typ="safe", pure=True)
    yaml.version = (1, 2)
    yaml.allow_duplicate_keys = False
    return yaml


def _resolved_tag(
    yaml: YAML,
    event: ScalarEvent | MappingStartEvent | SequenceStartEvent,
) -> str:
    if event.tag is not None:
        return str(event.tag)
    if isinstance(event, ScalarEvent):
        return str(yaml.resolver.resolve(ScalarNode, event.value, event.implicit))
    return (
        "tag:yaml.org,2002:map" if isinstance(event, MappingStartEvent) else "tag:yaml.org,2002:seq"
    )


def _admit_scalar(
    event: ScalarEvent,
    tag: str,
    frames: list[_Frame],
) -> YamlPreflightFailure | None:
    if frames and frames[-1].kind == "mapping" and frames[-1].expecting_key:
        if tag != "tag:yaml.org,2002:str":
            return _failure("mapping_key_invalid", event)
        frame = frames[-1]
        if event.value in frame.seen_keys:
            return _failure("duplicate_key", event)
        frame.seen_keys.add(event.value)
        frame.expecting_key = False
        return None
    _complete_value(frames)
    return None


def _close_collection(
    event: MappingEndEvent | SequenceEndEvent,
    frames: list[_Frame],
) -> YamlPreflightFailure | None:
    expected = "mapping" if isinstance(event, MappingEndEvent) else "sequence"
    if not frames or frames[-1].kind != expected:
        return _failure("invalid_syntax", event)
    frame = frames.pop()
    if frame.kind == "mapping" and not frame.expecting_key:
        return _failure("invalid_syntax", event)
    _complete_value(frames)
    return None


def _complete_value(frames: list[_Frame]) -> None:
    if frames and frames[-1].kind == "mapping":
        frames[-1].expecting_key = True


def _failure(code: str, event: object) -> YamlPreflightFailure:
    mark = getattr(event, "start_mark", None)
    return YamlPreflightFailure(
        code,
        1 if mark is None else mark.line + 1,
        1 if mark is None else mark.column + 1,
    )


def _yaml_failure(error: YAMLError) -> YamlPreflightFailure:
    if isinstance(error, MarkedYAMLError):
        mark = error.problem_mark or error.context_mark
        if mark is not None:
            return YamlPreflightFailure("invalid_syntax", mark.line + 1, mark.column + 1)
    return YamlPreflightFailure("invalid_syntax", 1, 1)

from __future__ import annotations

from typing import Literal, Never

import pytest
from ruamel.yaml import YAML
from ruamel.yaml.composer import Composer, MaxDepthExceededError
from ruamel.yaml.constructor import SafeConstructor
from ruamel.yaml.nodes import Node

from ci_coordinator.repo_context import (
    RevisionWorkflowCapability,
    parse_workflow_capability,
    workflow_syntax,
)

_PATH = ".github/workflows/check.yml"
_REVISION = "a" * 40
_WORKFLOW = b"on: push\njobs:\n  test:\n    runs-on: ubuntu-24.04\n"


def _parse(content: bytes) -> RevisionWorkflowCapability | None:
    return parse_workflow_capability(content, path=_PATH, revision_sha=_REVISION)


def _at_depth(depth: int, style: Literal["flow", "block"]) -> bytes:
    # Root is one, the first metadata sequence is two, and the leaf counts too.
    if style == "flow":
        metadata = b"metadata: " + b"[" * (depth - 2) + b"leaf" + b"]" * (depth - 2)
    else:
        metadata = b"metadata:\n" + b"".join(
            b"  " * level + b"-\n" for level in range(1, depth - 1)
        )
        metadata += b"  " * (depth - 1) + b"leaf"
    return _WORKFLOW + metadata + b"\n"


@pytest.mark.parametrize("style", ["flow", "block"])
def test_complete_capability_admits_depth_64_but_not_65(
    style: Literal["flow", "block"],
) -> None:
    baseline = _parse(_WORKFLOW)
    assert baseline is not None
    at_limit = _parse(_at_depth(64, style))
    assert at_limit is not None
    assert at_limit == baseline
    assert at_limit.to_stable_mapping() == baseline.to_stable_mapping()
    assert _parse(_at_depth(65, style)) is None


@pytest.mark.parametrize(("depth", "style"), [(65, "flow"), (40_000, "flow"), (200, "block")])
def test_excess_depth_is_rejected_before_graph_validation(
    monkeypatch: pytest.MonkeyPatch,
    depth: int,
    style: Literal["flow", "block"],
) -> None:
    assert _parse(_WORKFLOW) is not None
    content = _at_depth(depth, style)
    assert len(content) < workflow_syntax.MAX_WORKFLOW_FILE_BYTES

    def unexpected_graph(_root: Node) -> Never:
        raise AssertionError("composition must reject excess depth before graph validation")

    monkeypatch.setattr(workflow_syntax, "_validate_node_graph", unexpected_graph)
    assert _parse(content) is None


def test_single_yaml_instance_enforces_its_library_depth_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert _parse(_at_depth(64, "flow")) is not None
    load = YAML.load
    visited: list[YAML] = []
    rejected: list[bool] = []
    too_deep = _at_depth(65, "flow").decode()

    def guarded_load(yaml: YAML, _text: str) -> object:
        visited.append(yaml)
        assert yaml.typ == ["safe"] and yaml.pure is True
        assert yaml.version == (1, 2) and yaml.allow_duplicate_keys is False
        assert yaml.max_depth == 64
        try:
            result: object = load(yaml, too_deep)
        except MaxDepthExceededError:
            rejected.append(True)
            raise
        return result

    monkeypatch.setattr(YAML, "load", guarded_load)
    assert _parse(_at_depth(64, "flow")) is None
    assert rejected == [True]
    assert len(visited) == 1


@pytest.mark.parametrize("error_type", [MemoryError, RecursionError])
@pytest.mark.parametrize(
    "phase",
    [
        "loader",
        "constructor",
        "compose",
        "load",
        "graph",
        "node-projection",
        "construction",
        "control-projection",
        "capability",
    ],
)
def test_resource_failure_returns_absent_capability_at_each_phase(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    error_type: type[MemoryError] | type[RecursionError],
) -> None:
    assert _parse(_WORKFLOW) is not None
    calls = 0

    def fail(*_args: object, **_kwargs: object) -> Never:
        nonlocal calls
        calls += 1
        raise error_type("resource sentinel")

    def make_yaml(*, typ: str, pure: bool) -> Never:
        assert typ == "safe" and pure is True
        fail()

    if phase == "loader":
        monkeypatch.setattr(workflow_syntax, "YAML", make_yaml)
    elif phase == "constructor":
        monkeypatch.setattr(SafeConstructor, "__init__", fail)
    elif phase == "compose":
        monkeypatch.setattr(Composer, "compose_document", fail)
    elif phase == "load":
        monkeypatch.setattr(YAML, "load", fail)
    elif phase == "construction":
        monkeypatch.setattr(SafeConstructor, "construct_document", fail)
    else:
        target = {
            "graph": "_validate_node_graph",
            "node-projection": "_workflow_node_projection",
            "control-projection": "control_job_projection_hash",
            "capability": "RevisionWorkflowCapability",
        }[phase]
        monkeypatch.setattr(workflow_syntax, target, fail)

    assert _parse(_WORKFLOW) is None
    assert calls == 1


@pytest.mark.parametrize(
    ("metadata", "message"),
    [
        (b"x" * 65_537, "workflow scalar exceeds its bound"),
        (b"[" + b"0," * 20_000 + b"]", "workflow structure exceeds its bound"),
    ],
    ids=["scalar", "nodes"],
)
def test_graph_resource_bounds_remain_independent_of_composer_depth(
    monkeypatch: pytest.MonkeyPatch,
    metadata: bytes,
    message: str,
) -> None:
    assert _parse(_WORKFLOW) is not None
    content = _WORKFLOW + b"metadata: " + metadata + b"\n"
    assert len(content) < workflow_syntax.MAX_WORKFLOW_FILE_BYTES
    validate = workflow_syntax._validate_node_graph
    rejected: list[str] = []

    def observe_graph(root: Node) -> None:
        try:
            validate(root)
        except ValueError as error:
            rejected.append(str(error))
            raise

    monkeypatch.setattr(workflow_syntax, "_validate_node_graph", observe_graph)
    assert _parse(content) is None
    assert rejected == [message]

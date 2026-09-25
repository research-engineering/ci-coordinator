from __future__ import annotations

from typing import Literal, Never

import pytest
from ruamel.yaml import YAML
from ruamel.yaml.composer import MaxDepthExceededError
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


@pytest.mark.parametrize("phase", ["compose", "load"])
def test_each_yaml_instance_enforces_its_own_library_depth_limit(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    assert _parse(_at_depth(64, "flow")) is not None
    compose = YAML.compose
    load = YAML.load
    visited: list[tuple[str, YAML]] = []
    rejected: list[str] = []
    too_deep = _at_depth(65, "flow").decode()

    def guarded_compose(yaml: YAML, text: str) -> object:
        visited.append(("compose", yaml))
        assert yaml.max_depth == 64
        try:
            result: object = compose(yaml, too_deep if phase == "compose" else text)
        except MaxDepthExceededError:
            rejected.append("compose")
            raise
        return result

    def guarded_load(yaml: YAML, _text: str) -> object:
        visited.append(("load", yaml))
        assert yaml.max_depth == 64
        # Reach the second guard independently of the first composition guard.
        try:
            result: object = load(yaml, too_deep)
        except MaxDepthExceededError:
            rejected.append("load")
            raise
        return result

    monkeypatch.setattr(YAML, "compose", guarded_compose)
    monkeypatch.setattr(YAML, "load", guarded_load)
    assert _parse(_at_depth(64, "flow")) is None
    assert rejected == [phase]
    assert [name for name, _ in visited] == (
        ["compose"] if phase == "compose" else ["compose", "load"]
    )
    if phase == "load":
        assert visited[0][1] is not visited[1][1]


@pytest.mark.parametrize("error_type", [MemoryError, RecursionError])
@pytest.mark.parametrize(
    "phase",
    ["first-loader", "second-loader", "compose", "load", "graph", "projection"],
)
def test_resource_failure_returns_absent_capability_at_each_phase(
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    error_type: type[MemoryError] | type[RecursionError],
) -> None:
    assert _parse(_WORKFLOW) is not None
    calls = 0
    instances = 0

    def fail(*_args: object, **_kwargs: object) -> Never:
        nonlocal calls
        calls += 1
        raise error_type("resource sentinel")

    def make_yaml(*, typ: str, pure: bool) -> YAML:
        nonlocal instances
        instances += 1
        assert typ == "safe" and pure is True
        if instances == (1 if phase == "first-loader" else 2):
            fail()
        return YAML(typ=typ, pure=pure)

    if phase in {"first-loader", "second-loader"}:
        monkeypatch.setattr(workflow_syntax, "YAML", make_yaml)
    elif phase in {"compose", "load"}:
        monkeypatch.setattr(YAML, phase, fail)
    else:
        target = "_validate_node_graph" if phase == "graph" else "_job_authorities"
        monkeypatch.setattr(workflow_syntax, target, fail)

    assert _parse(_WORKFLOW) is None
    assert calls == 1
    if phase in {"first-loader", "second-loader"}:
        assert instances == (1 if phase == "first-loader" else 2)


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

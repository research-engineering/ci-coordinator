from __future__ import annotations

from pathlib import Path

import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.workflow_discovery import parser
from ci_coordinator.workflow_discovery._validation import MAX_WORKFLOW_FILE_BYTES
from ci_coordinator.workflow_discovery._yaml_preflight import preflight_yaml_events
from ci_coordinator.workflow_discovery.source import WorkflowSource, git_blob_sha1
from ci_coordinator.workflow_discovery.summary import (
    ParsedWorkflow,
    WorkflowParseFailure,
    WorkflowParseOutcome,
)

_ROOT = Path(__file__).resolve().parents[4]
_SCOPE = RepositoryScope(7, 11)
_REVISION = "a" * 40


def _parse(document: str, path: str = ".github/workflows/caller.yml") -> WorkflowParseOutcome:
    content = document.encode("utf-8")
    return parser.parse_workflow(
        WorkflowSource(path, git_blob_sha1(content), len(content), content),
        scope=_SCOPE,
        revision=_REVISION,
        default_branch="master",
    )


def test_every_native_workflow_is_parseable() -> None:
    paths = sorted(
        path for path in (_ROOT / ".github/workflows").iterdir() if path.suffix in {".yaml", ".yml"}
    )
    assert paths
    for path in paths:
        relative = path.relative_to(_ROOT).as_posix()
        result = _parse(path.read_text(encoding="utf-8"), relative)
        assert isinstance(result, ParsedWorkflow), (relative, result)
        assert result.summary.path == relative


@pytest.mark.parametrize("key", ("run", "with:\n          script"))
def test_large_opaque_scripts_are_bounded_source_not_returned_evidence(key: str) -> None:
    marker = "opaque-script-marker-"
    template = f"on: push\njobs:\n  test:\n    steps:\n      - {key}: {{body}}\n"
    baseline = _parse(template.format(body=marker))
    parsed = _parse(template.format(body=marker * 400))

    assert isinstance(baseline, ParsedWorkflow)
    assert isinstance(parsed, ParsedWorkflow)
    assert parsed.summary == baseline.summary
    assert marker not in repr(parsed)


@pytest.mark.parametrize("key", ("k" * 4096, "\u00e9" * 2048), ids=("ascii", "utf8"))
@pytest.mark.parametrize(
    "template",
    (
        "? {key}\n: ignored\non: push\njobs: {{test: {{steps: []}}}}\n",
        "on: push\njobs:\n  test:\n    env:\n      ? {key}\n      : ignored\n    steps: []\n",
    ),
    ids=("root", "nested"),
)
def test_mapping_keys_keep_their_exact_utf8_bound_before_composition(
    key: str, template: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert len(key.encode("utf-8")) == 4096
    assert isinstance(_parse(template.format(key=key)), ParsedWorkflow)
    monkeypatch.setattr(parser, "new_yaml", _reject_composition)

    parsed = _parse(template.format(key=key + "x"))

    assert isinstance(parsed, WorkflowParseFailure)
    assert parsed.code == "mapping_key_limit_exceeded"
    assert key not in repr(parsed)


@pytest.mark.parametrize(
    "template",
    (
        "name: {value}\non: push\njobs: {{test: {{steps: []}}}}\n",
        "on: push\nconcurrency: {value}\njobs: {{test: {{steps: []}}}}\n",
        "on: push\njobs:\n  test:\n    name: {value}\n    steps: []\n",
        "on: push\njobs:\n  test:\n    if: {value}\n    steps: []\n",
        "on: push\njobs:\n  test:\n    steps:\n      - name: {value}\n",
        "on: push\njobs:\n  test:\n    steps:\n      - uses: {value}\n",
    ),
    ids=("workflow-name", "concurrency", "job-name", "condition", "step-name", "step-uses"),
)
def test_reflected_fields_keep_their_small_text_bound(template: str) -> None:
    assert isinstance(_parse(template.format(value="x" * 4096)), ParsedWorkflow)

    parsed = _parse(template.format(value="x" * 4097))

    assert isinstance(parsed, WorkflowParseFailure)
    assert parsed.code == "unsupported_workflow_syntax"
    assert "x" * 4097 not in repr(parsed)


def test_reflected_unknown_syntax_keeps_its_small_text_bound() -> None:
    value = "${{ " + "x" * 4096 + " }}"
    parsed = _parse(f"name: {value}\non: push\njobs: {{test: {{steps: []}}}}\n")

    assert isinstance(parsed, WorkflowParseFailure)
    assert parsed.code == "unsupported_workflow_syntax"
    assert value not in repr(parsed)


def test_large_scripts_preserve_the_exact_source_file_byte_limit() -> None:
    prefix = "on: push\njobs:\n  test:\n    steps:\n      - run: "
    document = prefix + "x" * (MAX_WORKFLOW_FILE_BYTES - len(prefix) - 1) + "\n"
    assert len(document.encode("utf-8")) == MAX_WORKFLOW_FILE_BYTES
    parsed = _parse(document)
    assert isinstance(parsed, ParsedWorkflow)
    assert "x" * 4097 not in repr(parsed)

    with pytest.raises(ValueError, match="workflow content must be bounded exact bytes"):
        _parse(document + " ")


@pytest.mark.parametrize(
    ("extra", "code"),
    (
        ("ignored: [" + ",".join(["0"] * 20_000) + "]\n", "node_limit_exceeded"),
        ("ignored: " + "[" * 65 + "0" + "]" * 65 + "\n", "depth_limit_exceeded"),
    ),
    ids=("nodes", "depth"),
)
def test_parser_keeps_node_and_depth_limits_before_composition(
    extra: str, code: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(parser, "new_yaml", _reject_composition)
    parsed = _parse(extra + "on: push\njobs: {test: {steps: []}}\n")

    assert isinstance(parsed, WorkflowParseFailure)
    assert parsed.code == code


@pytest.mark.parametrize(
    ("document", "max_events", "max_scalar_bytes", "code"),
    (
        ("value: [0]\n", 4, MAX_WORKFLOW_FILE_BYTES, "event_limit_exceeded"),
        ("value: abcdefghi\n", 50_000, 8, "scalar_limit_exceeded"),
    ),
)
def test_preflight_retains_explicit_event_and_scalar_budgets(
    document: str, max_events: int, max_scalar_bytes: int, code: str
) -> None:
    failure = preflight_yaml_events(
        document,
        max_events=max_events,
        max_nodes=20_000,
        max_depth=64,
        max_scalar_bytes=max_scalar_bytes,
    )

    assert failure is not None
    assert failure.code == code


def _reject_composition() -> None:
    raise AssertionError("resource rejection must precede YAML node composition")

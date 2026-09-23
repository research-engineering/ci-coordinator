from __future__ import annotations

import importlib.metadata
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest
import ruamel.yaml.main
from scripts import diagram_check, diagram_inventory, documentation_graph_filesystem
from scripts.bounded_git import run_git
from scripts.bounded_process import CommandResult, StopPredicate, spawn
from scripts.diagram_contract import (
    PROFILE_PATH,
    DiagramManifest,
    DiagramReport,
    DiagramResult,
    admit_report,
    load_profile,
)
from scripts.diagram_inventory import build_manifest, extract_diagrams, semantic_source
from scripts.documentation_graph_policy import DEFAULT_PROFILE_PATH, load_policy

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def document_repository(tmp_path: Path) -> Path:
    run_git(tmp_path, ["init", "--quiet"])
    for path in (PROFILE_PATH, DEFAULT_PROFILE_PATH):
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / path).read_bytes())
    (tmp_path / "README.md").write_text("```mermaid\nflowchart TD\nA --> B\n```\n")
    return tmp_path


@pytest.fixture
def checked_repository(document_repository: Path) -> Path:
    for path in ("backend/requirements-dev.lock", "frontend/tools/diagrams.mjs"):
        destination = document_repository / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / path).read_bytes())
    return document_repository


def install_successful_renderer(
    monkeypatch: pytest.MonkeyPatch,
    during_render: Callable[[], object],
) -> list[DiagramManifest]:
    rendered: list[DiagramManifest] = []

    def render(*args: object, input_text: str, **kwargs: object) -> CommandResult:
        manifest = DiagramManifest.model_validate_json(input_text)
        rendered.append(manifest)
        during_render()
        report = DiagramReport(
            schemaVersion=1,
            inventoryDigest=manifest.digest,
            rendererVersion=manifest.profile.rendererVersion,
            lintVersion=manifest.profile.lintVersion,
            results=[
                DiagramResult(id=diagram.id, errors=[], warnings=[])
                for diagram in manifest.diagrams
            ],
        )
        return CommandResult(status=0, stdout=report.model_dump_json(), stderr="")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(diagram_check, "spawn", render)
    return rendered


@pytest.mark.parametrize(
    "source, expected",
    [
        ("```mermaid\nflowchart TD\nA --> B\n```\n", "flowchart TD\nA --> B\n"),
        ("~~~ mermaid\nflowchart TD\nA --> B\n~~~\n", "flowchart TD\nA --> B\n"),
        ("> ```mermaid\n> flowchart TD\n> A --> B\n> ```\n", "flowchart TD\nA --> B\n"),
        ("- ```mermaid\n  flowchart TD\n  A --> B\n  ```\n", "flowchart TD\nA --> B\n"),
        (
            "   ```mermaid\n   flowchart TD\n   A --> B\n```\n",
            "flowchart TD\nA --> B\n",
        ),
        ("```mermaid\r\nflowchart TD\r\nA --> B\r\n```\r\n", "flowchart TD\nA --> B\n"),
        ("```mermaid\nflowchart TD\nA --> B\n", "flowchart TD\nA --> B\n"),
    ],
)
def test_commonmark_normalizes_real_fence_bodies(source: str, expected: str) -> None:
    blocks = extract_diagrams("docs/example.md", source.encode(), load_profile(ROOT))
    assert len(blocks) == 1
    assert blocks[0].body == expected
    assert blocks[0].line == 1


@pytest.mark.parametrize(
    "source",
    [
        "````markdown\n```mermaid\nflowchart TD\nA --> B\n```\n````\n",
        "    ```mermaid\n    flowchart TD\n    A --> B\n    ```\n",
        "<pre>\n```mermaid\nflowchart TD\nA --> B\n```\n</pre>\n",
    ],
)
def test_examples_do_not_become_diagrams(source: str) -> None:
    assert extract_diagrams("docs/example.md", source.encode(), load_profile(ROOT)) == []


def test_metadata_projection_preserves_lines_and_node_identity() -> None:
    body = "---\ntitle: A[Metadata]\n---\nflowchart TD\nA[Actual] --> B\n"
    assert semantic_source(body) == "\n\n\nflowchart TD\nA[Actual] --> B\n"


@pytest.mark.parametrize("key", ["title", "accTitle", "accDescr"])
@pytest.mark.parametrize(
    "value",
    [
        '"Documentation %% mermaid-lint reference"',
        "'Documentation %%{init: {}}%% reference'",
        '"%% mermaid-lint-disable"',
        "|\n  Documentation\n  %% mermaid-lint-disable-diagram all: literal example",
        "|\n  Before\n  ---\n  After",
    ],
)
def test_metadata_literal_directive_markers_are_data(key: str, value: str) -> None:
    body = f"---\n{key}: {value}\n---\nflowchart TD\nA[Actual] --> B\n"
    shadow = semantic_source(body)
    assert shadow.count("\n") == body.count("\n")
    assert shadow.lstrip("\n") == "flowchart TD\nA[Actual] --> B\n"
    diagram = extract_diagrams(
        "docs/literal-metadata.md", f"```mermaid\n{body}```\n".encode(), load_profile(ROOT)
    )[0]
    assert diagram.body == body
    assert diagram.semanticBody == shadow


@pytest.mark.parametrize(
    "directive",
    [
        "%%{init: {}}%%",
        "%%{init:",
        "%% mermaid-lint-disable-diagram duplicate-ids: hidden",
        "%% mermaid-lint-disable",
        "%% mermaid-lint-unknown all: hidden",
    ],
)
def test_metadata_literal_does_not_hide_a_real_directive(directive: str) -> None:
    body = (
        '---\ntitle: "Documentation %% mermaid-lint reference"\n---\n'
        f"flowchart TD\n{directive}\nA[First]\nA[Second]\n"
    )
    with pytest.raises(ValueError, match="directives are not admitted"):
        semantic_source(body)


@pytest.mark.parametrize(
    "statement",
    [
        "accTitle: Documentation %% mermaid-lint reference",
        "accDescr: Documentation %% mermaid-lint-disable reference",
        "accDescr { Documentation %% mermaid-lint reference }",
        'A["Documentation %% mermaid-lint reference"]',
        'A["Documentation %% { reference"]',
    ],
)
def test_inline_literal_markers_are_not_comment_directives(statement: str) -> None:
    body = f"flowchart TD\n{statement}\nA --> B\n"
    assert semantic_source(body) == body


def test_label_separator_text_does_not_start_metadata() -> None:
    body = 'flowchart TD\nA["Before\n---\nAfter"] --> B\n'
    diagram = extract_diagrams(
        "docs/literal-separator.md", f"```mermaid\n{body}```\n".encode(), load_profile(ROOT)
    )[0]
    assert diagram.body == diagram.semanticBody == body


@pytest.mark.parametrize(
    "statement",
    [
        "accTitle: Before %%{init: {}}%% After",
        'A["Before %%{init: {}}%% After"]',
        "  %% mermaid-lint-disable",
        "  %% mermaid-lint-unknown all: hidden",
        "accDescr {\n %% mermaid-lint-disable\n}",
    ],
)
def test_preprocessed_directives_cannot_hide_in_text(statement: str) -> None:
    with pytest.raises(ValueError, match="directives are not admitted"):
        semantic_source(f"flowchart TD\n{statement}\nA --> B\n")


def test_optional_c_parser_cannot_change_metadata_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class UnadmittedParser:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pytest.fail("an optional YAML C parser was selected")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(ruamel.yaml.main, "CParser", UnadmittedParser)
    assert (
        semantic_source("---\ntitle: Example\n---\nflowchart TD\nA --> B\n")
        == "\n\n\nflowchart TD\nA --> B\n"
    )


@pytest.mark.parametrize(
    "body",
    [
        "---\nconfig: {maxTextSize: 1}\n---\nflowchart TD\nA --> B",
        "---\ntitle: [bad, title]\n---\nflowchart TD\nA --> B",
        "---\ntitle: open\nflowchart TD\nA --> B",
        "%% comment\n---\ntitle: misplaced\n---\nflowchart TD\nA --> B",
        "%%{init: {}}%%\nflowchart TD\nA --> B",
        "flowchart TD\n%% mermaid-lint-disable-diagram all: hidden\nA --> B",
    ],
)
def test_unsafe_or_unsupported_metadata_fails_explicitly(body: str) -> None:
    with pytest.raises(ValueError):
        extract_diagrams(
            "docs/invalid-metadata.md", f"```mermaid\n{body}\n```\n".encode(), load_profile(ROOT)
        )


def test_oversize_astral_text_is_measured_in_utf16_units() -> None:
    with pytest.raises(ValueError, match="UTF-16"):
        extract_diagrams(
            "docs/example.md",
            ("```mermaid\nflowchart TD\n%%" + "\U0001f600" * 25_000 + "\n```\n").encode(),
            load_profile(ROOT),
        )


def test_new_type_is_an_explicit_admission_failure() -> None:
    with pytest.raises(ValueError, match="unsupported diagram type"):
        extract_diagrams("docs/example.md", b'```mermaid\npie\n"A": 1\n```\n', load_profile(ROOT))


def test_identity_distinguishes_same_body_at_distinct_locations() -> None:
    source = b"```mermaid\nflowchart TD\nA --> B\n```\n\n```mermaid\nflowchart TD\nA --> B\n```\n"
    first, second = extract_diagrams("docs/example.md", source, load_profile(ROOT))
    assert first.bodySha256 == second.bodySha256
    assert first.id != second.id


def test_result_cardinality_cannot_hide_missing_identity() -> None:
    manifest = build_manifest(ROOT)
    results = [
        DiagramResult(id=diagram.id, errors=[], warnings=[]) for diagram in manifest.diagrams
    ]
    report = DiagramReport(
        schemaVersion=1,
        inventoryDigest=manifest.digest,
        rendererVersion=manifest.profile.rendererVersion,
        lintVersion=manifest.profile.lintVersion,
        results=results,
    )
    admit_report(manifest, report)
    duplicated = report.model_copy(update={"results": [results[0], *results[:-1]]})
    with pytest.raises(ValueError, match="missing, duplicate or foreign"):
        admit_report(manifest, duplicated)
    with pytest.raises(ValueError, match="source and evaluator identity"):
        admit_report(manifest, report.model_copy(update={"inventoryDigest": "0" * 64}))


def test_worktree_inventory_includes_new_documents(document_repository: Path) -> None:
    before = build_manifest(document_repository)
    (document_repository / "docs/new.md").write_text(
        "```mermaid\nsequenceDiagram\nA->>B: hello\n```\n"
    )
    after = build_manifest(document_repository)
    assert len(before.diagrams) == 1
    assert len(after.diagrams) == 2
    assert before.digest != after.digest


@pytest.mark.parametrize("changed_path", ["README.md", "frontend/tools/diagrams.mjs"])
def test_check_rejects_inputs_changed_during_render(
    checked_repository: Path, monkeypatch: pytest.MonkeyPatch, changed_path: str
) -> None:
    source = checked_repository / changed_path
    original = source.read_bytes()
    changed = (
        b"```mermaid\nflowchart TD\nC --> D\n```\n"
        if changed_path == "README.md"
        else original + b"\n"
    )
    rendered = install_successful_renderer(monkeypatch, lambda: source.write_bytes(changed))

    with pytest.raises(ValueError, match=r"^diagram inputs changed during validation$"):
        diagram_check.check(checked_repository)

    assert len(rendered) == 1
    assert rendered[0].diagrams[0].body == "flowchart TD\nA --> B\n"
    assert source.read_bytes() == changed
    assert original != changed


def test_check_accepts_unchanged_inputs_after_render(
    checked_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rendered = install_successful_renderer(monkeypatch, lambda: None)

    result = diagram_check.check(checked_repository)

    assert len(rendered) == 1
    assert result == {
        "schemaVersion": 1,
        "mode": "render",
        "revision": None,
        "inventoryDigest": rendered[0].digest,
        "documentCount": 1,
        "diagramCount": 1,
        "rendererVersion": rendered[0].profile.rendererVersion,
    }
    assert rendered[0].diagrams[0].body == "flowchart TD\nA --> B\n"


def test_commit_check_revalidates_committed_inputs_despite_dirty_markdown(
    checked_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_git(checked_repository, ["add", "."])
    run_git(
        checked_repository,
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "fixture",
        ],
    )
    revision = run_git(checked_repository, ["rev-parse", "HEAD"]).stdout.strip()
    source = checked_repository / "README.md"
    source.write_text("```mermaid\nflowchart TD\nC --> D\n```\n")
    changed_during_render = "```mermaid\nflowchart TD\nE --> F\n```\n"
    rendered = install_successful_renderer(
        monkeypatch, lambda: source.write_text(changed_during_render)
    )

    result = diagram_check.check(checked_repository, revision)

    assert len(rendered) == 1
    assert rendered[0].diagrams[0].body == "flowchart TD\nA --> B\n"
    assert result["mode"] == "render"
    assert result["revision"] == revision
    assert result["inventoryDigest"] == rendered[0].digest
    assert source.read_text() == changed_during_render


@pytest.mark.parametrize(
    "kind", ["symlink", "missing", "invalid-utf8", "empty-corpus", "unsupported-source"]
)
def test_unavailable_or_unsupported_inputs_cannot_pass(
    document_repository: Path, kind: str
) -> None:
    source = document_repository / "README.md"
    if kind == "symlink":
        source.unlink()
        source.symlink_to(ROOT / "README.md")
    elif kind == "missing":
        run_git(document_repository, ["add", "README.md"])
        source.unlink()
    elif kind == "invalid-utf8":
        source.write_bytes(b"\xff")
    elif kind == "empty-corpus":
        source.write_text("No diagrams.\n")
    else:
        (document_repository / "new.mmd").write_text("flowchart TD\nA --> B\n")
    with pytest.raises((ValueError, UnicodeError)):
        build_manifest(document_repository)


def test_commit_inventory_uses_blobs_and_rejects_changed_evaluator(
    document_repository: Path,
) -> None:
    run_git(document_repository, ["add", "."])
    run_git(
        document_repository,
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "fixture",
        ],
    )
    head = run_git(document_repository, ["rev-parse", "HEAD"]).stdout.strip()
    (document_repository / "README.md").write_text("```mermaid\nflowchart TD\nC --> D\n```\n")
    manifest = build_manifest(document_repository, head)
    assert "A --> B" in manifest.diagrams[0].body
    assert "C --> D" not in manifest.diagrams[0].body
    profile = document_repository / PROFILE_PATH
    profile.write_text(profile.read_text() + "\n")
    with pytest.raises(ValueError, match="evaluator differs"):
        build_manifest(document_repository, head)


def test_inline_suppression_is_rejected_but_code_examples_are_not() -> None:
    directive = "<!-- mermaid-lint-disable-file all: hidden -->"
    diagram = "\n```mermaid\nflowchart TD\nA --> B\n```\n"
    with pytest.raises(ValueError, match="file-scope"):
        extract_diagrams(
            "docs/example.md",
            ("Text " + directive + diagram).encode(),
            load_profile(ROOT),
        )
    for example in (f"`{directive}`\n", f"```html\n{directive}\n```\n"):
        assert (
            len(
                extract_diagrams(
                    "docs/example.md", (example + diagram).encode(), load_profile(ROOT)
                )
            )
            == 1
        )


def test_git_count_limit_precedes_any_blob_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits = replace(load_policy(ROOT).limits, max_document_count=1)

    def forbidden(*args: object, **kwargs: object) -> bytes:
        pytest.fail("a blob was read before count admission")

    monkeypatch.setattr(diagram_inventory, "git_source", forbidden)
    with pytest.raises(ValueError, match="document count"):
        diagram_inventory.read_git_documents(ROOT, "a" * 40, ["a.md", "b.md"], limits)


def test_git_aggregate_budget_bounds_each_next_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limits = replace(load_policy(ROOT).limits, max_document_bytes=8, max_total_bytes=10)
    observed: list[tuple[str, int]] = []

    def bounded_source(
        root: Path,
        revision: str,
        path: str,
        maximum: int,
        deadline: float | None = None,
        *,
        stop_requested: StopPredicate | None = None,
    ) -> bytes:
        observed.append((path, maximum))
        if maximum < 8:
            raise ValueError("aggregate source budget exhausted")
        return b"12345678"

    monkeypatch.setattr(diagram_inventory, "git_source", bounded_source)
    with pytest.raises(ValueError, match="aggregate source budget"):
        diagram_inventory.read_git_documents(ROOT, "a" * 40, ["a.md", "b.md", "c.md"], limits)
    assert observed == [("a.md", 8), ("b.md", 2)]


def test_git_reads_share_one_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter([1.0, 6.0])
    observed: list[str] = []
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(diagram_inventory, "monotonic", lambda: next(times))

    def source(
        root: Path,
        revision: str,
        path: str,
        maximum: int,
        deadline: float | None = None,
        *,
        stop_requested: StopPredicate | None = None,
    ) -> bytes:
        observed.append(path)
        return b"short"

    monkeypatch.setattr(diagram_inventory, "git_source", source)
    with pytest.raises(ValueError, match="deadline"):
        diagram_inventory.read_git_documents(
            ROOT, "a" * 40, ["a.md", "b.md"], load_policy(ROOT).limits, 5.0
        )
    assert observed == ["a.md"]


def test_all_inventory_git_boundaries_receive_cancellation(
    document_repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_git(document_repository, ["add", "."])
    run_git(
        document_repository,
        [
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "fixture",
        ],
    )
    stop = Mock(return_value=False)
    git_reads = Mock(wraps=run_git)
    discovery = Mock(wraps=spawn)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(diagram_inventory, "run_git", git_reads)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(documentation_graph_filesystem, "spawn", discovery)
    build_manifest(document_repository, "HEAD", stop_requested=stop)
    assert {call.args[1][1] for call in git_reads.call_args_list} == {
        "rev-parse",
        "ls-tree",
        "show",
    }
    assert all(call.kwargs["stop_requested"] is stop for call in git_reads.call_args_list)
    assert discovery.call_count == 1
    assert discovery.call_args.kwargs["stop_requested"] is stop

    builder = Mock(wraps=build_manifest)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(diagram_check, "build_manifest", builder)
    monkeypatch.setattr(diagram_check, "admit_python_packages", lambda root: None)
    diagram_check.check(document_repository, inventory_only=True, stop_requested=stop)
    assert builder.call_count == 2
    assert all(call.kwargs["stop_requested"] is stop for call in builder.call_args_list)
    assert discovery.call_count == 3
    assert all(call.kwargs["stop_requested"] is stop for call in discovery.call_args_list)
    assert stop.call_count > 0


def test_stale_python_parser_is_rejected_before_inventory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    installed = importlib.metadata.version
    monkeypatch.setattr(
        importlib.metadata,
        "version",
        lambda name: "0.0.0" if name == "markdown-it-py" else installed(name),
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("inventory started under an unadmitted Python parser")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(diagram_check, "build_manifest", forbidden)
    with pytest.raises(ValueError, match="installed markdown-it-py differs"):
        diagram_check.check(ROOT, inventory_only=True)

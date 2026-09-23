from __future__ import annotations

import re
from collections.abc import Collection
from pathlib import Path
from time import monotonic

from markdown_it import MarkdownIt
from markdown_it.token import Token
from ruamel.yaml import YAML

from scripts.bounded_git import run_git
from scripts.bounded_process import StopPredicate
from scripts.diagram_contract import (
    Diagram,
    DiagramManifest,
    DiagramProfile,
    digest_bytes,
    digest_json,
    is_document_path,
    is_evaluator_path,
    load_profile,
)
from scripts.documentation_graph_contract import DocumentationLimits
from scripts.documentation_graph_filesystem import (
    admit_inventory,
    discover_repository_paths,
    read_document_sources,
)
from scripts.documentation_graph_policy import load_policy

_TYPE = re.compile(r"^(flowchart|graph|sequenceDiagram|stateDiagram-v2|stateDiagram)(?:\s|;|$)")
_DIRECTIVE = re.compile(r"%%\{|^[^\S\n]*%%[^\S\n]*mermaid-lint", re.MULTILINE)


def remaining_seconds(deadline: float | None) -> float:
    remaining = 30.0 if deadline is None else deadline - monotonic()
    if remaining <= 0:
        raise ValueError("diagram run deadline exceeded")
    return remaining


def resolve_commit(
    root: Path,
    revision: str,
    deadline: float | None = None,
    *,
    stop_requested: StopPredicate | None = None,
) -> str:
    return run_git(
        root,
        [
            "--no-replace-objects",
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{revision}^{{commit}}",
        ],
        timeout_seconds=min(30.0, remaining_seconds(deadline)),
        stop_requested=stop_requested,
    ).stdout.strip()


def tree_entries(
    root: Path,
    revision: str,
    limits: DocumentationLimits,
    deadline: float | None = None,
    *,
    stop_requested: StopPredicate | None = None,
) -> dict[str, str]:
    result = run_git(
        root,
        ["--no-replace-objects", "ls-tree", "-r", "-z", "--full-tree", revision],
        max_buffer=limits.max_inventory_bytes,
        timeout_seconds=min(30.0, remaining_seconds(deadline)),
        stop_requested=stop_requested,
    ).stdout
    if result and not result.endswith("\0"):
        raise ValueError("diagram Git inventory is not NUL-terminated")
    entries: dict[str, str] = {}
    for record in result.split("\0"):
        if not record:
            continue
        metadata, path = record.split("\t", 1)
        mode, kind, _object_id = metadata.split()
        if path in entries:
            raise ValueError("diagram Git inventory contains duplicate paths")
        entries[path] = mode if kind == "blob" else kind
    admit_inventory(tuple(entries), limits)
    return entries


def git_source(
    root: Path,
    revision: str,
    path: str,
    maximum: int,
    deadline: float | None = None,
    *,
    stop_requested: StopPredicate | None = None,
) -> bytes:
    source = run_git(
        root,
        ["--no-replace-objects", "show", f"{revision}:{path}"],
        max_buffer=maximum + 1,
        timeout_seconds=min(30.0, remaining_seconds(deadline)),
        stop_requested=stop_requested,
    ).stdout.encode("utf-8")
    if len(source) > maximum:
        raise ValueError(f"{path}: source exceeds {maximum} bytes")
    return source


def admit_evaluator(
    root: Path,
    revision: str,
    limits: DocumentationLimits,
    entries: dict[str, str],
    deadline: float | None = None,
    *,
    stop_requested: StopPredicate | None = None,
) -> None:
    current = admit_inventory(
        discover_repository_paths(
            root,
            limits,
            timeout_seconds=min(30.0, remaining_seconds(deadline)),
            stop_requested=stop_requested,
        ),
        limits,
    )
    paths = sorted(path for path in set(entries) | current if is_evaluator_path(path))
    sources, issues = read_document_sources(root, paths, limits)
    if issues:
        raise ValueError("\n".join(issues))
    for path in paths:
        if entries.get(path) not in {"100644", "100755"}:
            raise ValueError(
                f"{path}: pushed evaluator is absent or not a regular blob; "
                "check out the pushed commit"
            )
        if sources[path] != git_source(
            root, revision, path, limits.max_document_bytes, deadline, stop_requested=stop_requested
        ):
            raise ValueError(
                f"{path}: evaluator differs from pushed commit; "
                "check out that commit and install its lockfiles"
            )


def read_git_documents(
    root: Path,
    revision: str,
    paths: list[str],
    limits: DocumentationLimits,
    deadline: float | None = None,
    *,
    stop_requested: StopPredicate | None = None,
) -> dict[str, bytes]:
    if len(paths) > limits.max_document_count:
        raise ValueError("diagram document count exceeds the documentation limit")
    sources: dict[str, bytes] = {}
    remaining = limits.max_total_bytes
    for path in paths:
        remaining_seconds(deadline)
        source = git_source(
            root,
            revision,
            path,
            min(limits.max_document_bytes, remaining),
            deadline,
            stop_requested=stop_requested,
        )
        sources[path] = source
        remaining -= len(source)
    return sources


def contains_file_suppression(token: Token) -> bool:
    pending = [token]
    while pending:
        current = pending.pop()
        if current.type in {"html_block", "html_inline"} and "mermaid-lint" in current.content:
            return True
        pending.extend(current.children or ())
    return False


def semantic_source(body: str) -> str:
    lines = body.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        if _DIRECTIVE.search(body):
            raise ValueError("initialization and lint suppression directives are not admitted")
        return body
    closing = next((i for i in range(1, len(lines)) if lines[i].rstrip("\r\n") == "---"), None)
    if closing is None:
        raise ValueError("diagram metadata is not closed")
    if lines[0].rstrip("\r\n") != "---":
        raise ValueError("diagram metadata delimiters must be unindented ---")
    metadata = YAML(typ="safe", pure=True).load("".join(lines[1:closing]))
    if not isinstance(metadata, dict) or any(
        key not in {"title", "accTitle", "accDescr"} or not isinstance(value, str)
        for key, value in metadata.items()
    ):
        raise ValueError(
            "diagram metadata permits only string title, accTitle and accDescr; "
            "config overrides are not admitted"
        )
    semantic = "".join(
        "\n" if line.endswith("\n") else "" for line in lines[: closing + 1]
    ) + "".join(lines[closing + 1 :])
    if _DIRECTIVE.search(semantic):
        raise ValueError("initialization and lint suppression directives are not admitted")
    return semantic


def extract_diagrams(path: str, source: bytes, profile: DiagramProfile) -> list[Diagram]:
    parser = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    diagrams: list[Diagram] = []
    for token in parser.parse(source.decode("utf-8", errors="strict")):
        if contains_file_suppression(token):
            raise ValueError(f"{path}: file-scope lint suppression is not admitted")
        if token.type != "fence" or token.info.split()[:1] != ["mermaid"]:
            continue
        if token.map is None:
            raise ValueError(f"{path}: Mermaid fence has no source coordinates")
        start, end = token.map
        body = token.content
        try:
            if not body.strip():
                raise ValueError("diagram is empty")
            if len(body.encode("utf-16-le")) // 2 > profile.maxDiagramUtf16Units:
                raise ValueError("diagram exceeds the UTF-16 text limit")
            semantic = semantic_source(body)
            header = next(
                (
                    line.strip()
                    for line in semantic.splitlines()
                    if line.strip() and not line.lstrip().startswith("%%")
                ),
                "",
            )
            match = _TYPE.match(header)
            if match is None or match[1] not in profile.supportedTypes:
                raise ValueError(f"unsupported diagram type: {header[:80]!r}")
        except Exception as error:
            raise ValueError(f"{path}:{start + 1}: {error}") from error
        body_digest = digest_bytes(body.encode("utf-8"))
        identity = {
            "path": path,
            "line": start + 1,
            "endLine": end,
            "bodySha256": body_digest,
        }
        diagrams.append(
            Diagram(
                id=digest_json(identity),
                path=path,
                line=start + 1,
                endLine=end,
                body=body,
                semanticBody=semantic,
                type=match[1],
                bodySha256=body_digest,
            )
        )
    return diagrams


def build_manifest(
    root: Path,
    revision: str | None = None,
    *,
    deadline: float | None = None,
    stop_requested: StopPredicate | None = None,
) -> DiagramManifest:
    policy = load_policy(root)
    profile = load_profile(root)
    deadline = monotonic() + profile.runTimeoutSeconds if deadline is None else deadline
    remaining_seconds(deadline)
    inventory: Collection[str]
    if revision is not None:
        revision = resolve_commit(root, revision, deadline, stop_requested=stop_requested)
        entries = tree_entries(
            root, revision, policy.limits, deadline, stop_requested=stop_requested
        )
        paths = sorted(path for path in entries if is_document_path(path))
        if len(paths) > policy.limits.max_document_count:
            raise ValueError("diagram document count exceeds the documentation limit")
        admit_evaluator(
            root, revision, policy.limits, entries, deadline, stop_requested=stop_requested
        )
        if any(entries[path] not in {"100644", "100755"} for path in paths):
            raise ValueError("diagram sources must be regular Git blobs")
        sources = read_git_documents(
            root, revision, paths, policy.limits, deadline, stop_requested=stop_requested
        )
        inventory = entries.keys()
    else:
        inventory = admit_inventory(
            discover_repository_paths(
                root,
                policy.limits,
                timeout_seconds=min(30.0, remaining_seconds(deadline)),
                stop_requested=stop_requested,
            ),
            policy.limits,
        )
        paths = sorted(path for path in inventory if is_document_path(path))
        if len(paths) > policy.limits.max_document_count:
            raise ValueError("diagram document count exceeds the documentation limit")
        sources, issues = read_document_sources(root, paths, policy.limits)
        if issues:
            raise ValueError("\n".join(issues))
    unsupported = sorted(path for path in inventory if path.endswith((".mmd", ".mermaid", ".mdx")))
    if unsupported:
        raise ValueError(
            f"diagram source format outside the admitted Markdown scope: {unsupported}"
        )
    if (
        len(paths) > policy.limits.max_document_count
        or sum(map(len, sources.values())) > policy.limits.max_total_bytes
    ):
        raise ValueError("diagram document inventory exceeds the documentation limits")
    diagrams = []
    for path in paths:
        remaining_seconds(deadline)
        diagrams.extend(extract_diagrams(path, sources[path], profile))
        if len(diagrams) > profile.maxDiagrams:
            raise ValueError("diagram corpus exceeds the admitted diagram count")
    if not diagrams or len(diagrams) > profile.maxDiagrams:
        raise ValueError("diagram corpus is empty or exceeds the admitted diagram count")
    evaluator_paths = sorted(path for path in inventory if is_evaluator_path(path))
    evaluator_sources, issues = read_document_sources(root, evaluator_paths, policy.limits)
    if issues:
        raise ValueError("\n".join(issues))
    payload = {
        "schemaVersion": 1,
        "revision": revision,
        "files": {
            path: digest_bytes(source)
            for path, source in sorted((sources | evaluator_sources).items())
        },
        "diagrams": [diagram.model_dump() for diagram in diagrams],
        "profile": profile.model_dump(),
    }
    result = DiagramManifest.model_validate({**payload, "digest": digest_json(payload)})
    remaining_seconds(deadline)
    return result

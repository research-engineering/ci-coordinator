from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from fnmatch import fnmatchcase
from pathlib import Path

from markdown_it import MarkdownIt

from scripts.documentation_graph_contract import (
    DocumentationGraphError,
    DocumentationGraphPolicy,
    DocumentationGraphSummary,
    DocumentationLimits,
    ParsedDocument,
)
from scripts.documentation_graph_filesystem import (
    admit_inventory,
    read_document_sources,
    repository_directories,
)
from scripts.documentation_graph_filesystem import (
    discover_repository_paths as _discover_repository_paths,
)
from scripts.documentation_graph_markdown import admit_link, parse_document
from scripts.documentation_graph_policy import (
    DEFAULT_PROFILE_PATH,
    enforce_limit_ceilings,
)
from scripts.documentation_graph_policy import (
    load_policy as _load_policy,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def load_policy(
    repo_root: Path = REPO_ROOT,
    profile_path: Path = DEFAULT_PROFILE_PATH,
) -> DocumentationGraphPolicy:
    return _load_policy(repo_root, profile_path)


def admit_documentation_graph(
    repo_root: Path = REPO_ROOT,
    *,
    policy: DocumentationGraphPolicy | None = None,
    repository_paths: Collection[str] | None = None,
) -> DocumentationGraphSummary:
    selected_policy = load_policy(repo_root) if policy is None else policy
    enforce_limit_ceilings(selected_policy.limits)
    raw_inventory = (
        discover_repository_paths(repo_root, selected_policy.limits)
        if repository_paths is None
        else tuple(repository_paths)
    )
    inventory = admit_inventory(raw_inventory, selected_policy.limits)
    document_paths = tuple(
        sorted(
            path
            for path in inventory
            if any(_glob_matches(path, pattern) for pattern in selected_policy.markdown_globs)
        )
    )
    issues: list[str] = []
    if len(document_paths) > selected_policy.limits.max_document_count:
        issues.append(
            "documentation graph: document count exceeds "
            f"{selected_policy.limits.max_document_count}"
        )
    issues.extend(
        f"documentation graph: root is absent: {root_path}"
        for root_path in selected_policy.root_paths
        if root_path not in document_paths
    )
    if selected_policy.readiness_claim.owner_path not in document_paths:
        issues.append(
            "documentation graph: readiness owner is absent: "
            f"{selected_policy.readiness_claim.owner_path}"
        )
    if issues:
        raise DocumentationGraphError(issues)

    source_payloads, metadata_issues = read_document_sources(
        repo_root,
        document_paths,
        selected_policy.limits,
    )
    if metadata_issues:
        raise DocumentationGraphError(metadata_issues)

    parser = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    parsed = _parse_documents(
        document_paths,
        source_payloads,
        parser,
        selected_policy,
    )
    total_links = sum(len(document.links) for document in parsed.values())
    if total_links > selected_policy.limits.max_link_count:
        raise DocumentationGraphError(
            [f"documentation graph: link count exceeds {selected_policy.limits.max_link_count}"]
        )

    directories = repository_directories(inventory)
    known_targets = set(inventory) | set(directories)
    targets_by_casefold: dict[str, list[str]] = {}
    for target in sorted(known_targets):
        targets_by_casefold.setdefault(target.casefold(), []).append(target)
    edges = {path: set[str]() for path in document_paths}
    local_link_count, external_link_count, link_issues = _admit_links(
        repo_root=repo_root,
        parsed=parsed,
        known_targets=known_targets,
        targets_by_casefold=targets_by_casefold,
        policy=selected_policy,
        edges=edges,
    )
    reachable = _reachable_documents(selected_policy.root_paths, edges)
    link_issues.extend(
        f"documentation graph: unreachable document: {orphan}"
        for orphan in sorted(set(document_paths) - reachable)
    )
    if link_issues:
        raise DocumentationGraphError(link_issues)
    return DocumentationGraphSummary(
        document_count=len(document_paths),
        external_link_count=external_link_count,
        local_link_count=local_link_count,
    )


def discover_repository_paths(
    repo_root: Path = REPO_ROOT,
    limits: DocumentationLimits | None = None,
) -> tuple[str, ...]:
    selected_limits = limits or load_policy(repo_root).limits
    return _discover_repository_paths(repo_root, selected_limits)


def main() -> int:
    try:
        summary = admit_documentation_graph()
    except (DocumentationGraphError, OSError, UnicodeError, ValueError) as error:
        print(str(error))
        return 1
    print(
        f"admitted {summary.document_count} Markdown documents, "
        f"{summary.local_link_count} local links, and "
        f"{summary.external_link_count} external links"
    )
    return 0


def _parse_documents(
    document_paths: Sequence[str],
    source_payloads: Mapping[str, bytes],
    parser: MarkdownIt,
    policy: DocumentationGraphPolicy,
) -> dict[str, ParsedDocument]:
    parsed: dict[str, ParsedDocument] = {}
    issues: list[str] = []
    for relative_path in document_paths:
        document, parse_issues = parse_document(
            relative_path,
            parser,
            policy,
            source_payloads[relative_path],
        )
        parsed[relative_path] = document
        issues.extend(parse_issues)
    if issues:
        raise DocumentationGraphError(issues)
    return parsed


def _admit_links(
    *,
    repo_root: Path,
    parsed: Mapping[str, ParsedDocument],
    known_targets: Collection[str],
    targets_by_casefold: Mapping[str, Sequence[str]],
    policy: DocumentationGraphPolicy,
    edges: dict[str, set[str]],
) -> tuple[int, int, list[str]]:
    issues: list[str] = []
    local_link_count = 0
    external_link_count = 0
    path_issue_cache: dict[str, str | None] = {}
    for source_path, document in parsed.items():
        for link in document.links:
            local_target, external, link_issues = admit_link(
                repo_root=repo_root,
                source_path=source_path,
                link=link,
                known_targets=known_targets,
                targets_by_casefold=targets_by_casefold,
                documents=parsed,
                policy=policy,
                path_issue_cache=path_issue_cache,
            )
            issues.extend(link_issues)
            if external:
                external_link_count += 1
            else:
                local_link_count += 1
            if link.creates_graph_edge and local_target is not None and local_target in parsed:
                edges[source_path].add(local_target)
    return local_link_count, external_link_count, issues


def _reachable_documents(
    roots: Sequence[str],
    edges: Mapping[str, Collection[str]],
) -> set[str]:
    reachable: set[str] = set()
    pending = list(reversed(roots))
    while pending:
        current = pending.pop()
        if current in reachable:
            continue
        reachable.add(current)
        pending.extend(sorted(edges.get(current, ()), reverse=True))
    return reachable


def _glob_matches(path: str, pattern: str) -> bool:
    if fnmatchcase(path, pattern):
        return True
    return "/**/" in pattern and fnmatchcase(path, pattern.replace("/**/", "/"))


if __name__ == "__main__":
    raise SystemExit(main())

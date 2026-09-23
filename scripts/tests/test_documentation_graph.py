from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from scripts import documentation_graph_filesystem
from scripts.documentation_graph import (
    REPO_ROOT,
    admit_documentation_graph,
    load_policy,
)
from scripts.documentation_graph_contract import (
    DocumentationGraphError,
    DocumentationGraphPolicy,
    DocumentationLimits,
    ReadinessClaimPolicy,
)


def _policy() -> DocumentationGraphPolicy:
    return DocumentationGraphPolicy(
        allowed_external_schemes=frozenset({"https"}),
        limits=DocumentationLimits(
            max_document_count=16,
            max_document_bytes=4_096,
            max_inventory_bytes=16_384,
            max_link_count=32,
            max_repository_path_count=64,
            max_total_bytes=16_384,
        ),
        markdown_globs=("*.md", "docs/**/*.md"),
        readiness_claim=ReadinessClaimPolicy(
            marker="State:",
            owner_path="README.md",
            subject_heading_level=2,
        ),
        root_paths=("README.md",),
    )


def _write(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _profile_record() -> dict[str, object]:
    return {
        "allowedExternalSchemes": ["https"],
        "limits": {
            "maxDocumentBytes": 4_096,
            "maxDocumentCount": 16,
            "maxInventoryBytes": 16_384,
            "maxLinkCount": 32,
            "maxRepositoryPathCount": 64,
            "maxTotalBytes": 16_384,
        },
        "markdownGlobs": ["*.md"],
        "nonClaims": ["Fixture only."],
        "profileId": "ci-coordinator.documentation-graph",
        "readinessClaim": {
            "marker": "State:",
            "ownerPath": "README.md",
            "subjectHeadingLevel": 2,
        },
        "rootPaths": ["README.md"],
        "schemaVersion": 1,
    }


def _issues(
    root: Path,
    paths: tuple[str, ...],
    *,
    policy: DocumentationGraphPolicy | None = None,
) -> tuple[str, ...]:
    with pytest.raises(DocumentationGraphError) as caught:
        admit_documentation_graph(
            root,
            policy=policy or _policy(),
            repository_paths=paths,
        )
    return caught.value.issues


def test_commonmark_links_images_fragments_and_external_urls_are_admitted(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "README.md",
        "# Root\n\n[Document][doc]\n\n![Asset](docs/image.png)\n\n"
        "<https://example.com/reference>\n\n[doc]: docs/document.md#target\n",
    )
    _write(tmp_path, "docs/document.md", "# Target\n")
    _write(tmp_path, "docs/image.png", "fixture")

    summary = admit_documentation_graph(
        tmp_path,
        policy=_policy(),
        repository_paths=("README.md", "docs/document.md", "docs/image.png"),
    )

    assert summary.document_count == 2
    assert summary.local_link_count == 2
    assert summary.external_link_count == 1


def test_profile_bytes_and_configurable_limits_have_hard_ceilings(
    tmp_path: Path,
) -> None:
    profile = _profile_record()
    limits = profile["limits"]
    assert isinstance(limits, dict)
    limits["maxLinkCount"] = 65_537
    _write(tmp_path, "profile.json", json.dumps(profile))

    with pytest.raises(ValueError, match="maxLinkCount exceeds implementation ceiling"):
        load_policy(tmp_path, Path("profile.json"))

    (tmp_path / "profile.json").write_bytes(b" " * 65_537)
    with pytest.raises(ValueError, match="profile exceeds 65536 bytes"):
        load_policy(tmp_path, Path("profile.json"))

    _write(tmp_path, "README.md", "# Root\n")
    injected = replace(
        _policy(),
        limits=replace(_policy().limits, max_link_count=65_537),
    )
    with pytest.raises(ValueError, match="maxLinkCount exceeds implementation ceiling"):
        admit_documentation_graph(
            tmp_path,
            policy=injected,
            repository_paths=("README.md",),
        )


def test_profile_rejects_duplicate_keys(tmp_path: Path) -> None:
    source = json.dumps(_profile_record())
    duplicate = source.replace(
        '"schemaVersion": 1',
        '"schemaVersion": 1, "schemaVersion": 1',
    )
    _write(tmp_path, "profile.json", duplicate)

    with pytest.raises(ValueError, match="profile has duplicate key: schemaVersion"):
        load_policy(tmp_path, Path("profile.json"))


@pytest.mark.parametrize(
    ("destination", "expected"),
    (
        ("docs/missing.md", "target does not exist"),
        ("DOCS/document.md", "target case differs from docs/document.md"),
        ("DOCS", "target case differs from docs"),
        ("../../outside.md", "local path escapes the repository"),
        ("/docs/document.md", "absolute local paths are not admitted"),
        ("docs/document.md?raw=1", "local links may not contain a query"),
        ("//example.com/path", "protocol-relative URLs are not admitted"),
        ("mailto:operator@example.com", "unsupported external scheme: mailto"),
        ("https:relative", "external URL has no authority"),
        ("https://@/path", "external URL has no authority"),
        ("https://example.com:bad/path", "malformed external authority"),
        ("https://example.com:99999/path", "malformed external authority"),
        ("docs/%FF", "percent-decoded URL component is not strict UTF-8"),
        ("docs/document.md#missing", "fragment does not resolve"),
        ("docs/document.md#caf%C3%A9", "fragment is outside the ASCII heading profile"),
        ("docs/document.md#bad%0Afragment", "fragment contains a control character"),
    ),
)
def test_invalid_link_forms_fail_closed(
    tmp_path: Path,
    destination: str,
    expected: str,
) -> None:
    _write(tmp_path, "README.md", f"# Root\n\n[Target]({destination})\n")
    _write(tmp_path, "docs/document.md", "# Target\n")

    issues = _issues(tmp_path, ("README.md", "docs/document.md"))

    assert any(expected in issue for issue in issues)


@pytest.mark.parametrize(
    "html",
    (
        '<a href="docs/document.md">Document</a>',
        '<iframe src="docs/document.md"></iframe>',
        '<img src="docs/image.png">',
    ),
)
def test_raw_html_cannot_bypass_link_admission(tmp_path: Path, html: str) -> None:
    _write(tmp_path, "README.md", f"# Root\n\n{html}\n")

    issues = _issues(tmp_path, ("README.md",))

    assert any("raw HTML is not admitted" in issue for issue in issues)


def test_link_target_cannot_be_a_symlink(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "# Root\n\n[Asset](docs/asset.txt)\n")
    _write(tmp_path, "asset.txt", "outside")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/asset.txt").symlink_to(tmp_path / "asset.txt")

    issues = _issues(tmp_path, ("README.md", "asset.txt", "docs/asset.txt"))

    assert any("path traverses symlink component: docs/asset.txt" in issue for issue in issues)


def test_link_target_cannot_traverse_a_symlinked_directory(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "# Root\n\n[Asset](docs/link/asset.txt)\n")
    _write(tmp_path, "real/asset.txt", "outside")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/link").symlink_to(tmp_path / "real", target_is_directory=True)

    issues = _issues(
        tmp_path,
        ("README.md", "docs/link/asset.txt", "real/asset.txt"),
    )

    assert any("path traverses symlink component: docs/link" in issue for issue in issues)


def test_markdown_source_cannot_be_a_symlink(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "# Root\n\n[Detail](docs/detail.md)\n")
    _write(tmp_path, "detail.md", "# Detail\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/detail.md").symlink_to(tmp_path / "detail.md")

    issues = _issues(tmp_path, ("README.md", "detail.md", "docs/detail.md"))

    assert any("path traverses symlink component: docs/detail.md" in issue for issue in issues)


def test_markdown_source_must_be_strict_utf8(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "# Root\n\n[Detail](docs/detail.md)\n")
    detail = tmp_path / "docs/detail.md"
    detail.parent.mkdir()
    detail.write_bytes(b"# Detail\n\xff")

    issues = _issues(tmp_path, ("README.md", "docs/detail.md"))

    assert any("document is not strict UTF-8" in issue for issue in issues)


def test_unreachable_markdown_document_fails_closed(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "# Root\n")
    _write(tmp_path, "docs/orphan.md", "# Orphan\n")

    issues = _issues(tmp_path, ("README.md", "docs/orphan.md"))

    assert any("unreachable document: docs/orphan.md" in issue for issue in issues)


def test_image_target_does_not_make_a_document_reachable(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "# Root\n\n![Orphan](docs/orphan.md)\n")
    _write(tmp_path, "docs/orphan.md", "# Orphan\n")

    issues = _issues(tmp_path, ("README.md", "docs/orphan.md"))

    assert any("unreachable document: docs/orphan.md" in issue for issue in issues)


def test_readiness_declarations_have_one_owner_subject_and_value(
    tmp_path: Path,
) -> None:
    _write(
        tmp_path,
        "README.md",
        "# Root\n\n## Capability\n\nState: ready.\n\nState: stable.\n\n[Detail](docs/detail.md)\n",
    )
    _write(tmp_path, "docs/detail.md", "# Detail\n\n## Other\n\nState: implemented.\n")

    issues = _issues(tmp_path, ("README.md", "docs/detail.md"))

    assert any("duplicate readiness subject: Capability" in issue for issue in issues)
    assert any("readiness declaration is owned by README.md" in issue for issue in issues)


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        ("# Root\n\nState: ready.\n", "readiness declaration has no level 2 subject"),
        ("# Root\n\n## Capability\n\nState:\n", "readiness declaration has no value"),
    ),
)
def test_incomplete_readiness_declarations_fail_closed(
    tmp_path: Path,
    source: str,
    expected: str,
) -> None:
    _write(tmp_path, "README.md", source)

    assert any(expected in issue for issue in _issues(tmp_path, ("README.md",)))


def test_nested_state_prose_is_not_a_readiness_declaration(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "README.md",
        "# Root\n\n## Capability\n\n- State: illustrative list item\n\n"
        "> State: illustrative quotation\n",
    )

    summary = admit_documentation_graph(
        tmp_path,
        policy=_policy(),
        repository_paths=("README.md",),
    )

    assert summary.document_count == 1


def test_document_and_aggregate_byte_limits_fail_before_parsing(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "# Root\n\n[Detail](docs/detail.md)\n")
    _write(tmp_path, "docs/detail.md", "# Detail\n")
    base = _policy()
    individual = replace(
        base,
        limits=replace(base.limits, max_document_bytes=8),
    )
    aggregate = replace(
        base,
        limits=replace(base.limits, max_total_bytes=32),
    )

    individual_issues = _issues(
        tmp_path,
        ("README.md", "docs/detail.md"),
        policy=individual,
    )
    aggregate_issues = _issues(
        tmp_path,
        ("README.md", "docs/detail.md"),
        policy=aggregate,
    )

    assert any("document bytes exceed 8" in issue for issue in individual_issues)
    assert any("aggregate bytes exceed 32" in issue for issue in aggregate_issues)


def test_aggregate_budget_stops_document_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_source = "# Root\n\n[One](docs/one.md)\n\n[Two](docs/two.md)\n"
    _write(tmp_path, "README.md", root_source)
    _write(tmp_path, "docs/one.md", "# One\n")
    _write(tmp_path, "docs/two.md", "# Two\n")
    max_total_bytes = len(root_source.encode("utf-8"))
    base = _policy()
    bounded = replace(
        base,
        limits=replace(
            base.limits,
            max_document_bytes=max_total_bytes,
            max_total_bytes=max_total_bytes,
        ),
    )
    real_read = documentation_graph_filesystem._read_bounded
    read_count = 0

    def count_read(file_descriptor: int, limit: int) -> bytes:
        nonlocal read_count
        read_count += 1
        return real_read(file_descriptor, limit)

    monkeypatch.setattr(documentation_graph_filesystem, "_read_bounded", count_read)

    issues = _issues(
        tmp_path,
        ("README.md", "docs/one.md", "docs/two.md"),
        policy=bounded,
    )

    assert any(f"aggregate bytes exceed {max_total_bytes}" in issue for issue in issues)
    assert read_count == 1


def test_document_link_and_inventory_count_limits_fail_closed(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "README.md",
        "# Root\n\n[One](docs/detail.md)\n\n[Two](docs/detail.md)\n",
    )
    _write(tmp_path, "docs/detail.md", "# Detail\n")
    paths = ("README.md", "docs/detail.md")
    base = _policy()

    document_issues = _issues(
        tmp_path,
        paths,
        policy=replace(base, limits=replace(base.limits, max_document_count=1)),
    )
    link_issues = _issues(
        tmp_path,
        paths,
        policy=replace(base, limits=replace(base.limits, max_link_count=1)),
    )
    inventory_issues = _issues(
        tmp_path,
        paths,
        policy=replace(base, limits=replace(base.limits, max_repository_path_count=1)),
    )

    assert any("document count exceeds 1" in issue for issue in document_issues)
    assert any("link count exceeds 1" in issue for issue in link_issues)
    assert any("repository path count exceeds 1" in issue for issue in inventory_issues)


def test_empty_link_destinations_are_counted(tmp_path: Path) -> None:
    _write(tmp_path, "README.md", "# Root\n\n[One]()\n\n[Two]()\n")
    base = _policy()

    issues = _issues(
        tmp_path,
        ("README.md",),
        policy=replace(base, limits=replace(base.limits, max_link_count=1)),
    )

    assert any("link count exceeds 1" in issue for issue in issues)


def test_inventory_bytes_duplicates_and_unsafe_paths_fail_closed(
    tmp_path: Path,
) -> None:
    _write(tmp_path, "README.md", "# Root\n")
    base = _policy()
    byte_limited = replace(
        base,
        limits=replace(base.limits, max_inventory_bytes=8),
    )

    byte_issues = _issues(tmp_path, ("README.md",), policy=byte_limited)
    malformed_issues = _issues(tmp_path, ("README.md", "README.md", "../escape.md"))

    assert any("repository path bytes exceed 8" in issue for issue in byte_issues)
    assert any("inventory contains duplicates" in issue for issue in malformed_issues)
    assert any("repository path must be a safe" in issue for issue in malformed_issues)


def test_diagnostics_are_deduplicated_and_sorted(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "README.md",
        "# Root\n\n[B](docs/b.md)\n\n[A](docs/a.md)\n\n[A again](docs/a.md)\n",
    )

    issues = _issues(tmp_path, ("README.md",))

    assert issues == tuple(sorted(set(issues)))


def test_document_source_swap_to_symlink_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "README.md"
    external = tmp_path / "external.md"
    _write(tmp_path, "README.md", "# Root\n")
    _write(tmp_path, "external.md", "# Root\n")
    real_stat = os.stat
    swapped = False

    def swap_after_metadata(
        path: os.PathLike[str] | str | bytes,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> os.stat_result:
        nonlocal swapped
        result = real_stat(path, dir_fd=dir_fd, follow_symlinks=follow_symlinks)
        if path == "README.md" and dir_fd is not None and not swapped:
            source.unlink()
            source.symlink_to(external)
            swapped = True
        return result

    monkeypatch.setattr("scripts.documentation_graph_filesystem.os.stat", swap_after_metadata)

    issues = _issues(tmp_path, ("README.md",))

    assert any("path changed during admission at README.md" in issue for issue in issues)


def test_filesystem_diagnostics_do_not_depend_on_checkout_path(
    tmp_path: Path,
) -> None:
    issue_sets: list[tuple[str, ...]] = []
    for checkout in (tmp_path / "first", tmp_path / "second"):
        _write(checkout, "README.md", "# Root\n\n[Missing](docs/missing.md)\n")
        issue_sets.append(_issues(checkout, ("README.md", "docs/missing.md")))

    assert issue_sets[0] == issue_sets[1]


def test_repository_documentation_graph_is_admitted() -> None:
    summary = admit_documentation_graph(REPO_ROOT)

    assert summary.document_count >= 80
    assert summary.local_link_count > summary.document_count

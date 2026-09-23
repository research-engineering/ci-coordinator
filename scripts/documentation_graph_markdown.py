from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from pathlib import Path, PurePosixPath
from urllib.parse import unquote_to_bytes, urlsplit

from markdown_it import MarkdownIt
from markdown_it.token import Token

from scripts.documentation_graph_contract import (
    DocumentationGraphPolicy,
    LinkReference,
    ParsedDocument,
    contains_control,
)
from scripts.documentation_graph_filesystem import path_component_issue

_ASCII_FRAGMENT_TEXT = re.compile(r"[\x20-\x7e]+")
_PERCENT_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")


def parse_document(
    relative_path: str,
    parser: MarkdownIt,
    policy: DocumentationGraphPolicy,
    payload: bytes,
) -> tuple[ParsedDocument, list[str]]:
    issues: list[str] = []
    try:
        source = payload.decode("utf-8", errors="strict")
    except UnicodeError:
        return ParsedDocument(frozenset(), (), ()), [
            f"{relative_path}: document is not strict UTF-8"
        ]

    tokens = parser.parse(source)
    anchors: list[str] = []
    anchor_counts: dict[str, int] = {}
    links: list[LinkReference] = []
    readiness_subjects: list[str] = []
    heading_context: dict[int, str] = {}
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and index + 1 < len(tokens):
            level = int(token.tag.removeprefix("h"))
            inline = tokens[index + 1]
            if inline.type != "inline":
                continue
            text = _inline_text(inline.children or ())
            if token.level == 0:
                for previous_level in tuple(heading_context):
                    if previous_level >= level:
                        del heading_context[previous_level]
                heading_context[level] = text
            slug = _heading_slug(text)
            if slug is not None:
                count = anchor_counts.get(slug, 0)
                anchors.append(slug if count == 0 else f"{slug}-{count}")
                anchor_counts[slug] = count + 1
        if token.type in {"html_block", "html_inline"}:
            line = (token.map[0] + 1) if token.map else 1
            issues.append(f"{relative_path}:{line}: raw HTML is not admitted")
        if token.type != "inline":
            continue
        line = (token.map[0] + 1) if token.map else 1
        for child in token.children or ():
            if child.type == "html_inline":
                issues.append(f"{relative_path}:{line}: raw HTML is not admitted")
                continue
            if child.type not in {"link_open", "image"}:
                continue
            attribute = "href" if child.type == "link_open" else "src"
            destination = child.attrGet(attribute)
            if isinstance(destination, str):
                links.append(
                    LinkReference(
                        destination=destination,
                        line=line,
                        creates_graph_edge=child.type == "link_open",
                    )
                )
        if not _is_top_level_paragraph(tokens, index) or not token.content.startswith(
            policy.readiness_claim.marker
        ):
            continue
        subject = heading_context.get(policy.readiness_claim.subject_heading_level)
        if subject is None:
            issues.append(
                f"{relative_path}:{line}: readiness declaration has no level "
                f"{policy.readiness_claim.subject_heading_level} subject"
            )
            continue
        if relative_path != policy.readiness_claim.owner_path:
            issues.append(
                f"{relative_path}:{line}: readiness declaration is owned by "
                f"{policy.readiness_claim.owner_path}"
            )
        if subject in readiness_subjects:
            issues.append(f"{relative_path}:{line}: duplicate readiness subject: {subject}")
        if not token.content.removeprefix(policy.readiness_claim.marker).strip():
            issues.append(f"{relative_path}:{line}: readiness declaration has no value")
        readiness_subjects.append(subject)
    return (
        ParsedDocument(
            anchors=frozenset(anchors),
            links=tuple(links),
            readiness_subjects=tuple(readiness_subjects),
        ),
        issues,
    )


def admit_link(
    *,
    repo_root: Path,
    source_path: str,
    link: LinkReference,
    known_targets: Collection[str],
    targets_by_casefold: Mapping[str, Sequence[str]],
    documents: Mapping[str, ParsedDocument],
    policy: DocumentationGraphPolicy,
    path_issue_cache: dict[str, str | None],
) -> tuple[str | None, bool, list[str]]:
    prefix = f"{source_path}:{link.line}: {link.destination}"
    if contains_control(link.destination):
        return None, False, [f"{prefix}: URL contains a control byte"]
    try:
        parsed = urlsplit(link.destination)
    except ValueError as error:
        return None, False, [f"{prefix}: malformed URL: {error}"]
    if parsed.scheme:
        issues: list[str] = []
        if parsed.scheme not in policy.allowed_external_schemes:
            issues.append(f"{prefix}: unsupported external scheme: {parsed.scheme}")
        try:
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError as error:
            issues.append(f"{prefix}: malformed external authority: {error}")
            hostname = None
        if not parsed.netloc or not hostname:
            issues.append(f"{prefix}: external URL has no authority")
        if "\\" in parsed.netloc or any(character.isspace() for character in parsed.netloc):
            issues.append(f"{prefix}: external authority contains a forbidden character")
        return None, True, issues
    if parsed.netloc:
        return None, False, [f"{prefix}: protocol-relative URLs are not admitted"]
    if parsed.query:
        return None, False, [f"{prefix}: local links may not contain a query"]

    try:
        decoded_path = _strict_percent_decode(parsed.path)
        decoded_fragment = _strict_percent_decode(parsed.fragment)
    except (UnicodeError, ValueError) as error:
        return None, False, [f"{prefix}: {error}"]
    issues = []
    if contains_control(decoded_path) or "\\" in decoded_path:
        return None, False, [f"{prefix}: local path contains a forbidden character"]
    if contains_control(decoded_fragment):
        return None, False, [f"{prefix}: fragment contains a control character"]
    try:
        target = _resolve_local_path(source_path, decoded_path)
    except ValueError as error:
        return None, False, [f"{prefix}: {error}"]
    if target not in known_targets:
        case_matches = targets_by_casefold.get(target.casefold(), ())
        if case_matches:
            issues.append(f"{prefix}: target case differs from {case_matches[0]}")
        else:
            issues.append(f"{prefix}: target does not exist")
        return target, False, issues

    if target not in path_issue_cache:
        path_issue_cache[target] = path_component_issue(repo_root, target)
    component_issue = path_issue_cache[target]
    if component_issue is not None:
        issues.append(f"{prefix}: {component_issue}")
    if decoded_fragment:
        if target not in documents:
            issues.append(f"{prefix}: fragment target is not an admitted Markdown document")
        elif not _ASCII_FRAGMENT_TEXT.fullmatch(decoded_fragment):
            issues.append(f"{prefix}: fragment is outside the ASCII heading profile")
        elif decoded_fragment not in documents[target].anchors:
            issues.append(f"{prefix}: fragment does not resolve")
    return target, False, issues


def _resolve_local_path(source_path: str, destination: str) -> str:
    if destination.startswith("/"):
        raise ValueError("absolute local paths are not admitted")
    source_parent = PurePosixPath(source_path).parent
    candidate = source_parent / destination if destination else PurePosixPath(source_path)
    parts: list[str] = []
    for part in candidate.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                raise ValueError("local path escapes the repository")
            parts.pop()
            continue
        parts.append(part)
    if not parts:
        raise ValueError("local path does not identify a repository target")
    return PurePosixPath(*parts).as_posix()


def _strict_percent_decode(value: str) -> str:
    index = 0
    while index < len(value):
        if value[index] != "%":
            index += 1
            continue
        escape = value[index : index + 3]
        if _PERCENT_ESCAPE.fullmatch(escape) is None:
            raise ValueError(f"invalid percent escape: {escape}")
        index += 3
    try:
        return unquote_to_bytes(value).decode("utf-8", errors="strict")
    except UnicodeError as error:
        raise ValueError("percent-decoded URL component is not strict UTF-8") from error


def _is_top_level_paragraph(tokens: Sequence[Token], index: int) -> bool:
    return index > 0 and tokens[index - 1].type == "paragraph_open" and tokens[index].level == 1


def _heading_slug(text: str) -> str | None:
    if not text or not text.isascii():
        return None
    normalized = "".join(
        character
        for character in text.lower()
        if character.isalnum() or character in {" ", "-", "_"}
    )
    slug = "-".join(normalized.split())
    return slug or None


def _inline_text(tokens: Sequence[Token]) -> str:
    return "".join(
        token.content for token in tokens if token.type in {"text", "code_inline", "image"}
    )

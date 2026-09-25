"""Project verified shell resource URLs without changing the hashed build inputs."""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser


def project_operator_ui_index(
    index: bytes,
    *,
    asset_paths: frozenset[str],
    asset_prefix: str,
    maximum_bytes: int,
) -> bytes:
    if len(index) > maximum_bytes:
        raise ValueError("operator UI index exceeded its byte bound")
    source = index.decode("utf-8", errors="strict")
    parser = _ResourceReferences(source, asset_paths, asset_prefix, maximum_bytes)
    parser.feed(source)
    parser.close()
    parts: list[bytes] = []
    size = 0
    cursor = 0
    for start, end, replacement in (*parser.replacements, (len(source), len(source), "")):
        for text in (source[cursor:start], replacement):
            part = text.encode("utf-8")
            size += len(part)
            if size > maximum_bytes:
                raise ValueError("operator UI projected index exceeded its byte bound")
            parts.append(part)
        cursor = end
    return b"".join(parts)


class _ResourceReferences(HTMLParser):
    def __init__(
        self, source: str, asset_paths: frozenset[str], asset_prefix: str, maximum_bytes: int
    ) -> None:
        super().__init__(convert_charrefs=False)
        self._source = source
        self._asset_paths = asset_paths
        self._asset_prefix = asset_prefix
        self._maximum_bytes = maximum_bytes
        self._replacement_bytes = 0
        self._line = 1
        self._line_offset = 0
        self.replacements: list[tuple[int, int, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._project(tag, attrs, closing=False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._project(tag, attrs, closing=True)

    def _project(self, tag: str, attrs: list[tuple[str, str | None]], *, closing: bool) -> None:
        if tag == "base":
            raise ValueError("operator UI index must not contain a base element")
        values = dict(attrs)
        if len(values) != len(attrs):
            raise ValueError("operator UI index contains duplicate attributes")
        attribute = {"script": "src", "link": "href", "img": "src"}.get(tag)
        if attribute is None:
            return
        value = values.get(attribute)
        if value is None:
            if tag == "script":
                raise ValueError("operator UI index must not contain inline scripts")
            return
        relative = (
            value.removeprefix("./assets/")
            if value.startswith("./assets/")
            else value.removeprefix("/assets/")
            if value.startswith("/assets/")
            else ""
        )
        if relative not in self._asset_paths:
            raise ValueError("operator UI index resource is not an exact manifest member")
        projected = self._asset_prefix + relative
        attributes = "".join(
            f" {name}"
            if content is None
            else f' {name}="{escape(projected if name == attribute else content, quote=True)}"'
            for name, content in attrs
        )
        original = self.get_starttag_text()
        if original is None:
            raise ValueError("operator UI index resource tag is unavailable")
        line, column = self.getpos()
        while self._line < line:
            self._line_offset = self._source.index("\n", self._line_offset) + 1
            self._line += 1
        start = self._line_offset + column
        replacement = f"<{tag}{attributes}{' /' if closing else ''}>"
        self._replacement_bytes += len(replacement.encode("utf-8"))
        if self._replacement_bytes > self._maximum_bytes:
            raise ValueError("operator UI projected index exceeded its byte bound")
        self.replacements.append((start, start + len(original), replacement))

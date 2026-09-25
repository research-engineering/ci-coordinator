from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeGuard

_MAX_PATH_PATTERN_CHARACTERS = 512
_MAX_REPOSITORY_PATH_CHARACTERS = 4_096
type _TokenKind = Literal["literal", "single", "segment", "recursive", "recursive_directories"]


@dataclass(frozen=True, slots=True)
class _PatternToken:
    kind: _TokenKind
    alternatives: tuple[str, ...] = ()


def is_unicode_scalar_string(value: object) -> TypeGuard[str]:
    return type(value) is str and not any(0xD800 <= ord(character) <= 0xDFFF for character in value)


def is_safe_relative_path(value: object) -> TypeGuard[str]:
    if not is_unicode_scalar_string(value):
        return False
    if not value or "\x00" in value:
        return False
    return not (value.startswith(("/", "./")) or "\\" in value or ".." in value.split("/"))


def validate_path_pattern(pattern: object) -> str | None:
    if type(pattern) is not str:
        return "unsafe relative path pattern"
    if len(pattern) > _MAX_PATH_PATTERN_CHARACTERS:
        return "path pattern exceeds admitted size"
    if not is_safe_relative_path(pattern):
        return "unsafe relative path pattern"
    if not pattern.strip():
        return "empty path pattern"
    if any(character in pattern for character in "[]!()+@"):
        return "unsupported glob operator"

    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "}":
            return "unmatched closing brace"
        if character != "{":
            index += 1
            continue
        end = pattern.find("}", index + 1)
        if end == -1:
            return "unclosed brace expansion"
        alternatives = pattern[index + 1 : end].split(",")
        if len(alternatives) < 2 or any(
            not alternative or any(token in alternative for token in "*?{}")
            for alternative in alternatives
        ):
            return "unsupported brace expansion"
        index = end + 1
    return None


def matches_path_pattern(pattern: str, path: str) -> bool:
    if (
        validate_path_pattern(pattern) is not None
        or type(path) is not str
        or len(path) > _MAX_REPOSITORY_PATH_CHARACTERS
        or not is_safe_relative_path(path)
    ):
        return False
    reachable = frozenset({0})
    for token in _tokenize(pattern):
        reachable = _advance(token, path, reachable)
        if not reachable:
            return False
    return len(path) in reachable


def _tokenize(pattern: str) -> tuple[_PatternToken, ...]:
    tokens: list[_PatternToken] = []
    index = 0
    while index < len(pattern):
        character = pattern[index]
        if character == "*" and index + 1 < len(pattern) and pattern[index + 1] == "*":
            if index + 2 < len(pattern) and pattern[index + 2] == "/":
                tokens.append(_PatternToken("recursive_directories"))
                index += 3
                continue
            tokens.append(_PatternToken("recursive"))
            index += 2
            continue
        if character == "*":
            tokens.append(_PatternToken("segment"))
            index += 1
            continue
        if character == "?":
            tokens.append(_PatternToken("single"))
            index += 1
            continue
        if character == "{":
            end = pattern.find("}", index + 1)
            if end == -1:
                raise ValueError("path pattern contains an unclosed brace expansion")
            tokens.append(
                _PatternToken(
                    "literal",
                    tuple(pattern[index + 1 : end].split(",")),
                )
            )
            index = end + 1
            continue
        end = index + 1
        while end < len(pattern) and pattern[end] not in "*?{":
            end += 1
        tokens.append(_PatternToken("literal", (pattern[index:end],)))
        index = end
    return tuple(tokens)


def _advance(
    token: _PatternToken,
    path: str,
    reachable: frozenset[int],
) -> frozenset[int]:
    if token.kind == "literal":
        return frozenset(
            start + len(alternative)
            for start in reachable
            for alternative in token.alternatives
            if path.startswith(alternative, start)
        )
    if token.kind == "single":
        return frozenset(
            start + 1 for start in reachable if start < len(path) and path[start] != "/"
        )
    if token.kind == "recursive":
        return frozenset(range(min(reachable), len(path) + 1))
    if token.kind == "recursive_directories":
        minimum = min(reachable)
        return reachable | frozenset(
            index + 1 for index in range(minimum, len(path)) if path[index] == "/"
        )

    advanced: set[int] = set()
    active = False
    for index in range(len(path) + 1):
        if index in reachable:
            active = True
        if active:
            advanced.add(index)
        if index < len(path) and path[index] == "/":
            active = False
    return frozenset(advanced)

"""Pure admission predicates for Git reference names."""

from __future__ import annotations


def git_branch_name_is_admitted(value: str) -> bool:
    """Return whether one already-bounded scalar string is a canonical branch name."""
    components = value.split("/")
    return not (
        value == "HEAD"
        or value.startswith("-")
        or value.endswith(".")
        or ".." in value
        or "@{" in value
        or any(
            not component or component.startswith(".") or component.endswith(".lock")
            for component in components
        )
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(character in " ~^:?*[\\" for character in value)
    )

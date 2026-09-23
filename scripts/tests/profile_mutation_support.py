from collections.abc import Iterator
from typing import cast

type MutationPath = tuple[str | int, ...]


def profile_leaves(value: object, path: MutationPath = ()) -> Iterator[tuple[MutationPath, object]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield from profile_leaves(child, (*path, str(key)))
    elif isinstance(value, list) and value:
        for index, child in enumerate(value):
            yield from profile_leaves(child, (*path, index))
    else:
        yield path, value


def replace_profile_value(
    document: dict[str, object], path: MutationPath, replacement: object
) -> None:
    current: object = document
    for segment in path[:-1]:
        if isinstance(segment, str):
            current = cast(dict[str, object], current)[segment]
        else:
            current = cast(list[object], current)[segment]
    final = path[-1]
    if isinstance(final, str):
        cast(dict[str, object], current)[final] = replacement
    else:
        cast(list[object], current)[final] = replacement

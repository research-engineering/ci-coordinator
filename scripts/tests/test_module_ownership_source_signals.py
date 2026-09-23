from __future__ import annotations

from itertools import product
from pathlib import Path
from typing import cast

import pytest
from scripts.module_ownership_candidates import (
    RepositoryPathSnapshot,
    fixture_candidate_inventory,
)
from scripts.module_ownership_profile import ModuleOwnershipProfile, load_profile
from scripts.module_ownership_python_signals import signal_runtime_id
from scripts.module_ownership_source_signals import metric_values


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _snapshot(*paths: str) -> RepositoryPathSnapshot:
    ordered = tuple(sorted(paths))
    return RepositoryPathSnapshot(
        deleted_tracked_count=0,
        paths=ordered,
        tracked_count=len(ordered),
        untracked_count=0,
    )


def _candidates(
    profile: ModuleOwnershipProfile,
    root: Path,
    *paths: str,
) -> list[dict[str, object]]:
    inventory = fixture_candidate_inventory(profile, root, _snapshot(*paths))
    return cast(list[dict[str, object]], inventory["candidates"])


def _metrics(row: dict[str, object]) -> dict[str, int | None]:
    return cast(dict[str, int | None], row["metricValues"])


def _reasons(row: dict[str, object]) -> list[str]:
    return cast(list[str], row["selectionReasons"])


def _metric_source(metric_id: str, value: int) -> str:
    if metric_id == "physical-lines":
        return "# bounded line\n" * value
    if metric_id == "recognized-public-declarations":
        return "".join(f"def public_{index}():\n    return {index}\n" for index in range(value))
    if metric_id == "first-party-import-contexts":
        return "".join(
            f"from ci_coordinator.context_{index} import private_{index} as _private_{index}\n"
            for index in range(value)
        )
    raise AssertionError(f"unsupported test metric: {metric_id}")


@pytest.mark.parametrize(
    ("metric_id", "lower", "selected", "reason"),
    (
        ("physical-lines", 400, 401, "physical-lines:gt:400"),
        (
            "recognized-public-declarations",
            12,
            13,
            "recognized-public-declarations:gt:12",
        ),
        (
            "first-party-import-contexts",
            2,
            3,
            "first-party-import-contexts:gte:3",
        ),
    ),
)
def test_metric_boundaries_are_exact(
    tmp_path: Path,
    metric_id: str,
    lower: int,
    selected: int,
    reason: str,
) -> None:
    profile = load_profile()
    relative = "backend/src/ci_coordinator/sample.py"

    _write(tmp_path, relative, _metric_source(metric_id, lower))
    assert _candidates(profile, tmp_path, relative) == []

    _write(tmp_path, relative, _metric_source(metric_id, selected))
    rows = _candidates(profile, tmp_path, relative)
    assert len(rows) == 1
    assert rows[0]["selectionReasons"] == [reason]


@pytest.mark.parametrize("terminator", ("\n", "\r", "\r\n"))
def test_physical_line_metric_uses_universal_line_terminators(
    tmp_path: Path,
    terminator: str,
) -> None:
    profile = load_profile()
    relative = "backend/src/ci_coordinator/line_endings.py"
    _write(tmp_path, relative, terminator.join("# line" for _ in range(401)))

    rows = _candidates(profile, tmp_path, relative)

    assert len(rows) == 1
    assert _metrics(rows[0])["physical-lines"] == 401
    assert rows[0]["selectionReasons"] == ["physical-lines:gt:400"]


@pytest.mark.parametrize("length", range(7))
def test_physical_lines_match_independent_normalization(length: int) -> None:
    for values in product((0x0D, 0x0A, 0x61), repeat=length):
        payload = bytes(values)
        normalized = payload.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        expected = len(normalized.split(b"\n")) - int(normalized.endswith(b"\n")) if payload else 0

        assert metric_values("sample.bin", payload) == {"physical-lines": expected}, payload


@pytest.mark.parametrize(
    ("payload", "expected"),
    (
        (b"", 0),
        (b"\r", 1),
        (b"\n", 1),
        (b"\r\n", 1),
        (b"tail", 1),
        (b"\r\r\n", 2),
        (b"\n\r\n", 2),
        (b"\r\n\r\ntail", 3),
        (b"\x00\x0b\x0c\x1c\x1d\x1e\x85\xff", 1),
        (b"\xe2\x80\xa8\xe2\x80\xa9", 1),
        (b"\xff\r\n\x00\r\x85\n", 3),
    ),
)
def test_physical_lines_preserve_exact_byte_boundaries(payload: bytes, expected: int) -> None:
    assert metric_values("sample.bin", payload) == {"physical-lines": expected}


def test_dynamic_python_exports_select_review_as_unknown(tmp_path: Path) -> None:
    profile = load_profile()
    relative = "backend/src/ci_coordinator/dynamic_exports.py"
    _write(tmp_path, relative, "__all__ = build_exports()\n")

    rows = _candidates(profile, tmp_path, relative)

    assert len(rows) == 1
    assert rows[0]["metricValues"] == {
        "first-party-import-contexts": 0,
        "physical-lines": 1,
        "recognized-public-declarations": None,
    }
    assert rows[0]["selectionReasons"] == ["recognized-public-declarations:unknown"]


@pytest.mark.parametrize(
    "source",
    (
        "__all__ = ['first']\n__all__ = ['second']\n",
        "__all__ = ['first']\n__all__ += ['second']\n",
        "if enabled:\n    __all__ = ['conditional']\n",
        "__all__ = ['first']\n__all__.append('second')\n",
        "from config import exports as __all__\n",
        "def __all__():\n    return ()\n",
        "class __all__:\n    pass\n",
        "__all__ = ['first']\nclass Namespace:\n    __all__.append('second')\n",
        "try:\n    pass\nexcept Exception as __all__:\n    pass\n",
    ),
)
def test_every_non_static_dunder_all_shape_selects_unknown(
    tmp_path: Path,
    source: str,
) -> None:
    profile = load_profile()
    relative = "backend/src/ci_coordinator/mutated_exports.py"
    _write(tmp_path, relative, source)

    rows = _candidates(profile, tmp_path, relative)

    assert len(rows) == 1
    assert _metrics(rows[0])["recognized-public-declarations"] is None
    assert "recognized-public-declarations:unknown" in _reasons(rows[0])


def test_function_local_dunder_all_does_not_change_module_exports(
    tmp_path: Path,
) -> None:
    profile = load_profile()
    relative = "backend/src/ci_coordinator/local_exports.py"
    _write(
        tmp_path,
        relative,
        "def local():\n    __all__ = ['local']\n    return __all__\npublic = 1\n",
    )

    assert _candidates(profile, tmp_path, relative) == []


@pytest.mark.parametrize(
    "source",
    (
        "class Namespace:\n    __all__ = ['local']\npublic = 1\n",
        (
            "class Namespace:\n"
            "    __all__ = ['local']\n"
            "    observed = __all__\n"
            "    __all__.append('second')\n"
            "public = 1\n"
        ),
        "values = [__all__ for __all__ in ['local']]\npublic = 1\n",
    ),
)
def test_non_module_dunder_all_bindings_do_not_change_module_exports(
    tmp_path: Path,
    source: str,
) -> None:
    profile = load_profile()
    relative = "backend/src/ci_coordinator/scoped_exports.py"
    _write(tmp_path, relative, source)

    assert _candidates(profile, tmp_path, relative) == []


def test_python_analysis_recursion_fails_closed_after_parsing(
    tmp_path: Path,
) -> None:
    profile = load_profile()
    relative = "backend/src/ci_coordinator/recursive_expression.py"
    _write(
        tmp_path,
        relative,
        "value = " + "+".join("1" for _ in range(20_000)) + "\n",
    )

    inventory = fixture_candidate_inventory(profile, tmp_path, _snapshot(relative))
    rows = cast(list[dict[str, object]], inventory["candidates"])

    assert inventory["signalRuntimeId"] == signal_runtime_id()
    assert [row["path"] for row in rows] == [relative]
    assert _metrics(rows[0])["recognized-public-declarations"] is None
    assert _metrics(rows[0])["first-party-import-contexts"] is None


@pytest.mark.parametrize(
    "pattern",
    ("[*__all__]", "{**__all__}"),
)
def test_pattern_dunder_all_bindings_fail_closed(
    tmp_path: Path,
    pattern: str,
) -> None:
    profile = load_profile()
    relative = "backend/src/ci_coordinator/pattern_exports.py"
    _write(
        tmp_path,
        relative,
        f"match subject:\n    case {pattern}:\n        pass\n",
    )

    rows = _candidates(profile, tmp_path, relative)

    assert len(rows) == 1
    assert _metrics(rows[0])["recognized-public-declarations"] is None
    assert _reasons(rows[0]) == ["recognized-public-declarations:unknown"]


def test_nested_global_dunder_all_mutation_fails_closed(tmp_path: Path) -> None:
    profile = load_profile()
    relative = "backend/src/ci_coordinator/installed_exports.py"
    exports = ", ".join(repr(f"public_{index}") for index in range(13))
    _write(
        tmp_path,
        relative,
        (
            "def install_exports():\n"
            "    global __all__\n"
            f"    __all__ = [{exports}]\n"
            "install_exports()\n"
        ),
    )

    rows = _candidates(profile, tmp_path, relative)

    assert len(rows) == 1
    assert _metrics(rows[0])["recognized-public-declarations"] is None
    assert _reasons(rows[0]) == ["recognized-public-declarations:unknown"]


def test_python_grammar_and_parser_runtime_are_exactly_identified(
    tmp_path: Path,
) -> None:
    profile = load_profile()
    future = "backend/src/ci_coordinator/future_grammar.py"
    _write(tmp_path, future, 'value = t"python-314-template"\n')

    inventory = fixture_candidate_inventory(profile, tmp_path, _snapshot(future))
    rows = cast(list[dict[str, object]], inventory["candidates"])

    assert inventory["signalRuntimeId"] == signal_runtime_id()
    assert inventory["signalRuntimeId"] in profile.signal_runtime_ids
    assert [row["path"] for row in rows] == [future]
    assert _metrics(rows[0])["recognized-public-declarations"] is None
    assert _metrics(rows[0])["first-party-import-contexts"] is None

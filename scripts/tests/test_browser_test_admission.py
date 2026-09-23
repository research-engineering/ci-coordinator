from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import cast

import pytest
from scripts.browser_test_admission import admit_report

_PROJECTS = ("desktop-chromium", "mobile-chromium", "narrow-chromium")


def report(root: Path, *, execution: bool = False) -> dict[str, object]:
    tests = [
        {
            "projectName": project,
            "expectedStatus": "passed",
            "status": "expected" if execution else "skipped",
            "results": [{"status": "passed", "retry": 0, "errors": []}] if execution else [],
        }
        for project in _PROJECTS
    ]
    return {
        "config": {
            "rootDir": str(root),
            "forbidOnly": True,
            "failOnFlakyTests": True,
            "projects": [
                {"name": project, "testDir": str(root), "repeatEach": 1, "retries": 0}
                for project in _PROJECTS
            ],
        },
        "suites": [
            {
                "title": "one.spec.ts",
                "specs": [
                    {
                        "id": "native-id",
                        "file": "one.spec.ts",
                        "title": "retains the observed effect",
                        "line": 7,
                        "column": 1,
                        "tests": tests,
                    }
                ],
            }
        ],
        "errors": [],
        "stats": {
            "expected": 3 if execution else 0,
            "unexpected": 0,
            "flaky": 0,
            "skipped": 0,
        },
    }


def save(path: Path, value: dict[str, object]) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def change(value: dict[str, object], path: tuple[str | int, ...], replacement: object) -> None:
    current: object = value
    for segment in path[:-1]:
        match current, segment:
            case dict() as mapping, str() as key:
                current = mapping[key]
            case list() as sequence, int() as index:
                current = sequence[index]
            case _:
                raise AssertionError("invalid fixture path")
    final = path[-1]
    match current, final:
        case dict() as mapping, str() as key:
            mapping[key] = replacement
        case list() as sequence, int() as index:
            sequence[index] = replacement
        case _:
            raise AssertionError("invalid fixture path")


def test_native_browser_listing_and_complete_execution(tmp_path: Path) -> None:
    listing = save(tmp_path / "listing.json", report(tmp_path))
    plan = admit_report(listing, test_root=tmp_path, expected_files=("one.spec.ts",))
    execution = save(tmp_path / "execution.json", report(tmp_path, execution=True))
    assert (
        admit_report(execution, test_root=tmp_path, expected_files=("one.spec.ts",), planned=plan)
        == plan
    )
    assert len(plan) == 3


def nested_report(root: Path, *, execution: bool = False) -> dict[str, object]:
    value = report(root, execution=execution)
    outer = cast(list[dict[str, object]], value["suites"])[0]
    spec = cast(list[dict[str, object]], outer["specs"])[0]
    tests = cast(list[dict[str, object]], spec["tests"])
    outer["specs"] = []
    outer["suites"] = [
        {
            "title": f"synthetic {scenario}",
            "specs": [
                dict(spec, id=f"{scenario}-{test['projectName']}", tests=[test]) for test in tests
            ],
        }
        for scenario in ("populated", "empty")
    ]
    change(value, ("stats", "expected"), 6 if execution else 0)
    return value


def test_native_parameterized_suites_keep_distinct_source_occurrences(tmp_path: Path) -> None:
    plan = admit_report(
        save(tmp_path / "listing.json", nested_report(tmp_path)),
        test_root=tmp_path,
        expected_files=("one.spec.ts",),
    )
    assert len(plan) == 6
    assert {item.ancestor_titles for item in plan} == {
        ("one.spec.ts", "synthetic populated"),
        ("one.spec.ts", "synthetic empty"),
    }
    assert (
        admit_report(
            save(tmp_path / "execution.json", nested_report(tmp_path, execution=True)),
            test_root=tmp_path,
            expected_files=("one.spec.ts",),
            planned=plan,
        )
        == plan
    )


def test_distinct_native_ids_cannot_duplicate_the_same_suite_source_occurrence(
    tmp_path: Path,
) -> None:
    value = nested_report(tmp_path)
    change(value, ("suites", 0, "suites", 1, "title"), "synthetic populated")
    with pytest.raises(ValueError, match="source occurrence is duplicated"):
        admit_report(
            save(tmp_path / "listing.json", value),
            test_root=tmp_path,
            expected_files=("one.spec.ts",),
        )


@pytest.mark.parametrize("execution", [False, True])
def test_distinct_suites_cannot_reuse_a_native_identity(tmp_path: Path, execution: bool) -> None:
    plan = admit_report(
        save(tmp_path / "valid-listing.json", nested_report(tmp_path)),
        test_root=tmp_path,
        expected_files=("one.spec.ts",),
    )
    value = nested_report(tmp_path, execution=execution)
    change(
        value,
        ("suites", 0, "suites", 1, "specs", 0, "id"),
        "populated-desktop-chromium",
    )
    with pytest.raises(ValueError, match="native identity is duplicated"):
        admit_report(
            save(tmp_path / "report.json", value),
            test_root=tmp_path,
            expected_files=("one.spec.ts",),
            planned=plan if execution else None,
        )


@pytest.mark.parametrize("substitution", ["rename", "repartition"])
def test_execution_preserves_structured_ancestor_titles(tmp_path: Path, substitution: str) -> None:
    plan = admit_report(
        save(tmp_path / "listing.json", nested_report(tmp_path)),
        test_root=tmp_path,
        expected_files=("one.spec.ts",),
    )
    execution = nested_report(tmp_path, execution=True)
    outer = cast(list[dict[str, object]], execution["suites"])[0]
    suites = cast(list[dict[str, object]], outer["suites"])
    if substitution == "rename":
        suites[0]["title"] = "substituted scenario"
    else:
        suites[0] = {
            "title": "synthetic",
            "specs": [],
            "suites": [dict(suites[0], title="populated")],
        }
    with pytest.raises(ValueError, match="differs from its frozen native listing"):
        admit_report(
            save(tmp_path / "execution.json", execution),
            test_root=tmp_path,
            expected_files=("one.spec.ts",),
            planned=plan,
        )


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("config", "forbidOnly"), False),
        (("config", "failOnFlakyTests"), False),
        (("config", "rootDir"), "/foreign"),
        (("config", "projects", 0, "name"), "foreign-project"),
        (("config", "projects", 0, "retries"), 1),
        (("config", "projects", 0, "repeatEach"), 2),
        (("errors",), [{"message": "collection failed"}]),
        (("suites", 0, "title"), "invalid\nancestor"),
        (("suites", 0, "specs", 0, "file"), "../one.spec.ts"),
        (("suites", 0, "specs", 0, "file"), "foreign.spec.ts"),
        (("suites", 0, "specs", 0, "tests", 0, "projectName"), "foreign"),
        (
            ("suites", 0, "specs", 0, "tests", 0, "results"),
            [{"status": "passed", "retry": 0, "errors": []}],
        ),
    ],
)
def test_listing_rejects_independent_policy_and_identity_substitutions(
    tmp_path: Path, path: tuple[str | int, ...], replacement: object
) -> None:
    value = report(tmp_path)
    change(value, path, replacement)
    with pytest.raises(ValueError):
        admit_report(
            save(tmp_path / "report.json", value),
            test_root=tmp_path,
            expected_files=("one.spec.ts",),
        )


def test_listing_cannot_define_away_an_independent_candidate_file(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="omits an authored"):
        admit_report(
            save(tmp_path / "report.json", report(tmp_path)),
            test_root=tmp_path,
            expected_files=("omitted.spec.ts", "one.spec.ts"),
        )


def test_duplicate_identities_fail_even_with_consistent_counters(
    tmp_path: Path,
) -> None:
    value = report(tmp_path)
    suites = value["suites"]
    assert isinstance(suites, list)
    suites.append(copy.deepcopy(suites[0]))
    with pytest.raises(ValueError, match="duplicated"):
        admit_report(
            save(tmp_path / "report.json", value),
            test_root=tmp_path,
            expected_files=("one.spec.ts",),
        )


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("suites", 0, "specs", 0, "id"), "another-native-id"),
        (("suites", 0, "specs", 0, "title"), "another assertion"),
        (("suites", 0, "specs", 0, "tests", 0, "results"), []),
        (("suites", 0, "specs", 0, "tests", 0, "results", 0, "retry"), 1),
        (
            ("suites", 0, "specs", 0, "tests", 0, "results", 0, "errors"),
            [{"message": "hook error"}],
        ),
        (("suites", 0, "specs", 0, "tests", 0, "status"), "flaky"),
        (("suites", 0, "specs", 0, "tests", 0, "status"), "unexpected"),
        (("suites", 0, "specs", 0, "tests", 0, "status"), "skipped"),
        (("suites", 0, "specs", 0, "tests", 0, "expectedStatus"), "failed"),
        (("stats", "expected"), 2),
        (("stats", "skipped"), True),
    ],
)
def test_execution_rejects_missing_flaky_skipped_foreign_or_miscounted_results(
    tmp_path: Path, path: tuple[str | int, ...], replacement: object
) -> None:
    plan = admit_report(
        save(tmp_path / "listing.json", report(tmp_path)),
        test_root=tmp_path,
        expected_files=("one.spec.ts",),
    )
    value = report(tmp_path, execution=True)
    change(value, path, replacement)
    with pytest.raises(ValueError):
        admit_report(
            save(tmp_path / "execution.json", value),
            test_root=tmp_path,
            expected_files=("one.spec.ts",),
            planned=plan,
        )


def test_only_exact_non_desktop_production_asset_cases_may_skip(tmp_path: Path) -> None:
    title = "native build rejects aliased fixtures and copied synthetic assets"
    listing = report(tmp_path)
    execution = report(tmp_path, execution=True)
    for value in (listing, execution):
        change(value, ("suites", 0, "specs", 0, "title"), title)
        change(value, ("suites", 0, "specs", 0, "file"), "demoBoundary.spec.ts")
    for index in (1, 2):
        prefix: tuple[str | int, ...] = ("suites", 0, "specs", 0, "tests", index)
        change(execution, (*prefix, "expectedStatus"), "skipped")
        change(execution, (*prefix, "status"), "skipped")
        change(execution, (*prefix, "results", 0, "status"), "skipped")
    change(execution, ("stats", "expected"), 1)
    change(execution, ("stats", "skipped"), 2)
    plan = admit_report(
        save(tmp_path / "listing.json", listing),
        test_root=tmp_path,
        expected_files=("demoBoundary.spec.ts",),
    )
    assert (
        admit_report(
            save(tmp_path / "execution.json", execution),
            test_root=tmp_path,
            expected_files=("demoBoundary.spec.ts",),
            planned=plan,
        )
        == plan
    )
    change(execution, ("suites", 0, "specs", 0, "tests", 0, "status"), "skipped")
    with pytest.raises(ValueError):
        admit_report(
            save(tmp_path / "execution.json", execution),
            test_root=tmp_path,
            expected_files=("demoBoundary.spec.ts",),
            planned=plan,
        )


def test_nested_suite_cannot_reuse_an_allowed_skip_for_a_second_occurrence(tmp_path: Path) -> None:
    listing = report(tmp_path)
    change(listing, ("suites", 0, "specs", 0, "file"), "demoBoundary.spec.ts")
    change(
        listing,
        ("suites", 0, "specs", 0, "title"),
        "native build rejects aliased fixtures and copied synthetic assets",
    )
    suites = listing["suites"]
    assert isinstance(suites, list)
    outer = suites[0]
    assert isinstance(outer, dict)
    nested = copy.deepcopy(outer)
    nested["title"] = "another independently authored scenario"
    change({"suite": nested}, ("suite", "specs", 0, "id"), "second-native-occurrence")
    change({"suite": nested}, ("suite", "specs", 0, "line"), 42)
    outer["suites"] = [nested]
    with pytest.raises(ValueError, match="skip exception names multiple native occurrences"):
        admit_report(
            save(tmp_path / "listing.json", listing),
            test_root=tmp_path,
            expected_files=("demoBoundary.spec.ts",),
        )

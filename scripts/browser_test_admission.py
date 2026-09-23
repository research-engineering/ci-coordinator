from __future__ import annotations

from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from scripts.ci_test_plan import Artifact, artifact_bytes

PROJECTS = ("desktop-chromium", "mobile-chromium", "narrow-chromium")
BROWSER_ROOT = "frontend/tests/browser"
MAX_TESTS = 10_000
_MAX_DEPTH = 32
_PRODUCTION_TITLES = (
    "native build rejects aliased fixtures and copied synthetic assets",
    "built assets reject copied development data: CI_COORDINATOR_DEMO_MODE",
    "built assets reject copied development data: Synthetic session",
    "built assets reject copied development data: bart.simpson",
)
_ALLOWED_SKIPS = frozenset(
    ("demoBoundary.spec.ts", title, project)
    for title in _PRODUCTION_TITLES
    for project in ("mobile-chromium", "narrow-chromium")
)
type Outcome = Literal["expected", "unexpected", "flaky", "skipped"]
type Status = Literal["passed", "failed", "timedOut", "skipped", "interrupted"]
type Count = Annotated[int, Field(ge=0, le=MAX_TESTS)]


class _Native(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True, strict=True)


class _Attempt(_Native):
    status: Status
    retry: Count
    errors: list[dict[str, object]]


class _Test(_Native):
    projectName: str
    expectedStatus: Status
    status: Outcome
    results: list[_Attempt]


class _Spec(_Native):
    id: str
    file: str
    title: str
    line: Annotated[int, Field(ge=1)]
    column: Annotated[int, Field(ge=1)]
    tests: list[_Test]


class _Suite(_Native):
    title: str
    specs: list[_Spec]
    suites: list[_Suite] = []


class _Project(_Native):
    name: str
    testDir: str
    repeatEach: int
    retries: int


class _Config(_Native):
    rootDir: str
    forbidOnly: bool
    failOnFlakyTests: bool
    projects: list[_Project]


class _Stats(_Native):
    expected: Count
    unexpected: Count
    flaky: Count
    skipped: Count


class _Report(_Native):
    config: _Config
    suites: list[_Suite]
    errors: list[dict[str, object]]
    stats: _Stats


class BrowserIdentity(Artifact):
    native_id: str
    file: str
    ancestor_titles: tuple[str, ...]
    title: str
    line: int
    column: int
    project: str


def _identity_key(
    item: BrowserIdentity,
) -> tuple[str, tuple[str, ...], str, int, int, str, str]:
    return (
        item.file,
        item.ancestor_titles,
        item.title,
        item.line,
        item.column,
        item.project,
        item.native_id,
    )


def _specs(
    suites: list[_Suite], depth: int = 0, ancestor_titles: tuple[str, ...] = ()
) -> list[tuple[_Spec, tuple[str, ...]]]:
    if depth > _MAX_DEPTH or len(suites) > MAX_TESTS:
        raise ValueError("browser suite tree exceeds its admitted bounds")
    result: list[tuple[_Spec, tuple[str, ...]]] = []
    for suite in suites:
        if any(character in suite.title for character in "\0\r\n"):
            raise ValueError("browser suite title contains an invalid identity character")
        path = (*ancestor_titles, suite.title)
        result.extend((spec, path) for spec in suite.specs)
        result.extend(_specs(suite.suites, depth + 1, path))
        if len(result) > MAX_TESTS:
            raise ValueError("browser spec population exceeds its admitted bound")
    return result


def admit_report(
    path: Path,
    *,
    test_root: Path,
    expected_files: tuple[str, ...],
    planned: tuple[BrowserIdentity, ...] | None = None,
) -> tuple[BrowserIdentity, ...]:
    report = _Report.model_validate_json(artifact_bytes(path))
    if (
        report.errors
        or not report.config.forbidOnly
        or not report.config.failOnFlakyTests
        or Path(report.config.rootDir) != test_root
        or tuple(sorted(project.name for project in report.config.projects)) != PROJECTS
        or any(
            Path(project.testDir) != test_root or project.repeatEach != 1 or project.retries != 0
            for project in report.config.projects
        )
    ):
        raise ValueError("browser report changes the admitted execution or project policy")
    if not expected_files or expected_files != tuple(sorted(set(expected_files))):
        raise ValueError("browser candidate files must be independently nonempty and unique")
    identities: list[BrowserIdentity] = []
    counts: Counter[str] = Counter()
    skip_candidates: Counter[tuple[str, str, str]] = Counter()
    skipped: set[tuple[str, str, str]] = set()
    for spec, ancestor_titles in _specs(report.suites):
        file = PurePosixPath(spec.file)
        if (
            file.is_absolute()
            or file.as_posix() != spec.file
            or ".." in file.parts
            or "\\" in spec.file
            or spec.file not in expected_files
            or not spec.id
            or not spec.title
            or any(character in spec.id + spec.title for character in "\0\r\n")
        ):
            raise ValueError("browser spec identity is outside its admitted source scope")
        for test in spec.tests:
            if test.projectName not in PROJECTS:
                raise ValueError("browser test names a foreign project")
            skip = spec.file, spec.title, test.projectName
            if skip in _ALLOWED_SKIPS:
                skip_candidates[skip] += 1
                if skip_candidates[skip] > 1:
                    raise ValueError("browser skip exception names multiple native occurrences")
            identities.append(
                BrowserIdentity(
                    native_id=spec.id,
                    file=spec.file,
                    ancestor_titles=ancestor_titles,
                    title=spec.title,
                    line=spec.line,
                    column=spec.column,
                    project=test.projectName,
                )
            )
            if planned is None:
                if test.results:
                    raise ValueError("browser listing unexpectedly executed tests")
                continue
            if len(test.results) != 1 or test.results[0].retry != 0:
                raise ValueError("browser test is missing its single admitted terminal attempt")
            attempt = test.results[0]
            if attempt.errors:
                raise ValueError("browser attempt contains errors")
            counts[test.status] += 1
            if (
                test.status == "expected"
                and test.expectedStatus == "passed"
                and attempt.status == "passed"
            ):
                continue
            if (
                test.status != "skipped"
                or test.expectedStatus != "skipped"
                or attempt.status != "skipped"
                or skip not in _ALLOWED_SKIPS
            ):
                raise ValueError("browser test has an unadmitted terminal outcome")
            skipped.add(skip)
    if not identities or len(identities) > MAX_TESTS:
        raise ValueError("browser identity population is empty or exceeds its bound")
    canonical = tuple(sorted(identities, key=_identity_key))
    keys = tuple((item.native_id, item.project) for item in canonical)
    if len(keys) != len(set(keys)):
        raise ValueError("browser native identity is duplicated")
    coordinates = [
        (item.file, item.ancestor_titles, item.line, item.column, item.title, item.project)
        for item in canonical
    ]
    if len(coordinates) != len(set(coordinates)):
        raise ValueError("browser source occurrence is duplicated")
    for project in PROJECTS:
        if (
            tuple(sorted({item.file for item in canonical if item.project == project}))
            != expected_files
        ):
            raise ValueError("browser project omits an authored candidate file")
    if planned is not None:
        if canonical != planned:
            raise ValueError("browser execution differs from its frozen native listing")
        if report.stats.model_dump() != {
            "expected": counts["expected"],
            "unexpected": counts["unexpected"],
            "flaky": counts["flaky"],
            "skipped": counts["skipped"],
        }:
            raise ValueError("browser counters differ from the observed terminal identities")
        expected_skips = {
            (item.file, item.title, item.project)
            for item in planned
            if (item.file, item.title, item.project) in _ALLOWED_SKIPS
        }
        if skipped != expected_skips:
            raise ValueError("browser skip disposition differs from its exact owner policy")
    return canonical

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Final, Literal, Never, NotRequired, TypedDict
from xml.etree.ElementTree import (
    Element,
    ParseError,
    TreeBuilder,
    XMLParser,
)

from scripts.repository_paths import read_repository_regular_file

PYTEST_REPORT_RELATIVE_PATH: Final = Path(".mutation-witness/pytest-report.xml")
_MAXIMUM_REPORT_BYTES: Final = 8 * 1024 * 1024
_MAXIMUM_TESTS: Final = 100_000
_MAXIMUM_ELEMENTS: Final = 400_010
_MAXIMUM_DEPTH: Final = 8
_CASE_METADATA: Final = frozenset({"properties", "system-out", "system-err"})


class PytestResult(TypedDict):
    state: Literal["failed_tests", "passed_tests"]
    reportDigest: str
    executedIdentityDigest: str
    skippedIdentityDigest: str
    totalTests: int
    failedTests: int


class InvalidPytestReport(TypedDict):
    state: Literal["invalid"]
    reason: str
    reportDigest: NotRequired[str]
    admissionDetail: NotRequired[str]


type PytestEvidence = PytestResult | InvalidPytestReport
type TestIdentity = tuple[str, str, str]


class _BoundedReportTree(TreeBuilder):
    def __init__(self) -> None:
        super().__init__()
        self._depth = 0
        self._elements = 0

    def start(self, tag: str, attrs: dict[str, str]) -> Element:
        self._depth += 1
        self._elements += 1
        if self._depth > _MAXIMUM_DEPTH or self._elements > _MAXIMUM_ELEMENTS:
            raise ValueError("report tree exceeds its bound")
        return super().start(tag, attrs)

    def end(self, tag: str) -> Element:
        self._depth -= 1
        return super().end(tag)

    def doctype(self, _name: str, _pubid: str | None, _system: str | None) -> Never:
        raise ValueError("report document types are forbidden")


def is_pytest_command(command: list[str]) -> bool:
    return bool(command) and (
        Path(command[0]).name.removesuffix(".exe") == "pytest"
        or any(
            argument == "pytest" and index > 0 and command[index - 1] == "-m"
            for index, argument in enumerate(command)
        )
    )


def prepare_pytest_command(command: list[str], worktree: Path) -> list[str]:
    if not is_pytest_command(command):
        return command
    if _overrides_report_authority(command):
        raise RuntimeError("pytest mutation command cannot override report or selection authority")
    report_path = worktree / PYTEST_REPORT_RELATIVE_PATH
    report_path.parent.mkdir(parents=True, exist_ok=True)
    if report_path.parent.is_symlink():
        raise RuntimeError("pytest mutation report directory must not be a symlink")
    if report_path.is_dir():
        raise RuntimeError("pytest mutation report path is not a file")
    report_path.unlink(missing_ok=True)
    return [*command, f"--junitxml={report_path}", "-o", "junit_family=xunit1"]


def _overrides_report_authority(command: list[str]) -> bool:
    for index, argument in enumerate(command[1:], start=1):
        if argument.startswith(("--junit", "--maxfail", "--exitfirst")) or (
            argument.startswith("-") and set(argument[1:]) <= set("qvslx") and "x" in argument
        ):
            return True
        override = ""
        if argument in {"-o", "--override-ini"} and index + 1 < len(command):
            override = command[index + 1]
        elif argument.startswith("--override-ini="):
            override = argument.split("=", 1)[1]
        elif argument.startswith("-o"):
            override = argument[2:]
        key = override.partition("=")[0].strip()
        if key.startswith("junit_") or key == "addopts":
            return True
    return False


def read_pytest_evidence(worktree: Path) -> PytestEvidence:
    try:
        content = read_repository_regular_file(
            worktree,
            PYTEST_REPORT_RELATIVE_PATH,
            "pytest mutation report",
            maximum_bytes=_MAXIMUM_REPORT_BYTES,
        )
    except (OSError, ValueError):
        return {"state": "invalid", "reason": "report_missing_or_unreadable"}
    digest = hashlib.sha256(content).hexdigest()
    try:
        parser = XMLParser(  # noqa: S314 -- DTDs are rejected; input bytes and tree are bounded.
            target=_BoundedReportTree()
        )
        parser.feed(content)
        return _admit_report(parser.close(), digest)
    except ParseError:
        return {"state": "invalid", "reason": "report_invalid", "reportDigest": digest}
    except ValueError as error:
        return {
            "state": "invalid",
            "reason": "report_invalid",
            "reportDigest": digest,
            "admissionDetail": str(error),
        }


def _admit_report(root: Element, digest: str) -> PytestEvidence:
    if root.tag != "testsuites" or len(root) != 1 or root[0].tag != "testsuite":
        raise ValueError("report must contain one pytest suite")
    suite = root[0]
    counters = {
        name: _count(suite.get(name)) for name in ("tests", "errors", "failures", "skipped")
    }
    if counters["errors"]:
        raise ValueError("report contains setup, teardown, or collection errors")
    if not 0 < counters["tests"] <= _MAXIMUM_TESTS:
        raise ValueError("report did not prove a test outcome")
    executed: set[TestIdentity] = set()
    skipped: set[TestIdentity] = set()
    failed = 0
    for case in suite:
        if case.tag == "properties":
            _admit_metadata(case)
            continue
        if case.tag != "testcase":
            raise ValueError("report has an unexpected suite child")
        identity = (
            _identity_part(case, "file"),
            _identity_part(case, "classname"),
            _identity_part(case, "name"),
        )
        if identity in executed or identity in skipped:
            raise ValueError("report repeats a test identity")
        outcomes: list[str] = []
        for child in case:
            if child.tag in _CASE_METADATA:
                _admit_metadata(child)
            elif child.tag in {"failure", "skipped"} and not len(child):
                outcomes.append(str(child.tag))
                if child.tag == "failure" and not (child.text or "").strip():
                    raise ValueError("report has no failure evidence")
            else:
                raise ValueError("report contains an error phase or unexpected child")
        if len(outcomes) > 1:
            raise ValueError("report has contradictory test outcomes")
        if outcomes == ["skipped"]:
            skipped.add(identity)
        else:
            executed.add(identity)
            failed += outcomes == ["failure"]
        if len(executed) + len(skipped) > _MAXIMUM_TESTS:
            raise ValueError("report has too many test cases")
    if (
        not executed
        or counters["tests"] != len(executed) + len(skipped)
        or counters["failures"] != failed
        or counters["skipped"] != len(skipped)
    ):
        raise ValueError("report counters contradict its cases")
    return {
        "state": "failed_tests" if failed else "passed_tests",
        "reportDigest": digest,
        "executedIdentityDigest": _identity_digest(executed),
        "skippedIdentityDigest": _identity_digest(skipped),
        "totalTests": counters["tests"],
        "failedTests": failed,
    }


def _admit_metadata(element: Element) -> None:
    if element.tag == "properties":
        if any(child.tag != "property" or len(child) for child in element):
            raise ValueError("report properties contain an unexpected child")
    elif len(element):
        raise ValueError("report output must not contain elements")


def _count(value: str | None) -> int:
    if value is None or not value.isascii() or not value.isdecimal() or len(value) > 6:
        raise ValueError("report counter is invalid")
    count = int(value)
    if count > _MAXIMUM_TESTS:
        raise ValueError("report counter exceeds its bound")
    return count


def _identity_part(case: Element, name: str) -> str:
    value = case.get(name)
    if value is None or not value or len(value) > 2048:
        raise ValueError("report test identity is invalid")
    return value


def _identity_digest(identities: set[TestIdentity]) -> str:
    encoded = json.dumps(sorted(identities), ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()

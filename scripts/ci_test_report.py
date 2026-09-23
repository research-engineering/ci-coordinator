from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from time import monotonic
from typing import Literal

import pytest

from scripts.ci_test_plan import Artifact, Seconds, TestNode, write_artifact


class PhaseResult(Artifact):
    node_id: str
    phase: Literal["setup", "call", "teardown"]
    outcome: Literal["passed", "failed", "skipped"]
    expected_failure: bool
    skip_reason: str | None
    elapsed_seconds: Seconds


class ModuleResult(Artifact):
    file: str
    outcome: Literal["passed", "failed", "skipped"]


class NativeReport(Artifact):
    schema_version: Literal["ci-coordinator-native-test-report/v2"] = (
        "ci-coordinator-native-test-report/v2"
    )
    nodes: tuple[TestNode, ...]
    collected_nodes: tuple[TestNode, ...]
    deselected_node_ids: tuple[str, ...]
    modules: tuple[ModuleResult, ...]
    collection_issues: tuple[str, ...]
    collection_finished: bool
    phases: tuple[PhaseResult, ...]
    exit_status: int
    elapsed_seconds: Seconds


class _Capture:
    def __init__(self) -> None:
        self.started = monotonic()
        self.nodes: tuple[TestNode, ...] = ()
        self.collected_nodes: list[TestNode] = []
        self.deselected_node_ids: list[str] = []
        self.modules: list[ModuleResult] = []
        self.collection_issues: list[str] = []
        self.collection_finished = False
        self.phases: list[PhaseResult] = []


_CAPTURE = pytest.StashKey[_Capture]()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--ci-report", help="Exclusive native collection and phase report path")


def pytest_sessionstart(session: pytest.Session) -> None:
    session.config.stash[_CAPTURE] = _Capture()


def _node(item: pytest.Item) -> TestNode:
    file = item.path.resolve().relative_to(item.config.rootpath.resolve()).as_posix()
    return TestNode(
        node_id=file + "::" + item.nodeid.split("::", 1)[1],
        file=file,
        markers=tuple(sorted({marker.name for marker in item.iter_markers()})),
        fixtures=tuple(sorted(set(getattr(item, "fixturenames", ())))),
    )


def pytest_itemcollected(item: pytest.Item) -> None:
    item.config.stash[_CAPTURE].collected_nodes.append(_node(item))


def pytest_deselected(items: list[pytest.Item]) -> None:
    for item in items:
        item.config.stash[_CAPTURE].deselected_node_ids.append(_node(item).node_id)


@pytest.hookimpl(wrapper=True)
def pytest_make_collect_report(
    collector: pytest.Collector,
) -> Generator[None, pytest.CollectReport, pytest.CollectReport]:
    report = yield
    capture = collector.config.stash[_CAPTURE]
    if isinstance(collector, pytest.Module):
        capture.modules.append(
            ModuleResult(
                file=collector.path.resolve()
                .relative_to(collector.config.rootpath.resolve())
                .as_posix(),
                outcome=report.outcome,
            )
        )
    if report.outcome != "passed":
        capture.collection_issues.append(f"{report.nodeid}: {report.outcome}: {report.longrepr}")
    return report


def pytest_collection_finish(session: pytest.Session) -> None:
    session.config.stash[_CAPTURE].collection_finished = True


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_collection_modifyitems(
    session: pytest.Session, items: list[pytest.Item]
) -> Generator[None, object, object]:
    result = yield
    nodes = tuple(_node(item) for item in items)
    session.config.stash[_CAPTURE].nodes = tuple(sorted(nodes, key=lambda item: item.node_id))
    return result


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    reason = (
        str(report.longrepr[2]) if report.skipped and isinstance(report.longrepr, tuple) else None
    )
    item.config.stash[_CAPTURE].phases.append(
        PhaseResult(
            node_id=item.path.resolve().relative_to(item.config.rootpath.resolve()).as_posix()
            + "::"
            + item.nodeid.split("::", 1)[1],
            phase=report.when,
            outcome=report.outcome,
            expected_failure=hasattr(report, "wasxfail"),
            skip_reason=reason,
            elapsed_seconds=report.duration,
        )
    )
    return report


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    capture = session.config.stash[_CAPTURE]
    path = session.config.getoption("--ci-report")
    if path is None:
        raise ValueError("native report requires an explicit output path")
    write_artifact(
        Path(path),
        NativeReport(
            nodes=capture.nodes,
            collected_nodes=tuple(sorted(capture.collected_nodes, key=lambda item: item.node_id)),
            deselected_node_ids=tuple(sorted(capture.deselected_node_ids)),
            modules=tuple(sorted(capture.modules, key=lambda item: item.file)),
            collection_issues=tuple(capture.collection_issues),
            collection_finished=capture.collection_finished,
            phases=tuple(capture.phases),
            exit_status=int(exitstatus),
            elapsed_seconds=monotonic() - capture.started,
        ),
    )

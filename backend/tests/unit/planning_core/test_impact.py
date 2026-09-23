from __future__ import annotations

from ci_coordinator.planning_core import analyze_impact
from ci_coordinator.repo_context import DependencyGraphNode, DiffFileChangeInput

from .conftest import MakeInput


def test_impact_closes_transitive_dependents_and_accumulates_risk(make_input: MakeInput) -> None:
    input = make_input(
        DiffFileChangeInput(path="src/a.py", status="modified"),
        graph_nodes=(
            DependencyGraphNode("src/a.py", ("src/b.py",), ("backend",)),
            DependencyGraphNode("src/b.py", ("src/c.py",), ("test",)),
            DependencyGraphNode("src/c.py", (), ("docs",)),
        ),
        global_risk_paths=(),
    )

    impact = analyze_impact(input)

    assert impact.changed_paths == ("src/a.py",)
    assert impact.impacted_paths == ("src/a.py", "src/b.py", "src/c.py")
    assert impact.impacted_risk_classes == ("backend", "docs", "test")
    assert impact.unknown_paths == ()


def test_impact_marks_unknown_and_global_risk_paths(make_input: MakeInput) -> None:
    input = make_input(
        DiffFileChangeInput(path="ci/pipeline.yml", status="modified"),
        DiffFileChangeInput(path="src/unknown.py", status="modified"),
    )

    impact = analyze_impact(input)

    assert impact.global_risk_paths == ("ci/pipeline.yml",)
    assert impact.unknown_paths == ("src/unknown.py",)

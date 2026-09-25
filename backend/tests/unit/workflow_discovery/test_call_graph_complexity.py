from __future__ import annotations

from collections.abc import Iterator
from itertools import product

import pytest

from ci_coordinator.workflow_discovery.graph import _cycle_pairs, analyze_call_graph
from ci_coordinator.workflow_discovery.parser import parse_workflow
from ci_coordinator.workflow_discovery.summary import ParsedWorkflow

from .test_workflow_discovery import REVISION, SCOPE, _source


@pytest.mark.parametrize("mask", range(1 << 9))
def test_cycle_edges_match_independent_transitive_closure(mask: int) -> None:
    pairs = tuple(product(range(3), repeat=2))
    edges = {(str(a), str(b)) for bit, (a, b) in enumerate(pairs) if mask & (1 << bit)}
    reachable = [[(str(a), str(b)) in edges for b in range(3)] for a in range(3)]
    for via in range(3):
        for source in range(3):
            for destination in range(3):
                reachable[source][destination] |= (
                    reachable[source][via] and reachable[via][destination]
                )
    expected = {(a, b) for a, b in edges if reachable[int(b)][int(a)]}

    assert _cycle_pairs(edges) == expected


@pytest.mark.parametrize("cyclic", [False, True])
def test_maximum_workflow_dag_does_not_repeatedly_scan_edges(cyclic: bool) -> None:
    edges = _CountingEdges(_layered_pairs(32))
    if cyclic:
        edges.update((f"31-{a}", f"0-{b}") for a, b in product(range(2), repeat=2))

    result = _cycle_pairs(edges)

    assert result == (set(edges) if cyclic else set())
    assert edges.visits <= 4 * len(edges)


def test_dense_maximum_graph_retains_every_cycle_edge() -> None:
    edges = _CountingEdges((str(a), str(b)) for a, b in product(range(64), repeat=2))

    assert _cycle_pairs(edges) == set(edges)
    assert edges.visits <= 4 * len(edges)


def test_public_analysis_preserves_depth_and_evidence_for_layered_workflows() -> None:
    pairs = _layered_pairs(32)
    workflows: list[ParsedWorkflow] = []
    for layer, side in product(range(32), range(2)):
        node = f"{layer}-{side}"
        targets = sorted(destination for source, destination in pairs if source == node)
        jobs = (
            "".join(
                f"  call-{index}:\n    uses: ./.github/workflows/{target}.yml\n"
                for index, target in enumerate(targets)
            )
            or "  leaf:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo complete\n"
        )
        parsed = parse_workflow(
            _source(f"on: workflow_call\njobs:\n{jobs}", f".github/workflows/{node}.yml"),
            scope=SCOPE,
            revision=REVISION,
            default_branch="master",
        )
        assert isinstance(parsed, ParsedWorkflow)
        workflows.append(parsed)

    result = analyze_call_graph(tuple(workflows))

    assert len(result.edges) == len(pairs) == 124
    assert {edge.status for edge in result.edges} == {"depth_exceeded"}
    assert {unknown.reason for unknown in result.unknowns} == {"local_call_depth_exceeded"}
    assert len(result.unknowns) == 124
    assert result.facts == ()
    assert result.local_graph_closed is False
    assert result == analyze_call_graph(tuple(reversed(workflows)))


def _layered_pairs(layers: int) -> set[tuple[str, str]]:
    return {
        (f"{layer}-{a}", f"{layer + 1}-{b}")
        for layer in range(layers - 1)
        for a, b in product(range(2), repeat=2)
    }


class _CountingEdges(set[tuple[str, str]]):
    visits: int = 0

    def __iter__(self) -> Iterator[tuple[str, str]]:
        for pair in super().__iter__():
            self.visits += 1
            assert self.visits <= 4 * len(self), "cycle analysis repeatedly scans the edge set"
            yield pair

from __future__ import annotations

import sys
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.ci_utility_inventory import go_modules
from scripts.self_ci_generate import SelfCiArtifacts, render_self_ci

MUTATION_DIR = Path(__file__).resolve().parents[1] / "mutation"
sys.path.insert(0, str(MUTATION_DIR))


@pytest.fixture(scope="session")
def self_ci_artifacts_factory() -> Callable[[], SelfCiArtifacts]:
    root = Path(__file__).resolve().parents[2]
    baseline = render_self_ci(root)

    def fresh() -> SelfCiArtifacts:
        baseline.assert_inputs_current(root)
        go_modules(root, tuple(path for path, _mode in baseline.responsibility.path_inventory))
        return deepcopy(baseline)

    return fresh


@pytest.fixture
def self_ci_artifacts(
    self_ci_artifacts_factory: Callable[[], SelfCiArtifacts],
) -> SelfCiArtifacts:
    return self_ci_artifacts_factory()

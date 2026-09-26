from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[2]


def _assert_runtime_qualification_events(events: dict[str, Any]) -> None:
    assert set(events) == {"workflow_dispatch", "push", "pull_request"}
    assert events["push"] == {"branches": ["master"]}, "runtime qualification events changed"
    assert events["pull_request"] == {}, "runtime qualification events changed"


def test_runtime_qualification_is_current_ref_scoped_and_read_only() -> None:
    workflow: dict[str, Any] = YAML(typ="safe").load(
        (ROOT / ".github/workflows/runtime-image-qualification.yml").read_text()
    )
    events = workflow["on"]
    _assert_runtime_qualification_events(events)
    assert workflow["permissions"] == {"contents": "read"}
    steps = workflow["jobs"]["qualify"]["steps"]
    checkout = next(step for step in steps if step.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["fetch-depth"] == 0
    comparison = next(
        step for step in steps if step.get("run") == "python3 -I -B docker/runtime/compare.py"
    )
    assert comparison["env"]["BASELINE_COMMIT"] == (
        "${{ github.event.pull_request.base.sha || github.event.before || inputs.baseline_commit }}"
    )
    assert "GH_TOKEN" not in comparison["env"]
    assert events["workflow_dispatch"]["inputs"]["baseline_commit"]["type"] == "string"
    build = next(step for step in steps if step.get("id") == "build")["with"]
    scope = "scope=runtime-qualification-amd64-${{ github.ref }}"
    assert scope in build["cache-from"]
    assert scope in build["cache-to"]
    assert build["push"] is False
    scripts = "\n".join(step.get("run", "") for step in steps)
    assert "050b7ef3947ad69b5d1e7762308a75a57503ee4e" not in scripts
    assert "python3 - <<" not in scripts
    assert "python3 -I -B docker/runtime/check_layer_reuse.py" in scripts


@pytest.mark.parametrize("event", ["push", "pull_request"])
@pytest.mark.parametrize("filter_name", ["paths", "paths-ignore"])
def test_runtime_qualification_rejects_path_filters(event: str, filter_name: str) -> None:
    workflow: dict[str, Any] = YAML(typ="safe").load(
        (ROOT / ".github/workflows/runtime-image-qualification.yml").read_text()
    )
    events = workflow["on"]
    _assert_runtime_qualification_events(events)
    events[event][filter_name] = ["backend/alembic/**"]
    with pytest.raises(AssertionError, match=r"^runtime qualification events changed(?:\n|$)"):
        _assert_runtime_qualification_events(events)


def test_exploratory_base_comparison_has_no_obsolete_branch_trigger() -> None:
    workflow: dict[str, Any] = YAML(typ="safe").load(
        (ROOT / ".github/workflows/runtime-base-comparison.yml").read_text()
    )
    assert workflow["on"] == {"workflow_dispatch": {}}
    assert workflow["permissions"] == {}

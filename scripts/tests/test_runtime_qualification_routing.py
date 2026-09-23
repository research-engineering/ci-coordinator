from __future__ import annotations

from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[2]


def test_runtime_qualification_is_current_ref_scoped_and_read_only() -> None:
    workflow: dict[str, Any] = YAML(typ="safe").load(
        (ROOT / ".github/workflows/runtime-image-qualification.yml").read_text()
    )
    events = workflow["on"]
    assert set(events) == {"workflow_dispatch", "push", "pull_request"}
    assert events["push"]["branches"] == ["master"]
    assert set(events["push"]["paths"]) == set(events["pull_request"]["paths"])
    assert {"Dockerfile", "docker/runtime/**", "backend/uv.lock"} <= set(events["push"]["paths"])
    assert workflow["permissions"] == {"contents": "read"}
    steps = workflow["jobs"]["qualify"]["steps"]
    build = next(step for step in steps if step.get("id") == "build")["with"]
    scope = "scope=runtime-qualification-amd64-${{ github.ref }}"
    assert scope in build["cache-from"]
    assert scope in build["cache-to"]
    assert build["push"] is False
    scripts = "\n".join(step.get("run", "") for step in steps)
    assert "EXPECTED_MALLOC_PROVIDER=" in scripts
    assert "python3 - <<" not in scripts
    assert "python3 -I -B docker/runtime/check_layer_reuse.py" in scripts


def test_exploratory_base_comparison_has_no_obsolete_branch_trigger() -> None:
    workflow: dict[str, Any] = YAML(typ="safe").load(
        (ROOT / ".github/workflows/runtime-base-comparison.yml").read_text()
    )
    assert workflow["on"] == {"workflow_dispatch": {}}
    assert workflow["permissions"] == {}

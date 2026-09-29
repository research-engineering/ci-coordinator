from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML

ROOT = Path(__file__).resolve().parents[2]

_AMD64_BRIDGE = {
    "image": "tonistiigi/binfmt@sha256:"
    "400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0",
    "platforms": "amd64",
    "cache-image": False,
}


def _assert_amd64_bridge(steps: list[dict[str, Any]], protected_step: str) -> None:
    setup = [step for step in steps if step.get("uses", "").startswith("docker/setup-qemu-action@")]
    assert len(setup) == 1
    assert setup[0]["uses"] == ("docker/setup-qemu-action@99012661954931238ded8c8b007157a8430204e1")
    assert setup[0]["with"] == _AMD64_BRIDGE
    protected = next(step for step in steps if step.get("name") == protected_step)
    assert steps.index(setup[0]) < steps.index(protected)


@pytest.mark.parametrize(
    ("filename", "job_id", "protected_step"),
    [
        ("python-persistence.yml", "container-runtime-smoke", "Qualify build inputs"),
        (
            "python-persistence.yml",
            "connected-administrator",
            "Prove authenticated persistent effect and replay",
        ),
        ("coordinated-checks.yml", "container-runtime-smoke", "Qualify build inputs"),
        (
            "coordinated-checks.yml",
            "connected-administrator",
            "Prove authenticated persistent effect and replay",
        ),
        ("runtime-image-qualification.yml", "qualify", "Set up pinned builder"),
        ("runtime-base-comparison.yml", "compare", "Set up pinned builder"),
        ("release-artifact.yml", "build", "Set up pinned Buildx and BuildKit"),
    ],
)
def test_arm_image_jobs_prepare_exact_emulation_before_consuming_amd64(
    filename: str, job_id: str, protected_step: str
) -> None:
    workflow = YAML(typ="safe").load(ROOT / ".github/workflows/" / filename)
    _assert_amd64_bridge(workflow["jobs"][job_id]["steps"], protected_step)


@pytest.mark.parametrize("failure", ["missing", "wrong-platform", "mutable-image", "late"])
def test_emulation_oracle_rejects_unqualified_or_late_setup(failure: str) -> None:
    steps: list[dict[str, Any]] = [
        {
            "uses": "docker/setup-qemu-action@99012661954931238ded8c8b007157a8430204e1",
            "with": dict(_AMD64_BRIDGE),
        },
        {"name": "Build target"},
    ]
    if failure == "missing":
        steps.pop(0)
    elif failure == "late":
        steps.reverse()
    else:
        steps[0]["with"]["platforms" if failure == "wrong-platform" else "image"] = (
            "arm64" if failure == "wrong-platform" else "tonistiigi/binfmt:latest"
        )
    with pytest.raises(AssertionError):
        _assert_amd64_bridge(steps, "Build target")


def test_linux_workflow_jobs_use_arm_and_macos_observers_remain_explicit() -> None:
    macos_jobs = set()
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        workflow = YAML(typ="safe").load(path)
        for job_id, job in workflow["jobs"].items():
            if "uses" in job:
                assert "runs-on" not in job
            elif job_id == "developer-host-lifecycle":
                assert job["runs-on"] == "macos-15"
                macos_jobs.add(path.name)
            else:
                assert job["runs-on"] == "ubuntu-26.04-arm", (path.name, job_id)
    assert macos_jobs == {"python-persistence.yml", "coordinated-checks.yml"}


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

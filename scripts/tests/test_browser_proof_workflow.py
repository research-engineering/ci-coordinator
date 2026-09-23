import json
from pathlib import Path

from ruamel.yaml import YAML


def test_browser_job_builds_once_and_verifies_exact_assets_after_mutation() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = YAML(typ="safe").load(root / ".github/workflows/python-persistence.yml")
    steps = workflow["jobs"]["operator-workbench"]["steps"]
    build = "pnpm --filter @ci-coordinator/operator-ui build"
    browser = next(step for step in steps if step.get("id") == "browser_witnesses")
    assert browser["run"].splitlines() == [
        "backend/.venv/bin/python -m scripts.frontend_bundle",
        "backend/.venv/bin/python -m scripts.browser_test_execution --output .ci-native/browser",
    ]
    build_steps = [step for step in steps if build in step.get("run", "").splitlines()]
    assert len(build_steps) == 1
    mutation = next(
        step for step in steps if step["name"] == "Run frontend safety mutation witnesses"
    )
    assert steps.index(build_steps[0]) < steps.index(mutation) < steps.index(browser)
    package = json.loads((root / "frontend/package.json").read_text())
    assert package["scripts"]["test:browser"] == "pnpm build && playwright test"


def test_diagram_steps_are_required_after_the_single_existing_browser_install() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = YAML(typ="safe").load(root / ".github/workflows/python-persistence.yml")
    for event in ("pull_request", "merge_group", "workflow_dispatch"):
        configuration = workflow["on"][event] or {}
        assert "paths" not in configuration
        assert "paths-ignore" not in configuration
    jobs = workflow["jobs"]
    for job_id in ("repository-quality", "operator-workbench"):
        assert "if" not in jobs[job_id]
        assert job_id in jobs["pull-request-gate"]["needs"]
    inventory = [
        step
        for step in jobs["repository-quality"]["steps"]
        if step.get("run") == "backend/.venv/bin/python -m scripts.diagram_check --inventory-only"
    ]
    assert len(inventory) == 1
    steps = jobs["operator-workbench"]["steps"]
    installations = [
        step
        for step in steps
        if step.get("run")
        == "pnpm --filter @ci-coordinator/operator-ui exec playwright install --with-deps chromium"
    ]
    assert len(installations) == 1
    falsifiers = [step for step in steps if step.get("run") == "node tools/diagrams-checks.mjs"]
    renders = [
        step
        for step in steps
        if step.get("run") == "backend/.venv/bin/python -m scripts.diagram_check"
    ]
    assert len(falsifiers) == len(renders) == 1
    processes = [
        step
        for step in steps
        if step.get("run")
        == "backend/.venv/bin/python scripts/tests/test_diagram_process.py --qualify"
    ]
    assert len(processes) == 1
    assert processes[0]["timeout-minutes"] == 2
    assert processes[0].get("working-directory", ".") == "."
    assert falsifiers[0]["working-directory"] == "frontend"
    assert (
        steps.index(installations[0])
        < steps.index(falsifiers[0])
        < steps.index(processes[0])
        < steps.index(renders[0])
    )
    for step in (*inventory, *falsifiers, *processes, *renders):
        assert "if" not in step
        assert "continue-on-error" not in step

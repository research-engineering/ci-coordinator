from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.mutation.detached_worktree_lifecycle import DetachedWorktreeLifecycle
from scripts.mutation.mutation_manifest import Mutant, MutationManifest
from scripts.mutation.mutation_suite_runner import (
    _baseline_is_valid,
    _execute_witness,
    classify_mutation_execution,
)


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("pass", "survived"),
        ("assertion", "killed"),
        ("collect", "invalid"),
        ("skip", "invalid"),
        ("rename", "invalid"),
        ("skip-rename", "invalid"),
        ("repeat-drop", "invalid"),
        ("repeat-replace", "invalid"),
        ("repeat-skip-swap", "invalid"),
        ("repeat-assertion", "killed"),
    ],
)
def test_locked_vitest_mutation_preserves_actual_native_identities(
    tmp_path: Path, mode: str, expected: str
) -> None:
    root = Path(__file__).resolve().parents[2]
    executable = root / "frontend/node_modules/.bin/vitest"
    assert executable.is_file(), "the operator job must install locked frontend dependencies"
    lifecycle = DetachedWorktreeLifecycle(repo_root=tmp_path, temp_prefix="vitest-identity-")
    lifecycle.worktree.mkdir()
    config = lifecycle.worktree / "vitest.config.mjs"
    config.write_text(
        'export default { test: { include: ["probe.test.ts"], environment: "node" } };\n'
    )
    source = (
        "import { describe, test, expect } from "
        + json.dumps(str(root / "frontend/node_modules/vitest/dist/index.js"))
        + ';\nconst MODE = "pass";\n'
        + 'if (MODE === "collect") throw new Error("collection canary");\n'
        + 'describe("selected witness", () => {\n'
        + 'test(MODE === "rename" ? "other" : "guard", (context) => {\n'
        + '  if (MODE === "skip") context.skip();\n'
        + '  expect(MODE).not.toBe("assertion");\n});\n'
        + 'test.skip(MODE === "skip-rename" ? "other omission" : "omitted", () => {});\n'
        + 'const names = MODE === "repeat-drop" ? ["repeat"] : ["repeat",\n'
        + '  MODE === "repeat-replace" ? "substitute" : "repeat"];\n'
        + 'test.each(names)("%s", () => { expect(MODE).not.toBe("repeat-assertion"); });\n'
        + 'test.for([0, 1])("mixed repeat", (index, context) => {\n'
        + '  if (index === (MODE === "repeat-skip-swap" ? 1 : 0)) context.skip();\n'
        + "  expect(index).toBeGreaterThanOrEqual(0);\n});\n"
        + '});\ndescribe("unselected sibling", () => {\n'
        + '  test.each([0, 1, 2])("constant title", () => { throw new Error("filtered"); });\n'
        + "});\n"
    )
    mutant: Mutant = {
        "id": "identity",
        "file": "probe.test.ts",
        "operator": "identity-substitution",
        "original": 'const MODE = "pass";',
        "replacement": f'const MODE = "{mode}";',
        "witnessId": "identity",
        "requirementIds": ["REQ-PROBE-001"],
        "command": [str(executable), "run", "--config", str(config), "-t", "selected witness"],
    }
    manifest: MutationManifest = {
        "expectedKilled": 1,
        "expectedMutantIds": ["identity"],
        "mutants": [],
        "outerTimeoutMs": 60_000,
        "timeoutMs": 20_000,
    }
    try:
        path = lifecycle.worktree / "probe.test.ts"
        path.write_text(source)
        baseline = _execute_witness(lifecycle, manifest, mutant)
        assert _baseline_is_valid(mutant, baseline), baseline
        path.write_text(source.replace(mutant["original"], mutant["replacement"]))
        execution = _execute_witness(lifecycle, manifest, mutant)
        assert (
            classify_mutation_execution(mutant, execution, baseline=baseline)["status"] == expected
        ), (baseline, execution)
        assert classify_mutation_execution(mutant, execution)["status"] == "invalid"
    finally:
        assert lifecycle.cleanup().state == "passed"

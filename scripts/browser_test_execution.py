from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from scripts.bounded_process import spawn
from scripts.browser_test_admission import BROWSER_ROOT, admit_report
from scripts.ci_test_execution import current_epoch
from scripts.ci_test_plan import artifact_bytes
from scripts.ci_utility_inventory import admitted_sources, repository_paths

ROOT = Path(__file__).resolve().parent.parent
_REPORT_LIMIT = 32 * 1024 * 1024


def browser_inputs(root: Path) -> tuple[tuple[str, ...], str]:
    paths = tuple(
        path
        for path in repository_paths(root)
        if path.startswith(BROWSER_ROOT + "/") and path.endswith(".spec.ts")
    )
    owners = (
        "frontend/package.json",
        "frontend/playwright.config.ts",
        "pnpm-lock.yaml",
        "scripts/browser_test_admission.py",
        "scripts/browser_test_execution.py",
    )
    names = admitted_sources(root, tuple(sorted((*paths, *owners))))
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode("utf-8") + b"\0" + artifact_bytes(root / name) + b"\0")
    files = tuple(path.removeprefix(BROWSER_ROOT + "/") for path in paths)
    if not files:
        raise ValueError("browser source inventory is empty")
    return files, digest.hexdigest()


def _playwright(root: Path, output: Path, *, listing: bool) -> None:
    environment = dict(os.environ)
    for name in tuple(environment):
        if name.startswith("PLAYWRIGHT_JSON_OUTPUT"):
            environment.pop(name)
    environment["PLAYWRIGHT_JSON_OUTPUT_FILE"] = str(output)
    command = (
        "--filter",
        "@ci-coordinator/operator-ui",
        "exec",
        "playwright",
        "test",
        "--reporter=line,json",
        *(("--list",) if listing else ()),
    )
    result = spawn(
        "pnpm",
        command,
        cwd=root,
        env=environment,
        max_buffer=_REPORT_LIMIT,
        timeout_seconds=120 if listing else 1_200,
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    output.with_suffix(".process.json").write_text(
        json.dumps(
            {
                "exit": result.status,
                "failureKind": result.failure_kind,
                "reportPresent": output.is_file(),
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if result.status != 0 or result.error is not None:
        raise ValueError("browser native process failed; diagnostics are not pass evidence")


def run(root: Path, output: Path) -> None:
    if output.exists():
        raise ValueError("browser evidence output must be exclusive to this attempt")
    output.mkdir(parents=True)
    epoch = current_epoch(root)
    files, source_digest = browser_inputs(root)
    listed = output / "listing.json"
    executed = output / "execution.json"
    _playwright(root, listed, listing=True)
    plan = admit_report(listed, test_root=root / BROWSER_ROOT, expected_files=files)
    if browser_inputs(root) != (files, source_digest) or current_epoch(root) != epoch:
        raise ValueError("browser inputs changed during collection")
    _playwright(root, executed, listing=False)
    admitted = admit_report(
        executed, test_root=root / BROWSER_ROOT, expected_files=files, planned=plan
    )
    if browser_inputs(root) != (files, source_digest) or current_epoch(root) != epoch:
        raise ValueError("browser inputs changed during execution")
    receipt = {
        "schemaVersion": "ci-coordinator-browser-execution/v1",
        "epoch": epoch.model_dump(mode="json"),
        "inputDigest": source_digest,
        "listingSha256": hashlib.sha256(artifact_bytes(listed)).hexdigest(),
        "executionSha256": hashlib.sha256(artifact_bytes(executed)).hexdigest(),
        "candidateFiles": files,
        "nodes": [identity.model_dump(mode="json") for identity in admitted],
    }
    (output / "receipt.json").write_text(
        json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8"
    )
    print(json.dumps({"browserFiles": len(files), "browserIdentities": len(admitted)}))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        run(ROOT, arguments.output.resolve())
    except (OSError, ValueError) as error:
        print(f"browser evidence admission failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

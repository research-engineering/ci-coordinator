from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import re
import shutil
import sys
from pathlib import Path
from time import monotonic

from scripts.bounded_process import StopPredicate, spawn
from scripts.diagram_contract import (
    DiagramReport,
    admit_report,
    is_document_path,
    load_profile,
)
from scripts.diagram_inventory import build_manifest, remaining_seconds
from scripts.diagram_process import cancellation_signals
from scripts.documentation_graph_filesystem import read_document_sources
from scripts.documentation_graph_policy import load_policy

REPO_ROOT = Path(__file__).resolve().parent.parent


def admit_python_packages(root: Path) -> None:
    sources, issues = read_document_sources(
        root, ["backend/requirements-dev.lock"], load_policy(root).limits
    )
    if issues:
        raise ValueError("\n".join(issues))
    requirements = sources["backend/requirements-dev.lock"].decode("utf-8")
    for name in (
        "markdown-it-py",
        "mdurl",
        "ruamel-yaml",
        "pydantic",
        "pydantic-core",
        "annotated-types",
        "typing-extensions",
        "typing-inspection",
    ):
        expected = re.findall(r"^" + re.escape(name) + r"==([^\s]+)", requirements, re.MULTILINE)
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError as error:
            raise ValueError(
                f"installed {name} is absent; prepare locked backend dependencies"
            ) from error
        if len(expected) != 1 or actual != expected[0]:
            raise ValueError(
                f"installed {name} differs from the Python lock; "
                "prepare locked backend dependencies"
            )


def check(
    root: Path,
    revision: str | None = None,
    *,
    inventory_only: bool = False,
    artifacts: Path | None = None,
    stop_requested: StopPredicate | None = None,
) -> dict[str, object]:
    started = monotonic()
    deadline = started + load_profile(root).runTimeoutSeconds
    if sys.version.split()[0] != "3.13.15":
        raise ValueError("diagram check requires the locked Python 3.13.15 environment")
    admit_python_packages(root)
    manifest = build_manifest(root, revision, deadline=deadline, stop_requested=stop_requested)
    if stop_requested is not None and stop_requested():
        raise ValueError("diagram validation cancelled")
    if not inventory_only:
        node = shutil.which("node")
        if node is None:
            raise ValueError(
                "Node is unavailable; run mise install --locked and prepare locked dependencies"
            )
        args = ["tools/diagrams.mjs"]
        if artifacts is not None:
            args.extend(["--artifacts", str(artifacts.resolve())])
        result = spawn(
            node,
            args,
            cwd=root / "frontend",
            env={
                key: os.environ[key]
                for key in ("PATH", "HOME", "TMPDIR", "PLAYWRIGHT_BROWSERS_PATH")
                if key in os.environ
            },
            input_text=manifest.model_dump_json(),
            timeout_seconds=remaining_seconds(deadline),
            max_buffer=manifest.profile.maxOutputBytes,
            stop_requested=stop_requested,
        )
        if result.error:
            raise ValueError(f"diagram renderer failed: {result.error}")
        if result.stderr:
            print(result.stderr.rstrip(), file=sys.stderr)
        try:
            report = DiagramReport.model_validate_json(result.stdout)
        except ValueError as error:
            raise ValueError(
                "diagram renderer did not produce a complete report; "
                "prepare locked frontend dependencies and Chromium"
            ) from error
        admit_report(manifest, report)
        if result.status != 0 or any(row.errors for row in report.results):
            failures = sum(bool(row.errors) for row in report.results)
            raise ValueError(
                f"diagram validation failed for {failures} of {len(report.results)} diagrams"
            )
    if (
        build_manifest(
            root, manifest.revision, deadline=deadline, stop_requested=stop_requested
        ).digest
        != manifest.digest
    ):
        raise ValueError("diagram inputs changed during validation")
    if stop_requested is not None and stop_requested():
        raise ValueError("diagram validation cancelled")
    return {
        "schemaVersion": 1,
        "mode": "inventory" if inventory_only else "render",
        "revision": manifest.revision,
        "inventoryDigest": manifest.digest,
        "documentCount": sum(is_document_path(path) for path in manifest.files),
        "diagramCount": len(manifest.diagrams),
        "rendererVersion": manifest.profile.rendererVersion,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate the complete documentation Mermaid corpus"
    )
    parser.add_argument(
        "--ref",
        help="Validate a commit's Git blobs with the matching installed evaluator",
    )
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--artifacts", type=Path)
    options = parser.parse_args()
    with cancellation_signals() as cancellation:
        try:
            result = check(
                REPO_ROOT,
                options.ref,
                inventory_only=options.inventory_only,
                artifacts=options.artifacts,
                stop_requested=cancellation.requested,
            )
            if not cancellation.requested():
                print(json.dumps(result, sort_keys=True))
            status = 0
        except (ValueError, OSError, RuntimeError) as error:
            print(f"diagram check: {error}", file=sys.stderr)
            status = 1
        return cancellation.exit_code(status)


if __name__ == "__main__":
    raise SystemExit(main())

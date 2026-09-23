from __future__ import annotations

from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parent.parent
SOURCE_ROOT: Final = (
    REPO_ROOT / "backend" / "src" / "ci_coordinator" / "target_artifacts" / "control_source"
)
BUNDLE_PATH: Final = (
    REPO_ROOT
    / "backend"
    / "src"
    / "ci_coordinator"
    / "target_artifacts"
    / "resources"
    / "ci-coordinator.cjs"
)
_SOURCE_FILENAMES: Final = (
    "consume_plan.cjs",
    "main.cjs",
    "validate_gate.cjs",
    "validate_plan.cjs",
    "validation_core.cjs",
    "validation_envelope.cjs",
    "validation_execution.cjs",
)

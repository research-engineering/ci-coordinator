from __future__ import annotations

import os
import re

REPOSITORY = "research-engineering/ci-coordinator"
IMAGE = f"ghcr.io/{REPOSITORY}"
WORKFLOW_PATH = ".github/workflows/release-artifact.yml"
WORKFLOW_REF = f"{REPOSITORY}/{WORKFLOW_PATH}@refs/heads/master"
SIGNER_WORKFLOW = f"github.com/{REPOSITORY}/{WORKFLOW_PATH}"
REPAIR_PREDICATE = f"https://github.com/{REPOSITORY}/attestations/runtime-repairs/v1"


def _configured_id(name: str) -> int:
    value = os.environ.get(name, "")
    if re.fullmatch(r"[1-9][0-9]{0,19}", value) is None:
        raise ValueError(f"{name} must be configured as a canonical positive decimal ID")
    return int(value)


def repository_id() -> int:
    return _configured_id("CI_COORDINATOR_RELEASE_REPOSITORY_ID")


def main() -> int:
    values = {
        "repository": REPOSITORY,
        "repository_id": repository_id(),
        "gate_workflow_id": _configured_id("CI_COORDINATOR_FULL_CHECK_WORKFLOW_ID"),
        "image": IMAGE,
        "workflow_ref": WORKFLOW_REF,
        "signer_workflow": SIGNER_WORKFLOW,
        "repair_predicate": REPAIR_PREDICATE,
    }
    for key, value in values.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

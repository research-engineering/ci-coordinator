"""Content identities for the target-owned workflow adapter."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Final

_DIGEST: Final = re.compile(r"[0-9a-f]{64}")
_WORKFLOW_PATH: Final = re.compile(r"\.github/workflows/[^/\\]+\.ya?ml")

TARGET_CONTROL_FILE_PATHS: Final = (".ci-coordinator/ci-coordinator.cjs",)
# At most 39 cold Git Data reads per load: one control bundle, 32 workflows,
# the registry blob, and five commit/tree objects.
MAX_TARGET_ADAPTER_WORKFLOWS: Final = 32
MAX_TARGET_ADAPTER_FILES: Final = MAX_TARGET_ADAPTER_WORKFLOWS + len(TARGET_CONTROL_FILE_PATHS)


@dataclass(frozen=True, slots=True)
class TargetAdapterFileBinding:
    path: str
    sha256: str

    def __post_init__(self) -> None:
        if not is_adapter_file_path(self.path):
            raise ValueError("target adapter path is not admitted")
        if type(self.sha256) is not str or _DIGEST.fullmatch(self.sha256) is None:
            raise ValueError("target adapter digest must be lowercase SHA-256")

    @property
    def is_workflow(self) -> bool:
        return is_adapter_workflow_path(self.path)

    def to_identity_mapping(self) -> dict[str, str]:
        return {"path": self.path, "sha256": self.sha256}


def digest_adapter_file(content: bytes) -> str:
    if type(content) is not bytes:
        raise TypeError("target adapter content must be exact bytes")
    return hashlib.sha256(content).hexdigest()


def is_adapter_workflow_path(value: object) -> bool:
    return (
        type(value) is str
        and _WORKFLOW_PATH.fullmatch(value) is not None
        and len(value.encode("utf-8")) <= 256
    )


def is_adapter_file_path(value: object) -> bool:
    return is_adapter_workflow_path(value) or value in TARGET_CONTROL_FILE_PATHS

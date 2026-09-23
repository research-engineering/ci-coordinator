from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from scripts.documentation_graph_filesystem import read_policy_source

PROFILE_PATH = Path(
    "docs/specs/ci-coordinator-proofkit-adoption/documentation-diagrams-profile.v1.json"
)
EVALUATOR_PATHS = frozenset(
    {
        "scripts/__init__.py",
        "scripts/bounded_git.py",
        "scripts/bounded_process.py",
        "scripts/file_descriptor.py",
        "scripts/documentation_graph_contract.py",
        "scripts/documentation_graph_filesystem.py",
        "scripts/documentation_graph_policy.py",
        "scripts/proofkit_common.py",
        "package.json",
        "frontend/package.json",
        "pnpm-lock.yaml",
        "pnpm-workspace.yaml",
        "mise.toml",
        "mise.lock",
        "backend/pyproject.toml",
        "backend/requirements-dev.lock",
        "backend/uv.lock",
        ".githooks/pre-push",
        PROFILE_PATH.as_posix(),
        "docs/specs/ci-coordinator-proofkit-adoption/documentation-graph-profile.v1.json",
    }
)
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Positive = Annotated[int, Field(gt=0)]


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class DiagramProfile(ClosedModel):
    schemaVersion: Literal[1]
    rendererVersion: str
    lintVersion: str
    nodeVersion: str
    playwrightVersion: str
    supportedTypes: list[str]
    maxDiagrams: Annotated[int, Field(gt=0, le=512)]
    maxDiagramUtf16Units: Annotated[int, Field(gt=0, le=50_000)]
    diagramTimeoutMs: Annotated[int, Field(gt=0, le=15_000)]
    runTimeoutSeconds: Annotated[int, Field(gt=0, le=600)]
    maxOutputBytes: Annotated[int, Field(gt=0, le=16_777_216)]
    rules: dict[str, Literal["error", "warn", "off"]]
    rendererConfig: dict[str, JsonValue]


class Diagram(ClosedModel):
    id: Digest
    path: str
    line: Positive
    endLine: Positive
    body: str
    semanticBody: str
    type: str
    bodySha256: Digest


class DiagramManifest(ClosedModel):
    schemaVersion: Literal[1] = 1
    revision: str | None
    digest: Digest
    files: dict[str, Digest]
    diagrams: list[Diagram]
    profile: DiagramProfile


class DiagramResult(ClosedModel):
    id: Digest
    errors: list[str]
    warnings: list[str]


class DiagramReport(ClosedModel):
    schemaVersion: Literal[1]
    inventoryDigest: Digest
    rendererVersion: str
    lintVersion: str
    results: list[DiagramResult]


def is_document_path(path: str) -> bool:
    return path in {"README.md", "ROADMAP.md"} or (
        path.startswith("docs/") and path.endswith(".md")
    )


def is_evaluator_path(path: str) -> bool:
    return (
        path in EVALUATOR_PATHS
        or (path.startswith("scripts/diagram") and path.endswith(".py"))
        or (path.startswith("frontend/tools/diagrams") and path.endswith(".mjs"))
    )


def is_diagram_input(path: str) -> bool:
    return (
        is_document_path(path)
        or is_evaluator_path(path)
        or path.endswith((".mmd", ".mermaid", ".mdx"))
        or path.startswith("scripts/tests/test_diagram")
        or path == ".github/workflows/python-persistence.yml"
    )


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_profile(root: Path) -> DiagramProfile:
    return DiagramProfile.model_validate_json(read_policy_source(root, PROFILE_PATH))


def admit_report(manifest: DiagramManifest, report: DiagramReport) -> None:
    if (
        report.inventoryDigest != manifest.digest
        or report.rendererVersion != manifest.profile.rendererVersion
        or report.lintVersion != manifest.profile.lintVersion
    ):
        raise ValueError("diagram result does not match the source and evaluator identity")
    expected = {diagram.id for diagram in manifest.diagrams}
    actual = [result.id for result in report.results]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("diagram results contain missing, duplicate or foreign identities")

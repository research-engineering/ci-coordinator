from __future__ import annotations

import ast
import hashlib
from collections import Counter
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scripts.ci_matrix_risk_execution import (
    EXECUTION_INPUTS,
    admit_execution_binding,
    native_execution_owner,
)
from scripts.proofkit_common import as_array, as_object, parse_json_object
from scripts.proofkit_route_sources import load_route_authority
from scripts.repository_paths import read_repository_regular_file
from scripts.self_ci_source import NATIVE_PATH

PROFILE_PATH = Path("proofkit/ci-risk-coverage.v1.json")
BLUEPRINT_SHA256 = "a5c4d775207a6de30ab392bbd7ed9c70c427f596a5d5c849889dd6e83d05556f"
_CLASS_COUNTS = {
    "PY": 8,
    "API": 4,
    "DB": 5,
    "FE": 8,
    "CF": 22,
    "SC": 12,
    "IAC": 2,
    "OPS": 3,
    "DATA": 7,
    "ART": 3,
    "AG": 6,
    "ASY": 1,
    "UX": 3,
    "DOM": 2,
    "INT": 2,
    "AUTH": 1,
}
CLASS_IDS = frozenset(
    f"{prefix}{number:02d}"
    for prefix, count in _CLASS_COUNTS.items()
    for number in range(1, count + 1)
)
PROPERTY_IDS = frozenset(f"CI{number:02d}" for number in range(1, 26))
Text = Annotated[str, Field(min_length=1, max_length=2048)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Mode = Literal["pull-request", "merge-group", "manual", "scheduled", "release"]


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RiskBinding(Record):
    requirementId: Text
    witnessPath: Text
    assertion: Text
    sourceSha256: Digest
    commandIds: Annotated[list[Text], Field(min_length=1)]
    executionModes: Annotated[list[Mode], Field(min_length=1)]
    nativeWorkflowPath: Text
    evidenceState: Literal["configured-not-executed"]

    @model_validator(mode="after")
    def unique_sets(self) -> Self:
        if len(set(self.commandIds)) != len(self.commandIds) or len(
            set(self.executionModes)
        ) != len(self.executionModes):
            raise ValueError("risk binding commands and execution modes must be unique")
        return self


class RiskCandidate(Record):
    candidateId: Text
    family: Literal["tool-class", "property"]
    title: Text
    blueprintLine: Annotated[int, Field(ge=1, le=1031)]
    risk: Text
    applicability: Literal["covered", "partial", "deferred", "not-applicable", "unknown"]
    rationale: Text
    ownerPath: Text
    revisitWhen: Text
    bindings: list[RiskBinding]

    @model_validator(mode="after")
    def truthful_disposition(self) -> Self:
        if self.applicability in {"covered", "partial"} and not self.bindings:
            raise ValueError("covered or partial risk needs an existing requirement assertion")
        if self.applicability == "not-applicable" and self.bindings:
            raise ValueError("inapplicable risk cannot advertise configured proof")
        identities = [(row.requirementId, row.witnessPath, row.assertion) for row in self.bindings]
        if len(identities) != len(set(identities)):
            raise ValueError("risk assertion bindings must be unique")
        return self


class RiskCoverage(Record):
    schemaVersion: Literal["ci-coordinator.ci-risk-coverage/v1"]
    blueprintSha256: Literal["a5c4d775207a6de30ab392bbd7ed9c70c427f596a5d5c849889dd6e83d05556f"]
    candidateRows: Annotated[list[RiskCandidate], Field(min_length=114, max_length=114)]
    executionInputs: dict[Text, Digest]
    unresolvedScope: Annotated[list[Text], Field(min_length=1)]
    nonClaims: Annotated[list[Text], Field(min_length=1)]

    @model_validator(mode="after")
    def complete_candidates(self) -> Self:
        if set(self.executionInputs) != EXECUTION_INPUTS:
            raise ValueError("risk execution inputs must bind the complete native consumer chain")
        identities = [row.candidateId for row in self.candidateRows]
        if len(identities) != len(set(identities)) or set(identities) != CLASS_IDS | PROPERTY_IDS:
            raise ValueError("risk projection must retain all 89 classes and 25 properties")
        if any(
            (row.family == "property") != (row.candidateId in PROPERTY_IDS)
            for row in self.candidateRows
        ):
            raise ValueError("tool classes and domain properties are separate denominators")
        return self


def _source(root: Path, path: str) -> bytes:
    return read_repository_regular_file(
        root, Path(path), "CI risk coverage source", maximum_bytes=2 * 1024 * 1024
    )


def admit_assertion(source: str, path: str, selector: str) -> None:
    if path.endswith(".py"):
        nodes = [
            node
            for node in ast.walk(ast.parse(source, filename=path))
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == selector
        ]
        if len(nodes) != 1 or not selector.startswith("test_"):
            raise ValueError(
                f"risk assertion must name one Python test function: {path}:{selector}"
            )
        if not any(
            isinstance(node, ast.Assert)
            or (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"raises", "fail"}
            )
            for node in ast.walk(nodes[0])
        ):
            raise ValueError(f"risk witness has no local assertion or rejection oracle: {path}")
    elif not selector.strip() or source.count(selector) != 1:
        raise ValueError(f"risk assertion selector is absent or ambiguous: {path}")


def check(root: Path) -> dict[str, object]:
    raw = _source(root, PROFILE_PATH.as_posix())
    profile = RiskCoverage.model_validate(parse_json_object(raw.decode(), "CI risk coverage"))
    authority = load_route_authority(repo_root=root).binding_projection
    relation = {
        (row["requirementId"], row["witnessPath"], command)
        for value in as_array(authority["bindings"], "proof bindings")
        for row in [as_object(value, "proof binding")]
        for command in as_array(row["commandIds"], "proof command ids")
    }
    observed: dict[str, str] = {}
    source_cache: dict[str, bytes] = {}

    def source_bytes(path: str) -> bytes:
        if path not in source_cache:
            source_cache[path] = _source(root, path)
        return source_cache[path]

    for path, expected_digest in profile.executionInputs.items():
        actual_digest = hashlib.sha256(source_bytes(path)).hexdigest()
        if actual_digest != expected_digest:
            raise ValueError(f"risk execution owner source changed: {path}")
        observed[path] = actual_digest
    native_source = source_bytes(NATIVE_PATH)
    native_workflow, execution_commands = native_execution_owner(root, native_source)
    observed[NATIVE_PATH] = hashlib.sha256(native_source).hexdigest()
    binding_count = 0
    for row in profile.candidateRows:
        observed[row.ownerPath] = hashlib.sha256(source_bytes(row.ownerPath)).hexdigest()
        for binding in row.bindings:
            source = source_bytes(binding.witnessPath)
            digest = hashlib.sha256(source).hexdigest()
            if digest != binding.sourceSha256:
                raise ValueError(f"risk assertion source changed: {row.candidateId}")
            if any(
                (binding.requirementId, binding.witnessPath, command) not in relation
                for command in binding.commandIds
            ):
                raise ValueError(
                    f"risk binding is absent from canonical Proofkit routes: {row.candidateId}"
                )
            admit_assertion(source.decode(), binding.witnessPath, binding.assertion)
            admit_execution_binding(
                binding.nativeWorkflowPath,
                binding.commandIds,
                binding.witnessPath,
                execution_commands,
            )
            events = {
                "pull-request": "pull_request",
                "merge-group": "merge_group",
                "manual": "workflow_dispatch",
            }
            if any(
                mode not in events
                or events[mode] not in as_object(native_workflow["on"], "native events")
                for mode in binding.executionModes
            ):
                raise ValueError(f"risk execution mode is absent from its owner: {row.candidateId}")
            observed[binding.witnessPath] = digest
            binding_count += 1
    if any(
        hashlib.sha256(_source(root, path)).hexdigest() != digest
        for path, digest in observed.items()
    ):
        raise ValueError("risk coverage sources changed during admission")
    if _source(root, PROFILE_PATH.as_posix()) != raw:
        raise ValueError("risk coverage profile changed during admission")
    return {
        "scope": "89-blueprint-classes-and-25-domain-properties",
        "profileSha256": hashlib.sha256(raw).hexdigest(),
        "status": "admitted",
        "dispositions": dict(
            sorted(Counter(row.applicability for row in profile.candidateRows).items())
        ),
        "assertionBindingCount": binding_count,
        "openCandidates": [
            {"candidateId": row.candidateId, "disposition": row.applicability}
            for row in profile.candidateRows
            if row.applicability in {"partial", "deferred", "unknown"}
        ],
        "semanticCompleteness": "not-established",
        "executionEvidence": "not-observed-by-static-admission",
        "unresolvedScope": profile.unresolvedScope,
    }

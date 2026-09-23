from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from ruamel.yaml import YAML

from scripts.proofkit_common import parse_json_object
from scripts.quality_plan import QualityPlan, load_quality_plan
from scripts.repository_paths import read_repository_regular_file

PROFILE_PATH = Path("proofkit/ci-matrix.v1.json")
Identifier = Annotated[str, Field(pattern=r"^[a-z][a-z0-9.-]{0,95}$")]
Text = Annotated[str, Field(min_length=1, max_length=2048)]


class MatrixRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Surface(MatrixRecord):
    id: Identifier
    paths: Annotated[list[Text], Field(min_length=1)]
    commandIds: Annotated[list[Identifier], Field(min_length=1)]
    predicate: Text


class UtilityGroup(MatrixRecord):
    id: Identifier
    jobId: Identifier
    commandIds: Annotated[list[Identifier], Field(min_length=1)]
    reason: Text


class CommandCoverage(MatrixRecord):
    commandId: Identifier
    executionOwner: Text
    purpose: Text
    kind: Literal["native", "preparation", "aggregate", "discovery", "manual"]


class ExternalCheck(MatrixRecord):
    id: Identifier
    authority: Text
    predicate: Text
    omissionPolicy: Literal["not-controlled-by-source-diff"]


class ExecutionResult(MatrixRecord):
    commandId: Identifier
    argvSha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    elapsedMilliseconds: Annotated[int, Field(ge=0)]
    exitCode: int | None
    status: Literal["passed", "failed"]
    processError: str | None

    @model_validator(mode="after")
    def truthful_status(self) -> Self:
        succeeded = self.exitCode == 0 and self.processError is None
        if (self.status == "passed") != succeeded:
            raise ValueError("utility result status differs from the process result")
        return self


def admit_execution_results(
    expected: list[str], results: list[dict[str, object]]
) -> list[ExecutionResult]:
    if not expected or len(expected) != len(set(expected)):
        raise ValueError("expected utility result set must be nonempty and unique")
    admitted = [ExecutionResult.model_validate(row) for row in results]
    if [row.commandId for row in admitted] != expected:
        raise ValueError(
            "utility results must exactly match the independently declared command set"
        )
    return admitted


class MatrixProfile(MatrixRecord):
    schemaVersion: Literal["ci-coordinator.ci-matrix/v1"]
    ownerId: Literal["ci-coordinator.core"]
    nativeWorkflow: Literal[".github/workflows/python-persistence.yml"]
    surfaces: Annotated[list[Surface], Field(min_length=1)]
    commands: Annotated[list[CommandCoverage], Field(min_length=1)]
    utilityGroups: Annotated[list[UtilityGroup], Field(min_length=1)]
    externalChecks: Annotated[list[ExternalCheck], Field(min_length=1)]
    nonClaims: Annotated[list[Text], Field(min_length=1)]

    @model_validator(mode="after")
    def unique_relations(self) -> Self:
        for values in (
            [item.id for item in self.surfaces],
            [item.commandId for item in self.commands],
            [item.id for item in self.utilityGroups],
            [item.jobId for item in self.utilityGroups],
            [item.id for item in self.externalChecks],
        ):
            if len(set(values)) != len(values):
                raise ValueError("CI matrix identities must be unique")
        command_ids = {item.commandId for item in self.commands}
        selected = [name for group in self.utilityGroups for name in group.commandIds]
        if len(selected) != len(set(selected)):
            raise ValueError("a utility command must belong to exactly one execution group")
        if set(selected) != {name for name in command_ids if name.startswith("utility.")}:
            raise ValueError("utility groups must partition every utility command")
        references = [row.commandIds for row in self.surfaces]
        references.extend(row.commandIds for row in self.utilityGroups)
        for referenced in references:
            if len(referenced) != len(set(referenced)):
                raise ValueError("CI matrix command references must not repeat")
            if not set(referenced) <= command_ids:
                raise ValueError("CI matrix references an unknown command")
        for surface in self.surfaces:
            if len(surface.paths) != len(set(surface.paths)) or any(
                path.startswith("/") or ".." in path.split("/") or "\\" in path
                for path in surface.paths
            ):
                raise ValueError("CI matrix surface paths must be unique repository globs")
        return self


def load_matrix(root: Path) -> tuple[MatrixProfile, QualityPlan]:
    raw = read_repository_regular_file(root, PROFILE_PATH, "CI matrix", maximum_bytes=1_048_576)
    profile = MatrixProfile.model_validate(parse_json_object(raw.decode(), "CI matrix"))
    quality = load_quality_plan(root)
    expected = {row.commandId for row in profile.commands}
    actual = set(quality.commands)
    if expected != actual:
        raise ValueError(
            f"CI matrix command closure differs: missing={sorted(actual - expected)}, "
            f"foreign={sorted(expected - actual)}"
        )
    raw_workflow = read_repository_regular_file(
        root, Path(profile.nativeWorkflow), "native CI workflow", maximum_bytes=1_048_576
    )
    workflow = YAML(typ="safe", pure=True).load(raw_workflow.decode())
    admit_utility_workflow(profile, workflow)
    return profile, quality


def admit_utility_workflow(profile: MatrixProfile, workflow: object) -> None:
    if not isinstance(workflow, dict) or not isinstance(workflow.get("jobs"), dict):
        raise ValueError("native CI workflow jobs are unavailable")
    jobs = workflow["jobs"]
    gate = jobs.get("pull-request-gate")
    if not isinstance(gate, dict) or not isinstance(gate.get("needs"), list):
        raise ValueError("native CI gate dependencies are unavailable")
    gate_steps = gate.get("steps")
    if not isinstance(gate_steps, list) or len(gate_steps) != 1:
        raise ValueError("native CI gate assertion owner changed")
    gate_step = gate_steps[0]
    if not isinstance(gate_step, dict):
        raise ValueError("native CI gate assertion is unavailable")
    gate_env = gate_step.get("env")
    gate_script = gate_step.get("run")
    if not isinstance(gate_env, dict) or not isinstance(gate_script, str):
        raise ValueError("native CI gate assertion operands are unavailable")
    statements = {line.strip() for line in gate_script.splitlines()}
    for group in profile.utilityGroups:
        job = jobs.get(group.jobId)
        if not isinstance(job, dict) or group.jobId not in gate["needs"]:
            raise ValueError(f"native CI gate does not require utility group {group.id}")
        if "if" in job or job.get("continue-on-error", False) is not False:
            raise ValueError("native utility groups cannot be skipped or made advisory")
        invocation = f"tooling/quality/.venv/bin/python -m scripts.ci_matrix run --group {group.id}"
        steps = job.get("steps")
        if not isinstance(steps, list):
            raise ValueError("native utility steps are unavailable")
        matches = [
            step for step in steps if isinstance(step, dict) and step.get("run") == invocation
        ]
        if len(matches) != 1 or "if" in matches[0] or matches[0].get("continue-on-error", False):
            raise ValueError("native utility command must execute exactly once without suppression")
        variable = "UTILITY_" + group.id.upper().replace("-", "_") + "_RESULT"
        expression = "${{ needs." + group.jobId + ".result }}"
        if (
            gate_env.get(variable) != expression
            or f'test "${{{variable}}}" = success' not in statements
        ):
            raise ValueError("native CI gate lost an independent utility success assertion")

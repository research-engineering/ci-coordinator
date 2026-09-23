from __future__ import annotations

import hashlib
import heapq
import stat
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator
from ruamel.yaml import YAML

SCHEMA: Final = "ci-coordinator-native-test-plan/v2"
SHARD_COUNTS = {"backend": 4, "postgres": 4, "tooling": 3}
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024
type Cohort = Literal["backend", "postgres", "tooling"]
type Seconds = Annotated[float, Field(ge=0, allow_inf_nan=False)]


class Artifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExternalModuleQualification(Artifact):
    source_sha256: str
    command: tuple[str, ...]
    reason: str


EXTERNALLY_QUALIFIED_MODULES: Final = {
    "scripts/tests/test_diagram_process.py": ExternalModuleQualification(
        source_sha256="b60606bbb9f7c390f625df3747b67a202a1474bfe6055db35b55859f420352e7",
        command=("backend/.venv/bin/python", "scripts/tests/test_diagram_process.py", "--qualify"),
        reason=(
            "The required operator job owns eight qualify_* process witnesses; "
            "normal pytest collection is empty."
        ),
    )
}


class TestNode(Artifact):
    node_id: str
    file: str
    markers: tuple[str, ...]
    fixtures: tuple[str, ...]

    @model_validator(mode="after")
    def admit_identity(self) -> Self:
        path = PurePosixPath(self.file)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in self.file
            or path.as_posix() != self.file
            or not self.file.endswith(".py")
            or not self.node_id.startswith(self.file + "::")
            or any(character in self.node_id for character in "\0\r\n")
            or not self.file.startswith(
                ("backend/tests/", "scripts/tests/", "scripts/conformance/")
            )
        ):
            raise ValueError("native test identity is outside the admitted universe")
        if self.markers != tuple(sorted(set(self.markers))) or self.fixtures != tuple(
            sorted(set(self.fixtures))
        ):
            raise ValueError("test metadata is not canonical")
        return self


class Epoch(Artifact):
    source_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    run_id: Annotated[str, Field(pattern=r"^[1-9][0-9]*$")]
    attempt: Annotated[str, Field(pattern=r"^[1-9][0-9]*$")]
    python_version: str
    lock_digest: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class Assignment(Artifact):
    shard: Annotated[str, Field(pattern=r"^(backend|postgres|tooling)-[1-9][0-9]*$")]
    files: tuple[str, ...]
    node_ids: tuple[str, ...]
    estimated_seconds: Seconds


class TestPlan(Artifact):
    schema_version: Literal["ci-coordinator-native-test-plan/v2"] = SCHEMA
    epoch: Epoch
    candidate_files: tuple[str, ...]
    externally_qualified_empty_files: tuple[str, ...]
    nodes: tuple[TestNode, ...]
    assignments: tuple[Assignment, ...]

    @model_validator(mode="after")
    def admit_partition(self) -> Self:
        node_ids = tuple(node.node_id for node in self.nodes)
        if not node_ids or node_ids != tuple(sorted(set(node_ids))):
            raise ValueError("native collection must be nonempty and unique")
        node_files = {node.file for node in self.nodes}
        external = set(self.externally_qualified_empty_files)
        if (
            self.externally_qualified_empty_files != tuple(sorted(external))
            or not external <= EXTERNALLY_QUALIFIED_MODULES.keys()
            or external & node_files
            or self.candidate_files != tuple(sorted(node_files | external))
        ):
            raise ValueError("native nodes do not cover the independent candidate files")
        expected_shards = {
            f"{group}-{index}"
            for group, count in SHARD_COUNTS.items()
            for index in range(1, count + 1)
        }
        if tuple(item.shard for item in self.assignments) != tuple(sorted(expected_shards)):
            raise ValueError("shard inventory does not match scheduling policy")
        nodes_by_file: dict[str, list[TestNode]] = defaultdict(list)
        for node in self.nodes:
            nodes_by_file[node.file].append(node)
        seen_files: list[str] = []
        assigned_nodes: list[str] = []
        for assignment in self.assignments:
            if not assignment.files or assignment.files != tuple(sorted(set(assignment.files))):
                raise ValueError("assignment files must be nonempty and canonical")
            expected_nodes: list[str] = []
            for file in assignment.files:
                if file not in nodes_by_file or not assignment.shard.startswith(
                    cohort(nodes_by_file[file]) + "-"
                ):
                    raise ValueError("assignment crosses its native cohort")
                expected_nodes.extend(node.node_id for node in nodes_by_file[file])
            if assignment.node_ids != tuple(sorted(expected_nodes)):
                raise ValueError("assignment drops or adds test identities")
            assigned_nodes.extend(assignment.node_ids)
            seen_files.extend(assignment.files)
        if sorted(assigned_nodes) != list(node_ids) or len(seen_files) != len(set(seen_files)):
            raise ValueError("assignments are not an exact disjoint partition")
        return self


def cohort(nodes: list[TestNode]) -> Cohort:
    if any("persistence" in node.markers for node in nodes):
        return "postgres"
    return "backend" if nodes[0].file.startswith("backend/") else "tooling"


def candidate_files(root: Path, universe: tuple[str, ...]) -> tuple[str, ...]:
    from scripts.ci_utility_inventory import repository_paths

    candidates = tuple(
        path
        for path in repository_paths(root)
        if any(
            path == entry
            or (
                path.startswith(entry + "/")
                and (PurePosixPath(path).match("test_*.py") or path.endswith("_test.py"))
            )
            for entry in universe
        )
    )
    if not candidates:
        raise ValueError("native candidate file inventory is empty")
    return candidates


def build_plan(
    nodes: tuple[TestNode, ...],
    epoch: Epoch,
    costs: dict[str, float],
    *,
    expected_files: tuple[str, ...],
    externally_qualified_empty_files: tuple[str, ...] = (),
) -> TestPlan:
    grouped: dict[str, list[TestNode]] = defaultdict(list)
    for node in nodes:
        grouped[node.file].append(node)
    assignments: list[Assignment] = []
    for group, count in SHARD_COUNTS.items():
        files = sorted(file for file, entries in grouped.items() if cohort(entries) == group)
        observed = [costs[file] for file in files if file in costs and costs[file] > 0]
        fallback = sum(observed) / len(observed) if observed else 1.0
        weights = {file: max(costs.get(file, fallback), 0.001) for file in files}
        buckets: list[list[str]] = [[] for _ in range(count)]
        queue = [(0.0, index) for index in range(count)]
        heapq.heapify(queue)
        for file in sorted(files, key=lambda name: (-weights[name], name)):
            elapsed, index = heapq.heappop(queue)
            buckets[index].append(file)
            heapq.heappush(queue, (elapsed + weights[file], index))
        for index, bucket in enumerate(buckets, 1):
            assignments.append(
                Assignment(
                    shard=f"{group}-{index}",
                    files=tuple(sorted(bucket)),
                    node_ids=tuple(
                        sorted(node.node_id for file in bucket for node in grouped[file])
                    ),
                    estimated_seconds=sum(weights[file] for file in bucket),
                )
            )
    return TestPlan(
        epoch=epoch,
        candidate_files=expected_files,
        externally_qualified_empty_files=externally_qualified_empty_files,
        nodes=tuple(sorted(nodes, key=lambda item: item.node_id)),
        assignments=tuple(sorted(assignments, key=lambda item: item.shard)),
    )


def artifact_bytes(path: Path) -> bytes:
    status = path.lstat()
    if not stat.S_ISREG(status.st_mode) or status.st_size > MAX_ARTIFACT_BYTES:
        raise ValueError("CI artifact must be a bounded regular file")
    content = path.read_bytes()
    if len(content) > MAX_ARTIFACT_BYTES:
        raise ValueError("CI artifact exceeds its bound")
    return content


def write_artifact(path: Path, value: Artifact) -> None:
    content = value.model_dump_json().encode() + b"\n"
    if len(content) > MAX_ARTIFACT_BYTES:
        raise ValueError("CI artifact exceeds its bound")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as output:
        output.write(content)


def plan_digest(plan: TestPlan) -> str:
    return hashlib.sha256(plan.model_dump_json().encode()).hexdigest()


class CostHints(Artifact):
    source_sha: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    run_id: str
    metric: Literal["pytest_phase_elapsed_seconds"]
    files: dict[str, Seconds]


def load_costs(path: Path) -> dict[str, float]:
    return CostHints.model_validate_json(artifact_bytes(path)).files


def workflow_inventory(root: Path) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {}
    for path in sorted((root / ".github/workflows").glob("*.y*ml")):
        value = YAML(typ="safe").load(path)
        if not isinstance(value, dict) or not isinstance(value.get("jobs"), dict):
            raise ValueError("workflow has no admitted job inventory")
        rows: list[dict[str, object]] = []
        for identity, job in sorted(value["jobs"].items()):
            if not isinstance(job, dict):
                raise TypeError("workflow job must be an object")
            rows.append(
                {
                    "jobId": identity,
                    "name": job.get("name", identity),
                    "runsOn": job.get("runs-on"),
                    "uses": job.get("uses"),
                    "needs": job.get("needs", []),
                    "strategy": job.get("strategy"),
                    "stages": [
                        step.get("name", step.get("uses", "unnamed"))
                        for step in job.get("steps", [])
                    ],
                    "measuredElapsedSeconds": None,
                    "disposition": "retained_native_witness; hosted_step_cost_unmeasured",
                }
            )
        result[path.relative_to(root).as_posix()] = rows
    return result

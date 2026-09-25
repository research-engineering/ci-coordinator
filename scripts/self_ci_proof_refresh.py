from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ci_coordinator.runtime_settings.caller_inventory import parse_caller_inventory
from ci_coordinator.runtime_settings.entrypoint_disposition import parse_entrypoint_disposition
from scripts.ci_matrix_inventory import MAX_FILE_BYTES, MAX_PATHS, MAX_TOTAL_BYTES
from scripts.ci_matrix_risk_execution import admit_execution_binding, native_execution_owner
from scripts.ci_matrix_risks import PROFILE_PATH, RiskCoverage, admit_assertion
from scripts.ci_utility_inventory import repository_paths
from scripts.proofkit_common import JsonObject, as_array, parse_json_object, safe_repo_path
from scripts.proofkit_route_sources import BINDING_PATH, load_route_authority
from scripts.repository_paths import read_repository_regular_file, real_repository_directory
from scripts.self_ci_source import NATIVE_PATH

RISK_PATH = PROFILE_PATH.as_posix()
DISPOSITION_PATH = "docs/specs/ci-coordinator-runtime/runtime-entrypoint-disposition.v1.json"
BUNDLED_DISPOSITION_PATH = (
    "backend/src/ci_coordinator/runtime_settings/resources/runtime-entrypoint-disposition.v1.json"
)
CALLER_PATH = "docs/specs/ci-coordinator-runtime/runtime-caller-inventory.v1.json"
BUNDLED_CALLER_PATH = (
    "backend/src/ci_coordinator/runtime_settings/resources/runtime-caller-inventory.v1.json"
)
PROOF_PATHS = frozenset({RISK_PATH, DISPOSITION_PATH, BUNDLED_DISPOSITION_PATH})
_FINGERPRINT_KINDS = {
    "container.workflow-lint": "container",
    "deployment.documents": "deployment",
    "deployment.root-files": "deployment",
    "generated.documents": "generated_entrypoint",
    "workflow.documents": "workflow",
}


@dataclass(frozen=True)
class ProofSources:
    root: Path
    files: dict[str, bytes]
    modes: dict[str, int]

    @classmethod
    def capture(cls, root: Path) -> ProofSources:
        paths = repository_paths(root)
        if len(paths) > MAX_PATHS:
            raise ValueError("proof refresh exceeds the matrix path bound")
        files: dict[str, bytes] = {}
        modes: dict[str, int] = {}
        total = 0
        for path in paths:
            files[path] = read_repository_regular_file(
                root, Path(path), "proof refresh input", maximum_bytes=MAX_FILE_BYTES
            )
            total += len(files[path])
            if total > MAX_TOTAL_BYTES:
                raise ValueError("proof refresh exceeds the matrix aggregate byte bound")
            modes[path] = stat.S_IMODE((root / path).lstat().st_mode)
        return cls(root, files, modes)

    def read(self, path: str, maximum: int = MAX_FILE_BYTES) -> bytes:
        if safe_repo_path(path) != path or path not in self.files:
            raise ValueError(f"proof refresh source is outside its Git inventory: {path}")
        content = self.files[path]
        if len(content) > maximum:
            raise ValueError(f"proof refresh source exceeds its byte bound: {path}")
        return content

    def assert_current(self, replacements: Mapping[str, bytes] | None = None) -> None:
        current = self.capture(self.root)
        if current.files != self.files | dict(replacements or {}) or current.modes != self.modes:
            raise ValueError("proof refresh sources or path/mode inventory changed")


def risk_projection(sources: ProofSources) -> bytes:
    original = sources.read(RISK_PATH, 2 * 1024 * 1024)
    document = parse_json_object(original.decode(), RISK_PATH)
    profile = RiskCoverage.model_validate(document)
    index = parse_json_object(sources.read(BINDING_PATH).decode(), BINDING_PATH)
    if index.get("schemaVersion") == 2:
        for raw_row in as_array(index["sources"], "proof route sources"):
            columns = as_array(raw_row, "proof route source")
            if len(columns) != 3:
                raise ValueError("proof refresh route source has invalid columns")
            sources.read(columns[1], 2 * 1024 * 1024)
    authority = load_route_authority(repo_root=sources.root).binding_projection
    relation = {
        (row["requirementId"], row["witnessPath"], command)
        for row in authority["bindings"]
        for command in row["commandIds"]
    }
    native, commands = native_execution_owner(sources.root, sources.read(NATIVE_PATH))
    events = {
        "pull-request": "pull_request",
        "merge-group": "merge_group",
        "manual": "workflow_dispatch",
    }
    for row, raw_row in zip(profile.candidateRows, document["candidateRows"], strict=True):
        sources.read(row.ownerPath, 2 * 1024 * 1024)
        for binding, raw_binding in zip(row.bindings, raw_row["bindings"], strict=True):
            source = sources.read(binding.witnessPath, 2 * 1024 * 1024)
            if any(
                (binding.requirementId, binding.witnessPath, command) not in relation
                for command in binding.commandIds
            ):
                raise ValueError("proof refresh binding is absent from canonical routes")
            admit_assertion(source.decode(), binding.witnessPath, binding.assertion)
            admit_execution_binding(
                binding.nativeWorkflowPath, binding.commandIds, binding.witnessPath, commands
            )
            if any(
                mode not in events or events[mode] not in native["on"]
                for mode in binding.executionModes
            ):
                raise ValueError("proof refresh mode is absent from its native owner")
            raw_binding["sourceSha256"] = hashlib.sha256(source).hexdigest()
    for path in profile.executionInputs:
        document["executionInputs"][path] = hashlib.sha256(
            sources.read(path, 2 * 1024 * 1024)
        ).hexdigest()
    projected = _projected_json(original, document)
    if len(projected) > 2 * 1024 * 1024:
        raise ValueError("proof refresh risk projection exceeds its byte bound")
    return projected


def entrypoint_projection(
    sources: ProofSources, generated: Mapping[str, bytes]
) -> dict[str, bytes]:
    callers = sources.read(CALLER_PATH)
    if callers != sources.read(BUNDLED_CALLER_PATH):
        raise ValueError("proof refresh cannot repair caller inventory disagreement")
    caller_inventory = parse_caller_inventory(callers)
    original = sources.read(DISPOSITION_PATH)
    bundled = sources.read(BUNDLED_DISPOSITION_PATH)
    disposition = parse_entrypoint_disposition(original, caller_inventory)
    parse_entrypoint_disposition(bundled, caller_inventory)
    document = parse_json_object(original.decode(), DISPOSITION_PATH)
    mirror = parse_json_object(bundled.decode(), BUNDLED_DISPOSITION_PATH)
    records = {record.source_id: record for record in disposition.records}
    for source_id, kind in _FINGERPRINT_KINDS.items():
        record = records.get(source_id)
        if (
            record is None
            or record.kind != kind
            or record.disposition != "non_runtime"
            or record.caller_id is not None
        ):
            raise ValueError(f"proof refresh fingerprint class is not admitted: {source_id}")
    if {
        record.source_id
        for record in disposition.records
        if record.current_target.startswith("sha256:")
    } != _FINGERPRINT_KINDS.keys():
        raise ValueError("proof refresh fingerprint classes differ from the admitted five")
    for value in (document, mirror):
        for row in value["sources"]:
            if row["sourceId"] in _FINGERPRINT_KINDS:
                if re.fullmatch(r"sha256:[0-9a-f]{64}", row["currentTarget"]) is None:
                    raise ValueError("proof refresh fingerprint must be a SHA-256 value")
                row["currentTarget"] = ""
    if document != mirror:
        raise ValueError("proof refresh cannot change disposition declarations or members")

    profile = disposition.discovery
    containers: list[str] = []
    with os.scandir(sources.root) as entries:
        for index, entry in enumerate(entries):
            if index >= MAX_PATHS:
                raise ValueError("proof refresh root discovery exceeds its path bound")
            if entry.name.startswith("Dockerfile") and (entry.is_file() or entry.is_symlink()):
                containers.append(entry.name)
    if tuple(sorted(containers)) != profile.container_files:
        raise ValueError("proof refresh cannot classify new or missing container members")
    for path in profile.container_files:
        sources.read(path)
    members = {
        "container.workflow-lint": ("Dockerfile.workflow-lint",),
        "deployment.documents": _directory_members(sources.root, profile.deployment_directories),
        "deployment.root-files": tuple(
            path
            for path in profile.root_deployment_files
            if (sources.root / path).exists() or (sources.root / path).is_symlink()
        ),
        "generated.documents": _directory_members(
            sources.root, profile.generated_entrypoint_directories
        ),
        "workflow.documents": _directory_members(
            sources.root, profile.workflow_directories, suffixes={".yaml", ".yml"}
        ),
    }
    if any(records[name].member_ids != paths for name, paths in members.items()):
        raise ValueError("proof refresh cannot classify new or missing entrypoint members")
    if any(
        {DISPOSITION_PATH, BUNDLED_DISPOSITION_PATH}.intersection(paths)
        for paths in members.values()
    ):
        raise ValueError("proof refresh disposition cannot fingerprint itself")
    if set(generated) - sources.files.keys():
        raise ValueError("proof refresh cannot introduce new generated members")
    for row in document["sources"]:
        source_id = row["sourceId"]
        if source_id not in members:
            continue
        digests = {}
        for path in members[source_id]:
            before = sources.read(path)
            digests[path] = hashlib.sha256(generated.get(path, before)).hexdigest()
        if source_id == "container.workflow-lint":
            digest = digests["Dockerfile.workflow-lint"]
        else:
            digest = hashlib.sha256(
                json.dumps(
                    digests, ensure_ascii=True, separators=(",", ":"), sort_keys=True
                ).encode()
            ).hexdigest()
        row["currentTarget"] = "sha256:" + digest
    projected = _projected_json(original, document)
    return {DISPOSITION_PATH: projected, BUNDLED_DISPOSITION_PATH: projected}


def _directory_members(
    root: Path, directories: tuple[str, ...], *, suffixes: set[str] | None = None
) -> tuple[str, ...]:
    members: set[str] = set()
    visited = 0

    for relative in directories:
        if safe_repo_path(relative) != relative:
            raise ValueError("proof refresh discovery path is not canonical")
        directory = root / relative
        if not directory.exists() and not directory.is_symlink():
            continue
        pending = [directory]
        while pending:
            current = pending.pop()
            real_repository_directory(root, current.relative_to(root), "proof refresh discovery")
            with os.scandir(current) as entries:
                for entry in entries:
                    visited += 1
                    if visited > MAX_PATHS:
                        raise ValueError("proof refresh discovery exceeds its path bound")
                    path = Path(entry.path)
                    if entry.is_symlink():
                        raise ValueError("proof refresh discovery contains a symlink")
                    if entry.is_dir(follow_symlinks=False):
                        pending.append(path)
                    elif entry.is_file(follow_symlinks=False):
                        if suffixes is None or path.suffix in suffixes:
                            members.add(path.relative_to(root).as_posix())
                    else:
                        raise ValueError("proof refresh discovery requires regular files")
    return tuple(sorted(members))


def _projected_json(original: bytes, document: JsonObject) -> bytes:
    if parse_json_object(original.decode(), "proof projection") == document:
        return original
    return (json.dumps(document, ensure_ascii=True, indent=2) + "\n").encode()

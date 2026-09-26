from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.architecture_traceability import validate_architecture_traceability
from scripts.bounded_git import BoundedGitCommandError, capture_git_text
from scripts.proofkit_cli import invoke_proofkit, resolve_proofkit_executable
from scripts.proofkit_common import (
    JsonObject,
    as_array,
    as_object,
    js_json_dumps,
    parse_json_object,
    read_json_object,
    write_json,
)
from scripts.proofkit_dependency import PACKAGE_SPEC
from scripts.proofkit_feedback import validate_proofkit_feedback_ledger
from scripts.proofkit_retirements import (
    requirement_replacement_pairs,
    retired_proof_owner_paths,
)
from scripts.proofkit_route_contract import assert_legacy_relation_preserved
from scripts.proofkit_route_sources import RouteAuthority, load_route_authority

REPO_ROOT = Path(__file__).resolve().parent.parent
_PROOFKIT_REPORT_KEYS = frozenset(
    {
        "diagnostics",
        "nonClaims",
        "reportId",
        "reportKind",
        "ruleResults",
        "schemaVersion",
        "state",
        "summary",
    }
)
_PROOFKIT_REPORT_KINDS = {
    "requirement-bindings": "proofkit.requirement-proof-bindings",
    "requirement-source-admission": "proofkit.requirement-source-admission",
    "requirement-source-transition": "proofkit.requirement-source-transition",
}


@dataclass(frozen=True, slots=True)
class ForwardedProofkitFailure(Exception):
    status: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class RouteStorageTransition:
    added_route_count: int
    added_required_tuple_count: int
    canonicalized_requirement_non_claim_count: int


def requirements_report(
    *,
    repo_root: Path = REPO_ROOT,
    env: Mapping[str, str] | None = None,
    proofkit_executable: str | Path | None = None,
) -> JsonObject:
    selected_env = os.environ if env is None else env
    executable = resolve_proofkit_executable(proofkit_executable)
    route_authority = load_route_authority(repo_root=repo_root)
    bindings = route_authority.binding_projection
    feedback_summary = validate_proofkit_feedback_ledger(
        read_json_object(repo_root / "proofkit/adoption-feedback.v1.json"),
        PACKAGE_SPEC,
        repo_root=repo_root,
        tracked_regular_paths=_tracked_regular_paths(repo_root),
    )
    architecture_traceability = validate_architecture_traceability(repo_root=repo_root)
    architecture_summary = as_object(
        architecture_traceability.get("summary"), "architecture traceability summary"
    )
    requirement_files = find_requirement_files(repo_root / "docs/specs", repo_root=repo_root)
    witness_command_catalog_count = assert_witness_command_catalogs_agree(
        repo_root,
        bindings=bindings,
    )
    required_binding_tuple_count = assert_required_binding_tuples(
        repo_root,
        bindings=bindings,
    )
    transition_base_ref = requirement_transition_base_ref(
        requirement_files, repo_root=repo_root, env=selected_env
    )
    previous_requirement_files = (
        []
        if transition_base_ref is None
        else requirement_files_at_ref(transition_base_ref, repo_root=repo_root)
    )
    removed_requirement_files = [
        path for path in previous_requirement_files if path not in requirement_files
    ]
    source_replacement_count = validate_requirement_source_replacements(
        removed_requirement_files,
        requirement_files,
        transition_base_ref,
        repo_root=repo_root,
    )
    route_storage_transition = validate_route_storage_transition(
        route_authority,
        transition_base_ref,
        repo_root=repo_root,
    )
    reports = [
        *[
            run_proofkit(
                "requirement-source-admission",
                ("--input", input_path),
                repo_root=repo_root,
                executable=executable,
            )
            for input_path in requirement_files
        ],
        run_proofkit_with_input(
            "requirement-bindings",
            bindings,
            repo_root=repo_root,
            executable=executable,
            input_path="proofkit/requirement-bindings.json (normalized)",
        ),
    ]
    transition_reports = [
        report
        for input_path in requirement_files
        for report in requirement_transition_report(
            input_path,
            transition_base_ref,
            previous_requirement_files,
            repo_root=repo_root,
            executable=executable,
        )
    ]
    return {
        "schemaVersion": 1,
        "reportId": "ci-coordinator.proofkit-requirements",
        "reportKind": "ci-coordinator.proofkit-requirements",
        "state": "passed",
        "summary": {
            "architectureTraceGroupCount": architecture_summary["traceGroupCount"],
            "architectureTracePremiseRouteCount": architecture_summary["premiseRouteCount"],
            "architectureTraceRequirementCount": architecture_summary["requirementCount"],
            "bindingAdmission": "passed",
            "bindingMigrationAddedRouteCount": route_storage_transition.added_route_count,
            "bindingMigrationCanonicalizedRequirementNonClaimCount": (
                route_storage_transition.canonicalized_requirement_non_claim_count
            ),
            "bindingSourceCount": route_authority.source_count,
            "bindingStorage": "compact" if route_authority.compact else "legacy",
            "feedbackRecordCount": feedback_summary.record_count,
            "requirementSourceCount": len(requirement_files),
            "sourceReplacementCount": source_replacement_count,
            "requirementTransitionCount": len(transition_reports),
            "requiredBindingTupleCount": required_binding_tuple_count,
            "requiredBindingTupleMigrationAddedCount": (
                route_storage_transition.added_required_tuple_count
            ),
            "witnessCommandCatalogCount": witness_command_catalog_count,
        },
        "reports": [
            {
                "command": report["command"],
                "inputPath": report["inputPath"],
                "state": report["state"],
                "summary": report["summary"],
            }
            for report in [*reports, *transition_reports]
        ],
        "nonClaims": [
            "This wrapper executes Proofkit admission commands and aggregates their summaries.",
            (
                "The feedback-ledger validator proves structure and dependency alignment, not that "
                "every observation is an upstream defect."
            ),
            (
                "Architecture traceability proves closed references, not implementation "
                "conformance "
                "or global architecture optimality."
            ),
            (
                "Requirement transitions compare caller-owned git snapshots and do not "
                "authenticate "
                "deployment history."
            ),
            "This wrapper does not execute native witnesses or prove witness freshness.",
        ],
    }


def validate_requirement_source_replacements(
    removed_files: Sequence[str],
    current_files: Sequence[str],
    previous_ref: str | None,
    *,
    repo_root: Path = REPO_ROOT,
) -> int:
    if not removed_files:
        return 0
    if previous_ref is None:
        raise ValueError("requirement source removal requires an explicit base revision")
    manifest = read_json_object(repo_root / "proofkit/proof-owner-retirements.json")
    retired_paths = set(retired_proof_owner_paths(manifest))
    replacements = requirement_replacement_pairs(manifest)
    replacement_by_previous = {
        _required_string(pair, "previousRequirementId"): _required_string(pair, "nextRequirementId")
        for pair in replacements
    }
    current_requirement_ids = {
        _required_string(requirement, "requirementId")
        for path in current_files
        for requirement in _rows(read_json_object(repo_root / path), "requirements")
    }
    previous_requirement_ids: list[str] = []
    for path in removed_files:
        if path not in retired_paths:
            raise ValueError(f"removed requirement source lacks proof-owner retirement: {path}")
        previous = _parse_json_object(_git(repo_root, ("show", f"{previous_ref}:{path}")), path)
        previous_requirement_ids.extend(
            _required_string(requirement, "requirementId")
            for requirement in _rows(previous, "requirements")
        )
    unresolved = [
        requirement_id
        for requirement_id in previous_requirement_ids
        if replacement_by_previous.get(requirement_id) not in current_requirement_ids
    ]
    if unresolved:
        raise ValueError(
            "removed requirement source lacks live requirement replacements: "
            + ", ".join(unresolved)
        )
    previous_requirement_set = set(previous_requirement_ids)
    unrelated = [
        pair
        for pair in replacements
        if _required_string(pair, "previousRequirementId") not in previous_requirement_set
    ]
    if unrelated:
        raise ValueError(
            "requirement replacement does not belong to a removed source: "
            + ", ".join(_required_string(pair, "previousRequirementId") for pair in unrelated)
        )
    return len(replacements)


def assert_witness_command_catalogs_agree(
    repo_root: Path = REPO_ROOT,
    *,
    bindings: Mapping[str, object] | None = None,
) -> int:
    if bindings is None:
        bindings = load_route_authority(repo_root=repo_root).binding_projection
    binding_rows = _rows(bindings, "bindings")
    assert_binding_witness_paths_exist(binding_rows, repo_root=repo_root)
    assert_binding_identities_are_unique(binding_rows)
    witness_plan = read_json_object(repo_root / "proofkit/witness-plan-input.json")
    binding_commands = {
        _required_string(command, "commandId"): command
        for command in _rows(bindings, "witnessCommands")
    }
    plan_commands = {
        _required_string(command, "id"): command for command in _rows(witness_plan, "commands")
    }
    binding_ids = sorted(binding_commands)
    plan_ids = sorted(plan_commands)
    if binding_ids != plan_ids:
        raise ValueError("requirement binding and witness plan command ids must match exactly")
    for command_id in binding_ids:
        binding_command = binding_commands[command_id]
        plan_command = plan_commands[command_id]
        plan_argv = " ".join(_string_array(plan_command.get("argv"), "witness plan argv"))
        if binding_command.get("command") != plan_argv:
            raise ValueError(f"witness command {command_id} does not match witness plan argv")
        environment = as_object(plan_command.get("environment"), "witness plan environment")
        classes = _string_array(environment.get("classes"), "witness plan environment classes")
        if binding_command.get("environmentClass") not in classes:
            raise ValueError(
                f"witness command {command_id} environment class is not admitted by its plan"
            )
    return len(binding_ids)


def assert_required_binding_tuples(
    repo_root: Path = REPO_ROOT,
    *,
    bindings: Mapping[str, object] | None = None,
) -> int:
    if bindings is None:
        bindings = load_route_authority(repo_root=repo_root).binding_projection
    manifest = read_json_object(repo_root / "proofkit/required-binding-tuples.v1.json")
    if manifest.get("schemaVersion") != 1:
        raise ValueError("required binding tuple schemaVersion must be 1")
    if manifest.get("contractId") != "ci-coordinator.required-binding-tuples":
        raise ValueError("required binding tuple contractId is invalid")
    return validate_required_binding_tuples(_rows(bindings, "bindings"), _rows(manifest, "owners"))


def validate_required_binding_tuples(
    bindings: Sequence[Mapping[str, object]],
    owners: Sequence[Mapping[str, object]],
) -> int:
    required = required_binding_tuple_relation(owners)

    admitted = {
        (
            _required_string(binding, "requirementId"),
            _required_string(binding, "witnessPath"),
            command_id,
        )
        for binding in bindings
        for command_id in _string_array(
            binding.get("commandIds"), "requirement binding command ids"
        )
    }
    missing = [binding_tuple for binding_tuple in required if binding_tuple not in admitted]
    if missing:
        raise ValueError(
            "required requirement-path-command tuples are missing:\n"
            + "\n".join(" | ".join(binding_tuple) for binding_tuple in missing)
        )
    return len(required)


def required_binding_tuple_relation(
    owners: Sequence[Mapping[str, object]],
) -> tuple[tuple[str, str, str], ...]:
    owner_keys: list[tuple[str, str]] = []
    required: list[tuple[str, str, str]] = []
    expected_fields = {"requirementId", "witnessPath", "requiredCommandIds"}
    for owner in owners:
        if set(owner) != expected_fields:
            raise ValueError(
                "required binding tuple owner must contain exactly requirementId, "
                "witnessPath, and requiredCommandIds"
            )
        requirement_id = _required_string(owner, "requirementId")
        witness_path = _required_string(owner, "witnessPath")
        command_ids = _string_array(
            owner.get("requiredCommandIds"), "required binding tuple command ids"
        )
        if not command_ids:
            raise ValueError("required binding tuple owner must name at least one command")
        if command_ids != sorted(set(command_ids)):
            raise ValueError("required binding tuple command ids must be unique and sorted")
        owner_keys.append((requirement_id, witness_path))
        required.extend((requirement_id, witness_path, command_id) for command_id in command_ids)
    if owner_keys != sorted(set(owner_keys)):
        raise ValueError("required binding tuple owners must be unique and sorted")
    return tuple(required)


def assert_required_tuple_relation_preserved(
    previous_owners: Sequence[Mapping[str, object]],
    current_owners: Sequence[Mapping[str, object]],
    *,
    allowed_added_paths: Sequence[str],
) -> int:
    previous = set(required_binding_tuple_relation(previous_owners))
    current = set(required_binding_tuple_relation(current_owners))
    removed = sorted(previous - current)
    if removed:
        raise ValueError(
            "compact required tuple storage removed legacy tuples:\n"
            + "\n".join(" | ".join(binding_tuple) for binding_tuple in removed)
        )
    allowed_paths = set(allowed_added_paths)
    undeclared = sorted(
        binding_tuple
        for binding_tuple in current - previous
        if binding_tuple[1] not in allowed_paths
    )
    if undeclared:
        raise ValueError(
            "compact required tuple storage introduced tuples for undeclared paths:\n"
            + "\n".join(" | ".join(binding_tuple) for binding_tuple in undeclared)
        )
    return len(current - previous)


def assert_binding_witness_paths_exist(
    bindings: Sequence[Mapping[str, object]], *, repo_root: Path = REPO_ROOT
) -> None:
    real_root = repo_root.resolve()
    lexical_root = Path(os.path.abspath(repo_root))
    for binding in bindings:
        witness_path = binding.get("witnessPath")
        if (
            not isinstance(witness_path, str)
            or not witness_path
            or Path(witness_path).is_absolute()
        ):
            raise ValueError("requirement binding witness path must be a repository-relative file")
        target = Path(os.path.abspath(lexical_root / witness_path))
        if not target.is_relative_to(lexical_root) or not target.exists():
            raise ValueError(f"requirement binding witness path does not exist: {witness_path}")
        real_target = target.resolve()
        if not real_target.is_relative_to(real_root) or not real_target.is_file():
            raise ValueError(
                "requirement binding witness path is not a repository-owned file: " + witness_path
            )


def assert_binding_identities_are_unique(
    bindings: Sequence[Mapping[str, object]],
) -> None:
    collisions: list[str] = []
    for field in ("scenarioId", "witnessId"):
        records_by_id: dict[str, list[tuple[int, Mapping[str, object]]]] = {}
        for index, binding in enumerate(bindings):
            identity = binding.get(field)
            if isinstance(identity, str):
                records_by_id.setdefault(identity, []).append((index, binding))
        for identity, records in records_by_id.items():
            if len(records) < 2:
                continue
            owners = ", ".join(
                f"bindings[{index}]({binding.get('requirementId', '<missing>')},"
                f"{binding.get('witnessPath', '<missing>')})"
                for index, binding in records
            )
            collisions.append(f"{field} {js_json_dumps(identity)}: {owners}")
    if collisions:
        raise ValueError("requirement binding identities must be unique:\n" + "\n".join(collisions))


def requirement_transition_report(
    input_path: str,
    previous_ref: str | None,
    previous_requirement_files: Sequence[str],
    *,
    repo_root: Path,
    executable: str,
) -> list[JsonObject]:
    if previous_ref is None or input_path not in previous_requirement_files:
        return []
    previous = _parse_json_object(
        _git(repo_root, ("show", f"{previous_ref}:{input_path}")), input_path
    )
    next_value = read_json_object(repo_root / input_path)
    if js_json_dumps(previous) == js_json_dumps(next_value):
        return []
    source_id = next_value.get("sourceId")
    if not isinstance(source_id, str):
        raise TypeError(f"requirement source has no sourceId: {input_path}")
    transition_id = "ci-coordinator.requirement-transition." + re.sub(
        r"[^a-zA-Z0-9.-]", ".", source_id
    )
    return [
        run_proofkit_with_input(
            "requirement-source-transition",
            {
                "schemaVersion": 1,
                "transitionId": transition_id,
                "previous": previous,
                "next": next_value,
                "nonClaims": [
                    (
                        "This transition validates lifecycle shape only; it does not approve "
                        "requirement meaning, proof adequacy, implementation, merge, or production "
                        "retention history."
                    )
                ],
            },
            repo_root=repo_root,
            executable=executable,
        )
    ]


def requirement_transition_base_ref(
    requirement_files: Sequence[str],
    *,
    repo_root: Path = REPO_ROOT,
    env: Mapping[str, str] | None = None,
) -> str | None:
    dirty_paths = {
        *_git_lines(repo_root, ("diff", "--name-only")),
        *_git_lines(repo_root, ("diff", "--cached", "--name-only")),
    }
    if any(path in dirty_paths for path in requirement_files):
        return _resolve_commit(repo_root, "HEAD")
    configured_base = None if env is None else env.get("PROOFKIT_BASE_REF", "").strip()
    if configured_base:
        return _resolve_commit(repo_root, configured_base)
    candidate = _try_git(repo_root, ("merge-base", "HEAD", "origin/master")) or _try_git(
        repo_root, ("rev-parse", "HEAD^")
    )
    return None if candidate is None else _resolve_commit(repo_root, candidate)


def validate_route_storage_transition(
    current: RouteAuthority,
    previous_ref: str | None,
    *,
    repo_root: Path = REPO_ROOT,
) -> RouteStorageTransition:
    if not current.compact or previous_ref is None:
        return RouteStorageTransition(0, 0, 0)
    previous = load_route_authority(repo_root=repo_root, ref=previous_ref)
    if previous.compact:
        return RouteStorageTransition(0, 0, 0)
    added_paths = {
        *_git_lines(
            repo_root,
            ("diff", "--diff-filter=A", "--name-only", previous_ref, "--"),
        ),
        *_git_lines(
            repo_root,
            ("ls-files", "--others", "--exclude-standard"),
        ),
    }
    parity = assert_legacy_relation_preserved(
        previous.binding_projection,
        current.binding_projection,
        allowed_added_paths=sorted(added_paths),
    )
    tuple_path = "proofkit/required-binding-tuples.v1.json"
    previous_tuple_manifest = _parse_json_object(
        _git(repo_root, ("show", f"{previous_ref}:{tuple_path}")), tuple_path
    )
    current_tuple_manifest = read_json_object(repo_root / tuple_path)
    added_required_tuple_count = assert_required_tuple_relation_preserved(
        _rows(previous_tuple_manifest, "owners"),
        _rows(current_tuple_manifest, "owners"),
        allowed_added_paths=sorted(added_paths),
    )
    return RouteStorageTransition(
        parity.extra_binding_count,
        added_required_tuple_count,
        parity.canonicalized_requirement_non_claim_count,
    )


def requirement_files_at_ref(ref: str, *, repo_root: Path = REPO_ROOT) -> list[str]:
    return [
        path
        for path in _git_lines(repo_root, ("ls-tree", "-r", "--name-only", ref, "--", "docs/specs"))
        if path.endswith("/requirements.v1.json")
    ]


def find_requirement_files(root_path: Path, *, repo_root: Path = REPO_ROOT) -> list[str]:
    return sorted(
        path.relative_to(repo_root).as_posix()
        for path in root_path.rglob("requirements.v1.json")
        if path.is_file()
    )


def run_proofkit(
    command: str,
    args: Sequence[str],
    *,
    repo_root: Path,
    executable: str,
) -> JsonObject:
    result = invoke_proofkit(executable, command, args, cwd=repo_root)
    input_index = args.index("--input") + 1 if "--input" in args else -1
    input_path = args[input_index] if 0 <= input_index < len(args) else "<unknown>"
    if result.returncode != 0:
        raise ForwardedProofkitFailure(
            result.returncode if result.returncode >= 0 else 1,
            result.stdout,
            result.stderr,
        )
    report = _parse_json_report(command, input_path, result.stdout)
    if report.get("state") != "passed":
        raise ForwardedProofkitFailure(1, result.stdout, "")
    summary = report.get("summary", {})
    return {
        "command": command,
        "inputPath": input_path,
        "state": report.get("state"),
        "summary": summary,
    }


def run_proofkit_with_input(
    command: str,
    input_value: Mapping[str, object],
    *,
    repo_root: Path,
    executable: str,
    input_path: str = "<generated-transition>",
) -> JsonObject:
    result = invoke_proofkit(
        executable,
        command,
        ("--input", "-"),
        cwd=repo_root,
        input_text=js_json_dumps(input_value),
    )
    if result.returncode != 0:
        raise ForwardedProofkitFailure(
            result.returncode if result.returncode >= 0 else 1,
            result.stdout,
            result.stderr,
        )
    report = _parse_json_report(command, input_path, result.stdout)
    if report.get("state") != "passed":
        raise ForwardedProofkitFailure(1, result.stdout, "")
    return {
        "command": command,
        "inputPath": input_path,
        "state": report.get("state"),
        "summary": report.get("summary", {}),
    }


def main() -> int:
    try:
        write_json(requirements_report())
    except ForwardedProofkitFailure as failure:
        sys.stdout.write(failure.stdout)
        sys.stderr.write(failure.stderr)
        return failure.status
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


def _parse_json_report(command: str, input_path: str, output: str) -> JsonObject:
    try:
        report = _parse_json_object(output, input_path)
    except ValueError as error:
        raise ValueError(
            f"{command} for {input_path} did not emit a JSON report: {error}"
        ) from error
    expected_kind = _PROOFKIT_REPORT_KINDS.get(command)
    if expected_kind is None:
        raise ValueError(f"unsupported Proofkit report command: {command}")
    if set(report) != _PROOFKIT_REPORT_KEYS:
        missing = sorted(_PROOFKIT_REPORT_KEYS - set(report))
        extra = sorted(set(report) - _PROOFKIT_REPORT_KEYS)
        raise ValueError(
            f"{command} for {input_path} report keys differ; missing={missing}, extra={extra}"
        )
    if type(report.get("schemaVersion")) is not int or report["schemaVersion"] != 1:
        raise ValueError(f"{command} for {input_path} report schemaVersion must be 1")
    if report.get("reportKind") != expected_kind:
        raise ValueError(f"{command} for {input_path} reportKind is invalid")
    if not isinstance(report.get("reportId"), str) or not report["reportId"]:
        raise ValueError(f"{command} for {input_path} reportId must be non-empty text")
    if report.get("state") not in {"passed", "failed"}:
        raise ValueError(f"{command} for {input_path} report state is invalid")
    as_object(report.get("summary"), f"{command} report summary")
    as_array(report.get("diagnostics"), f"{command} report diagnostics")
    as_array(report.get("ruleResults"), f"{command} report ruleResults")
    as_array(report.get("nonClaims"), f"{command} report nonClaims")
    return report


def _parse_json_object(source: str, context: str) -> JsonObject:
    return parse_json_object(source, context)


def _rows(document: Mapping[str, object], field: str) -> list[JsonObject]:
    return [as_object(row, field) for row in as_array(document.get(field, []), field)]


def _required_string(value: Mapping[str, object], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str):
        raise TypeError(f"{field} must be a string")
    return result


def _string_array(value: object, context: str) -> list[str]:
    raw = as_array(value, context)
    if any(not isinstance(item, str) for item in raw):
        raise ValueError(f"{context} must contain only strings")
    return [item for item in raw if isinstance(item, str)]


def _git_lines(repo_root: Path, args: Sequence[str]) -> list[str]:
    output = _git(repo_root, args)
    return [line for line in output.split("\n") if line] if output else []


def _tracked_regular_paths(repo_root: Path) -> frozenset[str]:
    output = capture_git_text(repo_root, ("ls-files", "--stage", "-z"))
    paths: set[str] = set()
    for entry in output.split("\0"):
        if not entry:
            continue
        metadata, separator, path = entry.partition("\t")
        fields = metadata.split(" ")
        if separator != "\t" or len(fields) != 3:
            raise ValueError("git tracked-file inventory is malformed")
        mode, _object_id, stage = fields
        if stage == "0" and mode in {"100644", "100755"}:
            paths.add(path)
    return frozenset(paths)


def _try_git(repo_root: Path, args: Sequence[str]) -> str | None:
    try:
        return _git(repo_root, args)
    except BoundedGitCommandError:
        return None


def _git(repo_root: Path, args: Sequence[str]) -> str:
    return capture_git_text(repo_root, args, strip=True)


def _resolve_commit(repo_root: Path, ref: str) -> str:
    return _git(repo_root, ("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"))


if __name__ == "__main__":
    raise SystemExit(main())

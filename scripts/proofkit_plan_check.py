from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.proofkit_changed_paths import (
    ChangedPathContext,
    changed_path_context_from_git_context,
)
from scripts.proofkit_cli import invoke_proofkit, resolve_proofkit_executable
from scripts.proofkit_common import (
    JsonObject,
    as_array,
    as_object,
    js_json_dumps,
    parse_json_object,
    read_json_object,
    safe_repo_path,
    write_json,
)
from scripts.proofkit_git_range import ProofkitGitRange, ResolvedGitRange
from scripts.proofkit_pending_review import (
    assert_review_snapshot_unchanged,
    capture_review_snapshot,
    pending_review,
)
from scripts.proofkit_route_sources import load_route_authority
from scripts.proofkit_selective_contract import (
    assert_admitted_plan,
    assert_selective_plan_contract,
)
from scripts.proofkit_selective_plan import (
    REQUIREMENT_ADMISSION_PATH_PATTERNS,
    path_matches_any,
    selective_gate_plan_input,
)
from scripts.repository_paths import real_repository_directory

REPO_ROOT = Path(__file__).resolve().parent.parent
MAX_PLANNER_OUTPUT_BYTES = 16 * 1024 * 1024
PLAN_CHECK_ARTIFACT = Path(".ci-evidence/proofkit-plan-check.json")
UNBOUND_PROBE_PATHS = (
    ".github/workflows/unbound-proof-probe-20260715.yml",
    "backend/src/ci_coordinator/unbound_proof_probe_20260715.py",
    "scripts/unbound_proof_probe_20260715.py",
)
CRITICAL_REQUIREMENT_OWNERS: Mapping[str, frozenset[str]] = {
    "docs/specs/ci-coordinator-core/audit-json-resource-profile.v1.json": frozenset(
        {"REQ-CI-CORE-010", "REQ-CI-CORE-011"}
    ),
    "docs/specs/ci-coordinator-core/audit-persistence-byte-profile.v1.json": frozenset(
        {"REQ-CI-CORE-010", "REQ-CI-CORE-013"}
    ),
    "docs/bootstrap-workflow-contract.md": frozenset({"REQ-CI-RUNTIME-007"}),
    "fixtures/target-repository/.github/workflows/ci-coordinator-bootstrap.yml": frozenset(
        {"REQ-CI-RUNTIME-007", "REQ-CI-RUNTIME-011"}
    ),
    "fixtures/native-target-repository/.github/workflows/full-check.yml": frozenset(
        {"REQ-CI-RUNTIME-026"}
    ),
    "fixtures/native-target-repository/.github/workflows/native-full-check.yml": frozenset(
        {"REQ-CI-RUNTIME-026"}
    ),
    "fixtures/target-repository/.github/workflows/native-full-check.yml": frozenset(
        {"REQ-CI-RUNTIME-026"}
    ),
    "Dockerfile": frozenset({"REQ-CI-RUNTIME-008", "REQ-CI-RUNTIME-009"}),
    "docs/specs/ci-coordinator-runtime/python-runtime-profile.v1.json": frozenset(
        {"REQ-CI-RUNTIME-009"}
    ),
}
DOCUMENTATION_GRAPH_PROBE_PATHS = (
    "README.md",
    "ROADMAP.md",
    "docs/INDEX.md",
    "docs/architecture/INDEX.md",
)
DOCUMENTATION_DIAGRAM_PROBE_PATHS = (
    "README.md",
    "ROADMAP.md",
    "docs/architecture/INDEX.md",
    "docs/features/diagram-validation.md",
    "docs/specs/ci-coordinator-proofkit-adoption/documentation-diagrams-profile.v1.json",
    "docs/specs/ci-coordinator-proofkit-adoption/documentation-graph-profile.v1.json",
    ".githooks/pre-push",
    ".github/workflows/python-persistence.yml",
    "package.json",
    "frontend/package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "backend/pyproject.toml",
    "backend/requirements-dev.lock",
    "backend/uv.lock",
    "mise.toml",
    "mise.lock",
    "scripts/diagram_contract.py",
    "scripts/diagram_inventory.py",
    "scripts/diagram_check.py",
    "scripts/diagram_process.py",
    "scripts/diagram_push.py",
    "scripts/tests/test_diagram_inventory.py",
    "scripts/tests/test_diagram_process.py",
    "scripts/tests/test_diagram_push.py",
    "frontend/tools/diagrams.mjs",
    "frontend/tools/diagrams-render.mjs",
    "frontend/tools/diagrams-semantic.mjs",
    "frontend/tools/diagrams-checks.mjs",
    "frontend/tools/diagrams-process-checks.mjs",
    "frontend/tools/diagrams-semantic-checks.mjs",
)
DOCUMENTATION_DIAGRAM_UNBOUND_PROBE_PATHS = (
    "docs/features/diagram-routing-probe.md",
    "docs/features/diagram-routing-probe.mmd",
    "docs/features/diagram-routing-probe.mermaid",
    "docs/features/diagram-routing-probe.mdx",
    "frontend/tools/diagrams-routing-probe.mjs",
    "scripts/diagram_routing_probe.py",
    "scripts/tests/test_diagram_routing_probe.py",
)


@dataclass(slots=True)
class BindingExpectation:
    commands: set[str] = field(default_factory=set)
    requirement_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class PlannerInvocation:
    report: JsonObject
    status: int


def plan_check_report(
    plan_args: Sequence[str],
    *,
    repo_root: Path = REPO_ROOT,
    env: Mapping[str, str] | None = None,
    proofkit_executable: str | Path | None = None,
) -> JsonObject:
    selected_env = os.environ if env is None else env
    executable = resolve_proofkit_executable(proofkit_executable)
    context = selective_plan_context(plan_args, repo_root=repo_root, env=selected_env)
    review_snapshot = capture_review_snapshot(context, repo_root=repo_root)
    route_authority = load_route_authority(repo_root=repo_root)
    bindings = route_authority.binding_projection
    base_bindings = load_route_authority(
        repo_root=repo_root,
        ref=context.base_ref,
    ).binding_projection
    actual_input = selective_gate_plan_input(
        base_ref=context.base_ref,
        paths=context.paths,
        repo_root=repo_root,
        requirement_bindings=bindings,
        base_requirement_bindings=base_bindings,
    )
    assert_removed_paths_are_explicitly_ignored(actual_input, repo_root=repo_root)
    actual_report = run_planner(
        actual_input,
        "current changed-path plan",
        repo_root=repo_root,
        executable=executable,
    )
    assert_admitted_plan(
        actual_report,
        "current changed-path plan",
        input_value=actual_input,
    )

    assert_bindings_classified_by_current_profile(bindings, repo_root=repo_root)
    command_catalog = {
        _required_string(command, "commandId"): _required_string(command, "command")
        for command in _rows(bindings, "witnessCommands")
    }
    expectations = binding_expectations(
        _rows(bindings, "bindings"),
        _rows(bindings, "requirements"),
        command_catalog,
        repo_root=repo_root,
    )
    routing_input = selective_gate_plan_input(
        base_ref=context.base_ref,
        paths=[*context.paths, *expectations],
        repo_root=repo_root,
        requirement_bindings=bindings,
        base_requirement_bindings=base_bindings,
    )
    routing_report = run_planner(
        routing_input,
        "complete binding-routing plan",
        repo_root=repo_root,
        executable=executable,
    )
    assert_admitted_plan(
        routing_report,
        "complete binding-routing plan",
        input_value=routing_input,
    )
    assert_every_binding_routes(routing_report, expectations)
    assert_critical_requirement_owners(routing_report)
    documentation_base_commit = context.head_ref
    documentation_base_bindings = load_route_authority(
        repo_root=repo_root, ref=documentation_base_commit
    ).binding_projection
    assert_documentation_graph_routes(
        command_catalog,
        bindings=bindings,
        base_bindings=documentation_base_bindings,
        base_ref=documentation_base_commit,
        repo_root=repo_root,
        executable=executable,
    )
    assert_documentation_diagram_routes(
        command_catalog,
        bindings=bindings,
        base_bindings=documentation_base_bindings,
        base_ref=documentation_base_commit,
        repo_root=repo_root,
        executable=executable,
    )
    assert_unbound_proof_paths_fail_closed(
        context,
        bindings=bindings,
        base_bindings=base_bindings,
        repo_root=repo_root,
        executable=executable,
    )
    if (
        ProofkitGitRange(repo_root).resolve_commit("HEAD", "plan checkout closeout")
        != context.head_ref
    ):
        raise ValueError("proofkit checkout HEAD changed while validating the plan")
    review = pending_review(
        snapshot=review_snapshot,
        bindings=bindings,
        base_bindings=base_bindings,
        actual_input=actual_input,
        actual_report=actual_report,
    )
    assert_review_snapshot_unchanged(
        review_snapshot, capture_review_snapshot(context, repo_root=repo_root)
    )
    return {
        "schemaVersion": 1,
        "reportId": "ci-coordinator.proofkit-plan-check",
        "reportKind": "ci-coordinator.proofkit-plan-check",
        "state": "passed",
        "summary": {
            "boundPathCount": len(expectations),
            "changedPathCount": len(_array_or_empty(actual_report.get("changedPaths"))),
            "ignoredRemovedProofPathCount": len(
                _array_or_empty(actual_input.get("ignoredProofLikePaths"))
            ),
            "documentationGraphProbeCount": len(DOCUMENTATION_GRAPH_PROBE_PATHS),
            "documentationDiagramProbeCount": len(DOCUMENTATION_DIAGRAM_PROBE_PATHS)
            + len(DOCUMENTATION_DIAGRAM_UNBOUND_PROBE_PATHS),
            "requiredCommandCount": len(_array_or_empty(actual_report.get("requiredCommands"))),
            "routedBindingCount": len(_rows(bindings, "bindings")),
            "unboundProbeCount": len(UNBOUND_PROBE_PATHS),
            "pendingRequirementCount": len(review["requirements"]),
        },
        "pendingReview": review,
        "actualPlanInput": actual_input,
        "actualPlan": actual_report,
        "nonClaims": [
            (
                "This wrapper validates selective routing and fail-closed behavior without "
                "executing routed commands."
            ),
            (
                "Removed baseline-classified proof paths are admitted only through an exact "
                "baseline binding-state digest and explicit obsolete disposition while every "
                "affected requirement or its explicit replacement retains a live head witness."
            ),
            (
                "A passing plan does not prove witness freshness, provider execution, "
                "merge safety, "
                "or deployment readiness."
            ),
        ],
    }


def assert_documentation_graph_routes(
    command_catalog: Mapping[str, str],
    *,
    bindings: Mapping[str, object],
    base_bindings: Mapping[str, object],
    base_ref: str,
    repo_root: Path,
    executable: str,
) -> None:
    expected_command = command_catalog.get("documentation.graph")
    if expected_command is None:
        raise ValueError("binding command catalog has no documentation.graph")
    for path in DOCUMENTATION_GRAPH_PROBE_PATHS:
        input_value = selective_gate_plan_input(
            base_ref=base_ref,
            paths=[path],
            repo_root=repo_root,
            requirement_bindings=bindings,
            base_requirement_bindings=base_bindings,
        )
        report = run_planner(
            input_value,
            f"documentation graph routing probe for {path}",
            repo_root=repo_root,
            executable=executable,
        )
        assert_admitted_plan(
            report,
            f"documentation graph routing probe for {path}",
            input_value=input_value,
        )
        required_commands = {
            command.get("command")
            for command in _object_rows(report.get("requiredCommands"), "required commands")
        }
        if expected_command not in required_commands:
            raise ValueError(f"documentation graph did not route for {path}")


def assert_documentation_diagram_routes(
    command_catalog: Mapping[str, str],
    *,
    bindings: Mapping[str, object],
    base_bindings: Mapping[str, object],
    base_ref: str,
    repo_root: Path,
    executable: str,
) -> None:
    expected_commands: set[str] = set()
    for command_id in (
        "documentation.diagrams-inventory",
        "documentation.diagrams",
        "documentation.diagrams-falsifiers",
        "documentation.diagrams-process",
    ):
        command = command_catalog.get(command_id)
        if command is None:
            raise ValueError(f"binding command catalog has no {command_id}")
        expected_commands.add(command)
    for path in (*DOCUMENTATION_DIAGRAM_PROBE_PATHS, *DOCUMENTATION_DIAGRAM_UNBOUND_PROBE_PATHS):
        input_value = selective_gate_plan_input(
            base_ref=base_ref,
            paths=[path],
            repo_root=repo_root,
            requirement_bindings=bindings,
            base_requirement_bindings=base_bindings,
        )
        label = f"documentation diagram routing probe for {path}"
        result = invoke_planner(input_value, repo_root=repo_root, executable=executable)
        assert_selective_plan_contract(result.report, label, input_value=input_value)
        if path in DOCUMENTATION_DIAGRAM_UNBOUND_PROBE_PATHS:
            unknown_paths = {
                edge.get("path")
                for edge in _object_rows(result.report.get("unknownEdges"), "unknown edges")
            }
            if (
                result.status == 0
                or result.report.get("planState") != "fail_closed"
                or path not in unknown_paths
            ):
                raise ValueError(f"unbound diagram input did not fail closed: {path}")
        else:
            if result.status != 0:
                raise ValueError(f"{label} failed with status {result.status}")
            assert_admitted_plan(result.report, label, input_value=input_value)
        required_commands = {
            _required_string(command, "command")
            for command in _object_rows(result.report.get("requiredCommands"), "required commands")
        }
        assert_set_contains(required_commands, expected_commands, label)


def assert_bindings_classified_by_current_profile(
    bindings: Mapping[str, object], *, repo_root: Path = REPO_ROOT
) -> None:
    profile = read_json_object(repo_root / "proofkit/repo-profile.json")
    proofs = as_object(profile.get("proofs"), "repo profile proofs")
    raw_patterns = proofs.get("proofLikePaths")
    if (
        not isinstance(raw_patterns, list)
        or not raw_patterns
        or any(not isinstance(pattern, str) or not pattern for pattern in raw_patterns)
    ):
        raise ValueError("current repo profile has no valid proof-owner patterns")
    patterns = [pattern for pattern in raw_patterns if isinstance(pattern, str)]
    uncovered_paths = sorted(
        {
            path
            for binding in _rows(bindings, "bindings")
            if isinstance((path := binding.get("witnessPath")), str)
            and not path_matches_any(patterns, path)
        }
    )
    if uncovered_paths:
        visible = ", ".join(uncovered_paths[:10])
        omitted = max(0, len(uncovered_paths) - 10)
        suffix = f"; {omitted} more" if omitted > 0 else ""
        raise ValueError(
            f"current repo profile does not classify bound witnesses: {visible}{suffix}"
        )


def assert_critical_requirement_owners(report: Mapping[str, object]) -> None:
    requirements_by_path: dict[str, set[str]] = {}
    for witness in _object_rows(report.get("touchedRequirementWitnesses"), "touched witnesses"):
        path = witness.get("path")
        if not isinstance(path, str):
            continue
        requirement_ids = requirements_by_path.setdefault(path, set())
        requirement_ids.update(
            requirement_id
            for requirement_id in _array_or_empty(witness.get("requirementIds"))
            if isinstance(requirement_id, str)
        )
    for path, expected in CRITICAL_REQUIREMENT_OWNERS.items():
        assert_set_contains(requirements_by_path.get(path, set()), expected, f"{path} owners")


def binding_expectations(
    bindings: Sequence[Mapping[str, object]],
    requirements: Sequence[Mapping[str, object]],
    command_catalog: Mapping[str, str],
    *,
    repo_root: Path = REPO_ROOT,
) -> dict[str, BindingExpectation]:
    verify_command = command_catalog.get("proofkit.verify")
    if verify_command is None:
        raise ValueError("binding command catalog has no proofkit.verify")
    expectations: dict[str, BindingExpectation] = {}
    for requirement in requirements:
        path = _required_string(requirement, "specPath")
        if not (repo_root / path).is_file():
            raise ValueError(f"requirement references a missing source: {path}")
        expectation = expectations.setdefault(
            path,
            BindingExpectation(commands=_path_route_commands(path, command_catalog)),
        )
        expectation.requirement_ids.add(_required_string(requirement, "requirementId"))
    for binding in bindings:
        path = _required_string(binding, "witnessPath")
        if not (repo_root / path).is_file():
            raise ValueError(f"requirement binding references a missing witness: {path}")
        expectation = expectations.setdefault(
            path,
            BindingExpectation(commands=_path_route_commands(path, command_catalog)),
        )
        expectation.requirement_ids.add(_required_string(binding, "requirementId"))
        for command_id in _string_array(binding.get("commandIds"), "binding command ids"):
            command = command_catalog.get(command_id)
            if command is None:
                witness_id = binding.get("witnessId")
                raise ValueError(f"binding {witness_id} references unknown command {command_id}")
            expectation.commands.add(command)
    return expectations


def _path_route_commands(path: str, command_catalog: Mapping[str, str]) -> set[str]:
    command_ids = {"proofkit.verify"}
    if path_matches_any(REQUIREMENT_ADMISSION_PATH_PATTERNS, path):
        command_ids.add("requirements.admission")
    if path.endswith(".md"):
        command_ids.add("text.policy")
    commands: set[str] = set()
    for command_id in command_ids:
        command = command_catalog.get(command_id)
        if command is None:
            raise ValueError(f"binding command catalog has no {command_id}")
        commands.add(command)
    return commands


def assert_every_binding_routes(
    report: Mapping[str, object], expectations: Mapping[str, BindingExpectation]
) -> None:
    observed_by_path: dict[str, BindingExpectation] = {}
    for witness in _object_rows(report.get("touchedRequirementWitnesses"), "touched witnesses"):
        if set(witness) != {"commands", "path", "requirementIds"}:
            raise ValueError("selective planner route fields are not exact")
        path = _required_string(witness, "path")
        if path in observed_by_path:
            raise ValueError(f"selective planner repeated route path {path}")
        commands = _string_array(witness.get("commands"), f"{path} commands")
        requirement_ids = _string_array(witness.get("requirementIds"), f"{path} requirement ids")
        if len(commands) != len(set(commands)) or len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError(f"selective planner repeated route values for {path}")
        observed_by_path[path] = BindingExpectation(
            commands=set(commands),
            requirement_ids=set(requirement_ids),
        )
    assert_set_equal(set(observed_by_path), set(expectations), "selective route paths")
    for path, expected in expectations.items():
        routed = observed_by_path[path]
        assert_set_equal(routed.requirement_ids, expected.requirement_ids, f"{path} requirements")
        assert_set_equal(routed.commands, expected.commands, f"{path} commands")


def assert_set_equal(observed: set[str], expected: set[str] | frozenset[str], label: str) -> None:
    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)
    if missing or unexpected:
        raise ValueError(f"{label} differ; missing={missing}, unexpected={unexpected}")


def assert_set_contains(
    observed: set[str], expected: set[str] | frozenset[str], label: str
) -> None:
    missing = sorted(expected - observed)
    if missing:
        raise ValueError(f"{label} missing: {', '.join(missing)}")


def assert_removed_paths_are_explicitly_ignored(
    input_value: Mapping[str, object], *, repo_root: Path = REPO_ROOT
) -> None:
    changed_paths = set(_string_array(input_value.get("changedPaths"), "changed paths"))
    for path in _string_array(input_value.get("ignoredProofLikePaths"), "ignored proof-like paths"):
        if path not in changed_paths or (repo_root / path).exists():
            raise ValueError(f"ignored proof-like path is not a removed changed path: {path}")


def assert_unbound_proof_paths_fail_closed(
    context: ChangedPathContext,
    *,
    bindings: Mapping[str, object],
    base_bindings: Mapping[str, object],
    repo_root: Path,
    executable: str,
) -> None:
    input_value = selective_gate_plan_input(
        base_ref=context.base_ref,
        paths=[*context.paths, *UNBOUND_PROBE_PATHS],
        repo_root=repo_root,
        requirement_bindings=bindings,
        base_requirement_bindings=base_bindings,
    )
    result = invoke_planner(input_value, repo_root=repo_root, executable=executable)
    assert_selective_plan_contract(
        result.report,
        "unbound proof-path plan",
        input_value=input_value,
    )
    unknown_paths = {
        edge.get("path")
        for edge in _object_rows(result.report.get("unknownEdges"), "unknown edges")
    }
    if (
        result.status == 0
        or result.report.get("planState") != "fail_closed"
        or any(path not in unknown_paths for path in UNBOUND_PROBE_PATHS)
    ):
        raise ValueError("selective planning did not fail closed for every unbound proof path")


def run_planner(
    input_value: Mapping[str, object],
    label: str,
    *,
    repo_root: Path,
    executable: str,
) -> JsonObject:
    result = invoke_planner(input_value, repo_root=repo_root, executable=executable)
    if result.status != 0:
        raise ValueError(
            f"{label} failed with status {result.status}: "
            + js_json_dumps(failure_projection(result.report))
        )
    return result.report


def failure_projection(report: Mapping[str, object]) -> JsonObject:
    failures = _array_or_empty(report.get("failures"))
    unknown_edges = _object_rows(report.get("unknownEdges"), "unknown edges")
    return {
        "planState": report.get("planState", "unknown"),
        "failures": failures[:10],
        "unknownEdges": [
            {
                "edgeClass": edge.get("edgeClass"),
                "path": edge.get("path"),
                "reason": edge.get("reason"),
            }
            for edge in unknown_edges[:10]
        ],
        "omittedFailureCount": max(0, len(failures) - 10),
        "omittedUnknownEdgeCount": max(0, len(unknown_edges) - 10),
    }


def invoke_planner(
    input_value: Mapping[str, object], *, repo_root: Path, executable: str
) -> PlannerInvocation:
    result = invoke_proofkit(
        executable,
        "selective-gate-plan",
        ("--input", "-"),
        cwd=repo_root,
        input_text=js_json_dumps(input_value),
        max_output_bytes=MAX_PLANNER_OUTPUT_BYTES,
    )
    return PlannerInvocation(
        report=_parse_json_output(result.stdout, "selective-gate-plan"),
        status=result.returncode,
    )


def selective_plan_input(
    args: Sequence[str], *, repo_root: Path, env: Mapping[str, str]
) -> JsonObject:
    context = selective_plan_context(args, repo_root=repo_root, env=env)
    return selective_gate_plan_input(
        base_ref=context.base_ref, paths=context.paths, repo_root=repo_root
    )


def selective_plan_context(
    args: Sequence[str], *, repo_root: Path, env: Mapping[str, str]
) -> ChangedPathContext:
    git_range = ProofkitGitRange(repo_root)
    checkout_commit = git_range.resolve_commit("HEAD", "plan checkout")
    resolved_range: ResolvedGitRange | None = None

    def git_paths(args: Sequence[str]) -> list[str]:
        if len(args) >= 2 and args[0] == "diff" and args[1] == "--cached":
            return git_range.git_paths((*args, checkout_commit, "--"))
        return git_range.git_paths(args)

    def committed_paths(base_ref: str, head_ref: str) -> list[str]:
        nonlocal resolved_range
        resolved_range = git_range.committed_range(base_ref, head_ref)
        return list(resolved_range.paths)

    context = changed_path_context_from_git_context(
        args=args,
        env=env,
        normalize_path=safe_repo_path,
        git_paths=git_paths,
        committed_paths_since=committed_paths,
    )
    if git_range.resolve_commit("HEAD", "plan checkout confirmation") != checkout_commit:
        raise ValueError("proofkit checkout HEAD changed while building the plan context")
    if resolved_range is not None:
        if resolved_range.head_commit != checkout_commit:
            raise ValueError("proofkit range head changed while building the plan context")
        return ChangedPathContext(
            base_ref=resolved_range.base_commit,
            head_ref=resolved_range.head_commit,
            paths=context.paths,
        )
    return ChangedPathContext(
        base_ref=checkout_commit,
        head_ref=checkout_commit,
        paths=context.paths,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        report = plan_check_report(args)
    except Exception as error:
        report = {
            "schemaVersion": 1,
            "reportId": "ci-coordinator.proofkit-plan-check",
            "reportKind": "ci-coordinator.proofkit-plan-check",
            "state": "failed",
            "failure": {"kind": type(error).__name__, "message": str(error)},
        }
    try:
        artifact_digest = _write_report_artifact(report, repo_root=REPO_ROOT)
    except Exception as error:
        report = {
            "schemaVersion": 1,
            "reportId": "ci-coordinator.proofkit-plan-check",
            "reportKind": "ci-coordinator.proofkit-plan-check",
            "state": "failed",
            "failure": {
                "kind": type(error).__name__,
                "message": "Full report publication failed; no truncated review is admitted.",
            },
        }
        try:
            artifact_digest = _write_report_artifact(report, repo_root=REPO_ROOT)
        except Exception:
            print("proofkit plan-check artifact publication failed", file=sys.stderr)
            return 1
    write_json(
        {
            "schemaVersion": report.get("schemaVersion", 1),
            "reportId": report["reportId"],
            "reportKind": report.get("reportKind", "ci-coordinator.proofkit-plan-check"),
            "state": report["state"],
            "summary": report.get("summary", {}),
            "nonClaims": report.get("nonClaims", []),
            "semanticReviewState": "pending" if "pendingReview" in report else "unavailable",
            "artifact": PLAN_CHECK_ARTIFACT.as_posix(),
            "artifactSha256": artifact_digest,
        }
    )
    return 0 if report["state"] == "passed" else 1


def _write_report_artifact(report: Mapping[str, object], *, repo_root: Path) -> str:
    payload = (js_json_dumps(report) + "\n").encode("utf-8")
    if len(payload) > MAX_PLANNER_OUTPUT_BYTES:
        raise ValueError("proofkit plan-check artifact exceeds its byte bound")
    (repo_root / PLAN_CHECK_ARTIFACT.parent).mkdir(exist_ok=True)
    parent = real_repository_directory(repo_root, PLAN_CHECK_ARTIFACT.parent, "plan artifact")
    descriptor, temporary = tempfile.mkstemp(prefix=".proofkit-plan-check-", dir=parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        os.replace(temporary, repo_root / PLAN_CHECK_ARTIFACT)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return hashlib.sha256(payload).hexdigest()


def _parse_json_output(output: str, label: str) -> JsonObject:
    return parse_json_object(output, label)


def _rows(document: Mapping[str, object], field: str) -> list[JsonObject]:
    return _object_rows(document.get(field, []), field)


def _object_rows(value: object, context: str) -> list[JsonObject]:
    return [as_object(row, context) for row in _array_or_empty(value)]


def _array_or_empty(value: object) -> list[object]:
    return [] if value is None else list(as_array(value, "report field"))


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


if __name__ == "__main__":
    raise SystemExit(main())

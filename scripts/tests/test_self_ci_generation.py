from __future__ import annotations

import copy
import io
import json
import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from ruamel.yaml import YAML
from scripts.proofkit_common import JsonObject
from scripts.self_ci_catalog import (
    COVERAGE_JOBS,
    REQUESTER_JOBS,
    FamilySettings,
    policy_document,
    validation_catalog,
)
from scripts.self_ci_controls import (
    COORDINATED_GATE_ID,
    LOCAL_REQUESTER,
    dump_workflow_yaml,
    render_workflow,
)
from scripts.self_ci_generate import SelfCiArtifacts, render_self_ci
from scripts.self_ci_source import (
    CONTROL_TEMPLATE_PATH,
    GATE_ID,
    NATIVE_PATH,
    REQUESTER_ASSERTION_ID,
    WORKFLOW_PATH,
    admit_native_source,
    read_regular,
    workflow_value,
)

from ci_coordinator.config_control import ValidatedEpochDraft, admit_policy_document
from ci_coordinator.config_control.planning_projection import project_dynamic_ci_planning
from ci_coordinator.execution_orchestration import parse_target_execution_registry
from ci_coordinator.target_artifacts.renderer import render_target_artifacts
from ci_coordinator.target_artifacts.source_codec import parse_target_artifacts_source

ROOT = Path(__file__).resolve().parents[2]


def _yaml(value: object) -> bytes:
    output = io.StringIO()
    YAML().dump(value, output)
    return output.getvalue().encode()


def _native() -> JsonObject:
    return workflow_value(read_regular(ROOT, NATIVE_PATH), NATIVE_PATH)


@pytest.mark.parametrize("yaml_version", [(1, 1), (1, 2)])
def test_workflow_emitter_preserves_ambiguous_string_types(yaml_version: tuple[int, int]) -> None:
    fixture = {
        "on": {"workflow_dispatch": {}},
        "env": {
            "GOWORK": "off",
            "YES": "yes",
            "NO": "no",
            "NULL": "null",
            "OCTAL": "012",
            "SEXAGESIMAL": "1:20",
            "DATE": "2026-09-20",
            "NORMAL": "text",
            "BOOL": False,
            "NUMBER": 12,
        },
        "values": ["ON", "n", "~", "1.5"],
    }
    rendered = dump_workflow_yaml(fixture)
    yaml = YAML(typ="safe", pure=True)
    yaml.version = yaml_version
    decoded = yaml.load(rendered)
    assert decoded == fixture
    assert 'GOWORK: "off"' in rendered
    assert type(decoded["env"]["BOOL"]) is bool
    assert type(decoded["env"]["NUMBER"]) is int


def test_workflow_emitter_documents_only_the_two_known_oidc_requesters() -> None:
    fixture: JsonObject = {
        "env": {"GOWORK": "off"},
        "jobs": {
            "plan-request": {
                "uses": LOCAL_REQUESTER,
                "permissions": {"id-token": "write"},
            },
            "trusted-plan-request-entrypoint": {
                "uses": LOCAL_REQUESTER,
                "permissions": {"id-token": "write"},
                "with": {"installation_id": "1", "plan_url": "invalid"},
            },
            "unrecognized-requester": {
                "uses": LOCAL_REQUESTER,
                "permissions": {"id-token": "write"},
            },
        },
    }
    rendered = dump_workflow_yaml(fixture)
    decoded = YAML().load(rendered)
    assert decoded == fixture
    assert 'GOWORK: "off"' in rendered
    jobs = decoded["jobs"]
    assert "id-token" in jobs["plan-request"]["permissions"].ca.items
    assert "id-token" in jobs["trusted-plan-request-entrypoint"]["permissions"].ca.items
    assert jobs["unrecognized-requester"]["permissions"].ca.items == {}
    assert "Delegate GitHub OIDC to the target-free coordinator plan requester." in rendered
    assert "the invalid URL must reject before token acquisition." in rendered


@pytest.mark.parametrize("change", ["foreign-callee", "different-permissions", "valid-url"])
def test_workflow_emitter_does_not_document_a_different_requester_contract(change: str) -> None:
    job: JsonObject = {
        "uses": LOCAL_REQUESTER,
        "permissions": {"id-token": "write"},
        "with": {"installation_id": "1", "plan_url": "invalid"},
    }
    if change == "foreign-callee":
        job["uses"] = "$/.github/workflows/other.yml"
    elif change == "different-permissions":
        job["permissions"]["contents"] = "write"
    else:
        job["with"]["plan_url"] = "https://coordinator.example.test/plan"
    fixture = {"jobs": {"trusted-plan-request-entrypoint": job}}
    decoded = YAML().load(dump_workflow_yaml(fixture))
    assert decoded == fixture
    assert decoded["jobs"]["trusted-plan-request-entrypoint"]["permissions"].ca.items == {}


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-family",
        "foreign-job",
        "foreign-dependency",
        "lost-reason",
        "changed-reason",
        "unprojected-command",
        "dropped-gate-dependency",
        "root-environment",
        "manual-scope",
    ],
)
def test_native_projection_rejects_each_lost_authority_operand(mutation: str) -> None:
    native = _native()
    jobs = native["jobs"]
    assert isinstance(jobs, dict)
    gate = jobs[GATE_ID]
    step = gate["steps"][0]
    if mutation == "missing-family":
        del jobs["developer-host-lifecycle"]
    elif mutation == "foreign-job":
        jobs["foreign"] = copy.deepcopy(jobs["repository-quality"])
    elif mutation == "foreign-dependency":
        jobs["postgres-witness"]["needs"].append("serial-qualification")
    elif mutation == "lost-reason":
        step["run"] = step["run"].replace(
            'test "${TRUSTED_PLAN_REQUEST_REASON}" = plan_url_invalid\n', ""
        )
    elif mutation == "changed-reason":
        step["run"] = step["run"].replace("plan_url_invalid", "success")
    elif mutation == "unprojected-command":
        step["run"] += "exit 0\n"
    elif mutation == "dropped-gate-dependency":
        gate["needs"].remove("developer-host-lifecycle")
    elif mutation == "root-environment":
        native["env"]["UNKNOWN_AUTHORITY"] = "value"
    else:
        jobs["serial-qualification"]["if"] = "${{ always() }}"
    with pytest.raises(ValueError):
        admit_native_source(_yaml(native))


def test_duplicate_yaml_job_does_not_shrink_the_native_universe() -> None:
    content = read_regular(ROOT, NATIVE_PATH)
    assert content.endswith(b"\n")
    duplicate = content + b"  repository-quality:\n    runs-on: ubuntu-24.04\n"
    with pytest.raises(ValueError, match="syntax"):
        admit_native_source(duplicate)


@pytest.mark.parametrize(
    "mutation",
    [
        "missing-scope",
        "missing-base",
        "scope-type",
        "scope-required",
        "scope-default",
        "missing-range",
        "foreign-choice",
        "duplicate-choice",
        "base-type",
        "base-required",
        "foreign-field",
    ],
)
def test_native_manual_proof_inputs_reject_missing_or_widened_contract(mutation: str) -> None:
    native = _native()
    inputs = native["on"]["workflow_dispatch"]["inputs"]
    if mutation == "missing-scope":
        del inputs["proof_scope"]
    elif mutation == "missing-base":
        del inputs["proofkit_base_sha"]
    elif mutation == "scope-type":
        inputs["proof_scope"]["type"] = "string"
    elif mutation == "scope-required":
        inputs["proof_scope"]["required"] = "true"
    elif mutation == "scope-default":
        inputs["proof_scope"]["default"] = "range"
    elif mutation == "missing-range":
        inputs["proof_scope"]["options"] = ["full"]
    elif mutation == "foreign-choice":
        inputs["proof_scope"]["options"].append("deep")
    elif mutation == "duplicate-choice":
        inputs["proof_scope"]["options"].append("range")
    elif mutation == "base-type":
        inputs["proofkit_base_sha"]["type"] = "boolean"
    elif mutation == "base-required":
        inputs["proofkit_base_sha"]["required"] = True
    else:
        inputs["proofkit_base_sha"]["default"] = "HEAD"
    with pytest.raises(ValueError, match="native proof input"):
        admit_native_source(_yaml(native))


def test_generated_self_artifacts_are_closed_under_real_runtime_decoders(
    self_ci_artifacts: SelfCiArtifacts,
) -> None:
    result = self_ci_artifacts
    source = parse_target_artifacts_source(
        result.outputs[".ci-coordinator/target-artifacts-source.v1.json"]
    )
    rendered = render_target_artifacts(source)
    for name, content in rendered.by_filename():
        assert result.outputs[f".ci-coordinator/{name}"] == content
    registry = parse_target_execution_registry(rendered.execution_registry)
    assert registry is not None and registry.admits(result.catalog)
    workflow = workflow_value(result.outputs[WORKFLOW_PATH], WORKFLOW_PATH)
    native_inputs = _native()["on"]["workflow_dispatch"]["inputs"]
    assert workflow["on"] == {
        "push": {"branches": ["ci-qualification/**"]},
        "workflow_dispatch": {
            "inputs": {
                "proof_scope": native_inputs["proof_scope"],
                "proofkit_base_sha": native_inputs["proofkit_base_sha"],
            }
        },
    }
    dispatch_inputs = workflow["on"]["workflow_dispatch"]["inputs"]
    assert set(dispatch_inputs) == {"proof_scope", "proofkit_base_sha"}
    assert dispatch_inputs["proof_scope"]["type"] == "choice"
    assert dispatch_inputs["proof_scope"]["options"] == ["full", "range"]
    assert dispatch_inputs["proof_scope"]["default"] == "full"
    assert dispatch_inputs["proofkit_base_sha"]["type"] == "string"
    assert "env" not in workflow and "defaults" not in workflow
    assert WORKFLOW_PATH in {item.path for item in registry.adapter_files}
    assert NATIVE_PATH not in result.outputs
    assert ".ci-coordinator/plan-trust-root.v1.json" not in result.outputs


def test_coverage_and_requester_helpers_cannot_be_independent_obligations(
    self_ci_artifacts: SelfCiArtifacts,
) -> None:
    result = self_ci_artifacts
    obligations = {item.obligation_id: item for item in result.catalog.obligations}
    assert obligations["python-native-coverage"].required_witness_ids == COVERAGE_JOBS
    assert obligations["requester-negative-contract"].required_witness_ids == tuple(
        sorted(REQUESTER_JOBS)
    )
    assert not set((*COVERAGE_JOBS, *REQUESTER_JOBS)) & obligations.keys()
    assert {item.obligation_id for item in obligations.values() if item.omit_allowed} == {
        "utility-config",
        "utility-go-static",
    }
    assert all(
        not item.responsibility_risk_classes for item in obligations.values() if item.omit_allowed
    )
    workflow = workflow_value(result.outputs[WORKFLOW_PATH], WORKFLOW_PATH)
    reason = workflow["jobs"][REQUESTER_ASSERTION_ID]
    assert reason["needs"] == ["plan", "trusted-plan-request-entrypoint"]
    assert reason["steps"][0]["env"] == {
        "TRUSTED_PLAN_REQUEST_REASON": (
            "${{ needs.trusted-plan-request-entrypoint.outputs.reason }}"
        )
    }
    assert reason["steps"][0]["run"] == (
        'set -euo pipefail\ntest "${TRUSTED_PLAN_REQUEST_REASON}" = plan_url_invalid\n'
    )


def test_control_template_drift_cannot_redefine_the_runtime_gate() -> None:
    native = admit_native_source(read_regular(ROOT, NATIVE_PATH))
    template = workflow_value(
        read_regular(ROOT, CONTROL_TEMPLATE_PATH), ".github/workflows/full-check.yml"
    )
    template["jobs"]["full-check-gate"]["steps"][-1]["run"] = "true"
    node = json.loads(read_regular(ROOT, "package.json"))["engines"]["node"]
    with pytest.raises(ValueError, match="runtime-owned control"):
        render_workflow(native, _yaml(template), node)


def test_copied_matrix_artifacts_and_static_authority_preserve_native_operands(
    self_ci_artifacts: SelfCiArtifacts,
) -> None:
    source = admit_native_source(read_regular(ROOT, NATIVE_PATH))
    result = self_ci_artifacts
    workflow = workflow_value(result.outputs[WORKFLOW_PATH], WORKFLOW_PATH)
    jobs = workflow["jobs"]
    for job_id, native in source.jobs.items():
        projected = jobs[job_id]
        for field in (
            "steps",
            "strategy",
            "runs-on",
            "uses",
            "with",
            "permissions",
            "timeout-minutes",
        ):
            assert projected.get(field) == native.get(field), (job_id, field)
        assert "plan" in projected["needs"]
    assert jobs["postgres-witness"]["needs"] == [
        "native-test-plan",
        "native-test-shards",
        "plan",
    ]
    assert (
        "ci-coordinator/ci-coordinator.cjs validate-gate"
        in (jobs[COORDINATED_GATE_ID]["steps"][-1]["run"])
    )


def test_family_settings_reject_unknown_family_and_unowned_path_language(
    self_ci_artifacts: SelfCiArtifacts,
) -> None:
    result = self_ci_artifacts
    workflow = workflow_value(result.outputs[WORKFLOW_PATH], WORKFLOW_PATH)
    ids = {item.profile_id for item in result.catalog.execution_profiles}
    jobs = {key: value for key, value in workflow["jobs"].items() if key in ids}
    with pytest.raises(ValueError, match="unknown family"):
        validation_catalog(jobs, {"foreign": FamilySettings()})
    with pytest.raises(ValueError, match="applicability"):
        FamilySettings(responsibility_paths=("../outside",), omit_allowed=True)


def test_source_inventory_binds_native_aggregate_and_every_generator_input(
    self_ci_artifacts: SelfCiArtifacts,
) -> None:
    result = self_ci_artifacts
    inventory = json.loads(result.outputs[".ci-coordinator/self-ci-inventory.v1.json"])
    assert inventory["nativeAggregateOutputPredicates"] == [
        {
            "expression": "${{ needs.trusted-plan-request-entrypoint.outputs.reason }}",
            "equals": "plan_url_invalid",
        }
    ]
    assert inventory["separateManualJobs"] == [
        "api-contract-exploration",
        "serial-qualification",
    ]
    assert {row["path"] for row in inventory["sourceInputs"]} == result.inputs.keys()


def test_generated_gate_requires_complete_fallback_or_closed_selected_results(
    tmp_path: Path,
    self_ci_artifacts: SelfCiArtifacts,
) -> None:
    result = self_ci_artifacts
    registry_path = tmp_path / "registry.json"
    registry_path.write_bytes(result.outputs[".ci-coordinator/execution-registry.v1.json"])
    bundle_path = tmp_path / "control.cjs"
    bundle_path.write_bytes(result.outputs[".ci-coordinator/ci-coordinator.cjs"])
    registry = json.loads(registry_path.read_bytes())
    job_ids = [row["jobId"] for row in registry["workflows"][0]["executionJobs"]]
    environment = {
        "PATH": os.environ["PATH"],
        "CI_OWNER": "research-engineering",
        "CI_REPO": "ci-coordinator",
        "CI_WORKFLOW_PATH": WORKFLOW_PATH,
        "CI_WORKFLOW_REF": (
            "research-engineering/ci-coordinator/"
            + WORKFLOW_PATH
            + "@refs/heads/ci-qualification/test"
        ),
        "CI_TARGET_REGISTRY_PATH": str(registry_path),
        "CI_PLAN_RESULT": "failure",
        "CI_PLAN_VALID": "false",
        "CI_FALLBACK": "true",
        "CI_SELECTED_JOBS_JSON": "[]",
    }

    def run(outcomes: dict[str, str]) -> int:
        return subprocess.run(
            ["node", str(bundle_path), "validate-gate"],
            env={**environment, "CI_STATIC_JOB_RESULTS_JSON": json.dumps(outcomes)},
            timeout=10,
            check=False,
            capture_output=True,
        ).returncode

    outcomes = dict.fromkeys(job_ids, "success")
    assert run(outcomes) == 0
    for job_id in job_ids:
        for unsuccessful in ("cancelled", "failure", "skipped"):
            assert run(outcomes | {job_id: unsuccessful}) != 0, (job_id, unsuccessful)
    assert run({key: value for key, value in outcomes.items() if key != job_ids[0]}) != 0
    assert run(outcomes | {"foreign": "success"}) != 0
    selected = registry["workflows"][0]["requiredJobIds"]
    assert set(job_ids) - set(selected) == {"utility-config", "utility-go-static"}
    environment.update(
        {
            "CI_PLAN_RESULT": "success",
            "CI_PLAN_VALID": "true",
            "CI_FALLBACK": "false",
            "CI_SELECTED_JOBS_JSON": json.dumps(selected),
        }
    )
    selected_outcomes = {
        job_id: "success" if job_id in selected else "skipped" for job_id in job_ids
    }
    assert run(selected_outcomes) == 0
    assert run(selected_outcomes | {"postgres-witness": "skipped"}) != 0
    environment["CI_SELECTED_JOBS_JSON"] = json.dumps(
        [job_id for job_id in selected if job_id != "native-test-shards"]
    )
    assert run(selected_outcomes | {"native-test-shards": "skipped"}) != 0


@pytest.mark.parametrize("default_branch", ["master", "main"])
def test_policy_materializer_preserves_native_observation_and_dynamic_planning(
    default_branch: str,
    self_ci_artifacts: SelfCiArtifacts,
) -> None:
    artifacts = self_ci_artifacts
    catalog = artifacts.catalog
    fixture_policy = policy_document(
        catalog,
        installation_id=100,
        repository_id=200,
        default_branch=default_branch,
        dependency_graph=artifacts.dependency_graph,
    )
    admission = admit_policy_document(fixture_policy, "json")
    assert isinstance(admission, ValidatedEpochDraft)
    assert admission.scope.installation_id == 100
    assert admission.scope.repository_id == 200
    repository = json.loads(fixture_policy)["repository"]
    rule = repository["rules"][0]
    assert len(repository["rules"]) == 1
    assert rule["on"] == {"event": "pull_request", "branches": [default_branch]}
    assert rule["mode"] == "observe"
    assert rule["expectedSignals"] == [
        {
            "kind": "workflow",
            "name": _native()["name"],
            "workflowFile": "python-persistence.yml",
            "source": "native",
            "requiredConclusion": "success",
            "required": True,
        }
    ]
    dynamic = repository["dynamicCi"]
    for field, value in catalog.to_identity_mapping().items():
        assert dynamic[field] == value
    assert dynamic["dependencyGraph"] == {
        "source": artifacts.dependency_graph["source"],
        "globalRiskPaths": artifacts.dependency_graph["globalRiskPaths"],
    }
    projection = project_dynamic_ci_planning(admission)
    assert projection is not None and projection.validation_catalog == catalog
    assert projection.global_risk_paths == tuple(artifacts.dependency_graph["globalRiskPaths"])

    invalid = json.loads(fixture_policy)
    invalid["repository"]["rules"][0]["on"]["branches"] = ["ci-qualification/**"]
    rejection = admit_policy_document(json.dumps(invalid).encode(), "json")
    assert not isinstance(rejection, ValidatedEpochDraft)
    assert rejection[0].code == "semantics.invalid"
    assert rejection[0].rule_id == "rule.default-branch-covered"
    assert rejection[0].instance_pointer == "/repository/rules/0/on/branches"
    with pytest.raises(ValueError, match="admission"):
        policy_document(catalog, installation_id=0, repository_id=200, default_branch="master")


def test_shared_preparation_never_shares_mutable_artifacts(
    self_ci_artifacts_factory: Callable[[], SelfCiArtifacts],
) -> None:
    first = self_ci_artifacts_factory()
    second = self_ci_artifacts_factory()
    assert first == second and first is not second
    first.outputs.clear()
    first.inputs.clear()
    first.dependency_graph["nodes"].clear()
    first.responsibility.settings.clear()
    first.responsibility.graph["nodes"].clear()
    first.responsibility.owner_inputs.clear()
    assert second == self_ci_artifacts_factory()
    assert second.outputs and second.inputs and second.dependency_graph["nodes"]
    assert second.responsibility.settings and second.responsibility.graph["nodes"]
    assert second.responsibility.owner_inputs


def test_shared_preparation_rejects_owner_drift_before_returning_a_copy(
    self_ci_artifacts_factory: Callable[[], SelfCiArtifacts],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    self_ci_artifacts_factory()
    original = read_regular

    def changed(root: Path, path: str) -> bytes:
        return b"changed owner" if path == NATIVE_PATH else original(root, path)

    monkeypatch.setattr("scripts.self_ci_generate.read_regular", changed)
    with pytest.raises(ValueError, match="owner inputs changed"):
        self_ci_artifacts_factory()


def test_shared_preparation_rejects_path_mode_drift_before_returning_a_copy(
    self_ci_artifacts_factory: Callable[[], SelfCiArtifacts],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = self_ci_artifacts_factory()
    changed = list(baseline.responsibility.path_inventory)
    path, mode = changed[0]
    changed[0] = (path, 0o100755 if mode == 0o100644 else 0o100644)
    monkeypatch.setattr(
        "scripts.self_ci_responsibility.path_inventory", lambda *_args: tuple(changed)
    )
    with pytest.raises(ValueError, match="finite path universe changed"):
        self_ci_artifacts_factory()


def test_shared_preparation_rejects_same_path_go_content_drift(
    self_ci_artifacts_factory: Callable[[], SelfCiArtifacts],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import ci_utility_inventory

    self_ci_artifacts_factory()
    original = ci_utility_inventory.source_text

    def changed(root: Path, path: str) -> str:
        content = original(root, path)
        return "//go:build custom\n\n" + content if path.endswith(".go") else content

    monkeypatch.setattr(ci_utility_inventory, "source_text", changed)
    with pytest.raises(ValueError, match="explicit build-configuration owner"):
        render_self_ci(ROOT)
    with pytest.raises(ValueError, match="explicit build-configuration owner"):
        self_ci_artifacts_factory()

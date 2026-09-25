from __future__ import annotations

import copy
import json

# The repository's test assertion exception is scoped to backend/tests.
from collections import Counter
from pathlib import Path
from typing import Final, cast

import pytest
from ruamel.yaml import YAML
from scripts.mutation.mutation_manifest import decode_mutation_manifest
from scripts.mutation.mutation_suite_specs import (
    EXECUTION_ENVELOPE_AUTHORITY_RELATIVE_PATHS,
    OPERATOR_WORKBENCH_TIMEOUT_MINUTES,
    SUITES,
    ExecutionGroup,
    InventoryEvidence,
    InventoryPolicy,
    JsonObject,
    SuiteName,
    _decorate_audit_persistence_report,
    assert_direct_mutation_job_execution_envelope,
    main,
    validate_inventory,
)
from scripts.proofkit_route_sources import load_route_authority
from scripts.quality_plan import load_quality_plan

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
MUTATION_DIR: Final = REPO_ROOT / "scripts" / "mutation"
SUITE_COMMAND_PREFIX: Final = "backend/.venv/bin/python -m scripts.mutation.mutation_suite_specs "


def _load_json_object(path: Path) -> JsonObject:
    value = cast(object, json.loads(path.read_text(encoding="utf-8")))
    assert isinstance(value, dict)
    return cast(JsonObject, value)


def test_module_cli_rejects_an_unknown_suite(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["unknown"]) == 2
    assert "usage: python -m scripts.mutation.mutation_suite_specs" in capsys.readouterr().err


def test_module_cli_preflights_every_current_suite(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["preflight"]) == 0
    assert capsys.readouterr().out == ""


def test_suite_configuration_is_a_canonical_data_table() -> None:
    projected = {
        name: {
            "command": spec.command_id,
            "dependencies": spec.config.dependencies,
            "manifest": spec.config.manifest_relative_path,
            "report": spec.config.report_id,
            "prefix": spec.config.temp_prefix,
            "policy": spec.inventory_policy,
        }
        for name, spec in SUITES.items()
    }
    assert projected == {
        "audit-json-resource-limits": {
            "command": "mutation.audit-json-resources",
            "dependencies": ("backend/.venv",),
            "manifest": "fixtures/conformance/v1/audit-json-resource-mutants.v1.json",
            "report": "ci-coordinator.audit-json-resource-mutation",
            "prefix": "ci-audit-json-mutation-",
            "policy": InventoryPolicy.NONE,
        },
        "audit-persistence-byte-limits": {
            "command": "mutation.audit-persistence-bytes",
            "dependencies": ("backend/.venv",),
            "manifest": "fixtures/conformance/v1/audit-persistence-byte-mutants.v1.json",
            "report": "ci-coordinator.audit-persistence-byte-mutation",
            "prefix": "ci-audit-persistence-byte-mutation-",
            "policy": InventoryPolicy.AUDIT_PERSISTENCE_BYTES,
        },
        "frontend-safety-kernels": {
            "command": "mutation.frontend-safety-kernels",
            "dependencies": ("frontend/node_modules", "node_modules"),
            "manifest": "fixtures/conformance/v1/frontend-safety-kernel-mutants.v1.json",
            "report": "ci-coordinator.frontend-safety-kernel-mutation",
            "prefix": "ci-frontend-safety-mutation-",
            "policy": InventoryPolicy.FRONTEND_SAFETY,
        },
        "python-capacity-evidence": {
            "command": "mutation.python-capacity-evidence",
            "dependencies": ("backend/.venv",),
            "manifest": ("fixtures/conformance/v1/python-capacity-evidence-mutants.v1.json"),
            "report": "ci-coordinator.python-capacity-evidence-mutation",
            "prefix": "ci-python-capacity-evidence-mutation-",
            "policy": InventoryPolicy.CAPACITY_EVIDENCE,
        },
        "python-config-policy-admission": {
            "command": "mutation.python-config-policy-admission",
            "dependencies": ("backend/.venv",),
            "manifest": ("fixtures/conformance/v1/python-config-policy-admission-mutants.v1.json"),
            "report": "ci-coordinator.python-config-policy-admission-mutation",
            "prefix": "ci-python-config-policy-admission-mutation-",
            "policy": InventoryPolicy.CONFIG_POLICY,
        },
        "python-database-compatibility": {
            "command": "mutation.python-database-compatibility",
            "dependencies": ("backend/.venv",),
            "manifest": ("fixtures/conformance/v1/python-database-compatibility-mutants.v1.json"),
            "report": "ci-coordinator.python-database-compatibility-mutation",
            "prefix": "ci-python-database-compatibility-mutation-",
            "policy": InventoryPolicy.DATABASE_COMPATIBILITY,
        },
        "python-http-admission": {
            "command": "mutation.python-http-admission",
            "dependencies": ("backend/.venv",),
            "manifest": "fixtures/conformance/v1/python-http-admission-mutants.v1.json",
            "report": "ci-coordinator.python-http-admission-mutation",
            "prefix": "ci-python-http-admission-mutation-",
            "policy": InventoryPolicy.HTTP_ADMISSION,
        },
        "python-plan-identity": {
            "command": "mutation.python-plan-identity",
            "dependencies": ("backend/.venv",),
            "manifest": "fixtures/conformance/v1/python-plan-identity-mutants.v1.json",
            "report": "ci-coordinator.python-plan-identity-mutation",
            "prefix": "ci-python-plan-identity-mutation-",
            "policy": InventoryPolicy.NONE,
        },
        "python-persistence": {
            "command": "mutation.python-persistence",
            "dependencies": ("backend/.venv",),
            "manifest": "fixtures/conformance/v1/python-persistence-mutants.v1.json",
            "report": "ci-coordinator.python-persistence-mutation",
            "prefix": "ci-python-persistence-mutation-",
            "policy": InventoryPolicy.NONE,
        },
    }
    assert {name: spec.execution_group for name, spec in SUITES.items()} == {
        name: (
            ExecutionGroup.OPERATOR_WORKBENCH
            if name == "frontend-safety-kernels"
            else ExecutionGroup.PERSISTENCE_MUTATION
        )
        for name in SUITES
    }
    for spec in SUITES.values():
        assert spec.config.authority_relative_paths == (
            "scripts/mutation/mutation_suite_specs.py",
            *EXECUTION_ENVELOPE_AUTHORITY_RELATIVE_PATHS,
            spec.config.manifest_relative_path,
        )
    assert EXECUTION_ENVELOPE_AUTHORITY_RELATIVE_PATHS == (
        "scripts/quality_plan.py",
        "scripts/proofkit_common.py",
        "proofkit/quality-plan.v1.json",
        "proofkit/witness-plan-input.json",
    )


def test_plan_identity_manifest_has_exactly_eight_causal_witnesses() -> None:
    spec = SUITES["python-plan-identity"]
    path = REPO_ROOT / spec.config.manifest_relative_path
    manifest = decode_mutation_manifest(path.read_bytes())
    expected_ids = ["PI01", "PI02", "PI03", "PI04", "PI05", "PI06", "PI07", "PI08"]
    assert manifest["expectedKilled"] == 8
    assert manifest["expectedMutantIds"] == expected_ids
    assert [mutant["id"] for mutant in manifest["mutants"]] == expected_ids
    assert manifest["timeoutMs"] == 5000
    assert manifest["outerTimeoutMs"] == 140000
    quality = load_quality_plan()
    assert quality.commands[spec.command_id].timeout_ms == 140000
    assert spec.command_id in quality.branch_head_command_ids
    direct_test = "test_trusted_identity_binding_rejects_each_mismatched_operand"
    issuer_test = "test_signed_issuer_rejects_identity_mismatch_before_effects"
    run_guard = (
        "    if identity.run_id != request.workflow_run_id "
        "or identity.run_attempt != request.run_attempt:\n"
    )
    expected = (
        (
            "trusted_identity.py",
            '    if identity.repository != request.owner + "/" + request.repository:\n',
            "    if False:\n",
            direct_test,
            "repository",
        ),
        (
            "trusted_identity.py",
            "    if identity.repository_id != request.repository_id:\n",
            "    if False:\n",
            direct_test,
            "repository_id",
        ),
        (
            "trusted_identity.py",
            "    if identity.ref != request.ref:\n",
            "    if False:\n",
            direct_test,
            "ref",
        ),
        (
            "trusted_identity.py",
            run_guard,
            "    if identity.run_attempt != request.run_attempt:\n",
            direct_test,
            "run_id",
        ),
        (
            "trusted_identity.py",
            run_guard,
            "    if identity.run_id != request.workflow_run_id:\n",
            direct_test,
            "run_attempt",
        ),
        (
            "trusted_identity.py",
            "    if identity.event_name != request.event_name:\n",
            "    if False:\n",
            direct_test,
            "event_name",
        ),
        (
            "trusted_identity.py",
            "    if identity.execution_sha != request.execution_sha:\n",
            "    if False:\n",
            direct_test,
            "execution_sha",
        ),
        (
            "issuer.py",
            "        binding_error = bind_trusted_identity(request, identity)\n",
            "        binding_error = None\n",
            issuer_test,
            "repository_id",
        ),
    )
    for mutant, (filename, original, replacement, test, operand) in zip(
        manifest["mutants"], expected, strict=True
    ):
        assert mutant["file"] == f"backend/src/ci_coordinator/plan_issuance/{filename}"
        assert mutant["original"] == original
        assert mutant["replacement"] == replacement
        assert mutant["requirementIds"] == ["REQ-CI-CORE-001", "REQ-CI-RUNTIME-006"]
        assert mutant["command"] == [
            "backend/.venv/bin/python",
            "-m",
            "pytest",
            f"backend/tests/unit/test_plan_issuance.py::{test}[{operand}]",
            "-q",
        ]
        assert mutant["environment"] == {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": "backend/src",
        }


def _direct_suite_invocations(mutation_job: JsonObject) -> tuple[str, ...]:
    steps = mutation_job.get("steps")
    assert type(steps) is list
    invocations: list[str] = []
    for step in steps:
        if type(step) is not dict:
            continue
        run = step.get("run")
        if not isinstance(run, str):
            continue
        canonical_count = 0
        for raw_line in run.splitlines():
            line = raw_line.strip()
            if not line.startswith(SUITE_COMMAND_PREFIX):
                continue
            invocations.append(line.removeprefix(SUITE_COMMAND_PREFIX))
            canonical_count += 1
        assert run.count(SUITE_COMMAND_PREFIX) == canonical_count
    return tuple(invocations)


def _assert_direct_provider_job_projection(mutation_job: JsonObject) -> None:
    steps = mutation_job.get("steps")
    assert type(steps) is list
    governed_indexes = [
        index
        for index, step in enumerate(steps)
        if type(step) is dict and step.get("name") == "Run governed mutation witnesses"
    ]
    assert len(governed_indexes) == 1
    strategy = mutation_job["strategy"]
    assert isinstance(strategy, dict) and strategy["fail-fast"] is False
    assert Counter(strategy["matrix"]["suite"]) == Counter(
        name for name in SUITES if name != "frontend-safety-kernels"
    )
    assert mutation_job["needs"] == ["repository-quality"]
    step = steps[governed_indexes[0]]
    assert step["env"] == {"SUITE_NAME": "${{ matrix.suite }}"}
    assert _direct_suite_invocations(mutation_job) == ('"$SUITE_NAME"',)


def _assert_frontend_job_projection(frontend_job: JsonObject) -> None:
    steps = frontend_job.get("steps")
    assert isinstance(steps, list)
    names = [step.get("name") for step in steps if isinstance(step, dict)]
    mutation_name = "Run frontend safety mutation witnesses"
    assert names.count(mutation_name) == 1
    assert (
        names.index("Run frontend static, unit, and production witnesses")
        < names.index(mutation_name)
        < names.index("Run browser rendering and accessibility witnesses")
    )
    assert _direct_suite_invocations(frontend_job) == ("frontend-safety-kernels",)


def test_direct_provider_job_has_a_complete_bounded_suite_projection() -> None:
    workflow = YAML(typ="safe").load(REPO_ROOT / ".github/workflows/python-persistence.yml")
    assert type(workflow) is dict
    jobs = workflow["jobs"]
    assert type(jobs) is dict
    mutation_job = jobs["persistence-mutation"]
    frontend_job = jobs["operator-workbench"]
    assert type(mutation_job) is dict
    assert type(frontend_job) is dict
    assert frontend_job["timeout-minutes"] == OPERATOR_WORKBENCH_TIMEOUT_MINUTES
    quality_plan = load_quality_plan()
    assert mutation_job["timeout-minutes"] == quality_plan.direct_mutation_job_timeout_minutes
    _assert_direct_provider_job_projection(cast(JsonObject, mutation_job))
    _assert_frontend_job_projection(cast(JsonObject, frontend_job))
    assert _direct_suite_invocations(jobs["repository-quality"]) == ("preflight",)


@pytest.mark.parametrize("step_name", ["Run governed mutation witnesses", "Other step"])
def test_direct_provider_job_projection_rejects_a_second_invocation_step(
    step_name: str,
) -> None:
    mutation_job: JsonObject = {
        "needs": ["repository-quality"],
        "strategy": {
            "fail-fast": False,
            "matrix": {"suite": [name for name in SUITES if name != "frontend-safety-kernels"]},
        },
        "steps": [
            {
                "name": "Run governed mutation witnesses",
                "env": {"SUITE_NAME": "${{ matrix.suite }}"},
                "run": f'{SUITE_COMMAND_PREFIX}"$SUITE_NAME"',
            },
        ],
    }
    _assert_direct_provider_job_projection(mutation_job)
    steps = mutation_job["steps"]
    assert isinstance(steps, list)
    steps.append(
        {
            "name": step_name,
            "run": f"{SUITE_COMMAND_PREFIX}python-persistence",
        }
    )

    with pytest.raises(AssertionError):
        _assert_direct_provider_job_projection(mutation_job)


def test_direct_provider_job_envelope_fails_closed() -> None:
    quality_plan = load_quality_plan()
    available_ms = quality_plan.direct_mutation_job_timeout_minutes * 60_000
    admitted_suite_ms = available_ms - quality_plan.direct_mutation_job_reserve_ms
    assert_direct_mutation_job_execution_envelope(
        (admitted_suite_ms, admitted_suite_ms),
        quality_plan=quality_plan,
    )
    with pytest.raises(RuntimeError, match="does not preserve its execution reserve"):
        assert_direct_mutation_job_execution_envelope(
            (admitted_suite_ms + 1,),
            quality_plan=quality_plan,
        )


@pytest.mark.parametrize(
    ("suite_name", "expected_count"),
    [
        ("audit-persistence-byte-limits", 17),
        ("python-config-policy-admission", 75),
        ("python-database-compatibility", 36),
        ("frontend-safety-kernels", 12),
        ("python-capacity-evidence", 27),
        ("python-http-admission", 22),
    ],
)
def test_current_inventory_is_admitted(suite_name: str, expected_count: int) -> None:
    spec = SUITES[cast(SuiteName, suite_name)]
    manifest = _load_json_object(REPO_ROOT / spec.config.manifest_relative_path)
    evidence = validate_inventory(spec.inventory_policy, manifest)
    assert len(evidence.canonical_ids) == expected_count
    assert evidence.invalid_mutants == ()


def test_every_mutant_requirement_owner_routes_its_suite_command() -> None:
    document = load_route_authority(repo_root=REPO_ROOT).binding_projection
    raw_bindings = document["bindings"]
    assert isinstance(raw_bindings, list)
    routed = {
        (witness_path, requirement_id, command_id)
        for value in raw_bindings
        if isinstance(value, dict)
        for binding in [cast(JsonObject, value)]
        for witness_path in [binding.get("witnessPath")]
        if isinstance(witness_path, str)
        for requirement_id in [binding.get("requirementId")]
        if isinstance(requirement_id, str)
        for command_ids in [binding.get("commandIds")]
        if isinstance(command_ids, list)
        for command_id in command_ids
        if isinstance(command_id, str)
    }
    expected: set[tuple[str, str, str]] = set()
    for spec in SUITES.values():
        manifest = _load_json_object(REPO_ROOT / spec.config.manifest_relative_path)
        mutants = manifest["mutants"]
        assert isinstance(mutants, list)
        for value in mutants:
            assert isinstance(value, dict)
            requirement_ids = value.get("requirementIds")
            assert isinstance(requirement_ids, list)
            for requirement_id in requirement_ids:
                assert isinstance(requirement_id, str)
                expected.add(
                    (
                        spec.config.manifest_relative_path,
                        requirement_id,
                        spec.command_id,
                    )
                )

    assert routed >= expected, sorted(expected - routed)


@pytest.mark.parametrize(
    "suite_name",
    [
        "audit-persistence-byte-limits",
        "python-config-policy-admission",
        "python-database-compatibility",
        "frontend-safety-kernels",
        "python-capacity-evidence",
        "python-http-admission",
    ],
)
def test_inventory_expected_count_mismatch_fails_closed(suite_name: str) -> None:
    spec = SUITES[cast(SuiteName, suite_name)]
    manifest = _load_json_object(REPO_ROOT / spec.config.manifest_relative_path)
    manifest["expectedKilled"] = -1
    with pytest.raises(RuntimeError, match="inventory is not canonical"):
        validate_inventory(spec.inventory_policy, manifest)


def test_owner_class_coverage_must_be_unique_and_exhaustive() -> None:
    spec = SUITES["python-config-policy-admission"]
    manifest = _load_json_object(REPO_ROOT / spec.config.manifest_relative_path)
    coverage = manifest["ownerClassCoverage"]
    assert isinstance(coverage, dict)
    mutated = copy.deepcopy(manifest)
    mutated_coverage = mutated["ownerClassCoverage"]
    assert isinstance(mutated_coverage, dict)
    first_ids = next(iter(mutated_coverage.values()))
    assert isinstance(first_ids, list)
    first_ids.append("CP01")
    with pytest.raises(RuntimeError, match="not canonical and complete"):
        validate_inventory(InventoryPolicy.CONFIG_POLICY, mutated)


def test_proofkit_mutants_own_explicit_provider_independent_path_context() -> None:
    spec = SUITES["audit-json-resource-limits"]
    manifest = _load_json_object(REPO_ROOT / spec.config.manifest_relative_path)
    mutants = manifest["mutants"]
    assert isinstance(mutants, list)
    mutants_by_id = {
        mutant["id"]: mutant
        for value in mutants
        if isinstance(value, dict)
        for mutant in [cast(JsonObject, value)]
    }

    for mutant_id, changed_path in (
        ("M15-proof-owner-completeness", "proofkit/repo-profile.json"),
        ("M16-profile-compatibility-routing", "proofkit/routes/core.v2.json"),
    ):
        mutant = mutants_by_id[mutant_id]
        assert mutant["command"] == [
            "backend/.venv/bin/python",
            "-m",
            "scripts.proofkit_plan_check",
            "--changed-path",
            changed_path,
        ]
        assert mutant["environment"] == {
            "PROOFKIT_BASE_REF": "",
            "PROOFKIT_HEAD_REF": "",
        }


def test_invalid_inventory_member_requires_reason() -> None:
    spec = SUITES["audit-persistence-byte-limits"]
    manifest = _load_json_object(REPO_ROOT / spec.config.manifest_relative_path)
    mutants = manifest["mutants"]
    canonical_ids = manifest["canonicalInventoryIds"]
    expected_mutant_ids = manifest["expectedMutantIds"]
    assert isinstance(mutants, list)
    assert isinstance(canonical_ids, list)
    assert isinstance(expected_mutant_ids, list)
    invalid = cast(JsonObject, mutants.pop())
    invalid["status"] = "invalid"
    invalid["reason"] = ""
    manifest["invalidMutants"] = [invalid]
    manifest["expectedInvalidMutantIds"] = [invalid["id"]]
    expected_mutant_ids.pop()
    manifest["expectedKilled"] = len(mutants)
    assert canonical_ids == [
        *cast(list[str], expected_mutant_ids),
        invalid["id"],
    ]
    with pytest.raises(RuntimeError, match="require an explicit reason"):
        validate_inventory(InventoryPolicy.AUDIT_PERSISTENCE_BYTES, manifest)


def test_invalid_audit_inventory_decorates_report_and_fails_closed() -> None:
    invalid_mutant: JsonObject = {
        "id": "APB02",
        "status": "invalid",
        "reason": "witness unavailable",
    }
    evidence = InventoryEvidence(
        canonical_ids=("APB01", "APB02"),
        invalid_mutants=(invalid_mutant,),
    )
    report: dict[str, object] = {
        "state": "passed",
        "summary": {},
        "nonClaims": [],
    }

    _decorate_audit_persistence_report(report, evidence)

    assert report["state"] == "failed"
    assert report["inventoryState"] == "incomplete"
    assert report["summary"] == {
        "canonicalInventoryCount": 2,
        "declaredInvalidCount": 1,
        "supportedCount": 1,
    }
    assert report["inventory"] == {
        "canonicalIds": ["APB01", "APB02"],
        "invalid": [invalid_mutant],
    }
    assert report["nonClaims"] == [
        "Canonical inventory entries declared invalid are missing proof and never count as killed."
    ]

from __future__ import annotations

from pathlib import Path

import pytest
from scripts import proofkit_plan_check, proofkit_selective_plan
from scripts.proofkit_cli import resolve_proofkit_executable
from scripts.proofkit_common import JsonObject, read_json_object
from scripts.proofkit_plan_check import (
    BindingExpectation,
    assert_every_binding_routes,
    binding_expectations,
)
from scripts.proofkit_route_sources import load_route_authority
from scripts.proofkit_selective_contract import (
    RequiredCommandRoute,
    assert_admitted_plan,
    assert_required_commands_match_input,
    assert_selective_plan_contract,
)
from scripts.proofkit_selective_plan import REPO_ROOT, selective_gate_plan_input


@pytest.mark.parametrize(
    "source_path",
    (
        "README.md",
        "ROADMAP.md",
        "docs/features/new-diagram-owner.md",
        "docs/features/new-diagram-owner.mmd",
        "docs/features/new-diagram-owner.mermaid",
        "docs/features/new-diagram-owner.mdx",
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
        "scripts/diagram_future_helper.py",
        "scripts/diagram_process.py",
        "scripts/tests/test_diagram_future_helper.py",
        "scripts/tests/test_diagram_process.py",
        "frontend/tools/diagrams-future-helper.mjs",
        "frontend/tools/diagrams-semantic.mjs",
        "frontend/tools/diagrams-process-checks.mjs",
        "frontend/tools/diagrams-semantic-checks.mjs",
    ),
)
def test_each_diagram_input_routes_every_required_stage(source_path: str) -> None:
    plan = selective_gate_plan_input(base_ref="HEAD", paths=(source_path,), repo_root=REPO_ROOT)

    stages = {
        row["id"] for row in plan["baseCommands"] if row["reason"] == "documentation_diagram_input"
    }
    assert stages == {
        "documentation.diagrams-inventory",
        "documentation.diagrams",
        "documentation.diagrams-falsifiers",
        "documentation.diagrams-process",
    }


@pytest.mark.parametrize(
    "source_path",
    ("backend/src/ci_coordinator/kernel/canonical_json.py", "frontend/src/main.tsx", "notice.txt"),
)
def test_unrelated_change_does_not_select_diagram_stages(source_path: str) -> None:
    plan = selective_gate_plan_input(base_ref="HEAD", paths=(source_path,), repo_root=REPO_ROOT)

    assert not any(row["reason"] == "documentation_diagram_input" for row in plan["baseCommands"])


def test_independent_diagram_probe_rejects_disabled_input_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(proofkit_selective_plan, "is_diagram_input", lambda _path: False)
    monkeypatch.setattr(proofkit_plan_check, "DOCUMENTATION_DIAGRAM_PROBE_PATHS", ())
    monkeypatch.setattr(
        proofkit_plan_check,
        "DOCUMENTATION_DIAGRAM_UNBOUND_PROBE_PATHS",
        ("docs/features/diagram-routing-probe.md",),
    )
    witness_plan = read_json_object(REPO_ROOT / "proofkit/witness-plan-input.json")
    command_catalog = {row["id"]: " ".join(row["argv"]) for row in witness_plan["commands"]}
    bindings = load_route_authority(repo_root=REPO_ROOT).binding_projection

    with pytest.raises(ValueError, match=r"documentation diagram routing probe .* missing:"):
        proofkit_plan_check.assert_documentation_diagram_routes(
            command_catalog,
            bindings=bindings,
            base_bindings=load_route_authority(repo_root=REPO_ROOT, ref="HEAD").binding_projection,
            base_ref="HEAD",
            repo_root=REPO_ROOT,
            executable=resolve_proofkit_executable(),
        )


@pytest.mark.parametrize(
    ("source_path", "required_owners"),
    (
        (
            "backend/src/ci_coordinator/api/http/control_plane_authentication.py",
            (
                "REQ-CI-RUNTIME-044",
                "REQ-CI-RUNTIME-045",
                "REQ-CI-RUNTIME-050",
                "REQ-CI-RUNTIME-053",
            ),
        ),
        (
            "backend/src/ci_coordinator/integrations/github/request_admission.py",
            (
                "REQ-CI-RUNTIME-003",
                "REQ-CI-RUNTIME-018",
                "REQ-CI-RUNTIME-019",
                "REQ-CI-RUNTIME-026",
                "REQ-CI-RUNTIME-035",
                "REQ-CI-RUNTIME-038",
                "REQ-CI-RUNTIME-039",
                "REQ-CI-RUNTIME-042",
            ),
        ),
        (
            "backend/src/ci_coordinator/github_ingestion/event_common.py",
            ("REQ-CI-RUNTIME-038",),
        ),
        (
            "frontend/src/api/ciEconomics/sourceSchema.ts",
            ("REQ-CI-UI-018", "REQ-CI-UI-019"),
        ),
        (
            "scripts/proofkit_common.py",
            ("REQ-CI-CONTROL-001", "REQ-CI-CORE-018", "REQ-CI-PROOFKIT-003", "REQ-CI-PROOFKIT-009"),
        ),
        (
            "backend/tests/unit/integrations/test_github_request_binding.py",
            (
                "REQ-CI-RUNTIME-003",
                "REQ-CI-RUNTIME-018",
                "REQ-CI-RUNTIME-019",
                "REQ-CI-RUNTIME-026",
                "REQ-CI-RUNTIME-035",
                "REQ-CI-RUNTIME-038",
                "REQ-CI-RUNTIME-039",
                "REQ-CI-RUNTIME-042",
            ),
        ),
    ),
)
def test_extracted_rule_path_retains_its_consumer_requirements(
    source_path: str, required_owners: tuple[str, ...]
) -> None:
    plan = selective_gate_plan_input(base_ref="HEAD", paths=(source_path,), repo_root=REPO_ROOT)

    touched = plan["touchedRequirementWitnesses"]
    assert isinstance(touched, list)
    witness = next(item for item in touched if item["path"] == source_path)
    assert set(required_owners) <= set(witness["requirementIds"])


def test_path_index_preserves_combined_owners_commands_and_unbound_paths() -> None:
    source = "scripts/proofkit_selective_plan.py"
    spec = "docs/specs/ci-coordinator-core/requirements.v1.json"
    missing = "scripts/unbound-proof-index-witness.py"
    bindings: JsonObject = {
        "requirements": [
            {"requirementId": "REQ-A", "specPath": spec},
            {"requirementId": "REQ-B", "specPath": spec},
        ],
        "bindings": [
            {"requirementId": "REQ-B", "witnessPath": source, "commandIds": ["python.test"]},
            {
                "requirementId": "REQ-A",
                "witnessPath": source,
                "commandIds": ["python.lint", "python.test", "python.lint"],
            },
        ],
    }
    plan = selective_gate_plan_input(
        base_ref="HEAD",
        paths=(source, spec, missing, source),
        requirement_bindings=bindings,
        base_requirement_bindings=bindings,
    )
    touched = {row["path"]: row for row in plan["touchedRequirementWitnesses"]}
    assert set(touched) == {source, spec}
    assert touched[source]["requirementIds"] == ["REQ-A", "REQ-B"]
    assert touched[spec]["requirementIds"] == ["REQ-A", "REQ-B"]
    assert touched[source]["commands"] == [
        "backend/.venv/bin/python -m scripts.proofkit_admission verify",
        "backend/.venv/bin/python -m scripts.python_witness lint",
        "backend/.venv/bin/python -m scripts.python_witness test",
    ]
    assert touched[spec]["commands"] == [
        "backend/.venv/bin/python -m scripts.proofkit_admission verify",
        "backend/.venv/bin/python -m scripts.proofkit_requirements",
    ]
    assert [row["path"] for row in plan["unknownEdges"]] == [missing]

    bindings["bindings"].clear()
    updated = selective_gate_plan_input(
        base_ref="HEAD",
        paths=(source, spec),
        requirement_bindings=bindings,
        base_requirement_bindings=bindings,
    )
    assert [row["path"] for row in updated["touchedRequirementWitnesses"]] == [spec]
    assert [row["path"] for row in updated["unknownEdges"]] == [source]


def test_path_index_still_rejects_malformed_selected_command_ids() -> None:
    source = "scripts/proofkit_selective_plan.py"
    bindings: JsonObject = {
        "requirements": [],
        "bindings": [
            {"requirementId": "REQ-A", "witnessPath": source, "commandIds": [42]},
        ],
    }
    with pytest.raises(TypeError, match="binding command id must be a string"):
        selective_gate_plan_input(
            base_ref="HEAD",
            paths=(source,),
            requirement_bindings=bindings,
            base_requirement_bindings=bindings,
        )


@pytest.mark.parametrize(
    "workflow_path",
    (
        "fixtures/native-target-repository/.github/workflows/full-check.yml",
        "fixtures/native-target-repository/.github/workflows/native-full-check.yml",
        "fixtures/target-repository/.github/workflows/native-full-check.yml",
        ".devcontainer/post-create.sh",
        "docker/development/secret-entrypoint.sh",
    ),
)
def test_target_workflow_change_routes_workflow_lint(workflow_path: str) -> None:
    plan = selective_gate_plan_input(
        base_ref="HEAD",
        paths=(workflow_path,),
        repo_root=REPO_ROOT,
    )

    touched = plan["touchedRequirementWitnesses"]
    assert isinstance(touched, list)
    witness = next(item for item in touched if item["path"] == workflow_path)
    assert "backend/.venv/bin/python -m scripts.workflow_lint" in witness["commands"]


def test_target_control_source_change_routes_exact_bundle_check() -> None:
    source_path = "backend/src/ci_coordinator/target_artifacts/control_source/main.cjs"
    plan = selective_gate_plan_input(
        base_ref="HEAD",
        paths=(source_path,),
        repo_root=REPO_ROOT,
    )

    routes = plan["pathTriggeredCommands"]
    assert isinstance(routes, list)
    bundle_route = next(
        item for item in routes if item["command"]["id"] == "target-control-bundle.check"
    )
    assert (
        "backend/src/ci_coordinator/target_artifacts/control_source/**"
        in bundle_route["pathPatterns"]
    )


def test_control_plane_profile_change_routes_semantic_admission() -> None:
    profile_path = "docs/specs/ci-coordinator-control-plane/control-plane-profile.v1.json"
    plan = selective_gate_plan_input(
        base_ref="HEAD",
        paths=(profile_path,),
        repo_root=REPO_ROOT,
    )

    touched = plan["touchedRequirementWitnesses"]
    assert isinstance(touched, list)
    witness = next(item for item in touched if item["path"] == profile_path)
    assert "backend/.venv/bin/python -m scripts.python_witness test" in witness["commands"]


@pytest.mark.parametrize(
    ("field", "unexpected"),
    (("requirementIds", "REQ-CI-EXTRA-001"), ("commands", "python -m unexpected")),
)
def test_complete_binding_route_rejects_unowned_additions(field: str, unexpected: str) -> None:
    observed = {
        "touchedRequirementWitnesses": [
            {
                "commands": ["python -m expected"],
                "path": "owner.py",
                "requirementIds": ["REQ-CI-CORE-001"],
            }
        ]
    }
    witness = observed["touchedRequirementWitnesses"][0]
    assert isinstance(witness, dict)
    values = witness[field]
    assert isinstance(values, list)
    values.append(unexpected)

    with pytest.raises(ValueError, match="unexpected"):
        assert_every_binding_routes(
            observed,
            {
                "owner.py": BindingExpectation(
                    commands={"python -m expected"},
                    requirement_ids={"REQ-CI-CORE-001"},
                )
            },
        )


def test_complete_binding_route_rejects_unowned_paths() -> None:
    observed = {
        "touchedRequirementWitnesses": [
            {
                "commands": ["python -m expected"],
                "path": "owner.py",
                "requirementIds": ["REQ-CI-CORE-001"],
            },
            {
                "commands": ["python -m unexpected"],
                "path": "unowned.py",
                "requirementIds": ["REQ-CI-EXTRA-001"],
            },
        ]
    }

    with pytest.raises(ValueError, match="selective route paths differ"):
        assert_every_binding_routes(
            observed,
            {
                "owner.py": BindingExpectation(
                    commands={"python -m expected"},
                    requirement_ids={"REQ-CI-CORE-001"},
                )
            },
        )


def test_complete_binding_route_rejects_duplicate_paths() -> None:
    route = {
        "commands": ["python -m expected"],
        "path": "owner.py",
        "requirementIds": ["REQ-CI-CORE-001"],
    }

    with pytest.raises(ValueError, match=r"repeated route path owner\.py"):
        assert_every_binding_routes(
            {"touchedRequirementWitnesses": [route, route]},
            {"owner.py": BindingExpectation()},
        )


def test_binding_expectations_include_source_owned_requirements(tmp_path: Path) -> None:
    source_path = "docs/specs/example/requirements.v1.json"
    witness_path = "scripts/example_witness.py"
    for path in (source_path, witness_path):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()

    expectations = binding_expectations(
        [
            {
                "commandIds": ["example.check"],
                "requirementId": "REQ-EXAMPLE-001",
                "witnessId": "example-witness",
                "witnessPath": witness_path,
            }
        ],
        [
            {
                "requirementId": "REQ-EXAMPLE-001",
                "specPath": source_path,
            },
            {
                "requirementId": "REQ-EXAMPLE-002",
                "specPath": source_path,
            },
        ],
        {
            "example.check": "python -m scripts.example_witness",
            "proofkit.verify": "agentic-proofkit verify --input proofkit-input.json",
            "requirements.admission": "python -m scripts.proofkit_requirements",
        },
        repo_root=tmp_path,
    )

    assert expectations == {
        source_path: BindingExpectation(
            commands={
                "agentic-proofkit verify --input proofkit-input.json",
                "python -m scripts.proofkit_requirements",
            },
            requirement_ids={"REQ-EXAMPLE-001", "REQ-EXAMPLE-002"},
        ),
        witness_path: BindingExpectation(
            commands={
                "agentic-proofkit verify --input proofkit-input.json",
                "python -m scripts.example_witness",
            },
            requirement_ids={"REQ-EXAMPLE-001"},
        ),
    }


def test_selective_plan_rejects_incomplete_success_shape() -> None:
    with pytest.raises(ValueError, match="output keys differ"):
        assert_admitted_plan(
            {"failures": [], "planState": "ok", "unknownEdges": []},
            "probe",
            input_value={},
        )


def test_selective_plan_rejects_incomplete_failure_shape() -> None:
    with pytest.raises(ValueError, match="output keys differ"):
        assert_selective_plan_contract(
            {
                "failures": ["unbound proof-like path"],
                "planState": "fail_closed",
                "unknownEdges": [{"path": "unbound.py"}],
            },
            "probe",
            input_value={},
        )


def test_selective_plan_rejects_missing_executable_command_route() -> None:
    input_value = _minimal_selective_input()
    report = _minimal_selective_report()
    report["requiredCommands"] = []

    with pytest.raises(ValueError, match="required command routes differ"):
        assert_required_commands_match_input(report, input_value, label="probe")


def test_selective_plan_rejects_unexpected_executable_command_route() -> None:
    input_value = _minimal_selective_input()
    report = _minimal_selective_report()
    required_commands = report["requiredCommands"]
    assert isinstance(required_commands, list)
    required_commands.append(
        {
            "command": "python -m unexpected",
            "id": "unexpected",
            "reason": "unexpected",
        }
    )

    with pytest.raises(ValueError, match="unexpected"):
        assert_required_commands_match_input(report, input_value, label="probe")


def test_selective_plan_rejects_duplicate_executable_command_route() -> None:
    input_value = _minimal_selective_input()
    report = _minimal_selective_report()
    required_commands = report["requiredCommands"]
    assert isinstance(required_commands, list)
    required_commands.append(required_commands[0])

    with pytest.raises(ValueError, match="repeated a required command route"):
        assert_required_commands_match_input(report, input_value, label="probe")


@pytest.mark.parametrize(
    ("route_id", "field", "replacement"),
    (
        ("proofkit.verify", "command", "proofkit incomplete"),
        ("proofkit.verify", "id", "proofkit.foreign"),
        ("proofkit.verify", "reason", "foreign_reason"),
        ("proofkit.verify", "sourcePath", "foreign.py"),
        ("text-policy", "commandOwnership", "caller_owned_external"),
    ),
)
def test_selective_plan_rejects_each_changed_executable_route_component(
    route_id: str,
    field: str,
    replacement: str,
) -> None:
    input_value = _minimal_selective_input()
    report = _minimal_selective_report()
    required_commands = report["requiredCommands"]
    assert isinstance(required_commands, list)
    route = next(item for item in required_commands if item["id"] == route_id)
    route[field] = replacement

    with pytest.raises(ValueError, match="required command routes differ"):
        assert_required_commands_match_input(report, input_value, label="probe")


def _minimal_selective_input() -> dict[str, object]:
    return {
        "artifactIntegrityPolicies": [],
        "baseCommands": [{"command": "proofkit verify", "id": "proofkit.verify", "reason": "base"}],
        "changedPaths": [],
        "dependencyFreshness": {"command": "python install", "paths": []},
        "fallbackCoverage": [],
        "fullWorkspaceCommand": None,
        "generatedArtifactRules": [],
        "ignoredProofLikePaths": [],
        "packageCommands": [],
        "pathTriggeredCommands": [],
        "proofLikePathPatterns": ["proofkit/**"],
        "publicApi": {"command": "python package", "touched": False},
        "requirementImpact": {"command": "proofkit verify", "touched": False},
        "scanObligation": {
            "command": "proofkit text-policy",
            "commandId": "text-policy",
            "commandOwnership": "proofkit_text_policy",
            "reason": "text_policy",
        },
        "touchedRequirementWitnesses": [],
    }


def _minimal_selective_report() -> dict[str, object]:
    expected = {
        RequiredCommandRoute(
            command="proofkit verify",
            command_id="proofkit.verify",
            command_ownership=None,
            reason="base",
            source_path=None,
        ),
        RequiredCommandRoute(
            command="proofkit text-policy",
            command_id="text-policy",
            command_ownership="proofkit_text_policy",
            reason="text_policy",
            source_path=None,
        ),
    }
    return {
        "requiredCommands": [
            {
                "command": route.command,
                **(
                    {"commandOwnership": route.command_ownership}
                    if route.command_ownership is not None
                    else {}
                ),
                "id": route.command_id,
                "reason": route.reason,
            }
            for route in expected
        ]
    }

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType
from typing import Literal

import pytest

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.execution_orchestration import (
    CONTROL_INVOCATION_JOB_ID,
    LOCAL_PLAN_REQUEST_WORKFLOW_REF,
    TargetJobBinding,
    TargetWorkflowBinding,
    parse_target_execution_registry,
)
from ci_coordinator.github_ingestion import load_bundled_profile
from ci_coordinator.workflow_discovery import (
    AdoptionAssessment,
    AdoptionRequiredChange,
    AdoptionTargetJob,
    AdoptionTargetProjection,
    AdoptionTargetWorkflow,
    assess_workflow_adoption,
)
from ci_coordinator.workflow_discovery import adoption as adoption_module
from ci_coordinator.workflow_discovery._syntax_fields import PROPOSAL_EVENT_ACTIONS
from ci_coordinator.workflow_discovery._yaml_preflight import preflight_yaml_events
from ci_coordinator.workflow_discovery.adoption_target import INVOCATION_CLASSIFIER_JOB_ID
from ci_coordinator.workflow_discovery.graph import analyze_call_graph
from ci_coordinator.workflow_discovery.graph_model import GraphAnalysis
from ci_coordinator.workflow_discovery.outcomes import (
    WorkflowDiscoveryCompleted,
    WorkflowDiscoveryForbidden,
)
from ci_coordinator.workflow_discovery.parser import parse_workflow
from ci_coordinator.workflow_discovery.report import PARSER_VERSION
from ci_coordinator.workflow_discovery.service import WorkflowDiscoveryService
from ci_coordinator.workflow_discovery.source import (
    RepositoryIdentity,
    RepositoryWorkflowSnapshot,
    WorkflowSource,
    WorkflowSourceFailure,
    git_blob_sha1,
)
from ci_coordinator.workflow_discovery.summary import (
    JobSummary,
    ParsedWorkflow,
    WorkflowParseFailure,
)

SCOPE = RepositoryScope(7, 11)
REVISION = "a" * 40
REPO_ROOT = Path(__file__).resolve().parents[4]


def test_parser_closes_every_predicate_without_retaining_run_content() -> None:
    source = _source(
        """\
name: CI
on:
  push:
permissions:
  contents: read
jobs:
  test:
    name: Full CI
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: echo do-not-return-this-value
"""
    )

    result = parse_workflow(
        source,
        scope=SCOPE,
        revision=REVISION,
        default_branch="master",
    )

    assert isinstance(result, ParsedWorkflow)
    assert len(result.facts) + len(result.unknowns) == 32
    assert result.summary.triggers == ("push",)
    assert result.summary.jobs[0].provider_signal_name == "Full CI"
    assert "do-not-return-this-value" not in repr(result)
    assert all(item.provenance.location.line >= 1 for item in result.facts)
    assert all(item.provenance.location.line >= 1 for item in result.unknowns)


@pytest.mark.parametrize(
    ("document", "code"),
    [
        ("jobs: &jobs {}\ncopy: *jobs\n", "anchor_forbidden"),
        ("jobs: {}\njobs: {}\n", "duplicate_key"),
        ("name: !unsafe value\njobs: {}\n", "custom_tag_forbidden"),
        ("jobs: {}\n---\njobs: {}\n", "multiple_documents"),
    ],
)
def test_parser_rejects_non_json_yaml_features(document: str, code: str) -> None:
    result = parse_workflow(
        _source(document),
        scope=SCOPE,
        revision=REVISION,
        default_branch="master",
    )

    assert isinstance(result, WorkflowParseFailure)
    assert result.code == code
    assert result.unknown.reason == code


def test_event_preflight_enforces_depth_before_node_composition() -> None:
    failure = preflight_yaml_events(
        "value: [[[1]]]",
        max_events=100,
        max_nodes=100,
        max_depth=2,
        max_scalar_bytes=100,
    )

    assert failure is not None
    assert failure.code == "depth_limit_exceeded"


@pytest.mark.parametrize(
    ("coordinate", "expected_kind", "expected_status", "immutable"),
    [
        (
            f"example/shared/.github/workflows/python.yml@{'b' * 40}",
            "remote",
            "remote",
            True,
        ),
        ("example/shared/.github/workflows/python.yaml@main", "remote", "remote", False),
        ("example/shared/.github/workflows/nested/python.yml@main", "unknown", "invalid", None),
        ("example/shared/.github/workflows/python.yml@", "unknown", "invalid", None),
        ("/shared/.github/workflows/python.yml@main", "unknown", "invalid", None),
    ],
)
def test_remote_workflow_coordinates_use_the_exact_provider_grammar(
    coordinate: str,
    expected_kind: str,
    expected_status: str,
    immutable: bool | None,
) -> None:
    parsed = parse_workflow(
        _source(f"name: CI\non: push\njobs:\n  call:\n    uses: {coordinate}\n"),
        scope=SCOPE,
        revision=REVISION,
        default_branch="master",
    )
    assert isinstance(parsed, ParsedWorkflow)

    edge = analyze_call_graph((parsed,)).edges[0]

    assert (edge.kind, edge.status, edge.remote_ref_immutable) == (
        expected_kind,
        expected_status,
        immutable,
    )


def test_forbidden_scope_performs_no_snapshot_read() -> None:
    reader = _Reader(_snapshot(_source(_valid_document())))
    service = WorkflowDiscoveryService(authorizer=_Authorizer(False), reader=reader)

    result = asyncio.run(service(actor="operator", scope=SCOPE, revision=None))

    assert isinstance(result, WorkflowDiscoveryForbidden)
    assert reader.calls == 0


def test_partial_source_failure_is_incomplete_and_blocks_policy_bytes() -> None:
    source = _source(_valid_document())
    failure = WorkflowSourceFailure(
        ".github/workflows/missing.yml",
        "b" * 40,
        10,
        "not_found",
    )
    service = WorkflowDiscoveryService(
        authorizer=_Authorizer(True),
        reader=_Reader(_snapshot(source, failures=(failure,))),
    )

    result = asyncio.run(service(actor="operator", scope=SCOPE, revision=REVISION))

    assert isinstance(result, WorkflowDiscoveryCompleted)
    assert result.report.complete is False
    assert [
        item.reason for item in result.report.unknowns if item.field == "workflow.document"
    ] == ["source_not_found"]
    assert result.proposal.state == "blocked"
    assert result.proposal.policy_source is None
    assert "inventory_incomplete" in result.proposal.blockers


def test_identical_snapshot_produces_identical_admitted_proposal() -> None:
    snapshot = _snapshot(_source(_valid_document()))
    service = WorkflowDiscoveryService(authorizer=_Authorizer(True), reader=_Reader(snapshot))

    first = asyncio.run(service(actor="operator", scope=SCOPE, revision=None))
    second = asyncio.run(service(actor="operator", scope=SCOPE, revision=None))

    assert isinstance(first, WorkflowDiscoveryCompleted)
    assert isinstance(second, WorkflowDiscoveryCompleted)
    assert first.report.inventory_digest == second.report.inventory_digest
    assert first.proposal.manifest_id == second.proposal.manifest_id
    assert first.proposal.policy_source == second.proposal.policy_source
    assert first.proposal.admission is not None
    assert first.proposal.admission.source_bytes == first.proposal.policy_source


def test_report_binds_the_current_parser_version_and_refuses_relabeled_v4() -> None:
    report = _completed(_source(_valid_document())).report

    assert report.parser_version == "github-actions-static/v5"
    assert {fact.provenance.parser_version for fact in report.facts} | {
        unknown.provenance.parser_version for unknown in report.unknowns
    } == {"github-actions-static/v5"}
    with pytest.raises(ValueError, match="parser version is not admitted"):
        replace(report, parser_version="github-actions-static/v4")


def test_adoption_assessment_is_total_and_fail_closed_without_owner_contract() -> None:
    valid = _source(_valid_document(), ".github/workflows/a-valid.yml")
    invalid = _source("jobs: {}\njobs: {}\n", ".github/workflows/b-invalid.yml")

    result = _completed(valid, invalid)

    assert tuple(item.workflow_path for item in result.adoption_assessments) == (
        valid.path,
        invalid.path,
    )
    assert tuple(item.state for item in result.adoption_assessments) == (
        "full_only",
        "invalid",
    )
    assert result.adoption_assessments[0].blockers == (
        "inventory_incomplete",
        "target_semantic_projection_absent",
    )
    assert result.adoption_assessments[1].recommended_adapter == "none"
    assert {item.support_profile_id for item in result.adoption_assessments} == {
        "github.com/github-actions-static/v5"
    }
    assert _change_rows(result.adoption_assessments[1]) == (
        ("workflow_parse_failed", "repair_workflow_syntax", None, None, None, None),
    )
    assert _change_rows(result.adoption_assessments[0])[0] == (
        "inventory_incomplete",
        "repair_other_workflow_sources",
        None,
        None,
        None,
        None,
    )
    assert result.adoption_assessments[1].unknown_ids == tuple(
        unknown.unknown_id
        for unknown in result.report.unknowns
        if unknown.provenance.workflow_path == invalid.path
    )
    assert any(
        unknown.unknown_id in result.adoption_assessments[1].unknown_ids
        and unknown.field == "workflow.document"
        for unknown in result.report.unknowns
    )


def test_adoption_recommends_reusable_shape_without_claiming_selectability() -> None:
    result = _completed(
        _source(
            _call_document(
                f"example/shared/.github/workflows/python.yml@{'b' * 40}",
            )
        )
    )

    assert result.adoption_assessments[0].state == "full_only"
    assert result.adoption_assessments[0].recommended_adapter == "reusable_workflow_set"


def test_owner_registry_is_required_for_selectable_in_place_adoption() -> None:
    result = _completed(
        _source(_adapted_native_document()),
        target_projection=_native_adoption_projection(),
    )
    assessment = result.adoption_assessments[0]

    assert assessment.state == "in_place_job_set"
    assert assessment.recommended_adapter == "in_place_job_set"
    assert assessment.blockers == ()
    assert assessment.required_owner_inputs == ()
    assert assessment.required_changes == ()
    assert assessment.support_profile_id == "github.com/github-actions-static/v5"
    assert result.target_projection.status == "available"


def test_adapted_reusable_jobs_retain_their_structural_adapter_classification() -> None:
    result = _completed(
        _source(
            _adapted_native_document()
            .replace(
                "    runs-on: ubuntu-latest\n    steps: []\n  test:",
                "    uses: example/shared/.github/workflows/lint.yml@main\n  test:",
                1,
            )
            .replace(
                "    runs-on: ubuntu-latest\n    steps: []\n  gate:",
                "    uses: example/shared/.github/workflows/test.yml@main\n  gate:",
                1,
            )
        ),
        target_projection=_native_adoption_projection(),
    )
    assessment = result.adoption_assessments[0]

    assert assessment.state == "reusable_workflow_set"
    assert assessment.recommended_adapter == "reusable_workflow_set"
    assert assessment.blockers == ()


def test_existing_sharded_fixture_has_a_distinct_non_authoritative_state() -> None:
    target_projection = _sharded_fixture_adoption_projection()
    workflow = target_projection.workflows[0]
    document = (REPO_ROOT / "fixtures/target-repository" / workflow.workflow_path).read_text(
        encoding="utf-8"
    )

    result = _completed(
        _source(document, workflow.workflow_path),
        target_projection=target_projection,
    )
    assessment = result.adoption_assessments[0]

    assert assessment.state == "witness_shards"
    assert assessment.recommended_adapter == "none"
    assert assessment.blockers == ()
    assert assessment.required_owner_inputs == ()


def test_adoption_rejects_registry_signal_drift() -> None:
    result = _completed(
        _source(_adapted_native_document()),
        target_projection=_native_adoption_projection(gate_signal_name="Different Gate"),
    )
    assessment = result.adoption_assessments[0]

    assert assessment.state == "full_only"
    assert assessment.blockers == ("registered_gate_signal_mismatch",)
    assert _change_rows(assessment) == (
        ("registered_gate_signal_mismatch", "set_job_name", "gate", "gate", None, "Different Gate"),
    )


@pytest.mark.parametrize(
    ("mutation", "blocker"),
    (
        (
            ("    needs: [lint, plan]\n", "    needs: plan\n"),
            "registered_execution_dependencies_mismatch",
        ),
        (
            ("    needs: [lint, plan, test]\n", "    needs: test\n"),
            "registered_gate_dependencies_mismatch",
        ),
        (
            ("    if: always()\n", "    if: success()\n"),
            "registered_gate_condition_unsafe",
        ),
        (
            (
                "  plan:\n    name: Resolve plan\n    needs: plan-request\n",
                (
                    "  rogue:\n    name: Rogue\n    runs-on: ubuntu-latest\n    steps: []\n"
                    "  plan:\n    name: Resolve plan\n    needs: rogue\n"
                ),
            ),
            "registered_plan_dependencies_mismatch",
        ),
        (
            (
                "  gate:\n",
                "  rogue:\n    name: Rogue\n    runs-on: ubuntu-latest\n    steps: []\n  gate:\n",
            ),
            "unregistered_workflow_job_present",
        ),
    ),
)
def test_adoption_rejects_incomplete_or_unsafe_target_topology(
    mutation: tuple[str, str],
    blocker: str,
) -> None:
    document = _adapted_native_document().replace(*mutation)
    result = _completed(
        _source(document),
        target_projection=_native_adoption_projection(),
    )

    assert result.adoption_assessments[0].state == "full_only"
    assert blocker in result.adoption_assessments[0].blockers


def test_adoption_rejects_an_extra_gate_dependency_even_when_the_job_exists() -> None:
    document = (
        _adapted_native_document()
        .replace(
            "  gate:\n",
            "  rogue:\n    name: Rogue\n    runs-on: ubuntu-latest\n    steps: []\n  gate:\n",
        )
        .replace(
            "    needs: [lint, plan, test]\n",
            "    needs: [lint, plan, rogue, test]\n",
        )
    )

    result = _completed(
        _source(document),
        target_projection=_native_adoption_projection(),
    )

    assert result.adoption_assessments[0].state == "full_only"
    assert "registered_gate_dependencies_mismatch" in result.adoption_assessments[0].blockers
    assert "unregistered_workflow_job_present" in result.adoption_assessments[0].blockers


def test_unmodified_workflow_receives_computed_registration_changes() -> None:
    result = _completed(_source(_aggregate_document()))
    assessment = result.adoption_assessments[0]

    assert assessment.state == "full_only"
    assert assessment.blockers == ("target_semantic_projection_absent",)
    blocker = "target_semantic_projection_absent"
    assert _unregistered_change_rows(assessment) == (
        (blocker, "add_control_job", "gate", None, ("gate", "lint", "test"), ("plan",), None),
        (blocker, "add_control_job", "plan", None, (), ("plan_request",), None),
        (blocker, "add_control_job", "plan_request", None, ("ci-invocation",), (), None),
        (blocker, "add_job", "invocation_classifier", "ci-invocation", (), (), None),
        (blocker, "declare_registry_workflow", None, None, None, (), None),
        (blocker, "register_execution_job", "execution", "gate", ("test",), ("plan",), None),
        (blocker, "register_execution_job", "execution", "lint", (), ("plan",), None),
        (blocker, "register_execution_job", "execution", "test", ("lint",), ("plan",), None),
        (blocker, "set_job_condition", "gate", None, None, (), "always()"),
    )


def test_adoption_classifier_identity_is_the_target_registry_fixed_identity() -> None:
    assert INVOCATION_CLASSIFIER_JOB_ID == CONTROL_INVOCATION_JOB_ID
    with pytest.raises(ValueError, match="fixed id"):
        replace(
            _native_adoption_projection().workflows[0],
            invocation_job_id="owner-classifier",
        )
    with pytest.raises(ValueError, match="fixed, not owner-selected"):
        AdoptionRequiredChange(
            blocker="target_semantic_projection_absent",
            action="add_job",
            role="invocation_classifier",
            job_id="classify",
            needs=(),
            needs_control_roles=(),
            expected_value=None,
        )
    with pytest.raises(ValueError, match="owner-named control roles"):
        AdoptionRequiredChange(
            blocker="target_semantic_projection_absent",
            action="add_control_job",
            role="invocation_classifier",
            job_id=None,
            needs=(),
            needs_control_roles=(),
            expected_value=None,
        )
    with pytest.raises(ValueError, match="directly on the plan job"):
        AdoptionRequiredChange(
            blocker="target_semantic_projection_absent",
            action="register_execution_job",
            role="execution",
            job_id="test",
            needs=(),
            needs_control_roles=(),
            expected_value=None,
        )
    with pytest.raises(ValueError, match="only before workflow registration"):
        AdoptionRequiredChange(
            blocker="registered_execution_dependencies_mismatch",
            action="set_job_needs",
            role="execution",
            job_id="test",
            needs=(),
            needs_control_roles=("plan",),
            expected_value=None,
        )


@pytest.mark.parametrize(
    "case",
    ("single-job", "aggregate", "occupied-classifier", "aliased-classifier"),
)
def test_unregistered_recommendation_is_a_registry_admitted_selectable_topology(
    case: str,
) -> None:
    # Applying exactly the recommended changes with arbitrary owner-chosen
    # control ids must yield the target registry's required topology and a
    # selectable reassessment, not merely a list of current needs.
    owner_job_ids = {"gate": "owner-gate", "plan": "owner-plan", "plan_request": "owner-request"}
    result = _completed(_source(_unregistered_document(case)))
    topology, gate_condition = _apply_unregistered_changes(
        result.adoption_assessments[0],
        result.report.workflows[0].jobs,
        owner_job_ids,
    )
    execution_job_ids = sorted(set(topology) - {CONTROL_INVOCATION_JOB_ID, *owner_job_ids.values()})
    binding = TargetWorkflowBinding(
        workflow_path=".github/workflows/ci.yml",
        execution_kind="native-job-set",
        execution_jobs=tuple(
            TargetJobBinding(job_id, topology[job_id]) for job_id in execution_job_ids
        ),
        plan_request_job_id=owner_job_ids["plan_request"],
        plan_request_workflow_ref=LOCAL_PLAN_REQUEST_WORKFLOW_REF,
        plan_job_id=owner_job_ids["plan"],
        fallback_job_id=None,
        gate_job_id=owner_job_ids["gate"],
        gate_signal_name="Owner Gate",
    )
    assert binding.job_topology == tuple(sorted(topology.items()))
    assert gate_condition == "always()"

    adapted = _jobs_document(
        "".join(
            f"  {job_id}:\n"
            + ("    name: Owner Gate\n    if: always()\n" if job_id == "owner-gate" else "")
            + (f"    needs: [{', '.join(needs)}]\n" if needs else "")
            + "    runs-on: ubuntu-latest\n    steps: []\n"
            for job_id, needs in sorted(topology.items())
        )
    )
    reassessed = _completed(
        _source(adapted),
        target_projection=AdoptionTargetProjection.available(
            "e" * 64,
            (
                AdoptionTargetWorkflow(
                    workflow_path=binding.workflow_path,
                    execution_kind=binding.execution_kind,
                    execution_jobs=tuple(
                        AdoptionTargetJob(job.job_id, job.needs) for job in binding.execution_jobs
                    ),
                    invocation_job_id=binding.invocation_job_id,
                    plan_request_job_id=binding.plan_request_job_id,
                    plan_job_id=binding.plan_job_id,
                    fallback_job_id=None,
                    gate_job_id=binding.gate_job_id,
                    gate_signal_name=binding.gate_signal_name,
                ),
            ),
        ),
    ).adoption_assessments[0]
    assert reassessed.blockers == ()
    assert reassessed.state == "in_place_job_set"


@pytest.mark.parametrize(
    ("mutations", "expected"),
    (
        (
            (("    needs: [lint, plan]\n", "    needs: plan\n"),),
            (
                (
                    "registered_execution_dependencies_mismatch",
                    "set_job_needs",
                    "execution",
                    "test",
                    ("lint", "plan"),
                    None,
                ),
            ),
        ),
        (
            (("    needs: [lint, plan, test]\n", "    needs: test\n"),),
            (
                (
                    "registered_gate_dependencies_mismatch",
                    "set_job_needs",
                    "gate",
                    "gate",
                    ("lint", "plan", "test"),
                    None,
                ),
            ),
        ),
        (
            (("    if: always()\n", "    if: success()\n"),),
            (
                (
                    "registered_gate_condition_unsafe",
                    "set_job_condition",
                    "gate",
                    "gate",
                    None,
                    "always()",
                ),
            ),
        ),
        (
            (
                (
                    "  plan:\n    name: Resolve plan\n    needs: plan-request\n",
                    (
                        "  rogue:\n    name: Rogue\n    runs-on: ubuntu-latest\n    steps: []\n"
                        "  plan:\n    name: Resolve plan\n    needs: rogue\n"
                    ),
                ),
            ),
            (
                (
                    "registered_plan_dependencies_mismatch",
                    "set_job_needs",
                    "plan",
                    "plan",
                    ("plan-request",),
                    None,
                ),
                (
                    "unregistered_workflow_job_present",
                    "remove_or_register_job",
                    None,
                    "rogue",
                    None,
                    None,
                ),
            ),
        ),
        (
            (
                (
                    "  lint:\n    name: Lint\n    needs: plan\n"
                    "    runs-on: ubuntu-latest\n    steps: []\n",
                    "",
                ),
                ("    needs: [lint, plan]\n", "    needs: plan\n"),
            ),
            (
                (
                    "registered_execution_dependencies_mismatch",
                    "set_job_needs",
                    "execution",
                    "test",
                    ("lint", "plan"),
                    None,
                ),
                (
                    "registered_execution_job_absent",
                    "add_job",
                    "execution",
                    "lint",
                    ("plan",),
                    None,
                ),
            ),
        ),
        (
            (
                (
                    "  gate:\n    name: Pull Request Gate\n    if: always()\n"
                    "    needs: [lint, plan, test]\n    runs-on: ubuntu-latest\n    steps: []\n",
                    "",
                ),
            ),
            (
                (
                    "registered_gate_job_absent",
                    "add_job",
                    "gate",
                    "gate",
                    ("lint", "plan", "test"),
                    None,
                ),
                (
                    "registered_gate_job_absent",
                    "set_job_condition",
                    "gate",
                    "gate",
                    None,
                    "always()",
                ),
                (
                    "registered_gate_job_absent",
                    "set_job_name",
                    "gate",
                    "gate",
                    None,
                    "Pull Request Gate",
                ),
            ),
        ),
    ),
)
def test_registered_target_blockers_derive_exact_minimal_consumer_changes(
    mutations: tuple[tuple[str, str], ...],
    expected: tuple[tuple[object, ...], ...],
) -> None:
    document = _adapted_native_document()
    for old, new in mutations:
        assert old in document
        document = document.replace(old, new, 1)

    result = _completed(
        _source(document),
        target_projection=_native_adoption_projection(),
    )
    assessment = result.adoption_assessments[0]

    assert assessment.state == "full_only"
    assert assessment.blockers == tuple(sorted({str(row[0]) for row in expected}))
    assert _change_rows(assessment) == expected


def test_open_call_graph_names_the_open_caller_job_only_in_its_own_workflow() -> None:
    caller = _source(
        _call_document("./.github/workflows/missing.yml"),
        ".github/workflows/a-caller.yml",
    )
    bystander = _source(_valid_document(), ".github/workflows/b-bystander.yml")

    result = _completed(caller, bystander)

    assert result.report.local_graph_closed is False
    caller_changes = _change_rows(result.adoption_assessments[0])
    bystander_changes = _change_rows(result.adoption_assessments[1])
    assert ("local_graph_open", "resolve_reusable_workflow_call", None, "call", None, None) in (
        caller_changes
    )
    assert [row for row in bystander_changes if row[0] == "local_graph_open"] == [
        ("local_graph_open", "close_report_call_graph", None, None, None, None)
    ]


def test_assessment_unknowns_are_attributed_by_exact_workflow_path_not_name() -> None:
    first = _source(_valid_document(), ".github/workflows/first.yml")
    second = _source(
        _valid_document().replace(
            "    runs-on: ubuntu-latest\n", "    runs-on: ${{ inputs.os }}\n"
        ),
        ".github/workflows/second.yml",
    )

    result = _completed(first, second)

    assert {workflow.name for workflow in result.report.workflows} == {"CI"}
    by_path = {item.workflow_path: item.unknown_ids for item in result.adoption_assessments}
    for path in (first.path, second.path):
        assert by_path[path] == tuple(
            unknown.unknown_id
            for unknown in result.report.unknowns
            if unknown.provenance.workflow_path == path
        )
    assert by_path[second.path]
    assert set(by_path[first.path]).isdisjoint(by_path[second.path])
    assert len(by_path[second.path]) > len(by_path[first.path])
    assert {*by_path[first.path], *by_path[second.path]} == {
        unknown.unknown_id for unknown in result.report.unknowns
    }


def test_unadmitted_support_profile_is_full_only_without_inventing_a_consumer_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _completed(
        _source(_adapted_native_document()),
        target_projection=_native_adoption_projection(),
    )
    assert adoption_module.support_profile_for(PARSER_VERSION) == (
        "github.com/github-actions-static/v5"
    )
    assert adoption_module.support_profile_for("github-actions-static/v4") is None
    monkeypatch.setattr(adoption_module, "_ADMITTED_SUPPORT_PROFILES", MappingProxyType({}))

    assessment = assess_workflow_adoption(
        result.report,
        target_projection=_native_adoption_projection(),
    )[0]

    assert assessment.state == "full_only"
    assert assessment.support_profile_id is None
    assert assessment.blockers == ("support_profile_unknown",)
    assert assessment.required_changes == ()
    assert assessment.recommended_adapter == "in_place_job_set"


@pytest.mark.parametrize(
    ("status", "expected"),
    (
        ("unavailable", ()),
        (
            "invalid",
            (
                (
                    "target_semantic_projection_invalid",
                    "repair_target_registry",
                    None,
                    None,
                    None,
                    None,
                ),
            ),
        ),
    ),
)
def test_failed_target_projection_names_only_consumer_repairable_changes(
    status: Literal["invalid", "unavailable"],
    expected: tuple[tuple[object, ...], ...],
) -> None:
    result = _completed(
        _source(_adapted_native_document()),
        target_projection=AdoptionTargetProjection.failed(status),
    )
    assessment = result.adoption_assessments[0]

    assert assessment.state == "full_only"
    assert assessment.blockers == (f"target_semantic_projection_{status}",)
    assert _change_rows(assessment) == expected


def test_assessment_rejects_unexplained_missing_or_contradictory_changes() -> None:
    full_only = _completed(
        _source(_adapted_native_document()),
        target_projection=_native_adoption_projection(gate_signal_name="Different Gate"),
    ).adoption_assessments[0]
    selectable = _completed(
        _source(_adapted_native_document()),
        target_projection=_native_adoption_projection(),
    ).adoption_assessments[0]
    foreign_change = AdoptionRequiredChange(
        blocker="registered_gate_condition_unsafe",
        action="set_job_condition",
        role="gate",
        job_id="gate",
        needs=None,
        needs_control_roles=(),
        expected_value="always()",
    )

    with pytest.raises(ValueError, match="consumer-actionable blocker"):
        replace(full_only, required_changes=())
    with pytest.raises(ValueError, match="consumer-actionable blocker"):
        replace(full_only, required_changes=(foreign_change, *full_only.required_changes))
    with pytest.raises(ValueError, match="consumer-actionable blocker"):
        replace(selectable, required_changes=(foreign_change,))
    with pytest.raises(ValueError, match="unknown support profile"):
        replace(full_only, support_profile_id=None)
    with pytest.raises(ValueError, match="closed vocabulary"):
        replace(full_only, blockers=(*full_only.blockers, "zz_unmapped_blocker"))
    with pytest.raises(ValueError, match="not admitted for its blocker"):
        AdoptionRequiredChange(
            blocker="registered_gate_signal_mismatch",
            action="set_job_needs",
            role="gate",
            job_id="gate",
            needs=("plan",),
            needs_control_roles=(),
            expected_value=None,
        )
    with pytest.raises(ValueError, match="expected value"):
        replace(foreign_change, expected_value=None)


def test_explicit_historical_revision_cannot_claim_default_branch_fallback() -> None:
    result = _completed(_source(_valid_document()), explicit_revision=True)

    assert result.report.complete is True
    assert result.proposal.state == "blocked"
    assert result.proposal.blockers == ("default_branch_head_unproven",)


def test_proposal_event_actions_match_the_admitted_webhook_contract() -> None:
    profile = load_bundled_profile()
    admitted = {
        policy.event_name: tuple(action for action in policy.allowed_actions if action is not None)
        for policy in profile.event_actions
    }

    assert (
        tuple((event, admitted[event]) for event, _ in PROPOSAL_EVENT_ACTIONS)
        == PROPOSAL_EVENT_ACTIONS
    )


def test_branch_pattern_metacharacters_cannot_masquerade_as_exact_branch_identity() -> None:
    document = _aggregate_document().replace(
        "  pull_request:\n",
        "  pull_request:\n    branches: ['!main']\n",
    )

    parsed = parse_workflow(
        _source(document),
        scope=SCOPE,
        revision=REVISION,
        default_branch="!main",
    )

    assert isinstance(parsed, ParsedWorkflow)
    coverage = next(
        fact for fact in parsed.facts if fact.field == "workflow.default_branch_ci_events"
    )
    assert coverage.value == ()


@pytest.mark.parametrize("local_prefix", ["./", "$/"])
def test_local_call_graph_resolves_only_same_snapshot_targets_and_detects_cycles(
    local_prefix: str,
) -> None:
    caller = _source(
        _call_document(f"{local_prefix}.github/workflows/target.yml", trigger="workflow_call"),
        ".github/workflows/caller.yml",
    )
    target = _source(
        _call_document(f"{local_prefix}.github/workflows/caller.yml", trigger="workflow_call"),
        ".github/workflows/target.yml",
    )

    resolved = _completed(caller, _source(_callable_document(), target.path))
    cyclic = _completed(caller, target)

    assert resolved.report.local_graph_closed is True
    assert [(edge.kind, edge.status) for edge in resolved.report.call_edges] == [
        ("local", "resolved")
    ]
    assert cyclic.report.local_graph_closed is False
    assert {edge.status for edge in cyclic.report.call_edges} == {"cycle"}
    assert {item.reason for item in cyclic.report.unknowns if item.field == "call.target"} == {
        "local_call_cycle"
    }
    assert "local_graph_open" in cyclic.proposal.blockers
    with pytest.raises(ValueError, match="exactly one call edge"):
        replace(resolved.report, call_edges=())
    with pytest.raises(ValueError, match="closure"):
        replace(resolved.report, local_graph_closed=False)


@pytest.mark.parametrize("local_prefix", ["./", "$/"])
def test_local_call_graph_classifies_cycles_and_only_overlong_paths(local_prefix: str) -> None:
    long_chain = _call_chain("long", 11, local_prefix)
    limit_chain = _call_chain("limit", 10, local_prefix)
    short_chain = _call_chain("short", 2, local_prefix)
    cycle = (
        _source(
            _call_document(f"{local_prefix}.github/workflows/cycle-b.yml", trigger="workflow_call"),
            ".github/workflows/cycle-a.yml",
        ),
        _source(
            _call_document(f"{local_prefix}.github/workflows/cycle-a.yml", trigger="workflow_call"),
            ".github/workflows/cycle-b.yml",
        ),
    )

    result = _completed(*long_chain, *limit_chain, *short_chain, *cycle)
    status_by_caller = {edge.caller_workflow_path: edge.status for edge in result.report.call_edges}

    assert {status_by_caller[f".github/workflows/long-{index:02}.yml"] for index in range(10)} == {
        "depth_exceeded"
    }
    assert {status_by_caller[f".github/workflows/limit-{index:02}.yml"] for index in range(9)} == {
        "resolved"
    }
    assert status_by_caller[".github/workflows/short-00.yml"] == "resolved"
    assert {
        status_by_caller[".github/workflows/cycle-a.yml"],
        status_by_caller[".github/workflows/cycle-b.yml"],
    } == {"cycle"}
    assert result.report.local_graph_closed is False
    assert {item.reason for item in result.report.unknowns if item.field == "call.target"} == {
        "local_call_cycle",
        "local_call_depth_exceeded",
    }


@pytest.mark.parametrize("local_prefix", ["./", "$/"])
@pytest.mark.parametrize(
    ("target_trigger", "status", "reason"),
    [
        ("push", "not_callable", "local_call_target_not_callable"),
        ("[workflow_call, 7]", "callability_unknown", "local_call_target_callability_unknown"),
    ],
    ids=["static-without-workflow-call", "unknown-triggers"],
)
def test_local_call_graph_resolves_only_targets_that_declare_workflow_call(
    local_prefix: str,
    target_trigger: str,
    status: str,
    reason: str,
) -> None:
    target = ".github/workflows/target.yml"
    result = _completed(
        _source(_call_document(f"{local_prefix}{target}"), ".github/workflows/caller.yml"),
        _source(
            f"name: Target\non: {target_trigger}\njobs:\n"
            "  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest\n",
            target,
        ),
    )

    assert [(edge.kind, edge.status, edge.target_path) for edge in result.report.call_edges] == [
        ("local", status, target)
    ]
    assert {item.reason for item in result.report.unknowns if item.field == "call.target"} == {
        reason
    }
    assert not any(fact.field == "call.target" for fact in result.report.facts)
    assert result.report.local_graph_closed is False
    assert "local_graph_open" in result.proposal.blockers


@pytest.mark.parametrize("local_prefix", ["./", "$/"])
@pytest.mark.parametrize(
    ("called_workflows", "status"),
    [(50, "resolved"), (51, "callee_limit_exceeded")],
    ids=["unique-50", "unique-51"],
)
def test_local_call_graph_enforces_the_unique_called_workflow_ceiling(
    local_prefix: str,
    called_workflows: int,
    status: str,
) -> None:
    leaves = tuple(f".github/workflows/leaf-{index:02}.yml" for index in range(called_workflows))
    graph = _call_graph(
        _source(_fan_out_document(leaves, local_prefix), ".github/workflows/root.yml"),
        *(_source(_callable_document(), path) for path in leaves),
    )

    assert {edge.status for edge in graph.edges} == {status}
    assert len(graph.edges) == called_workflows
    assert graph.local_graph_closed is (status == "resolved")
    assert {item.reason for item in graph.unknowns} == (
        set() if status == "resolved" else {"local_call_unique_callee_limit_exceeded"}
    )


@pytest.mark.parametrize(
    ("shape", "status"),
    [("transitive-51", "callee_limit_exceeded"), ("shared-50", "resolved")],
)
def test_unique_called_workflow_ceiling_counts_each_tree_workflow_once(
    shape: str,
    status: str,
) -> None:
    root = ".github/workflows/root.yml"
    hubs: tuple[str, ...]
    if shape == "transitive-51":
        hubs = (".github/workflows/hub.yml",)
        leaves = tuple(f".github/workflows/leaf-{index:02}.yml" for index in range(50))
    else:
        hubs = (".github/workflows/hub-a.yml", ".github/workflows/hub-b.yml")
        leaves = tuple(f".github/workflows/leaf-{index:02}.yml" for index in range(48))
    graph = _call_graph(
        _source(_fan_out_document(hubs, "$/"), root),
        *(_source(_fan_out_document(leaves, "./", trigger="workflow_call"), hub) for hub in hubs),
        *(_source(_callable_document(), path) for path in leaves),
    )

    assert {edge.status for edge in graph.edges} == {status}
    assert graph.local_graph_closed is (status == "resolved")


@pytest.mark.parametrize(
    ("reference", "kind", "status"),
    [
        ("$/.github/workflows/missing.yml", "local", "missing"),
        ("$/.github/workflows/target.yml@main", "unknown", "invalid"),
        ("$/.github/workflows/nested/target.yml", "unknown", "invalid"),
        ("$/.github/workflows/../target.yml", "unknown", "invalid"),
    ],
)
def test_github_same_repository_calls_reject_unavailable_or_invalid_targets(
    reference: str, kind: str, status: str
) -> None:
    result = _completed(_source(_call_document(reference)))

    assert [(edge.kind, edge.status) for edge in result.report.call_edges] == [(kind, status)]
    assert result.report.local_graph_closed is False
    assert result.proposal.state == "blocked"


@pytest.mark.parametrize("local_prefix", ["./", "$/"])
@pytest.mark.parametrize("filename", ["target@release.yml", "target@nightly.yaml"])
def test_same_repository_call_cannot_admit_ambiguous_ref_suffix_as_a_filename(
    local_prefix: str,
    filename: str,
) -> None:
    target = f".github/workflows/{filename}"
    result = _completed(
        _source(_call_document(f"{local_prefix}{target}")),
        _source(_valid_document(), target),
    )

    assert [(edge.kind, edge.status) for edge in result.report.call_edges] == [
        ("unknown", "invalid")
    ]
    assert not any(fact.field == "call.target" for fact in result.report.facts)
    assert result.report.local_graph_closed is False
    assert "local_graph_open" in result.proposal.blockers


@pytest.mark.parametrize(
    ("case", "expected_events", "expected_job_id"),
    [
        ("single", ("push",), "test"),
        ("unicode-signal", ("push",), "test"),
        ("transitive", ("pull_request",), "gate"),
        ("all-events", ("merge_group", "pull_request", "push"), "gate"),
        ("filtered", ("pull_request",), "gate"),
    ],
)
def test_proposal_accepts_only_static_manual_fallback_topologies(
    case: str,
    expected_events: tuple[str, ...],
    expected_job_id: str,
) -> None:
    result = _completed(_source(_reviewable_document(case)))

    assert result.proposal.state == "reviewable"
    assert result.proposal.selected_events == expected_events
    assert result.proposal.selected_job_id == expected_job_id
    assert result.proposal.policy_source is not None
    policy = json.loads(result.proposal.policy_source)
    assert [rule["on"]["event"] for rule in policy["repository"]["rules"]] == list(expected_events)
    assert policy["repository"]["dynamicCi"] is None


@pytest.mark.parametrize(
    ("case", "blocker"),
    [
        ("no-supported-event", "stable_provider_signal_absent"),
        ("no-manual-fallback", "stable_provider_signal_absent"),
        ("path-filtered", "stable_provider_signal_absent"),
        ("wrong-default-branch", "stable_provider_signal_absent"),
        ("default-pull-request-actions", "stable_provider_signal_absent"),
        ("incomplete-event-types", "stable_provider_signal_absent"),
        ("merge-group-branch-filter", "stable_provider_signal_absent"),
        ("duplicate-provider-signal", "stable_provider_signal_ambiguous"),
        ("multiple-candidates", "stable_provider_signal_ambiguous"),
        ("disconnected-job", "stable_provider_signal_absent"),
        ("dependency-cycle", "stable_provider_signal_absent"),
        ("missing-dependency", "stable_provider_signal_absent"),
        ("conditional-terminal", "stable_provider_signal_absent"),
        ("matrix-terminal", "stable_provider_signal_absent"),
        ("reusable-terminal", "stable_provider_signal_absent"),
        ("missing-local-call", "local_graph_open"),
    ],
)
def test_proposal_generation_blocks_every_ambiguous_or_open_inventory(
    case: str,
    blocker: str,
) -> None:
    result = _completed(*_proposal_sources(case))

    assert result.proposal.state == "blocked"
    assert result.proposal.policy_source is None
    assert blocker in result.proposal.blockers


@dataclass
class _Authorizer:
    allowed: bool

    async def allows_scope(self, *, actor: str, scope: RepositoryScope) -> bool:
        return self.allowed


@dataclass
class _Reader:
    snapshot: RepositoryWorkflowSnapshot
    calls: int = 0

    async def read(
        self,
        *,
        scope: RepositoryScope,
        revision: str | None,
    ) -> RepositoryWorkflowSnapshot:
        self.calls += 1
        return self.snapshot


def _snapshot(
    *sources: WorkflowSource,
    failures: tuple[WorkflowSourceFailure, ...] = (),
    target_projection: AdoptionTargetProjection | None = None,
) -> RepositoryWorkflowSnapshot:
    return RepositoryWorkflowSnapshot.create(
        repository=RepositoryIdentity(SCOPE, "example", "repo", "master"),
        revision=REVISION,
        sources=sources,
        failures=failures,
        target_projection=target_projection,
    )


def _source(
    document: str,
    path: str = ".github/workflows/ci.yml",
) -> WorkflowSource:
    content = document.encode()
    return WorkflowSource(path, git_blob_sha1(content), len(content), content)


def _completed(
    *sources: WorkflowSource,
    explicit_revision: bool = False,
    target_projection: AdoptionTargetProjection | None = None,
) -> WorkflowDiscoveryCompleted:
    service = WorkflowDiscoveryService(
        authorizer=_Authorizer(True),
        reader=_Reader(_snapshot(*sources, target_projection=target_projection)),
    )
    result = asyncio.run(
        service(
            actor="operator",
            scope=SCOPE,
            revision=REVISION if explicit_revision else None,
        )
    )
    assert isinstance(result, WorkflowDiscoveryCompleted)
    return result


def _call_graph(*sources: WorkflowSource) -> GraphAnalysis:
    # Ceiling fixtures need 51+ workflows; their full discovery report would exceed
    # MAX_REPORT_BYTES, so they exercise the parsed call graph directly.
    parsed: list[ParsedWorkflow] = []
    for source in sources:
        outcome = parse_workflow(source, scope=SCOPE, revision=REVISION, default_branch="master")
        assert isinstance(outcome, ParsedWorkflow)
        parsed.append(outcome)
    return analyze_call_graph(tuple(parsed))


def _change_rows(assessment: AdoptionAssessment) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            change.blocker,
            change.action,
            change.role,
            change.job_id,
            change.needs,
            change.expected_value,
        )
        for change in assessment.required_changes
    )


def _unregistered_change_rows(assessment: AdoptionAssessment) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            change.blocker,
            change.action,
            change.role,
            change.job_id,
            change.needs,
            change.needs_control_roles,
            change.expected_value,
        )
        for change in assessment.required_changes
    )


def _apply_unregistered_changes(
    assessment: AdoptionAssessment,
    current_jobs: tuple[JobSummary, ...],
    owner_job_ids: dict[str, str],
) -> tuple[dict[str, tuple[str, ...]], str | None]:
    topology: dict[str, tuple[str, ...]] = {}
    for job in current_jobs:
        assert job.needs is not None
        topology[job.job_id] = job.needs
    gate_condition: str | None = None
    for change in assessment.required_changes:
        assert change.blocker == "target_semantic_projection_absent"
        needs = tuple(
            sorted(
                {
                    *(change.needs or ()),
                    *(owner_job_ids[role] for role in change.needs_control_roles),
                }
            )
        )
        if change.action == "add_control_job":
            assert change.role is not None
            topology[owner_job_ids[change.role]] = needs
        elif change.action in {"add_job", "register_execution_job", "set_job_needs"}:
            assert change.job_id is not None
            topology[change.job_id] = needs
        elif change.action == "remove_or_register_job":
            assert change.job_id is not None
            del topology[change.job_id]
        elif change.action == "set_job_condition":
            assert change.role == "gate"
            assert change.job_id is None
            gate_condition = change.expected_value
        else:
            assert change.action == "declare_registry_workflow"
    return topology, gate_condition


def _native_adoption_projection(
    *,
    gate_signal_name: str = "Pull Request Gate",
) -> AdoptionTargetProjection:
    return AdoptionTargetProjection.available(
        "d" * 64,
        (
            AdoptionTargetWorkflow(
                workflow_path=".github/workflows/ci.yml",
                execution_kind="native-job-set",
                execution_jobs=(
                    AdoptionTargetJob("lint", ("plan",)),
                    AdoptionTargetJob("test", ("lint", "plan")),
                ),
                invocation_job_id="ci-invocation",
                plan_request_job_id="plan-request",
                plan_job_id="plan",
                fallback_job_id=None,
                gate_job_id="gate",
                gate_signal_name=gate_signal_name,
            ),
        ),
    )


def _sharded_fixture_adoption_projection() -> AdoptionTargetProjection:
    content = (
        REPO_ROOT / "fixtures/target-repository/.ci-coordinator/execution-registry.v1.json"
    ).read_bytes()
    registry = parse_target_execution_registry(content)
    assert registry is not None
    workflow = registry.workflows[0]
    return AdoptionTargetProjection.available(
        registry.registry_hash,
        (
            AdoptionTargetWorkflow(
                workflow_path=workflow.workflow_path,
                execution_kind=workflow.execution_kind,
                execution_jobs=tuple(
                    AdoptionTargetJob(job.job_id, job.needs) for job in workflow.execution_jobs
                ),
                invocation_job_id="ci-invocation",
                plan_request_job_id=workflow.plan_request_job_id,
                plan_job_id=workflow.plan_job_id,
                fallback_job_id=workflow.fallback_job_id,
                gate_job_id=workflow.gate_job_id,
                gate_signal_name=workflow.gate_signal_name,
            ),
        ),
    )


def _call_document(target: str, *, trigger: str = "push") -> str:
    return f"name: Caller\non: {trigger}\njobs:\n  call:\n    uses: {target}\n"


def _fan_out_document(
    targets: tuple[str, ...],
    local_prefix: str,
    *,
    trigger: str = "push",
) -> str:
    jobs = "".join(
        f"  call-{index:02}:\n    uses: {local_prefix}{target}\n"
        for index, target in enumerate(targets)
    )
    return f"name: Caller\non: {trigger}\njobs:\n{jobs}"


def _callable_document() -> str:
    return _single_job_document(("workflow_call",))


def _call_chain(prefix: str, levels: int, local_prefix: str = "./") -> tuple[WorkflowSource, ...]:
    paths = tuple(f".github/workflows/{prefix}-{index:02}.yml" for index in range(levels))
    return tuple(
        _source(
            _callable_document()
            if index == levels - 1
            else _call_document(f"{local_prefix}{paths[index + 1]}", trigger="workflow_call"),
            path,
        )
        for index, path in enumerate(paths)
    )


def _proposal_sources(case: str) -> tuple[WorkflowSource, ...]:
    if case == "no-supported-event":
        return (_source(_single_job_document(("schedule", "workflow_dispatch"))),)
    if case == "no-manual-fallback":
        return (_source(_single_job_document(("pull_request",))),)
    if case == "path-filtered":
        return (
            _source(
                _aggregate_document().replace(
                    "  pull_request:\n",
                    "  pull_request:\n    paths: [backend/**]\n",
                )
            ),
        )
    if case == "wrong-default-branch":
        return (
            _source(
                _aggregate_document().replace(
                    "  pull_request:\n",
                    "  pull_request:\n    branches: [release]\n",
                )
            ),
        )
    if case == "incomplete-event-types":
        return (
            _source(
                _aggregate_document().replace(
                    "    types: [opened, reopened, synchronize, ready_for_review]\n",
                    "    types: [synchronize]\n",
                )
            ),
        )
    if case == "default-pull-request-actions":
        return (
            _source(
                _aggregate_document().replace(
                    "    types: [opened, reopened, synchronize, ready_for_review]\n",
                    "",
                )
            ),
        )
    if case == "merge-group-branch-filter":
        return (
            _source(
                _aggregate_document(
                    events=("merge_group", "workflow_dispatch"),
                ).replace(
                    "  merge_group:\n",
                    "  merge_group:\n    branches: [master]\n",
                )
            ),
        )
    if case == "duplicate-provider-signal":
        colliding = _single_job_document(("workflow_dispatch",)).replace(
            "name: CI",
            "name: Secondary CI",
        )
        return (
            _source(_valid_document(), ".github/workflows/primary.yml"),
            _source(colliding, ".github/workflows/secondary.yml"),
        )
    if case == "multiple-candidates":
        return (
            _source(_valid_document(), ".github/workflows/first.yml"),
            _source(_valid_document(), ".github/workflows/second.yml"),
        )
    if case == "disconnected-job":
        document = _aggregate_document() + (
            "  docs:\n    name: Docs\n    runs-on: ubuntu-latest\n    steps: []\n"
        )
        return (_source(document),)
    if case == "dependency-cycle":
        return (
            _source(
                _aggregate_document().replace(
                    "  lint:\n    name: Lint\n",
                    "  lint:\n    name: Lint\n    needs: gate\n",
                )
            ),
        )
    if case == "missing-dependency":
        return (_source(_aggregate_document().replace("    needs: test\n", "    needs: absent\n")),)
    if case == "conditional-terminal":
        return (
            _source(_aggregate_document().replace("    if: always()\n", "    if: success()\n")),
        )
    if case == "matrix-terminal":
        return (
            _source(
                _aggregate_document().replace(
                    "  gate:\n    name: Pull Request Gate\n",
                    "  gate:\n    name: Pull Request Gate\n"
                    "    strategy:\n      matrix:\n        python: ['3.14']\n",
                )
            ),
        )
    if case == "reusable-terminal":
        return (
            _source(
                _aggregate_document().replace(
                    "    needs: test\n    runs-on: ubuntu-latest\n    steps: []\n",
                    "    needs: test\n    uses: example/shared/.github/workflows/gate.yml@main\n",
                )
            ),
        )
    assert case == "missing-local-call"
    return (_source(_call_document("./.github/workflows/missing.yml")),)


def _valid_document() -> str:
    return _single_job_document(("push", "workflow_dispatch"))


def _reviewable_document(case: str) -> str:
    if case == "single":
        return _valid_document()
    if case == "unicode-signal":
        return _valid_document().replace("Full CI", "\u26e9\ufe0f Pull Request Gate")
    if case == "transitive":
        return _aggregate_document()
    if case == "filtered":
        return _aggregate_document().replace(
            "  pull_request:\n",
            "  pull_request:\n    branches: [master]\n",
        )
    assert case == "all-events"
    return _aggregate_document(
        events=("merge_group", "pull_request", "push", "workflow_dispatch"),
        condition="${{ always() }}",
    )


def _single_job_document(events: tuple[str, ...]) -> str:
    triggers = "".join(f"  {event}:\n" for event in events)
    return f"""\
name: CI
on:
{triggers}jobs:
  test:
    name: Full CI
    runs-on: ubuntu-latest
    steps:
      - run: pytest
"""


def _unregistered_document(case: str) -> str:
    if case == "single-job":
        return _valid_document()
    if case == "aggregate":
        return _aggregate_document()
    if case == "occupied-classifier":
        return _jobs_document(
            "  ci-invocation:\n    needs: lint\n    runs-on: ubuntu-latest\n    steps: []\n"
            "  lint:\n    runs-on: ubuntu-latest\n    steps: []\n"
            "  test:\n    needs: [ci-invocation, lint]\n    runs-on: ubuntu-latest\n"
            "    steps: []\n"
        )
    assert case == "aliased-classifier"
    return _jobs_document(
        "  CI-Invocation:\n    runs-on: ubuntu-latest\n    steps: []\n"
        "  test:\n    needs: CI-Invocation\n    runs-on: ubuntu-latest\n    steps: []\n"
    )


def _jobs_document(jobs: str) -> str:
    return f"name: CI\non:\n  pull_request:\n  workflow_dispatch:\njobs:\n{jobs}"


def _aggregate_document(
    *,
    events: tuple[str, ...] = ("pull_request", "workflow_dispatch"),
    condition: str = "always()",
) -> str:
    triggers = "".join(
        (
            "  pull_request:\n    types: [opened, reopened, synchronize, ready_for_review]\n"
            if event == "pull_request"
            else f"  {event}:\n"
        )
        for event in events
    )
    return f"""\
name: Full Check
on:
{triggers}jobs:
  lint:
    name: Lint
    runs-on: ubuntu-latest
    steps: []
  test:
    name: Test
    needs: lint
    runs-on: ubuntu-latest
    steps: []
  gate:
    name: Pull Request Gate
    if: {condition}
    needs: test
    runs-on: ubuntu-latest
    steps: []
"""


def _adapted_native_document() -> str:
    plan_request_ref = (
        "example-org/ci-coordinator/.github/workflows/trusted-plan-request.yml@" + "1" * 40
    )
    return f"""\
name: Full Check
on:
  pull_request: {{}}
  workflow_dispatch: {{}}
jobs:
  ci-invocation:
    name: Classify invocation
    runs-on: ubuntu-latest
    steps: []
  plan-request:
    name: Request plan
    needs: ci-invocation
    uses: {plan_request_ref}
  plan:
    name: Resolve plan
    needs: plan-request
    runs-on: ubuntu-latest
    steps: []
  lint:
    name: Lint
    needs: plan
    runs-on: ubuntu-latest
    steps: []
  test:
    name: Test
    needs: [lint, plan]
    runs-on: ubuntu-latest
    steps: []
  gate:
    name: Pull Request Gate
    if: always()
    needs: [lint, plan, test]
    runs-on: ubuntu-latest
    steps: []
"""

from __future__ import annotations

from pathlib import Path

import pytest
from package_b_support import PLAN_REQUEST_JOB_ID, PLAN_REQUEST_WORKFLOW_REF

from ci_coordinator.execution_orchestration import CONTROL_INVOCATION_JOB_ID
from ci_coordinator.repo_context import (
    DefaultBranchWorkflow,
    ProviderWorkflowInventory,
    RevisionWorkflowCapability,
    parse_workflow_capability,
)
from ci_coordinator.repo_context.workflow_control_plane import (
    admitted_control_job_projection_hashes,
    control_job_projection_hash,
)

REVISION = "a" * 40
PATH = ".github/workflows/bootstrap.yml"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_CONTROL_WORKFLOW_PATH = ".github/workflows/full-check.yml"
_CONTROL_WORKFLOW = _REPOSITORY_ROOT / "fixtures/native-target-repository" / _CONTROL_WORKFLOW_PATH
_DRAFT_EXCLUSION = "(github.event_name != 'pull_request' || !github.event.pull_request.draft)"


def test_workflow_capability_extracts_static_jobs_and_triggers() -> None:
    capability = parse_workflow_capability(
        b"""
name: Bootstrap
on:
  pull_request:
  workflow_dispatch:
jobs:
  selected-python:
    name: Selected Python
    runs-on: ubuntu-24.04
  final-gate:
    name: Dynamic CI Gate
    if: always()
    needs: selected-python
    runs-on: ubuntu-24.04
""",
        path=PATH,
        revision_sha=REVISION,
    )

    assert capability is not None
    assert capability.job_ids == ("final-gate", "selected-python")
    assert capability.job_needs == (
        ("final-gate", ("selected-python",)),
        ("selected-python", ()),
    )
    assert capability.provider_job_names == (
        ("final-gate", "Dynamic CI Gate"),
        ("selected-python", "Selected Python"),
    )
    assert capability.always_job_ids == ("final-gate",)
    assert capability.triggers == ("pull_request", "workflow_dispatch")
    assert capability.supports_dispatch is True
    assert capability.supports_reuse is False
    assert tuple(
        (item.job_id, item.selector.labels, item.selector.group)
        for item in capability.job_runner_selectors
    ) == (
        ("final-gate", ("ubuntu-24.04",), None),
        ("selected-python", ("ubuntu-24.04",), None),
    )


@pytest.mark.parametrize(
    ("runs_on", "expected"),
    [
        ("dev", (("dev",), None)),
        ("[self-hosted, Linux, X64]", (("linux", "self-hosted", "x64"), None)),
        ("\n      group: build-runners", ((), "build-runners")),
        (
            "\n      group: build-runners\n      labels: Linux-X64",
            (("linux-x64",), "build-runners"),
        ),
        ("${{ matrix.runner }}", None),
        ("[self-hosted, '${{ matrix.os }}']", None),
        ("d\u00e9v", None),
        ("\n      labels: [linux, x64]", (("linux", "x64"), None)),
        ("\n      group: build-runners\n      unknown: value", None),
    ],
    ids=[
        "scalar",
        "conjunctive-labels",
        "group",
        "group-and-label",
        "dynamic-scalar",
        "dynamic-sequence",
        "non-ascii-label",
        "mapping-sequence",
        "unsupported-mapping-key",
    ],
)
def test_workflow_capability_projects_only_static_runner_selectors(
    runs_on: str,
    expected: tuple[tuple[str, ...], str | None] | None,
) -> None:
    capability = parse_workflow_capability(
        f"on: push\njobs:\n  test:\n    runs-on: {runs_on}\n".encode(),
        path=PATH,
        revision_sha=REVISION,
    )

    assert capability is not None
    selector = capability.runner_selector("test")
    actual = None if selector is None else (selector.labels, selector.group)
    assert actual == expected


def test_workflow_capability_canonicalizes_static_dependency_lists() -> None:
    capability = parse_workflow_capability(
        b"""
on: push
jobs:
  build: {}
  lint: {}
  package:
    needs: [lint, build]
""",
        path=PATH,
        revision_sha=REVISION,
    )

    assert capability is not None
    assert capability.job_needs == (
        ("build", ()),
        ("lint", ()),
        ("package", ("build", "lint")),
    )


@pytest.mark.parametrize(
    "content",
    [
        b"on: push\njobs: {}\n",
        b"on: push\njobs:\n  duplicate: {}\n  duplicate: {}\n",
        b"shared: &shared {runs-on: ubuntu-24.04}\non: push\njobs:\n  one: *shared\n",
        b"on: [true]\njobs:\n  one: {}\n",
        b"on: push\njobs:\n  one: !custom {}\n",
        b"\xef\xbb\xbfon: push\njobs:\n  one: {}\n",
        b"on: {push: null}\n",
        b"on: push\njobs:\n  one:\n    needs: missing\n",
        b"on: push\njobs:\n  one:\n    needs: ${{ matrix.job }}\n",
    ],
    ids=[
        "empty-jobs",
        "duplicate-job",
        "alias",
        "non-string-trigger",
        "custom-tag",
        "byte-order-mark",
        "missing-jobs",
        "unknown-needs",
        "dynamic-needs",
    ],
)
def test_workflow_capability_rejects_ambiguous_or_incomplete_yaml(content: bytes) -> None:
    assert parse_workflow_capability(content, path=PATH, revision_sha=REVISION) is None


def test_inventory_requires_default_branch_activation_and_exact_revision_job() -> None:
    capability = parse_workflow_capability(
        b"on: [push]\njobs:\n"
        b"  plan: {}\n"
        b"  selected-python:\n"
        b"    name: Selected Python\n"
        b"    needs: plan\n"
        b"  gate:\n"
        b"    name: Dynamic CI Gate\n"
        b"    if: always()\n"
        b"    needs: [plan, selected-python]\n",
        path=PATH,
        revision_sha=REVISION,
    )
    assert capability is not None
    active = ProviderWorkflowInventory(
        revision_sha=REVISION,
        default_branch_workflows=(DefaultBranchWorkflow(1, PATH, True),),
        revision_capabilities=(capability,),
    )
    inactive = ProviderWorkflowInventory(
        revision_sha=REVISION,
        default_branch_workflows=(DefaultBranchWorkflow(1, PATH, False),),
        revision_capabilities=(capability,),
    )

    assert active.admits_static_job(workflow_path=PATH, job_id="selected-python")
    assert active.admits_static_gate(
        workflow_path=PATH,
        job_id="gate",
        job_name="Dynamic CI Gate",
        required_dependencies=("plan", "selected-python"),
    )
    assert active.admits_exact_job_topology(
        workflow_path=PATH,
        expected=(
            ("gate", ("plan", "selected-python")),
            ("plan", ()),
            ("selected-python", ("plan",)),
        ),
    )
    assert not active.admits_exact_job_topology(
        workflow_path=PATH,
        expected=(("selected-python", ("other",)),),
    )
    assert not active.admits_static_gate(
        workflow_path=PATH,
        job_id="gate",
        job_name="Dynamic CI Gate",
        required_dependencies=("plan",),
    )
    assert not active.admits_static_gate(
        workflow_path=PATH,
        job_id="gate",
        job_name="Other Name",
        required_dependencies=("plan", "selected-python"),
    )
    assert not active.admits_static_job(workflow_path=PATH, job_id="other")
    assert not inactive.admits_static_job(workflow_path=PATH, job_id="selected-python")


@pytest.mark.parametrize(
    "job",
    [
        "gate: {}",
        "gate:\n    name: ${{ matrix.name }}\n    strategy:\n      matrix:\n        name: [Gate]",
        f"gate:\n    name: Gate\n    uses: owner/repo/.github/workflows/gate.yml@{'a' * 40}",
    ],
    ids=["implicit-name", "matrix-expression", "reusable-call"],
)
def test_inventory_never_claims_an_unstable_provider_job_name(job: str) -> None:
    capability = parse_workflow_capability(
        f"on: [push]\njobs:\n  {job}\n".encode(),
        path=PATH,
        revision_sha=REVISION,
    )

    assert capability is not None
    inventory = ProviderWorkflowInventory(
        revision_sha=REVISION,
        default_branch_workflows=(DefaultBranchWorkflow(1, PATH, True),),
        revision_capabilities=(capability,),
    )
    assert not inventory.admits_static_gate(
        workflow_path=PATH,
        job_id="gate",
        job_name="Gate",
        required_dependencies=("gate",),
    )


def test_inventory_rejects_duplicate_static_provider_names_for_a_gate() -> None:
    capability = parse_workflow_capability(
        b"on: [push]\njobs:\n  gate:\n    name: Stable Gate\n  other:\n    name: Stable Gate\n",
        path=PATH,
        revision_sha=REVISION,
    )
    assert capability is not None
    inventory = ProviderWorkflowInventory(
        revision_sha=REVISION,
        default_branch_workflows=(DefaultBranchWorkflow(1, PATH, True),),
        revision_capabilities=(capability,),
    )

    assert not inventory.admits_static_gate(
        workflow_path=PATH,
        job_id="gate",
        job_name="Stable Gate",
        required_dependencies=("other",),
    )


def test_inventory_admits_only_the_exact_acyclic_local_reusable_workflow_closure() -> None:
    child_path = ".github/workflows/child.yml"
    leaf_path = ".github/workflows/leaf.yml"
    root = parse_workflow_capability(
        (
            "on: pull_request\njobs:\n"
            f"  child:\n    uses: ./{child_path}\n"
            "  gate:\n    name: Gate\n    if: always()\n    needs: child\n"
        ).encode(),
        path=PATH,
        revision_sha=REVISION,
    )
    child = parse_workflow_capability(
        (f"on: workflow_call\njobs:\n  leaf:\n    uses: ./{leaf_path}\n").encode(),
        path=child_path,
        revision_sha=REVISION,
    )
    leaf = parse_workflow_capability(
        b"on: workflow_call\njobs:\n  test: {}\n",
        path=leaf_path,
        revision_sha=REVISION,
    )
    assert root is not None and child is not None and leaf is not None
    inventory = ProviderWorkflowInventory(
        revision_sha=REVISION,
        default_branch_workflows=(
            DefaultBranchWorkflow(1, PATH, True),
            DefaultBranchWorkflow(2, child_path, True),
            DefaultBranchWorkflow(3, leaf_path, True),
        ),
        revision_capabilities=(root, child, leaf),
    )

    assert inventory.admits_local_reusable_workflow_closure(
        root_paths=(PATH,),
        expected_paths=(PATH, child_path, leaf_path),
    )
    assert not inventory.admits_local_reusable_workflow_closure(
        root_paths=(PATH,),
        expected_paths=(PATH, child_path),
    )
    assert not inventory.admits_local_reusable_workflow_closure(
        root_paths=(child_path,),
        expected_paths=(PATH, child_path, leaf_path),
    )


def test_inventory_rejects_a_local_reusable_workflow_cycle() -> None:
    child_path = ".github/workflows/child.yml"
    root = parse_workflow_capability(
        f"on: workflow_call\njobs:\n  child:\n    uses: ./{child_path}\n".encode(),
        path=PATH,
        revision_sha=REVISION,
    )
    child = parse_workflow_capability(
        f"on: workflow_call\njobs:\n  root:\n    uses: ./{PATH}\n".encode(),
        path=child_path,
        revision_sha=REVISION,
    )
    assert root is not None and child is not None
    inventory = ProviderWorkflowInventory(
        revision_sha=REVISION,
        default_branch_workflows=(
            DefaultBranchWorkflow(1, PATH, True),
            DefaultBranchWorkflow(2, child_path, True),
        ),
        revision_capabilities=(root, child),
    )

    assert not inventory.admits_local_reusable_workflow_closure(
        root_paths=(PATH,),
        expected_paths=(PATH, child_path),
    )


@pytest.mark.parametrize(
    "uses",
    [
        "./.github/workflows/${{ inputs.workflow }}.yml",
        "./.github/workflows/nested/child.yml",
        "./../outside.yml",
        "example/workflows/.github/workflows/check.yml@main",
        "example/workflows/.github/workflows/check.yml@${{ inputs.ref }}",
        f"example/workflows/.github/workflows/check.yml@{'a' * 41}",
        f"example/workflows/.github/workflows/check.yml@{'a' * 64}",
        f"example/workflows/.github/workflows/nested/check.yml@{'a' * 40}",
    ],
    ids=[
        "dynamic-local",
        "nested-local",
        "traversal-local",
        "mutable-external",
        "dynamic-external",
        "hex-branch-41",
        "hex-branch-64",
        "nested-external",
    ],
)
def test_workflow_capability_rejects_unbound_reusable_targets(uses: str) -> None:
    content = f"on: pull_request\njobs:\n  child:\n    uses: {uses}\n".encode()

    assert parse_workflow_capability(content, path=PATH, revision_sha=REVISION) is None


def test_workflow_capability_admits_an_immutable_external_reusable_target() -> None:
    content = (
        "on: pull_request\njobs:\n  child:\n"
        f"    uses: example/workflows/.github/workflows/check.yml@{'a' * 40}\n"
    ).encode()

    capability = parse_workflow_capability(content, path=PATH, revision_sha=REVISION)

    assert capability is not None
    assert capability.local_reusable_workflow_paths == ()


def test_workflow_capability_admits_explicit_reusable_workflow_secrets() -> None:
    content = (
        "on: pull_request\njobs:\n  child:\n"
        f"    uses: ./{PATH}\n"
        "    secrets:\n      TOKEN: ${{ secrets.TOKEN }}\n"
    ).encode()

    assert parse_workflow_capability(content, path=PATH, revision_sha=REVISION) is not None


def test_inventory_admits_the_exact_generated_control_plane() -> None:
    capability = parse_workflow_capability(
        _CONTROL_WORKFLOW.read_bytes(),
        path=_CONTROL_WORKFLOW_PATH,
        revision_sha=REVISION,
    )
    assert capability is not None
    assert _admits_native_control_plane(capability)


def test_inventory_admits_the_exact_non_draft_request_variant() -> None:
    source = _CONTROL_WORKFLOW.read_text(encoding="utf-8")
    marker = "github.event_name == 'merge_group') }}"
    assert marker in source
    capability = parse_workflow_capability(
        source.replace(
            marker,
            "github.event_name == 'merge_group') &&\n      " + _DRAFT_EXCLUSION + " }}",
            1,
        ).encode(),
        path=_CONTROL_WORKFLOW_PATH,
        revision_sha=REVISION,
    )
    assert capability is not None
    assert _admits_native_control_plane(capability)


def test_inventory_derives_request_events_from_the_exact_trigger_surface() -> None:
    source = _CONTROL_WORKFLOW.read_text(encoding="utf-8")
    trigger = "  merge_group:\n    types: [checks_requested]\n"
    predicate = " || github.event_name == 'merge_group'"
    assert trigger in source
    assert predicate in source
    capability = parse_workflow_capability(
        source.replace(trigger, "", 1).replace(predicate, "", 1).encode(),
        path=_CONTROL_WORKFLOW_PATH,
        revision_sha=REVISION,
    )
    assert capability is not None
    assert capability.triggers == ("pull_request", "push", "workflow_dispatch")
    assert _admits_native_control_plane(capability)


def test_inventory_admits_reuse_only_with_the_direct_request_condition() -> None:
    source = _CONTROL_WORKFLOW.read_text(encoding="utf-8")
    marker = "  workflow_dispatch: {}\n"
    assert marker in source
    capability = parse_workflow_capability(
        source.replace(marker, "  workflow_call: {}\n" + marker, 1).encode(),
        path=_CONTROL_WORKFLOW_PATH,
        revision_sha=REVISION,
    )
    assert capability is not None
    assert capability.supports_reuse
    assert _admits_native_control_plane(capability)


def test_control_plane_requires_at_least_one_dynamic_event() -> None:
    assert (
        admitted_control_job_projection_hashes(
            workflow_path=_CONTROL_WORKFLOW_PATH,
            workflow_triggers=("workflow_call", "workflow_dispatch"),
            execution_kind="native-job-set",
            execution_job_ids=("lint", "test"),
            invocation_job_id=CONTROL_INVOCATION_JOB_ID,
            plan_request_job_id=PLAN_REQUEST_JOB_ID,
            plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
            plan_job_id="plan",
            gate_job_id="full-check-gate",
            fallback_job_id=None,
        )
        is None
    )


@pytest.mark.parametrize("mutable_length", [41, 64])
def test_control_projection_normalizes_only_exact_github_object_ids(
    mutable_length: int,
) -> None:
    immutable_a = {"steps": [{"uses": f"actions/checkout@{'a' * 40}"}]}
    immutable_b = {"steps": [{"uses": f"actions/checkout@{'b' * 40}"}]}
    mutable_hex_ref = {"steps": [{"uses": f"actions/checkout@{'a' * mutable_length}"}]}

    immutable_hash = control_job_projection_hash(immutable_a, workflow={})

    assert immutable_hash == control_job_projection_hash(immutable_b, workflow={})
    assert immutable_hash != control_job_projection_hash(mutable_hex_ref, workflow={})


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            f"uses: {PLAN_REQUEST_WORKFLOW_REF}",
            "uses: example-org/ci-coordinator/"
            ".github/workflows/trusted-plan-request.yml@" + "2" * 40,
        ),
        (
            "  plan-request:\n    name: Request dynamic CI plan\n    needs: ci-invocation",
            "  plan-request:\n    name: Request dynamic CI plan\n    needs: []",
        ),
        (
            "      id-token: write # Delegate OIDC only to the target-free coordinator requester.",
            "      contents: read # Remove the requester OIDC authority.",
        ),
        (
            "    permissions:\n      contents: read\n    outputs:",
            "    permissions:\n      contents: read\n      id-token: write\n    outputs:",
        ),
        (
            "run: node .ci-coordinator/ci-coordinator.cjs consume-plan",
            "run: node .ci-coordinator/other.cjs",
        ),
        (
            "CI_COORDINATOR_PLAN_CHUNK_6: ${{ needs['plan-request'].outputs.plan_chunk_6 }}",
            "CI_COORDINATOR_PLAN_CHUNK_7: ${{ needs['plan-request'].outputs.plan_chunk_7 }}",
        ),
        (
            "CI_COORDINATOR_PLAN_OIDC_AUDIENCE: ${{ vars.CI_COORDINATOR_PLAN_URL }}",
            "CI_COORDINATOR_PLAN_OIDC_AUDIENCE: https://untrusted.invalid/plan",
        ),
        (
            "          PLAN_PATH: ${{ runner.temp }}/dynamic-ci-plan.json",
            "          EXTRA: value\n          PLAN_PATH: ${{ runner.temp }}/dynamic-ci-plan.json",
        ),
        (
            "    if: ${{ always() && (needs.plan.result != 'success'",
            "    if: ${{ vars['SELECT_TEST'] }}",
        ),
        (
            "    if: ${{ always() && needs.lint.result == 'success'",
            "    if: ${{ toJSON(vars) != '{}' }}",
        ),
        (
            "    run: exit 1",
            "    run: echo cancelled && exit 1",
        ),
        (
            "jobs:\n  ci-invocation:",
            "env:\n  NODE_OPTIONS: --require ./ambient.cjs\njobs:\n  ci-invocation:",
        ),
        (
            "      - name: Consume coordinator response",
            "      - uses: ./ci/local-action\n      - name: Consume coordinator response",
        ),
        (
            "  full-check-gate:\n    name:",
            "  full-check-gate:\n    continue-on-error: true\n    name:",
        ),
        (
            "  lint:\n    name:",
            "  lint:\n    continue-on-error: true\n    name:",
        ),
        (
            "      - name: Admit exactly one successful execution path\n",
            (
                "      - name: Admit exactly one successful execution path\n"
                "        continue-on-error: true\n"
            ),
        ),
        (
            "      plan_valid: ${{ steps.validate.outputs.plan_valid }}",
            "      plan_valid: 'true'",
        ),
        (
            "          CI_PLAN_VALID: ${{ needs.plan.outputs.plan_valid }}",
            "          CI_PLAN_VALID: 'true'",
        ),
        (
            "          ref: ${{ job.workflow_sha }}",
            "          ref: ${{ github.sha }}",
        ),
        (
            "github.event_name == 'merge_group') }}",
            "github.event_name == 'workflow_dispatch') }}",
        ),
        (
            "needs['ci-invocation'].outputs.direct == 'true'",
            "github.repository == github.repository",
        ),
        (
            "CI_CALLER_WORKFLOW_REF: ${{ github.workflow_ref }}",
            "CI_CALLER_WORKFLOW_REF: ${{ job.workflow_ref }}",
        ),
        (
            "CI_DEFINING_WORKFLOW_REF: ${{ job.workflow_ref }}",
            "CI_DEFINING_WORKFLOW_REF: ${{ github.workflow_ref }}",
        ),
        (
            "    timeout-minutes: 1\n    permissions: {}",
            "    timeout-minutes: 2\n    permissions: {}",
        ),
        (
            "    permissions: {}\n    outputs:",
            "    permissions:\n      contents: read\n    outputs:",
        ),
        (
            "      direct: ${{ steps.classify.outputs.direct }}",
            "      direct: 'true'",
        ),
        (
            "          direct=false",
            "          direct=true",
        ),
        (
            '[ "$CI_CALLER_WORKFLOW_REF" = "$CI_DEFINING_WORKFLOW_REF" ]',
            '[ "$CI_CALLER_WORKFLOW_REF" != "$CI_DEFINING_WORKFLOW_REF" ]',
        ),
        (
            "  ci-invocation:\n    name:",
            "  ci-invocation:\n    continue-on-error: true\n    name:",
        ),
        (
            "  ci-invocation:\n    name: Classify workflow invocation\n    runs-on: ubuntu-24.04",
            (
                "  ci-invocation:\n    name: Classify workflow invocation\n    needs: lint\n"
                "    runs-on: ubuntu-24.04"
            ),
        ),
        (
            "CI_WORKFLOW_REF: ${{ job.workflow_ref }}",
            "CI_WORKFLOW_REF: ${{ github.workflow_ref }}",
        ),
        (
            "CI_WORKFLOW_SHA: ${{ job.workflow_sha }}",
            "CI_WORKFLOW_SHA: ${{ github.workflow_sha }}",
        ),
    ],
    ids=[
        "unexpected-requester-revision",
        "wrong-requester-dependency",
        "requester-missing-oidc",
        "target-plan-oidc",
        "consumer-command",
        "incomplete-chunk-transport",
        "wrong-plan-audience",
        "request-environment",
        "bracketed-ambient-route-context",
        "bare-ambient-route-context",
        "cancel-command",
        "workflow-environment",
        "local-action",
        "gate-job-continue-on-error",
        "execution-job-continue-on-error",
        "gate-step-continue-on-error",
        "forged-plan-output",
        "forged-gate-input",
        "caller-checkout-source",
        "unsupported-request-event",
        "missing-direct-invocation-evidence",
        "caller-identity-source",
        "defining-identity-source",
        "classifier-timeout",
        "classifier-permissions",
        "classifier-output",
        "classifier-default",
        "inverted-invocation-comparison",
        "classifier-continue-on-error",
        "classifier-dependency",
        "caller-workflow-ref",
        "caller-workflow-sha",
    ],
)
def test_inventory_rejects_control_plane_authority_drift(old: str, new: str) -> None:
    content = _CONTROL_WORKFLOW.read_bytes()
    assert old.encode() in content
    capability = parse_workflow_capability(
        content.replace(old.encode(), new.encode(), 1),
        path=_CONTROL_WORKFLOW_PATH,
        revision_sha=REVISION,
    )
    assert capability is not None
    inventory = ProviderWorkflowInventory(
        revision_sha=REVISION,
        default_branch_workflows=(),
        revision_capabilities=(capability,),
    )

    assert not inventory.admits_control_plane(
        workflow_path=_CONTROL_WORKFLOW_PATH,
        execution_kind="native-job-set",
        invocation_job_id=CONTROL_INVOCATION_JOB_ID,
        plan_request_job_id=PLAN_REQUEST_JOB_ID,
        plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
        plan_job_id="plan",
        fallback_job_id=None,
        gate_job_id="full-check-gate",
        execution_job_ids=("lint", "test"),
    )


def test_requester_projection_does_not_embed_the_workflow_path() -> None:
    ordinary = _control_projection_hashes(_CONTROL_WORKFLOW_PATH)
    hostile = _control_projection_hashes(".github/workflows/full' || always() || '.yml")

    assert {candidate[1] for candidate in ordinary} == {candidate[1] for candidate in hostile}


def test_control_projection_candidates_are_complete_non_mixable_tuples() -> None:
    first, second = _control_projection_hashes(_CONTROL_WORKFLOW_PATH)

    assert len(first) == len(second) == 4
    assert first[1] != second[1]
    assert first[0] == second[0]
    assert first[2:] == second[2:]


def _control_projection_hashes(workflow_path: str) -> tuple[tuple[str, str, str, str], ...]:
    candidates = admitted_control_job_projection_hashes(
        workflow_path=workflow_path,
        workflow_triggers=("pull_request", "push", "merge_group"),
        execution_kind="native-job-set",
        execution_job_ids=("lint", "test"),
        invocation_job_id=CONTROL_INVOCATION_JOB_ID,
        plan_request_job_id=PLAN_REQUEST_JOB_ID,
        plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
        plan_job_id="plan",
        gate_job_id="full-check-gate",
        fallback_job_id=None,
    )
    assert candidates is not None
    return candidates


@pytest.mark.parametrize(
    "context",
    (
        "EnV",
        "VaRs",
        "JoB",
        "JoBs",
        "StEpS",
        "RuNnEr",
        "SeCrEtS",
        "StRaTeGy",
        "MaTrIx",
        "InPuTs",
    ),
)
def test_inventory_rejects_mixed_case_ambient_route_contexts(context: str) -> None:
    content = _CONTROL_WORKFLOW.read_text().replace(
        "    if: ${{ always() && (needs.plan.result != 'success' || "
        "needs.plan.outputs.plan_valid != 'true' || "
        "needs.plan.outputs.fallback != 'false' || "
        "contains(fromJSON(needs.plan.outputs.selected_jobs || '[]'), 'lint')) }}",
        f"    if: ${{{{ toJSON({context}) != '{{}}' }}}}",
        1,
    )
    capability = parse_workflow_capability(
        content.encode(),
        path=_CONTROL_WORKFLOW_PATH,
        revision_sha=REVISION,
    )
    assert capability is not None
    inventory = ProviderWorkflowInventory(
        revision_sha=REVISION,
        default_branch_workflows=(),
        revision_capabilities=(capability,),
    )

    assert not inventory.admits_control_plane(
        workflow_path=_CONTROL_WORKFLOW_PATH,
        execution_kind="native-job-set",
        invocation_job_id=CONTROL_INVOCATION_JOB_ID,
        plan_request_job_id=PLAN_REQUEST_JOB_ID,
        plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
        plan_job_id="plan",
        fallback_job_id=None,
        gate_job_id="full-check-gate",
        execution_job_ids=("lint", "test"),
    )


def test_inventory_normalizes_mixed_case_allowed_route_contexts() -> None:
    content = _CONTROL_WORKFLOW.read_text().replace(
        "    if: ${{ always() && (needs.plan.result != 'success' || "
        "needs.plan.outputs.plan_valid != 'true' || "
        "needs.plan.outputs.fallback != 'false' || "
        "contains(fromJSON(needs.plan.outputs.selected_jobs || '[]'), 'lint')) }}",
        "    if: ${{ GitHub.event_name == 'pull_request' && Needs.plan.result == 'success' }}",
        1,
    )
    capability = parse_workflow_capability(
        content.encode(),
        path=_CONTROL_WORKFLOW_PATH,
        revision_sha=REVISION,
    )
    assert capability is not None
    inventory = ProviderWorkflowInventory(
        revision_sha=REVISION,
        default_branch_workflows=(),
        revision_capabilities=(capability,),
    )

    assert inventory.admits_control_plane(
        workflow_path=_CONTROL_WORKFLOW_PATH,
        execution_kind="native-job-set",
        invocation_job_id=CONTROL_INVOCATION_JOB_ID,
        plan_request_job_id=PLAN_REQUEST_JOB_ID,
        plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
        plan_job_id="plan",
        fallback_job_id=None,
        gate_job_id="full-check-gate",
        execution_job_ids=("lint", "test"),
    )


@pytest.mark.parametrize(
    ("old", "new"),
    [
        (
            b"  full-ci:\n    name:",
            b"  full-ci:\n    continue-on-error: true\n    name:",
        ),
        (
            b"    if: ${{ always() && (needs.plan.result != 'success'",
            b"    if: ${{ vars['FORCE_FULL_CI'] }}",
        ),
    ],
    ids=["continue-on-error", "ambient-route-context"],
)
def test_inventory_rejects_unsafe_fallback_job(
    old: bytes,
    new: bytes,
) -> None:
    workflow_path = ".github/workflows/ci-coordinator-bootstrap.yml"
    source = (_REPOSITORY_ROOT / "fixtures/target-repository" / workflow_path).read_bytes()
    assert old in source
    capability = parse_workflow_capability(
        source.replace(old, new, 1),
        path=workflow_path,
        revision_sha=REVISION,
    )
    assert capability is not None
    inventory = ProviderWorkflowInventory(
        revision_sha=REVISION,
        default_branch_workflows=(),
        revision_capabilities=(capability,),
    )

    assert not inventory.admits_control_plane(
        workflow_path=workflow_path,
        execution_kind="witness-shards",
        invocation_job_id=CONTROL_INVOCATION_JOB_ID,
        plan_request_job_id=PLAN_REQUEST_JOB_ID,
        plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
        plan_job_id="plan",
        fallback_job_id="full-ci",
        gate_job_id="bootstrap-gate",
        execution_job_ids=(
            "selected-python-linux",
            "selected-python-postgres",
        ),
    )


@pytest.mark.parametrize(
    "fragment",
    [
        "steps:\n      - uses: actions/checkout@main",
        f"steps:\n      - uses: actions/checkout@{'a' * 41}",
        f"steps:\n      - uses: actions/checkout@{'a' * 64}",
        "steps:\n      - uses: docker://python:3.13",
        "container: python:3.13",
        "services:\n      postgres:\n        image: postgres:18.6",
    ],
    ids=[
        "mutable-action",
        "hex-branch-action-41",
        "hex-branch-action-64",
        "mutable-container-action",
        "mutable-job-image",
        "mutable-service",
    ],
)
def test_workflow_capability_rejects_mutable_execution_dependencies(fragment: str) -> None:
    content = (f"on: push\njobs:\n  test:\n    runs-on: ubuntu-24.04\n    {fragment}\n").encode()

    assert parse_workflow_capability(content, path=PATH, revision_sha=REVISION) is None


def test_workflow_capability_admits_content_addressed_execution_dependencies() -> None:
    capability = parse_workflow_capability(
        (
            "on: push\njobs:\n  test:\n"
            f"    container: python@sha256:{'a' * 64}\n"
            "    services:\n"
            f"      postgres:\n        image: postgres@sha256:{'b' * 64}\n"
            "    steps:\n"
            f"      - uses: actions/checkout@{'c' * 40}\n"
            f"      - uses: docker://python@sha256:{'d' * 64}\n"
        ).encode(),
        path=PATH,
        revision_sha=REVISION,
    )

    assert capability is not None


@pytest.mark.parametrize(
    "reference",
    [
        "$/.github/actions/check",
        "./.github/actions/check",
    ],
    ids=["running-commit", "workspace-relative"],
)
def test_workflow_capability_admits_static_local_action_forms(reference: str) -> None:
    capability = parse_workflow_capability(
        (
            "on: push\njobs:\n  test:\n"
            "    runs-on: ubuntu-24.04\n"
            f"    steps:\n      - uses: {reference}\n"
        ).encode(),
        path=PATH,
        revision_sha=REVISION,
    )

    assert capability is not None


@pytest.mark.parametrize(
    "reference",
    [
        "$/.github/actions/check@main",
        "$/.github/actions/../check",
        "./.github/actions/./check",
        "./.github/actions//check",
        r".\.github\actions\check",
    ],
    ids=[
        "dollar-root-ref",
        "parent-segment",
        "current-segment",
        "empty-segment",
        "backslash",
    ],
)
def test_workflow_capability_rejects_ambiguous_local_action_paths(reference: str) -> None:
    content = (
        "on: push\njobs:\n  test:\n"
        "    runs-on: ubuntu-24.04\n"
        f"    steps:\n      - uses: {reference}\n"
    ).encode()

    assert parse_workflow_capability(content, path=PATH, revision_sha=REVISION) is None


@pytest.mark.parametrize("prefix", ["./", "$/"], ids=["dot-root", "dollar-root"])
def test_workflow_capability_canonicalizes_local_reusable_workflow_paths(prefix: str) -> None:
    capability = parse_workflow_capability(
        (
            f"on: pull_request\njobs:\n  child:\n    uses: {prefix}.github/workflows/child.yml\n"
        ).encode(),
        path=PATH,
        revision_sha=REVISION,
    )

    assert capability is not None
    assert capability.local_reusable_workflow_paths == (".github/workflows/child.yml",)


@pytest.mark.parametrize(
    "unsafe_declaration",
    [
        "env:\n  AMBIENT: value\njobs:\n  test: {}\n",
        "defaults:\n  run:\n    shell: bash\njobs:\n  test: {}\n",
        "jobs:\n  test:\n    continue-on-error: true\n",
        "jobs:\n  test:\n    steps:\n      - run: exit 1\n        continue-on-error: true\n",
    ],
    ids=[
        "workflow-environment",
        "workflow-defaults",
        "job-continue-on-error",
        "step-continue-on-error",
    ],
)
def test_inventory_rejects_failure_weakening_in_called_workflow(
    unsafe_declaration: str,
) -> None:
    child_path = ".github/workflows/child.yml"
    root = parse_workflow_capability(
        f"on: pull_request\njobs:\n  child:\n    uses: $/{child_path}\n".encode(),
        path=PATH,
        revision_sha=REVISION,
    )
    child = parse_workflow_capability(
        f"on: workflow_call\n{unsafe_declaration}".encode(),
        path=child_path,
        revision_sha=REVISION,
    )
    assert root is not None and child is not None
    inventory = ProviderWorkflowInventory(
        revision_sha=REVISION,
        default_branch_workflows=(),
        revision_capabilities=(root, child),
    )

    assert not inventory.admits_local_reusable_workflow_closure(
        root_paths=(PATH,),
        expected_paths=(PATH, child_path),
    )


@pytest.mark.parametrize(
    ("shape", "called_workflows", "expected"),
    [
        ("depth", 9, True),
        ("depth", 10, False),
        ("unique", 50, True),
        ("unique", 51, False),
    ],
    ids=["depth-10", "depth-11", "unique-50", "unique-51"],
)
def test_inventory_enforces_provider_reusable_workflow_limits(
    shape: str,
    called_workflows: int,
    expected: bool,
) -> None:
    paths = tuple(f".github/workflows/child-{index}.yml" for index in range(called_workflows))
    if shape == "depth":
        capabilities = tuple(
            _workflow_call_capability(
                path=PATH if index == 0 else paths[index - 1],
                target=paths[index] if index < called_workflows else None,
            )
            for index in range(called_workflows + 1)
        )
    else:
        root = parse_workflow_capability(
            (
                "on: pull_request\njobs:\n"
                + "".join(
                    f"  child-{index}:\n    uses: ./{path}\n" for index, path in enumerate(paths)
                )
            ).encode(),
            path=PATH,
            revision_sha=REVISION,
        )
        assert root is not None
        capabilities = (
            root,
            *(_workflow_call_capability(path=path, target=None) for path in paths),
        )
    inventory = ProviderWorkflowInventory(
        revision_sha=REVISION,
        default_branch_workflows=(),
        revision_capabilities=tuple(
            sorted(capabilities, key=lambda item: item.path.encode("utf-16-be"))
        ),
    )

    assert (
        inventory.admits_local_reusable_workflow_closure(
            root_paths=(PATH,),
            expected_paths=tuple(sorted((PATH, *paths))),
        )
        is expected
    )


def _admits_native_control_plane(capability: RevisionWorkflowCapability) -> bool:
    inventory = ProviderWorkflowInventory(
        revision_sha=REVISION,
        default_branch_workflows=(),
        revision_capabilities=(capability,),
    )
    return inventory.admits_control_plane(
        workflow_path=_CONTROL_WORKFLOW_PATH,
        execution_kind="native-job-set",
        invocation_job_id=CONTROL_INVOCATION_JOB_ID,
        plan_request_job_id=PLAN_REQUEST_JOB_ID,
        plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
        plan_job_id="plan",
        fallback_job_id=None,
        gate_job_id="full-check-gate",
        execution_job_ids=("lint", "test"),
    )


def _workflow_call_capability(
    *,
    path: str,
    target: str | None,
) -> RevisionWorkflowCapability:
    body = (
        "on: workflow_call\njobs:\n"
        + ("  test: {}\n" if target is None else f"  child:\n    uses: ./{target}\n")
    ).encode()
    capability = parse_workflow_capability(body, path=path, revision_sha=REVISION)
    assert capability is not None
    return capability

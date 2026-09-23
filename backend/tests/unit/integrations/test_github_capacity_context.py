from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path

import pytest
from package_b_support import PLAN_REQUEST_JOB_ID, PLAN_REQUEST_WORKFLOW_REF

from ci_coordinator.execution_orchestration import (
    LOCAL_PLAN_REQUEST_WORKFLOW_PATH,
    LOCAL_PLAN_REQUEST_WORKFLOW_REF,
    TARGET_CONTROL_FILE_PATHS,
    TARGET_EXECUTION_REGISTRY_PATH,
)
from ci_coordinator.integrations.github._routes import GitHubRepository
from ci_coordinator.integrations.github.adapter_snapshot import (
    GitHubAdapterSnapshotLoader,
)
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.capacity_context import (
    CAPACITY_MANIFEST_PATH,
    GitHubCapacityInputsProvider,
)
from ci_coordinator.integrations.github.contracts import (
    GitHubRequest,
    GitHubResponse,
)
from ci_coordinator.integrations.github.transport import GitHubTransport
from ci_coordinator.integrations.github.workflow_discovery_client import (
    WorkflowDiscoveryClient,
)
from ci_coordinator.plan_issuance import PlanRequest
from ci_coordinator.runner_capacity import parse_test_manifest
from ci_coordinator.target_artifacts.requester import render_plan_requester

from ._adapter_snapshot_support import REVISION_SHA, AdapterSnapshotTransport

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
WORKFLOW_PATH = ".github/workflows/ci-coordinator-bootstrap.yml"
NATIVE_WORKFLOW_PATH = ".github/workflows/full-check.yml"
NATIVE_LINT_WORKFLOW_PATH = ".github/workflows/lint.yml"
CHECKOUT_REVISION = "3d3c42e5aac5ba805825da76410c181273ba90b1"


class _Factory:
    def __init__(self, transport: GitHubTransport) -> None:
        self._transport = transport
        self.installation_ids: list[int] = []

    def for_installation(self, installation_id: int) -> GitHubTransport:
        self.installation_ids.append(installation_id)
        return self._transport


class _QueueTransport:
    def __init__(self, responses: tuple[GitHubResponse, ...]) -> None:
        self._responses = list(responses)
        self.requests: list[GitHubRequest] = []

    async def send(self, request: GitHubRequest) -> GitHubResponse:
        self.requests.append(request)
        return self._responses.pop(0)


def test_capacity_manifest_is_strict_bounded_and_canonical() -> None:
    parsed = parse_test_manifest(_manifest_bytes())

    assert parsed is not None
    assert tuple(test.test_id for test in parsed.tests) == (
        "backend/test-a",
        "backend/test-b",
    )
    assert parsed.duration_history == (
        ("backend/test-a", 10.0),
        ("backend/test-b", 20.0),
    )
    assert (
        parse_test_manifest(
            b'{"schemaVersion":"dynamic-ci-test-manifest/v1",'
            b'"generator":{"id":"tests","version":"1"},"tests":[]}'
        )
        is not None
    )
    assert (
        parse_test_manifest(
            b'{"schemaVersion":"dynamic-ci-test-manifest/v1",'
            b'"schemaVersion":"dynamic-ci-test-manifest/v1",'
            b'"generator":{"id":"tests","version":"1"},"tests":[]}'
        )
        is None
    )


@pytest.mark.parametrize("modified", [False, True])
def test_local_requester_requires_canonical_provider_bytes_even_with_a_matching_digest(
    modified: bool,
) -> None:
    workflow = _workflow_bytes(execution_kind="native-job-set").replace(
        PLAN_REQUEST_WORKFLOW_REF.encode(),
        LOCAL_PLAN_REQUEST_WORKFLOW_REF.encode(),
    )
    assert LOCAL_PLAN_REQUEST_WORKFLOW_REF.encode() in workflow
    requester = render_plan_requester() + (b"# changed target requester\n" if modified else b"")
    additional = {LOCAL_PLAN_REQUEST_WORKFLOW_PATH: requester}
    registry = json.loads(
        _registry_bytes(
            execution_kind="native-job-set",
            workflow_content=workflow,
            additional_workflows=additional,
        )
    )
    registry["workflows"][0]["planRequestWorkflowRef"] = LOCAL_PLAN_REQUEST_WORKFLOW_REF
    transport = _transport(
        registry=_json(registry), workflow=workflow, additional_workflows=additional
    )

    result = asyncio.run(
        GitHubCapacityInputsProvider(_Factory(transport)).load(_request(), REVISION_SHA)
    )

    assert (result is not None) is (not modified)
    assert len(transport.requests) == 9
    assert transport.maximum_concurrent_blobs <= 4


def test_native_target_uses_only_the_exact_git_object_snapshot() -> None:
    workflow = _workflow_bytes(execution_kind="native-job-set")
    transport = _transport(
        registry=_registry_bytes(
            execution_kind="native-job-set",
            workflow_content=workflow,
        ),
        workflow=workflow,
    )
    factory = _Factory(transport)

    result = asyncio.run(GitHubCapacityInputsProvider(factory).load(_request(), REVISION_SHA))

    assert result is not None
    assert result.capacity_manifest is None
    assert factory.installation_ids == [100]
    assert [request.operation for request in transport.requests] == [
        "workflow_discovery.get_commit",
        "workflow_discovery.get_tree",
        "workflow_discovery.get_tree",
        "workflow_discovery.get_blob",
        "workflow_discovery.get_tree",
        "workflow_discovery.get_tree",
        *(["workflow_discovery.get_blob"] * 2),
    ]
    assert all(
        request.operation != "workflow_catalog.list_workflows" for request in transport.requests
    )


def test_sharded_target_loads_only_its_manifest_from_the_head_revision() -> None:
    workflow = _workflow_bytes()
    transport = _transport(
        registry=_registry_bytes(workflow_content=workflow),
        workflow=workflow,
        manifest=_manifest_bytes(),
    )

    result = asyncio.run(
        GitHubCapacityInputsProvider(_Factory(transport)).load(
            _request(),
            REVISION_SHA,
        )
    )

    assert result is not None
    assert result.capacity_manifest is not None
    assert tuple(selector.to_identity_mapping() for selector in result.capacity_selectors) == (
        {
            "capacityClassId": "self-hosted-default",
            "labels": ["ubuntu-24.04"],
            "group": None,
        },
    )
    manifest_requests = transport.requests[-4:]
    assert [request.operation for request in manifest_requests] == [
        "workflow_discovery.get_commit",
        "workflow_discovery.get_tree",
        "workflow_discovery.get_tree",
        "workflow_discovery.get_blob",
    ]
    assert manifest_requests[0].path.endswith(f"/git/commits/{_request().head_sha}")
    assert all(request.query == () for request in manifest_requests)


@pytest.mark.parametrize("replacement", [b"prod", b"${{ matrix.runner }}"])
def test_capacity_class_requires_one_static_selector_across_every_profile(
    replacement: bytes,
) -> None:
    workflow = _workflow_bytes()
    marker = b"  selected-python-postgres:\n"
    prefix, suffix = workflow.split(marker, 1)
    workflow = (
        prefix
        + marker
        + suffix.replace(
            b"    runs-on: ubuntu-24.04\n",
            b"    runs-on: " + replacement + b"\n",
            1,
        )
    )
    transport = _transport(
        registry=_registry_bytes(workflow_content=workflow),
        workflow=workflow,
        manifest=_manifest_bytes(),
    )

    result = asyncio.run(
        GitHubCapacityInputsProvider(_Factory(transport)).load(
            _request(),
            REVISION_SHA,
        )
    )

    assert result is not None
    assert result.capacity_manifest is not None
    assert result.capacity_selectors == ()


def test_symlinked_capacity_manifest_cannot_control_selected_shards() -> None:
    workflow = _workflow_bytes()
    transport = _transport(
        registry=_registry_bytes(workflow_content=workflow),
        workflow=workflow,
        manifest=_manifest_bytes(),
        mode_overrides={CAPACITY_MANIFEST_PATH: "120000"},
    )

    result = asyncio.run(
        GitHubCapacityInputsProvider(_Factory(transport)).load(
            _request(),
            REVISION_SHA,
        )
    )

    assert result is not None
    assert result.capacity_manifest is None
    assert transport.requests[-1].operation == "workflow_discovery.get_tree"


def test_unavailable_manifest_preserves_exact_target_authority() -> None:
    workflow = _workflow_bytes()
    transport = _transport(
        registry=_registry_bytes(workflow_content=workflow),
        workflow=workflow,
    )

    result = asyncio.run(
        GitHubCapacityInputsProvider(_Factory(transport)).load(
            _request(),
            REVISION_SHA,
        )
    )

    assert result is not None
    assert result.capacity_manifest is None
    assert result.target_registry.workflows[0].provider_signal.job_name == "Dynamic CI Bootstrap"


@pytest.mark.parametrize(
    ("child_workflow", "expected"),
    [
        (
            (
                "on: workflow_call\njobs:\n  lint:\n    runs-on: ubuntu-24.04\n"
                "    steps:\n      - run: printf 'safe lint\\n'\n"
            ),
            True,
        ),
        (
            (
                "on: workflow_call\nenv:\n  AMBIENT: value\njobs:\n  lint:\n"
                "    runs-on: ubuntu-24.04\n    steps:\n      - run: exit 0\n"
            ),
            False,
        ),
        (
            (
                "on: workflow_call\ndefaults:\n  run:\n    shell: bash\njobs:\n  lint:\n"
                "    runs-on: ubuntu-24.04\n    steps:\n      - run: exit 0\n"
            ),
            False,
        ),
        (
            (
                "on: workflow_call\njobs:\n  lint:\n    continue-on-error: true\n"
                "    runs-on: ubuntu-24.04\n    steps:\n      - run: exit 1\n"
            ),
            False,
        ),
        (
            (
                "on: workflow_call\njobs:\n  lint:\n    runs-on: ubuntu-24.04\n"
                "    steps:\n      - run: exit 1\n        continue-on-error: true\n"
            ),
            False,
        ),
    ],
    ids=[
        "safe",
        "workflow-environment",
        "workflow-defaults",
        "job-continue-on-error",
        "step-continue-on-error",
    ],
)
def test_capacity_authority_requires_safe_reusable_workflow_closure(
    child_workflow: str,
    expected: bool,
) -> None:
    root_workflow = _native_reusable_workflow_bytes()
    child_workflows = {NATIVE_LINT_WORKFLOW_PATH: child_workflow.encode()}
    transport = _transport(
        registry=_registry_bytes(
            execution_kind="native-job-set",
            workflow_content=root_workflow,
            additional_workflows=child_workflows,
        ),
        workflow=root_workflow,
        additional_workflows=child_workflows,
    )

    result = asyncio.run(
        GitHubCapacityInputsProvider(_Factory(transport)).load(
            _request(),
            REVISION_SHA,
        )
    )

    assert (result is not None) is expected


def test_capacity_authority_rejects_inherited_reusable_workflow_secrets() -> None:
    root_workflow = _native_reusable_workflow_bytes().replace(
        b"    uses: $/.github/workflows/lint.yml\n",
        b"    uses: $/.github/workflows/lint.yml\n    secrets: inherit\n",
    )
    child_workflows = {
        NATIVE_LINT_WORKFLOW_PATH: (
            b"on: workflow_call\njobs:\n  lint:\n    runs-on: ubuntu-24.04\n"
            b"    steps:\n      - run: exit 0\n"
        )
    }
    transport = _transport(
        registry=_registry_bytes(
            execution_kind="native-job-set",
            workflow_content=root_workflow,
            additional_workflows=child_workflows,
        ),
        workflow=root_workflow,
        additional_workflows=child_workflows,
    )

    result = asyncio.run(
        GitHubCapacityInputsProvider(_Factory(transport)).load(
            _request(),
            REVISION_SHA,
        )
    )

    assert result is None


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.replace(b"    if: ${{ always() }}\n", b""),
        lambda value: value.replace(
            b"  plan:\n",
            b"  rogue: {}\n  plan:\n",
        ),
        lambda value: value.replace(
            PLAN_REQUEST_WORKFLOW_REF.encode(),
            (
                "example-org/ci-coordinator/.github/workflows/trusted-plan-request.yml@" + "2" * 40
            ).encode(),
        ),
        lambda value: value.replace(
            b"  plan:\n    name: Resolve dynamic CI plan\n    needs: plan-request",
            b"  plan:\n    name: Resolve dynamic CI plan\n    needs: []",
        ),
        lambda value: value.replace(
            b"      id-token: write # Delegate OIDC only to the target-free coordinator requester.",
            b"      contents: read # Remove the requester OIDC authority.",
        ),
        lambda value: value.replace(
            b"    permissions:\n      contents: read\n    outputs:",
            b"    permissions:\n      contents: read\n      id-token: write\n    outputs:",
        ),
        lambda value: value.replace(
            b"node .ci-coordinator/ci-coordinator.cjs consume-plan",
            b"node .ci-coordinator/other.cjs",
        ),
        lambda value: value.replace(
            b"CI_COORDINATOR_PLAN_CHUNK_6: ${{ needs['plan-request'].outputs.plan_chunk_6 }}",
            b"CI_COORDINATOR_PLAN_CHUNK_7: ${{ needs['plan-request'].outputs.plan_chunk_7 }}",
        ),
        lambda value: value.replace(
            b"CI_COORDINATOR_PLAN_OIDC_AUDIENCE: ${{ vars.CI_COORDINATOR_PLAN_URL }}",
            b"CI_COORDINATOR_PLAN_OIDC_AUDIENCE: https://untrusted.invalid/plan",
        ),
        lambda value: value.replace(
            b"          PLAN_PATH: ${{ runner.temp }}/dynamic-ci-plan.json\n",
            b"          EXTRA: value\n"
            b"          PLAN_PATH: ${{ runner.temp }}/dynamic-ci-plan.json\n",
            1,
        ),
        lambda value: value.replace(
            b"if: ${{ needs.plan.result == 'success'",
            b"if: ${{ vars['SELECT_TEST'] && needs.plan.result == 'success'",
            1,
        ),
        lambda value: value.replace(
            f"actions/checkout@{CHECKOUT_REVISION}".encode(),
            b"actions/checkout@main",
        ),
    ],
    ids=[
        "conditional-gate",
        "unregistered-job",
        "unexpected-requester-revision",
        "wrong-plan-dependency",
        "requester-missing-oidc",
        "target-plan-oidc",
        "consumer-command",
        "incomplete-chunk-transport",
        "wrong-plan-audience",
        "control-environment",
        "ambient-route",
        "mutable-action",
    ],
)
def test_semantic_adapter_drift_never_becomes_target_authority(
    mutation: Callable[[bytes], bytes],
) -> None:
    baseline = _workflow_bytes()
    workflow = mutation(baseline)
    assert workflow != baseline
    transport = _transport(
        registry=_registry_bytes(workflow_content=workflow),
        workflow=workflow,
    )

    result = asyncio.run(
        GitHubCapacityInputsProvider(_Factory(transport)).load(
            _request(),
            REVISION_SHA,
        )
    )

    assert result is None


@pytest.mark.parametrize(
    "drift",
    ["workflow-bytes", "control-bytes"],
)
def test_content_outside_registry_digests_is_rejected(drift: str) -> None:
    bound_workflow = _workflow_bytes(execution_kind="native-job-set")
    actual_workflow = bound_workflow
    controls = _control_files()
    if drift == "workflow-bytes":
        actual_workflow += b"# unbound change\n"
    else:
        first_path = TARGET_CONTROL_FILE_PATHS[0]
        controls[first_path] += b"// unbound change\n"
    transport = _transport(
        registry=_registry_bytes(
            execution_kind="native-job-set",
            workflow_content=bound_workflow,
        ),
        workflow=actual_workflow,
        control_files=controls,
    )

    result = asyncio.run(
        GitHubCapacityInputsProvider(_Factory(transport)).load(
            _request(),
            REVISION_SHA,
        )
    )

    assert result is None


@pytest.mark.parametrize(
    "path",
    [
        TARGET_EXECUTION_REGISTRY_PATH,
        TARGET_CONTROL_FILE_PATHS[0],
        NATIVE_WORKFLOW_PATH,
    ],
    ids=["registry", "control", "workflow"],
)
def test_non_regular_adapter_objects_are_rejected_before_execution(
    path: str,
) -> None:
    workflow = _workflow_bytes(execution_kind="native-job-set")
    transport = _transport(
        registry=_registry_bytes(
            execution_kind="native-job-set",
            workflow_content=workflow,
        ),
        workflow=workflow,
        mode_overrides={path: "120000"},
    )

    result = asyncio.run(
        GitHubCapacityInputsProvider(_Factory(transport)).load(
            _request(),
            REVISION_SHA,
        )
    )

    assert result is None


@pytest.mark.parametrize(
    "authority_revision",
    ["", "A" * 40, "a" * 39, "a" * 41, "a" * 64, "a" * 65],
    ids=["empty", "uppercase", "short", "intermediate", "sha256-profile", "long"],
)
def test_invalid_authority_revision_performs_no_provider_io(
    authority_revision: str,
) -> None:
    transport = _QueueTransport(())

    result = asyncio.run(
        GitHubCapacityInputsProvider(_Factory(transport)).load(
            _request(),
            authority_revision,
        )
    )

    assert result is None
    assert transport.requests == []


def test_maximum_adapter_bundle_obeys_its_per_load_read_budget() -> None:
    workflows = {
        f".github/workflows/workflow-{index:02}.yml": (b"on: workflow_call\njobs:\n  test: {}\n")
        for index in range(32)
    }
    registry = _maximum_registry_bytes(workflows)
    transport = AdapterSnapshotTransport(
        registry=registry,
        workflows=workflows,
        control_files=_control_files(),
    )
    loader = GitHubAdapterSnapshotLoader(
        WorkflowDiscoveryClient(
            transport,
            api_version=GITHUB_API_VERSION,
        )
    )

    snapshot = asyncio.run(
        loader.load(
            GitHubRepository("example-org", "target"),
            revision_sha=REVISION_SHA,
        )
    )

    assert snapshot is not None
    assert len(transport.requests) == 39
    assert transport.maximum_concurrent_blobs == 4


@pytest.mark.parametrize("failure_kind", ["exception", "provider-rejection"])
def test_adapter_failure_cancels_and_joins_sibling_blob_reads(failure_kind: str) -> None:
    workflow = _workflow_bytes(execution_kind="native-job-set")
    auxiliary_workflows = {
        NATIVE_LINT_WORKFLOW_PATH: b"on: workflow_call\njobs:\n  lint: {}\n",
        ".github/workflows/test.yml": b"on: workflow_call\njobs:\n  test: {}\n",
    }
    controls = _control_files()
    assert 1 + len(auxiliary_workflows) + len(controls) == 4
    failed_content = controls[min(controls)]
    transport = AdapterSnapshotTransport(
        registry=_registry_bytes(
            execution_kind="native-job-set",
            workflow_content=workflow,
            additional_workflows=auxiliary_workflows,
        ),
        workflows={NATIVE_WORKFLOW_PATH: workflow, **auxiliary_workflows},
        control_files=controls,
        failing_blob_content=failed_content if failure_kind == "exception" else None,
        rejected_blob_content=failed_content if failure_kind == "provider-rejection" else None,
    )

    result = asyncio.run(
        GitHubCapacityInputsProvider(_Factory(transport)).load(
            _request(),
            REVISION_SHA,
        )
    )

    assert result is None
    assert transport.maximum_concurrent_blobs == 4
    assert transport.active_blob_reads == 0
    assert transport.cancelled_blob_reads >= 3


def _transport(
    *,
    registry: bytes,
    workflow: bytes,
    control_files: dict[str, bytes] | None = None,
    mode_overrides: dict[str, str] | None = None,
    manifest: bytes | None = None,
    additional_workflows: dict[str, bytes] | None = None,
) -> AdapterSnapshotTransport:
    workflow_path = json.loads(registry)["workflows"][0]["workflowPath"]
    return AdapterSnapshotTransport(
        registry=registry,
        workflows={workflow_path: workflow, **(additional_workflows or {})},
        control_files=control_files or _control_files(),
        mode_overrides=mode_overrides,
        manifest=manifest,
    )


def _control_files() -> dict[str, bytes]:
    resources = files("ci_coordinator.target_artifacts.resources")
    return {
        path: resources.joinpath(Path(path).name).read_bytes() for path in TARGET_CONTROL_FILE_PATHS
    }


def _registry_bytes(
    *,
    execution_kind: str = "witness-shards",
    workflow_content: bytes,
    additional_workflows: dict[str, bytes] | None = None,
) -> bytes:
    fixture_directory = _fixture_directory(execution_kind)
    controls = _control_files()
    registry = json.loads(
        (fixture_directory / ".ci-coordinator/execution-registry.v1.json").read_bytes()
    )
    workflow_path = registry["workflows"][0]["workflowPath"]
    registry["adapterFiles"] = _adapter_files(
        {workflow_path: workflow_content, **(additional_workflows or {})},
        controls,
    )
    return _json(registry)


def _maximum_registry_bytes(workflows: dict[str, bytes]) -> bytes:
    controls = _control_files()
    workflow_records = []
    profiles = []
    for index, path in enumerate(workflows):
        job_id = f"test-{index:02}"
        profile_id = f"profile-{index:02}"
        workflow_records.append(
            {
                "workflowPath": path,
                "executionKind": "native-job-set",
                "executionJobs": [{"jobId": job_id, "needs": ["plan"]}],
                "planRequestJobId": PLAN_REQUEST_JOB_ID,
                "planRequestWorkflowRef": PLAN_REQUEST_WORKFLOW_REF,
                "planJobId": "plan",
                "fallbackJobId": None,
                "gateJobId": "gate",
                "gateSignalName": f"Gate {index:02}",
                "requiredJobIds": [],
            }
        )
        profiles.append(
            {
                "profileId": profile_id,
                "workflowPath": path,
                "jobId": job_id,
                "executionKind": "native-job-set",
                "runnerProfileId": "ubuntu-24.04",
                "permissionProfileId": "contents-read",
                "credentialProfileId": "none",
                "fixtureProfileId": "unit",
                "serviceProfileIds": [],
                "capacityClassId": "hosted",
            }
        )
    return _json(
        {
            "schemaVersion": "dynamic-ci-target-execution-registry/v1",
            "generator": {"id": "target-workflow", "version": "1"},
            "adapterFiles": _adapter_files(workflows, controls),
            "workflows": workflow_records,
            "profiles": profiles,
        }
    )


def _adapter_files(
    workflows: dict[str, bytes],
    controls: dict[str, bytes],
) -> list[dict[str, str]]:
    return [
        {
            "path": path,
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for path, content in sorted(
            {**workflows, **controls}.items(),
            key=lambda item: item[0].encode("utf-16-be"),
        )
    ]


def _workflow_bytes(
    *,
    execution_kind: str = "witness-shards",
) -> bytes:
    fixture_directory = _fixture_directory(execution_kind)
    workflow_path = WORKFLOW_PATH if execution_kind == "witness-shards" else NATIVE_WORKFLOW_PATH
    return (fixture_directory / workflow_path).read_bytes()


def _native_reusable_workflow_bytes() -> bytes:
    source = _workflow_bytes(execution_kind="native-job-set")
    lint_start = source.index(b"  lint:\n")
    test_start = source.index(b"\n  test:\n", lint_start)
    reusable_lint = (
        b"  lint:\n"
        b"    name: Lint\n"
        b"    needs: plan\n"
        b"    if: ${{ always() && (needs.plan.result != 'success' || "
        b"needs.plan.outputs.plan_valid != 'true' || "
        b"needs.plan.outputs.fallback != 'false' || "
        b"contains(fromJSON(needs.plan.outputs.selected_jobs || '[]'), 'lint')) }}\n"
        b"    uses: $/.github/workflows/lint.yml\n"
    )
    return source[:lint_start] + reusable_lint + source[test_start:]


def _fixture_directory(execution_kind: str) -> Path:
    name = "target-repository" if execution_kind == "witness-shards" else "native-target-repository"
    return _REPOSITORY_ROOT / "fixtures" / name


def _manifest_bytes() -> bytes:
    return _json(
        {
            "schemaVersion": "dynamic-ci-test-manifest/v1",
            "generator": {"id": "target-tests", "version": "1"},
            "tests": [
                {
                    "testId": "backend/test-b",
                    "witnessId": "backend-tests",
                    "expectedSeconds": 20,
                },
                {
                    "testId": "backend/test-a",
                    "witnessId": "backend-tests",
                    "expectedSeconds": 10,
                },
            ],
        }
    )


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def _request() -> PlanRequest:
    return PlanRequest(
        "dynamic-ci-plan-request/v2",
        "request-1",
        100,
        200,
        "example-org",
        "ci-coordinator",
        "pull_request",
        "refs/pull/42/merge",
        "a" * 40,
        "b" * 40,
        7001,
        1,
        42,
        execution_sha="c" * 40,
    )

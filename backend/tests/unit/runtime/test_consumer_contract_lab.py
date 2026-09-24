from __future__ import annotations

import asyncio
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from package_b_support import (
    BASE_SHA,
    HEAD_SHA,
    assert_deterministic_plan,
    make_execution_projection,
    make_input,
    make_native_execution_projection,
    make_policy,
)
from plan_consumer_support import plan_transport_environment, run_plan_consumer
from production_admission_support import make_production_admission_fixture

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.execution_orchestration import (
    TargetWorkflowBinding,
)
from ci_coordinator.identity_admission import TrustedActionsRun
from ci_coordinator.kernel import FixedClock, canonical_json
from ci_coordinator.plan_issuance import (
    InMemoryIssuanceStore,
    Issued,
    PlanIssuanceContext,
    PlanRequest,
    RepositoryBinding,
    SignedPlanIssuer,
    SignedPlanSigner,
)
from ci_coordinator.planning_core import plan
from ci_coordinator.production_admission import (
    AuthorizedProductionAdmission,
    ProductionScopeSubject,
    project_candidate_subject,
    project_plan_subject,
)
from ci_coordinator.reconciliation import ReconciliationSubject
from ci_coordinator.repo_context import DiffFileChangeInput
from ci_coordinator.runner_capacity import TrustedExecutionProjection
from ci_coordinator.target_artifacts import render_plan_trust_root
from ci_coordinator.verification_core import VerifiedPlan, verify

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_TARGET_RUNTIME = _REPOSITORY_ROOT / "fixtures/target-repository/.ci-coordinator"
_CONTROL_PATH = _TARGET_RUNTIME / "ci-coordinator.cjs"
_OWNER = "example-org"
_REPOSITORY = "ci-coordinator"
_INSTALLATION_ID = 100
_REPOSITORY_ID = 200
_RUN_ID = 300
_RUN_ATTEMPT = 1
_PLAN_URL = "https://coordinator.example/api/v1/dynamic-ci/plan"
_REQUESTER_REF = (
    "example-org/ci-coordinator/.github/workflows/"
    "trusted-plan-request.yml@1111111111111111111111111111111111111111"
)


@pytest.mark.parametrize(
    ("execution_kind", "selected", "ttl_seconds"),
    [
        ("witness-shards", True, 120),
        ("native-job-set", True, 120),
        ("witness-shards", False, 120),
        ("native-job-set", False, 120),
        ("witness-shards", True, 300),
        ("witness-shards", False, 300),
    ],
    ids=(
        "sharded-selected",
        "native-selected",
        "sharded-fallback",
        "native-fallback",
        "maximum-selected",
        "maximum-fallback",
    ),
)
def test_consumer_contract_lab_round_trips_real_control_plane(
    tmp_path: Path,
    execution_kind: str,
    selected: bool,
    ttl_seconds: int,
) -> None:
    now = datetime.now(UTC)
    policy = make_policy()
    planning_input = make_input(DiffFileChangeInput(path="docs/guide.md", status="modified"))
    verified = verify(
        planning_input,
        policy,
        assert_deterministic_plan(plan(planning_input, policy)),
    )
    projection = _execution_projection(execution_kind, verified, now=now)
    request = _request()
    identity = _identity(projection.workflow_path, now=now)
    signer = _signer(now=now, ttl_seconds=ttl_seconds)
    authorization = (
        _authorize(request, identity, verified, projection, now=now) if selected else None
    )
    outcome = asyncio.run(
        SignedPlanIssuer(store=InMemoryIssuanceStore(), signer=signer).issue(
            request,
            identity,
            PlanIssuanceContext(
                RepositoryBinding(
                    _INSTALLATION_ID,
                    _REPOSITORY_ID,
                    _OWNER,
                    _REPOSITORY,
                ),
                verified,
                authorization,
                execution_projection=projection if selected else None,
            ),
        )
    )
    assert isinstance(outcome, Issued)
    workflow = _workflow(projection)
    envelope = outcome.record.envelope
    envelope_bytes = (
        canonical_json({**envelope.unsigned_mapping(), "signature": envelope.signature}) + b"\n"
    )

    trust_root_path = tmp_path / "plan-trust-root.v1.json"
    trust_root_path.write_bytes(
        render_plan_trust_root(
            key_id="contract-lab-key",
            public_key_pem=signer.public_key_pem(),
        )
    )
    request_environment = _run_environment(
        request,
        identity,
        projection.workflow_path,
        trust_root_path,
    )
    completed, plan_path = run_plan_consumer(
        tmp_path,
        {
            **request_environment,
            **plan_transport_environment(envelope_bytes),
        },
    )
    assert completed.returncode == 0, completed.stderr
    assert plan_path.read_bytes() == envelope_bytes

    registry_path = tmp_path / "execution-registry.v1.json"
    registry_path.write_bytes(
        canonical_json(projection.target_registry.to_identity_mapping()) + b"\n"
    )
    outputs = _validate_plan(
        tmp_path,
        plan_path=plan_path,
        registry_path=registry_path,
        workflow_path=projection.workflow_path,
        workflow_job_ids=workflow.execution_job_ids,
        environment=request_environment,
    )
    assert outputs["plan_valid"] == "true"
    assert outputs["fallback"] == ("false" if selected else "true")

    selected_jobs = json.loads(outputs["selected_jobs"])
    expected_jobs = list(_expected_selected_jobs(projection)) if selected else []
    assert selected_jobs == expected_jobs
    gate = _run_gate(
        registry_path=registry_path,
        workflow_path=projection.workflow_path,
        workflow_job_ids=workflow.execution_job_ids,
        outputs=outputs,
        selected=selected,
        execution_kind=execution_kind,
    )
    assert gate.returncode == 0, gate.stderr


def _execution_projection(
    execution_kind: str,
    verified: VerifiedPlan,
    *,
    now: datetime,
) -> TrustedExecutionProjection:
    if execution_kind == "witness-shards":
        return make_execution_projection(verified, now=now)
    if execution_kind == "native-job-set":
        return make_native_execution_projection(verified)
    raise AssertionError("unreachable execution kind")


def _request() -> PlanRequest:
    return PlanRequest(
        "dynamic-ci-plan-request/v2",
        f"{_REPOSITORY_ID}:{_RUN_ID}:{_RUN_ATTEMPT}",
        _INSTALLATION_ID,
        _REPOSITORY_ID,
        _OWNER,
        _REPOSITORY,
        "pull_request",
        "refs/pull/42/merge",
        BASE_SHA,
        HEAD_SHA,
        _RUN_ID,
        _RUN_ATTEMPT,
        42,
        execution_sha="c" * 40,
    )


def _identity(workflow_path: str, *, now: datetime) -> TrustedActionsRun:
    return TrustedActionsRun(
        "https://token.actions.githubusercontent.com",
        _PLAN_URL,
        f"{_OWNER}/{_REPOSITORY}",
        _REPOSITORY_ID,
        "refs/pull/42/merge",
        _RUN_ID,
        _RUN_ATTEMPT,
        "pull_request",
        f"{_OWNER}/{_REPOSITORY}/{workflow_path}@refs/pull/42/merge",
        "c" * 40,
        _REQUESTER_REF,
        "1" * 40,
        None,
        now,
        execution_sha="c" * 40,
    )


def _signer(*, now: datetime, ttl_seconds: int = 120) -> SignedPlanSigner:
    key = Ed25519PrivateKey.generate()
    return SignedPlanSigner(
        key_id="contract-lab-key",
        private_key_pem=key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()),
        ttl_seconds=ttl_seconds,
        clock=FixedClock(now),
    )


def _authorize(
    request: PlanRequest,
    identity: TrustedActionsRun,
    verified: VerifiedPlan,
    projection: TrustedExecutionProjection,
    *,
    now: datetime,
) -> AuthorizedProductionAdmission:
    scope = RepositoryScope(_INSTALLATION_ID, _REPOSITORY_ID)
    candidate = project_candidate_subject(scope, verified, identity)
    grant = make_production_admission_fixture(
        ProductionScopeSubject(
            scope=scope,
            config_epoch_id=candidate.config_epoch_id,
            compiled_policy_hash=candidate.compiled_policy_hash,
            policy_hash=candidate.policy_hash,
            catalog_hash=candidate.catalog_hash,
            target_registry_hash=projection.target_registry_hash,
            workflow_refs=(),
            job_workflow_refs=(_REQUESTER_REF,),
            workflow_paths=(f"{_OWNER}/{_REPOSITORY}/{projection.workflow_path}",),
            job_workflow_paths=(),
        ),
        now=now,
    ).grant
    subject_id = ReconciliationSubject.create(
        installation_id=request.installation_id,
        repository_id=request.repository_id,
        event_name=request.event_name,
        ref=request.ref,
        base_sha=request.base_sha,
        head_sha=request.head_sha,
        workflow_run_id=request.workflow_run_id,
        run_attempt=request.run_attempt,
    ).subject_id
    authorization = grant.authorize(
        project_plan_subject(
            scope,
            verified,
            identity,
            projection.target_registry_hash,
            subject_id,
        )
    )
    assert isinstance(authorization, AuthorizedProductionAdmission)
    return authorization


def _run_environment(
    request: PlanRequest,
    identity: TrustedActionsRun,
    workflow_path: str,
    trust_root_path: Path,
) -> dict[str, str]:
    return {
        "CI_COORDINATOR_PLAN_OIDC_AUDIENCE": identity.audience,
        "CI_COORDINATOR_PLAN_TRUST_ROOT_PATH": str(trust_root_path),
        "CI_INSTALLATION_ID": str(request.installation_id),
        "CI_REPOSITORY_ID": str(request.repository_id),
        "CI_OWNER": request.owner,
        "CI_REPO": request.repository,
        "CI_EVENT": request.event_name,
        "CI_REF": request.ref,
        "CI_BASE_SHA": request.base_sha,
        "CI_HEAD_SHA": request.head_sha,
        "CI_EXECUTION_SHA": request.execution_sha,
        "CI_RUN_ID": str(request.workflow_run_id),
        "CI_RUN_ATTEMPT": str(request.run_attempt),
        "CI_PULL_REQUEST_NUMBER": str(request.pull_request_number),
        "CI_MERGE_GROUP_HEAD_REF": "",
        "CI_WORKFLOW_PATH": workflow_path,
        "CI_WORKFLOW_REF": str(identity.workflow_ref),
        "CI_WORKFLOW_SHA": str(identity.workflow_sha),
    }


def _validate_plan(
    tmp_path: Path,
    *,
    plan_path: Path,
    registry_path: Path,
    workflow_path: str,
    workflow_job_ids: tuple[str, ...],
    environment: dict[str, str],
) -> dict[str, str]:
    output_path = tmp_path / "github-output"
    output_path.write_text("", encoding="utf-8")
    completed = subprocess.run(
        ["node", str(_CONTROL_PATH), "validate-plan"],
        cwd=_REPOSITORY_ROOT,
        env={
            **os.environ,
            **environment,
            "CI_TARGET_REGISTRY_PATH": str(registry_path),
            "CI_STATIC_JOB_IDS_JSON": json.dumps(workflow_job_ids, separators=(",", ":")),
            "CI_WORKFLOW_PATH": workflow_path,
            "GITHUB_OUTPUT": str(output_path),
            "PLAN_PATH": str(plan_path),
        },
        check=False,
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert completed.returncode == 0, completed.stderr
    return dict(line.split("=", 1) for line in output_path.read_text().splitlines() if line)


def _workflow(projection: TrustedExecutionProjection) -> TargetWorkflowBinding:
    workflow = projection.target_registry.workflow(projection.workflow_path)
    assert workflow is not None
    return workflow


def _expected_selected_jobs(
    projection: TrustedExecutionProjection,
) -> tuple[str, ...]:
    workflow = _workflow(projection)
    profiles = {item.profile_id: item for item in projection.target_registry.profiles}
    roots = tuple(
        sorted({profiles[profile_id].job_id for profile_id in projection.selected_profile_ids})
    )
    return (
        workflow.dependency_closure(roots)
        if projection.execution_kind == "native-job-set"
        else roots
    )


def _run_gate(
    *,
    registry_path: Path,
    workflow_path: str,
    workflow_job_ids: tuple[str, ...],
    outputs: dict[str, str],
    selected: bool,
    execution_kind: str,
) -> subprocess.CompletedProcess[str]:
    selected_jobs = json.loads(outputs["selected_jobs"])
    static_results = {
        job_id: (
            "success"
            if selected and job_id in selected_jobs
            else "success"
            if not selected and execution_kind == "native-job-set"
            else "skipped"
        )
        for job_id in workflow_job_ids
    }
    return subprocess.run(
        ["node", str(_CONTROL_PATH), "validate-gate"],
        cwd=_REPOSITORY_ROOT,
        env={
            **os.environ,
            "CI_PLAN_RESULT": "success",
            "CI_PLAN_VALID": outputs["plan_valid"],
            "CI_FALLBACK": outputs["fallback"],
            "CI_SELECTED_JOBS_JSON": outputs["selected_jobs"],
            "CI_STATIC_JOB_RESULTS_JSON": json.dumps(
                static_results,
                separators=(",", ":"),
                sort_keys=True,
            ),
            "CI_FULL_CI_RESULT": (
                "" if execution_kind == "native-job-set" else "skipped" if selected else "success"
            ),
            "CI_TARGET_REGISTRY_PATH": str(registry_path),
            "CI_OWNER": _OWNER,
            "CI_REPO": _REPOSITORY,
            "CI_WORKFLOW_PATH": workflow_path,
            "CI_WORKFLOW_REF": (f"{_OWNER}/{_REPOSITORY}/{workflow_path}@refs/pull/42/merge"),
        },
        check=False,
        text=True,
        capture_output=True,
        timeout=5,
    )

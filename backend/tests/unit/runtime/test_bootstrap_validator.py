from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from package_b_support import PLAN_REQUEST_WORKFLOW_REF
from ruamel.yaml import YAML

from ci_coordinator.execution_orchestration import (
    LOCAL_PLAN_REQUEST_WORKFLOW_PATH,
    LOCAL_PLAN_REQUEST_WORKFLOW_REF,
    ProviderSignal,
)
from ci_coordinator.kernel import canonical_json, hash_object
from ci_coordinator.target_artifacts.requester import render_plan_requester

_REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
_BOOTSTRAP_PATH = (
    _REPOSITORY_ROOT / "fixtures/target-repository/.github/workflows/ci-coordinator-bootstrap.yml"
)
_TARGET_REPOSITORY = _REPOSITORY_ROOT / "fixtures" / "target-repository"
_REGISTRY_PATH = _TARGET_REPOSITORY / ".ci-coordinator" / "execution-registry.v1.json"


def test_bootstrap_validator_accepts_selected_and_fallback_plans_and_rejects_mismatch() -> None:
    signing_key = Ed25519PrivateKey.generate()

    selected = _run_bootstrap_validator(_sign(signing_key, _payload()), signing_key)
    assert selected["plan_valid"] == "true"
    assert selected["fallback"] == "false"
    matrices = json.loads(selected["matrices"])
    assert matrices["selected-python-linux"]["include"][0]["provider_job_name"].startswith(
        "ci/python-linux/"
    )
    assert json.loads(selected["max_parallel_by_job"]) == {"selected-python-linux": 1}
    assert json.loads(selected["selected_jobs"]) == ["selected-python-linux"]

    fallback = _run_bootstrap_validator(_sign(signing_key, _payload(fallback=True)), signing_key)
    assert fallback["plan_valid"] == "true"
    assert fallback["fallback"] == "true"

    mismatch = _run_bootstrap_validator(
        _sign(signing_key, _payload(head_sha="other-head")), signing_key
    )
    assert mismatch["plan_valid"] == "false"
    assert mismatch["fallback"] == "true"
    assert mismatch["reason"] == "head_sha_mismatch"

    execution_mismatch_payload = _payload()
    _mapping(execution_mismatch_payload["request"])["executionSha"] = "other-execution"
    execution_mismatch = _run_bootstrap_validator(
        _sign(signing_key, execution_mismatch_payload), signing_key
    )
    assert execution_mismatch["plan_valid"] == "false"
    assert execution_mismatch["fallback"] == "true"
    assert execution_mismatch["reason"] == "execution_sha_mismatch"


def test_bootstrap_validator_accepts_a_push_bound_plan() -> None:
    signing_key = Ed25519PrivateKey.generate()

    result = _run_bootstrap_validator(
        _sign(signing_key, _payload(event_name="push")),
        signing_key,
        event_name="push",
    )

    assert result["plan_valid"] == "true"
    assert result["fallback"] == "false"


def test_bootstrap_validator_rejects_auth_mismatch_in_a_signed_fallback() -> None:
    signing_key = Ed25519PrivateKey.generate()
    payload = _payload(fallback=True)
    _mapping(payload["authenticatedRun"])["repository"] = "attacker/repository"

    result = _run_bootstrap_validator(_sign(signing_key, payload), signing_key)

    assert result["plan_valid"] == "false"
    assert result["fallback"] == "true"
    assert result["reason"] == "auth_repository_mismatch"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    [
        (
            "jobWorkflowRef",
            PLAN_REQUEST_WORKFLOW_REF.replace("1" * 40, "2" * 40),
            "auth_job_workflow_ref_mismatch",
        ),
        ("jobWorkflowSha", "2" * 40, "auth_job_workflow_sha_mismatch"),
        ("executionSha", "2" * 40, "auth_execution_sha_mismatch"),
    ],
)
def test_bootstrap_validator_exactly_binds_the_authenticated_plan_requester(
    field: str,
    value: str,
    reason: str,
) -> None:
    signing_key = Ed25519PrivateKey.generate()
    payload = _payload()
    _mapping(payload["authenticatedRun"])[field] = value

    result = _run_bootstrap_validator(_sign(signing_key, payload), signing_key)

    assert result["plan_valid"] == "false"
    assert result["fallback"] == "true"
    assert result["reason"] == reason


def test_bootstrap_validator_rejects_unknown_signed_payload_fields() -> None:
    signing_key = Ed25519PrivateKey.generate()
    payload = _payload()
    payload["canonicalOrderProbe"] = {"\ue000": 1, "\U00010000": 2}

    result = _run_bootstrap_validator(_sign(signing_key, payload), signing_key)

    assert result["plan_valid"] == "false"
    assert result["fallback"] == "true"
    assert result["reason"] == "signed_plan_payload_shape_invalid"


@pytest.mark.parametrize("mutation", ["wrong-key", "changed-signature-byte"])
def test_bootstrap_validator_rejects_canonical_invalid_signatures(mutation: str) -> None:
    signing_key = Ed25519PrivateKey.generate()
    envelope = _sign(signing_key, _payload())
    trusted_key = signing_key
    if mutation == "wrong-key":
        trusted_key = Ed25519PrivateKey.generate()
    else:
        signature = bytearray(base64.urlsafe_b64decode(str(envelope["signature"]) + "=="))
        assert len(signature) == 64
        signature[0] ^= 1
        envelope["signature"] = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")

    result = _run_bootstrap_validator(envelope, trusted_key)

    assert result["plan_valid"] == "false"
    assert result["fallback"] == "true"
    assert result["reason"] == "signed_plan_signature_invalid"


def test_bootstrap_validator_rejects_noncanonical_signature_encodings() -> None:
    signing_key = Ed25519PrivateKey.generate()
    envelope = _sign(signing_key, _payload())
    signature = str(envelope["signature"])
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    final_index = alphabet.index(signature[-1])
    alias = signature[:-1] + alphabet[final_index + 1]
    assert base64.urlsafe_b64decode(signature + "==") == base64.urlsafe_b64decode(alias + "==")

    for mutated_signature in (signature + "==", alias):
        mutated = {**envelope, "signature": mutated_signature}
        result = _run_bootstrap_validator(mutated, signing_key)

        assert result["plan_valid"] == "false"
        assert result["fallback"] == "true"
        assert result["reason"] == "signed_plan_signature_invalid"


def test_bootstrap_validator_routes_each_selected_profile_to_its_static_job() -> None:
    signing_key = Ed25519PrivateKey.generate()
    registry = json.loads(_REGISTRY_PATH.read_bytes())
    second_binding = _mapping(_sequence(registry["profiles"])[1])
    payload = _payload(registry=registry)
    execution = _mapping(payload["execution"])
    first_profile = _mapping(_sequence(execution["profiles"])[0])
    second_profile = deepcopy(first_profile)
    profile = _mapping(second_profile["profile"])
    for field in (
        "capacityClassId",
        "credentialProfileId",
        "fixtureProfileId",
        "permissionProfileId",
        "profileId",
        "runnerProfileId",
        "serviceProfileIds",
    ):
        profile[field] = second_binding[field]
    shard = _mapping(_sequence(second_profile["shards"])[0])
    shard_id = "ci_shard_1123456789abcdef0123456789abcdef"
    shard["shardId"] = shard_id
    shard["executionProfileId"] = "python-postgres"
    shard["witnessIds"] = ["python-postgres"]
    shard["testIds"] = ["backend/persistence"]
    shard["providerSignal"] = ProviderSignal.derive(
        execution_profile_id="python-postgres",
        shard_id=shard_id,
    ).to_identity_mapping()
    execution["selectedWitnessIds"] = ["python-postgres", "python-quality"]
    execution["profiles"] = [first_profile, second_profile]

    result = _run_bootstrap_validator(
        _sign(signing_key, payload),
        signing_key,
        registry=registry,
        static_job_ids=("selected-python-linux", "selected-python-postgres"),
    )

    assert result["plan_valid"] == "true"
    assert result["fallback"] == "false"
    assert json.loads(result["selected_jobs"]) == [
        "selected-python-linux",
        "selected-python-postgres",
    ]
    assert set(json.loads(result["matrices"])) == {
        "selected-python-linux",
        "selected-python-postgres",
    }


@pytest.mark.parametrize(
    ("constraint", "limit", "expected_reason"),
    [
        ("max-shards", 2, None),
        ("max-shards", 1, "profile_execution_invalid"),
        ("max-items", 2, None),
        ("max-items", 1, "execution_shard_invalid"),
        ("shard-order", 2, "profile_execution_invalid"),
    ],
)
def test_bootstrap_validator_enforces_signed_shard_policy(
    constraint: str,
    limit: int,
    expected_reason: str | None,
) -> None:
    signing_key = Ed25519PrivateKey.generate()
    payload = _payload()
    execution = _mapping(payload["execution"])
    profile = _mapping(_sequence(execution["profiles"])[0])
    policy = _mapping(_mapping(profile["profile"])["shardingPolicy"])
    first_shard = _mapping(_sequence(profile["shards"])[0])
    if constraint in {"max-shards", "shard-order"}:
        second_shard = deepcopy(first_shard)
        shard_id = (
            "ci_shard_0023456789abcdef0123456789abcdef"
            if constraint == "shard-order"
            else "ci_shard_1123456789abcdef0123456789abcdef"
        )
        second_shard["shardId"] = shard_id
        second_shard["testIds"] = ["backend/quality-secondary"]
        second_shard["providerSignal"] = ProviderSignal.derive(
            execution_profile_id="python-linux",
            shard_id=shard_id,
        ).to_identity_mapping()
        _sequence(profile["shards"]).append(second_shard)
        policy["maxShards"] = limit
        policy["maxParallel"] = limit
    else:
        first_shard["testIds"] = ["backend/quality-a", "backend/quality-b"]
        policy["maxItemsPerShard"] = limit

    result = _run_bootstrap_validator(_sign(signing_key, payload), signing_key)

    admitted = expected_reason is None
    assert result["plan_valid"] == ("true" if admitted else "false"), result
    assert result["fallback"] == ("false" if admitted else "true"), result
    if expected_reason is not None:
        assert result["reason"] == expected_reason


@pytest.mark.parametrize(("count", "admitted"), [(16, True), (17, False)])
def test_bootstrap_validator_uses_the_canonical_service_profile_bound(
    count: int,
    admitted: bool,
) -> None:
    signing_key = Ed25519PrivateKey.generate()
    registry = json.loads(_REGISTRY_PATH.read_bytes())
    service_profile_ids = [f"service-{index:02d}" for index in range(count)]
    registry_profile = _mapping(_sequence(registry["profiles"])[0])
    registry_profile["serviceProfileIds"] = service_profile_ids
    payload = _payload(registry=registry)
    signed_profile = _mapping(
        _mapping(_sequence(_mapping(payload["execution"])["profiles"])[0])["profile"]
    )
    signed_profile["serviceProfileIds"] = service_profile_ids

    result = _run_bootstrap_validator(
        _sign(signing_key, payload),
        signing_key,
        registry=registry,
    )

    assert result["plan_valid"] == ("true" if admitted else "false")
    assert result["fallback"] == ("false" if admitted else "true")
    if not admitted:
        assert result["reason"] == "target_registry_profile_invalid"


def test_bootstrap_validator_rejects_an_unselected_shard_job_dependency() -> None:
    signing_key = Ed25519PrivateKey.generate()
    registry = json.loads(_REGISTRY_PATH.read_bytes())
    workflow = _mapping(_sequence(registry["workflows"])[0])
    execution_job = next(
        _mapping(job)
        for job in _sequence(workflow["executionJobs"])
        if _mapping(job)["jobId"] == "selected-python-linux"
    )
    execution_job["needs"] = ["plan", "selected-python-postgres"]

    result = _run_bootstrap_validator(
        _sign(signing_key, _payload(registry=registry)),
        signing_key,
        registry=registry,
    )

    assert result["plan_valid"] == "false"
    assert result["fallback"] == "true"
    assert result["reason"] == "selected_job_dependency_missing"


def test_bootstrap_validator_selects_native_jobs_without_synthetic_matrices() -> None:
    signing_key = Ed25519PrivateKey.generate()
    workflow_path = ".github/workflows/native-ci.yml"
    registry = json.loads(_REGISTRY_PATH.read_bytes())
    workflow = _mapping(_sequence(registry["workflows"])[0])
    workflow["workflowPath"] = workflow_path
    workflow["executionKind"] = "native-job-set"
    workflow["fallbackJobId"] = None
    execution_jobs = _sequence(workflow["executionJobs"])
    _mapping(execution_jobs[0])["needs"] = ["plan", "selected-python-postgres"]
    for binding in _sequence(registry["profiles"]):
        profile_binding = _mapping(binding)
        profile_binding["workflowPath"] = workflow_path
        profile_binding["executionKind"] = "native-job-set"
    workflow_adapter = next(
        binding
        for binding in _sequence(registry["adapterFiles"])
        if str(binding["path"]).startswith(".github/workflows/")
    )
    workflow_adapter["path"] = workflow_path
    payload = _payload(registry=registry, workflow_path=workflow_path)
    execution = _mapping(payload["execution"])
    execution["executionKind"] = "native-job-set"
    execution["testManifestId"] = None
    profile = _mapping(_sequence(execution["profiles"])[0])
    execution["profiles"] = [
        {
            "executionKind": "native-job-set",
            "profile": profile["profile"],
            "jobId": "selected-python-linux",
            "witnessIds": ["python-quality"],
        }
    ]

    result = _run_bootstrap_validator(
        _sign(signing_key, payload),
        signing_key,
        registry=registry,
        workflow_path=workflow_path,
    )

    assert result["plan_valid"] == "true"
    assert result["fallback"] == "false"
    assert json.loads(result["matrices"]) == {}
    assert json.loads(result["max_parallel_by_job"]) == {}
    assert json.loads(result["selected_jobs"]) == [
        "selected-python-linux",
        "selected-python-postgres",
    ]


def test_bootstrap_validator_falls_back_before_provider_output_overflow() -> None:
    signing_key = Ed25519PrivateKey.generate()
    payload = _payload()
    execution = _mapping(payload["execution"])
    profile = _mapping(_sequence(execution["profiles"])[0])
    shard = _mapping(_sequence(profile["shards"])[0])
    shard["testIds"] = [f"{index:04d}-" + "x" * 120 for index in range(4_096)]

    result = _run_bootstrap_validator(_sign(signing_key, payload), signing_key)

    assert result == {
        "plan_valid": "false",
        "fallback": "true",
        "reason": "provider_output_budget_exceeded",
        "matrices": "{}",
        "max_parallel_by_job": "{}",
        "selected_jobs": "[]",
    }


def test_bootstrap_validator_rejects_oversized_file_before_json_parsing() -> None:
    signing_key = Ed25519PrivateKey.generate()

    result = _run_bootstrap_validator_bytes(b"{" + b" " * 1_048_576, signing_key)

    assert result["plan_valid"] == "false"
    assert result["fallback"] == "true"
    assert result["reason"] == "json_size_invalid"


def test_bootstrap_validator_rejects_symlinked_plan_input() -> None:
    signing_key = Ed25519PrivateKey.generate()
    envelope = _sign(signing_key, _payload())

    result = _run_bootstrap_validator_bytes(
        json.dumps(envelope, separators=(",", ":")).encode(),
        signing_key,
        plan_symlink=True,
    )

    assert result["plan_valid"] == "false"
    assert result["fallback"] == "true"
    assert result["reason"] == "json_read_failed"


def test_bootstrap_validator_rejects_an_unadmitted_versioned_trust_root() -> None:
    signing_key = Ed25519PrivateKey.generate()

    result = _run_bootstrap_validator_bytes(
        json.dumps(_sign(signing_key, _payload()), separators=(",", ":")).encode(),
        signing_key,
        trust_root_bytes=b"{}\n",
    )

    assert result["plan_valid"] == "false"
    assert result["fallback"] == "true"
    assert result["reason"] == "plan_trust_root_invalid"


def test_bootstrap_validator_requires_exact_static_job_set() -> None:
    signing_key = Ed25519PrivateKey.generate()

    result = _run_bootstrap_validator(
        _sign(signing_key, _payload()),
        signing_key,
        static_job_ids=("selected-python-linux",),
    )

    assert result["plan_valid"] == "false"
    assert result["fallback"] == "true"
    assert result["reason"] == "static_job_set_mismatch"


def test_bootstrap_validator_falls_back_when_a_required_job_is_not_selected() -> None:
    signing_key = Ed25519PrivateKey.generate()
    registry = json.loads(_REGISTRY_PATH.read_bytes())
    workflow = _mapping(_sequence(registry["workflows"])[0])
    workflow["requiredJobIds"] = ["selected-python-postgres"]

    result = _run_bootstrap_validator(
        _sign(signing_key, _payload(registry=registry)),
        signing_key,
        registry=registry,
    )

    assert result["plan_valid"] == "false"
    assert result["fallback"] == "true"
    assert result["reason"] == "required_target_job_missing"


def test_bootstrap_validator_rejects_job_ids_that_alias_in_github_expressions() -> None:
    signing_key = Ed25519PrivateKey.generate()
    registry = json.loads(_REGISTRY_PATH.read_bytes())
    workflow = _mapping(_sequence(registry["workflows"])[0])
    execution_jobs = _sequence(workflow["executionJobs"])
    first_job = _mapping(execution_jobs[0])
    first_job["jobId"] = "SELECTED-PYTHON-POSTGRES"
    first_profile = _mapping(_sequence(registry["profiles"])[0])
    first_profile["jobId"] = "SELECTED-PYTHON-POSTGRES"

    result = _run_bootstrap_validator(
        _sign(signing_key, _payload(registry=registry)),
        signing_key,
        registry=registry,
        static_job_ids=("SELECTED-PYTHON-POSTGRES", "selected-python-postgres"),
    )

    assert result["plan_valid"] == "false"
    assert result["fallback"] == "true"
    assert result["reason"] == "target_registry_workflow_invalid"


def test_bootstrap_validator_rejects_execution_job_without_the_exact_plan_dependency() -> None:
    signing_key = Ed25519PrivateKey.generate()
    registry = json.loads(_REGISTRY_PATH.read_bytes())
    workflow = _mapping(_sequence(registry["workflows"])[0])
    first_job = _mapping(_sequence(workflow["executionJobs"])[0])
    first_job["needs"] = []

    result = _run_bootstrap_validator(
        _sign(signing_key, _payload(registry=registry)),
        signing_key,
        registry=registry,
    )

    assert result["plan_valid"] == "false"
    assert result["fallback"] == "true"
    assert result["reason"] == "target_registry_execution_jobs_invalid"


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("none", None),
        ("missing-adapter", "target_registry_local_requester_missing"),
        ("job-repository", "auth_job_workflow_ref_mismatch"),
        ("job-path", "auth_job_workflow_ref_mismatch"),
        ("job-ref", "auth_job_workflow_ref_mismatch"),
        ("job-sha", "auth_job_workflow_sha_mismatch"),
        ("opaque-sha", "auth_job_workflow_sha_mismatch"),
        ("uppercase-sha", "auth_job_workflow_sha_mismatch"),
    ],
)
def test_bootstrap_validator_binds_local_requester_to_caller_and_adapter_closure(
    mutation: str,
    reason: str | None,
) -> None:
    signing_key = Ed25519PrivateKey.generate()
    registry = json.loads(_REGISTRY_PATH.read_bytes())
    registry["workflows"][0]["planRequestWorkflowRef"] = LOCAL_PLAN_REQUEST_WORKFLOW_REF
    registry["adapterFiles"].append(
        {
            "path": LOCAL_PLAN_REQUEST_WORKFLOW_PATH,
            "sha256": hashlib.sha256(render_plan_requester()).hexdigest(),
        }
    )
    registry["adapterFiles"].sort(key=lambda row: row["path"])
    if mutation == "missing-adapter":
        registry["adapterFiles"] = [
            row
            for row in registry["adapterFiles"]
            if row["path"] != LOCAL_PLAN_REQUEST_WORKFLOW_PATH
        ]
    workflow_sha = {"opaque-sha": "workflow-sha", "uppercase-sha": "A" * 40}.get(mutation, "1" * 40)
    payload = _payload(registry=registry, workflow_sha=workflow_sha)
    authenticated_run = payload["authenticatedRun"]
    if mutation == "job-repository":
        authenticated_run["jobWorkflowRef"] = authenticated_run["jobWorkflowRef"].replace(
            "example-org/sample-service/", "attacker/sample-service/"
        )
    elif mutation == "job-path":
        authenticated_run["jobWorkflowRef"] = authenticated_run["jobWorkflowRef"].replace(
            "trusted-plan-request.yml", "other-requester.yml"
        )
    elif mutation == "job-ref":
        authenticated_run["jobWorkflowRef"] = authenticated_run["jobWorkflowRef"].replace(
            "refs/heads/master", "refs/heads/other"
        )
    elif mutation == "job-sha":
        authenticated_run["jobWorkflowSha"] = "2" * 40

    result = _run_bootstrap_validator(
        _sign(signing_key, payload), signing_key, registry=registry, workflow_sha=workflow_sha
    )

    assert result["plan_valid"] == ("true" if reason is None else "false")
    assert result["fallback"] == ("false" if reason is None else "true")
    if reason is not None:
        assert result["reason"] == reason
        assert json.loads(result["selected_jobs"]) == []
    else:
        assert json.loads(result["selected_jobs"]) == ["selected-python-linux"]


def _run_bootstrap_validator(
    envelope: dict[str, Any],
    signing_key: Ed25519PrivateKey,
    *,
    event_name: str = "pull_request",
    registry: dict[str, Any] | None = None,
    static_job_ids: tuple[str, ...] = ("selected-python-linux", "selected-python-postgres"),
    workflow_path: str = ".github/workflows/ci-coordinator-bootstrap.yml",
    workflow_sha: str = "workflow-sha",
) -> dict[str, str]:
    return _run_bootstrap_validator_bytes(
        json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode(),
        signing_key,
        event_name=event_name,
        registry=registry,
        static_job_ids=static_job_ids,
        workflow_path=workflow_path,
        workflow_sha=workflow_sha,
    )


def _run_bootstrap_validator_bytes(
    plan_bytes: bytes,
    signing_key: Ed25519PrivateKey,
    *,
    event_name: str = "pull_request",
    registry: dict[str, Any] | None = None,
    static_job_ids: tuple[str, ...] = ("selected-python-linux", "selected-python-postgres"),
    workflow_path: str = ".github/workflows/ci-coordinator-bootstrap.yml",
    workflow_sha: str = "workflow-sha",
    plan_symlink: bool = False,
    trust_root_bytes: bytes | None = None,
) -> dict[str, str]:
    workflow = _read_workflow(_BOOTSTRAP_PATH)
    steps = _sequence(_mapping(_mapping(workflow["jobs"])["plan"])["steps"])
    script = next(step["run"] for step in steps if step.get("id") == "validate")
    public_key_der = signing_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    with TemporaryDirectory(prefix="ci-coordinator-bootstrap-") as directory:
        plan_path = Path(directory) / "plan.json"
        registry_path = Path(directory) / "execution-registry.v1.json"
        trust_root_path = Path(directory) / "plan-trust-root.v1.json"
        output_path = Path(directory) / "github-output"
        if plan_symlink:
            plan_target = Path(directory) / "plan-target.json"
            plan_target.write_bytes(plan_bytes)
            plan_path.symlink_to(plan_target)
        else:
            plan_path.write_bytes(plan_bytes)
        if registry is None:
            registry_path.write_bytes(_REGISTRY_PATH.read_bytes())
        else:
            registry_path.write_text(
                json.dumps(registry, ensure_ascii=False, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        trust_root_path.write_bytes(
            trust_root_bytes
            if trust_root_bytes is not None
            else canonical_json(
                {
                    "algorithm": "Ed25519",
                    "keyId": "key-1",
                    "publicKeySpkiBase64": base64.b64encode(public_key_der).decode("ascii"),
                    "schemaVersion": "ci-coordinator-plan-trust-root/v1",
                }
            )
            + b"\n"
        )
        output_path.write_text("", encoding="utf-8")
        environment = {
            **os.environ,
            "CI_COORDINATOR_PLAN_OIDC_AUDIENCE": "ci-coordinator",
            "CI_COORDINATOR_PLAN_TRUST_ROOT_PATH": str(trust_root_path),
            "CI_INSTALLATION_ID": "100",
            "CI_OWNER": "example-org",
            "CI_REPO": "sample-service",
            "CI_REPOSITORY_ID": "200",
            "CI_RUN_ATTEMPT": "1",
            "CI_RUN_ID": "300",
            "CI_WORKFLOW_REF": f"example-org/sample-service/{workflow_path}@refs/heads/master",
            "CI_WORKFLOW_PATH": workflow_path,
            "CI_WORKFLOW_SHA": workflow_sha,
            "GITHUB_OUTPUT": str(output_path),
            "PLAN_PATH": str(plan_path),
            "CI_TARGET_REGISTRY_PATH": str(registry_path),
            "CI_STATIC_JOB_IDS_JSON": json.dumps(static_job_ids, separators=(",", ":")),
            **_event_binding(event_name),
        }
        subprocess.run(
            ["bash", "-c", script],
            cwd=_TARGET_REPOSITORY,
            env=environment,
            check=True,
            text=True,
            capture_output=True,
        )
        return dict(line.split("=", 1) for line in output_path.read_text().splitlines() if line)


def _sign(signing_key: Ed25519PrivateKey, payload: dict[str, Any]) -> dict[str, Any]:
    issued_at = datetime.now(UTC)
    unsigned = {
        "schemaVersion": "dynamic-ci-signed-plan-envelope/v1",
        "keyId": "key-1",
        "algorithm": "Ed25519",
        "issuedAt": issued_at.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "expiresAt": (issued_at + timedelta(seconds=120))
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z"),
        "payload": payload,
    }
    signature = base64.urlsafe_b64encode(signing_key.sign(canonical_json(unsigned))).rstrip(b"=")
    return {**unsigned, "signature": signature.decode()}


def _payload(
    *,
    event_name: str = "pull_request",
    fallback: bool = False,
    head_sha: str | None = None,
    registry: dict[str, Any] | None = None,
    workflow_path: str = ".github/workflows/ci-coordinator-bootstrap.yml",
    workflow_sha: str = "workflow-sha",
) -> dict[str, Any]:
    binding = _event_binding(event_name)
    expected_head_sha = binding["CI_HEAD_SHA"] if head_sha is None else head_sha
    pull_request_number = binding["CI_PULL_REQUEST_NUMBER"]
    merge_group_head_ref = binding["CI_MERGE_GROUP_HEAD_REF"]
    plan_id = "fallback_plan_test" if fallback else "verified_plan_test"
    reason = "dynamic CI context is unavailable" if fallback else None
    shard_id = "ci_shard_0123456789abcdef0123456789abcdef"
    signal = ProviderSignal.derive(
        execution_profile_id="python-linux",
        shard_id=shard_id,
    )
    target_registry = json.loads(_REGISTRY_PATH.read_bytes()) if registry is None else registry
    workflow_binding = next(
        workflow
        for workflow in target_registry["workflows"]
        if workflow["workflowPath"] == workflow_path
    )
    plan_request_workflow_ref = str(workflow_binding["planRequestWorkflowRef"])
    local_requester = plan_request_workflow_ref == LOCAL_PLAN_REQUEST_WORKFLOW_REF
    requester_ref = (
        f"example-org/sample-service/{LOCAL_PLAN_REQUEST_WORKFLOW_PATH}@refs/heads/master"
        if local_requester
        else plan_request_workflow_ref
    )
    requester_sha = workflow_sha if local_requester else plan_request_workflow_ref.rsplit("@", 1)[1]
    execution: dict[str, Any] = (
        {"mode": "full-ci", "reason": reason}
        if fallback
        else {
            "mode": "selected",
            "executionKind": "witness-shards",
            "workflowPath": workflow_path,
            "gateProviderSignal": ProviderSignal.declared_native(
                workflow_path=workflow_path,
                job_id="bootstrap-gate",
                job_name="Dynamic CI Bootstrap",
            ).to_identity_mapping(),
            "verifiedPlanId": plan_id,
            "deterministicPlanId": "deterministic-plan-test",
            "catalogHash": "a" * 64,
            "targetRegistryHash": hash_object(target_registry),
            "selectedObligationIds": ["backend-tests"],
            "omittedObligationIds": ["docs-lint"],
            "selectedWitnessIds": ["python-quality"],
            "testManifestId": "test_manifest_0123456789abcdef0123456789abcdef",
            "profiles": [
                {
                    "executionKind": "witness-shards",
                    "profile": {
                        "profileId": "python-linux",
                        "runnerProfileId": "ubuntu-24-04",
                        "permissionProfileId": "contents-read",
                        "credentialProfileId": "no-credentials",
                        "fixtureProfileId": "no-fixtures",
                        "serviceProfileIds": [],
                        "capacityClassId": "self-hosted-default",
                        "shardingPolicy": {
                            "maxShards": 8,
                            "maxParallel": 8,
                            "maxItemsPerShard": 10000,
                            "setupSecondsPerShard": 1.0,
                            "cpuWeight": 1.0,
                            "wallWeight": 1.0,
                            "operatorWeight": 0.0,
                        },
                    },
                    "shards": [
                        {
                            "shardId": shard_id,
                            "manifestId": ("test_manifest_0123456789abcdef0123456789abcdef"),
                            "executionProfileId": "python-linux",
                            "witnessIds": ["python-quality"],
                            "testIds": ["backend/quality"],
                            "providerSignal": signal.to_identity_mapping(),
                        }
                    ],
                    "maxParallel": 1,
                    "capacityMode": "optimized",
                    "capacityReason": None,
                }
            ],
        }
    )
    return {
        "schemaVersion": "dynamic-ci-signed-plan-payload/v2",
        "planId": plan_id,
        "repository": {
            "installationId": 100,
            "repositoryId": 200,
            "owner": "example-org",
            "repository": "sample-service",
        },
        "request": {
            "schemaVersion": "dynamic-ci-plan-request/v2",
            "requestId": "200:300:1",
            "installationId": 100,
            "repositoryId": 200,
            "owner": "example-org",
            "repository": "sample-service",
            "eventName": event_name,
            "ref": binding["CI_REF"],
            "baseSha": binding["CI_BASE_SHA"],
            "headSha": expected_head_sha,
            "executionSha": binding["CI_EXECUTION_SHA"],
            "workflowRunId": 300,
            "runAttempt": 1,
            "pullRequestNumber": int(pull_request_number) if pull_request_number else None,
            "mergeGroupHeadRef": merge_group_head_ref or None,
        },
        "authenticatedRun": {
            "issuer": "https://token.actions.githubusercontent.com",
            "audience": "ci-coordinator",
            "repository": "example-org/sample-service",
            "repositoryId": 200,
            "ref": binding["CI_REF"],
            "executionSha": binding["CI_EXECUTION_SHA"],
            "runId": 300,
            "runAttempt": 1,
            "eventName": event_name,
            "workflowRef": f"example-org/sample-service/{workflow_path}@refs/heads/master",
            "workflowSha": workflow_sha,
            "jobWorkflowRef": requester_ref,
            "jobWorkflowSha": requester_sha,
            "checkRunId": None,
            "verifiedAt": "2026-07-15T00:00:00+00:00",
            "verifierVersion": "github-oidc/v1",
            "claimHash": None,
        },
        "verifiedPlanId": None if fallback else plan_id,
        "productionAdmissionReceiptId": (None if fallback else "production_admission_" + "a" * 32),
        "execution": execution,
        "verifierVersion": None if fallback else "verification-core/v1",
        "fallbackReason": reason,
    }


def _event_binding(event_name: str) -> dict[str, str]:
    if event_name == "pull_request":
        return {
            "CI_BASE_SHA": "base-sha",
            "CI_EVENT": event_name,
            "CI_EXECUTION_SHA": "execution-sha",
            "CI_HEAD_SHA": "head-sha",
            "CI_MERGE_GROUP_HEAD_REF": "",
            "CI_PULL_REQUEST_NUMBER": "42",
            "CI_REF": "refs/pull/42/merge",
        }
    if event_name == "push":
        return {
            "CI_BASE_SHA": "base-sha",
            "CI_EVENT": event_name,
            "CI_EXECUTION_SHA": "execution-sha",
            "CI_HEAD_SHA": "head-sha",
            "CI_MERGE_GROUP_HEAD_REF": "",
            "CI_PULL_REQUEST_NUMBER": "",
            "CI_REF": "refs/heads/master",
        }
    raise ValueError(f"unsupported test event: {event_name}")


def _read_workflow(path: Path) -> dict[str, Any]:
    parser = YAML(typ="safe")
    return _mapping(parser.load(path.read_text(encoding="utf-8")))


def _mapping(value: object) -> dict[str, Any]:
    assert isinstance(value, dict)
    return value


def _sequence(value: object) -> list[dict[str, Any]]:
    assert isinstance(value, list)
    assert all(isinstance(item, dict) for item in value)
    return value

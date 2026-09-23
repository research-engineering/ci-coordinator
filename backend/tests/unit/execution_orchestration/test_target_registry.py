from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from package_b_support import (
    PLAN_REQUEST_JOB_ID,
    PLAN_REQUEST_WORKFLOW_REF,
    make_adapter_files,
    make_catalog,
    make_target_registry,
)

from ci_coordinator.execution_orchestration import (
    CONTROL_INVOCATION_JOB_ID,
    LOCAL_PLAN_REQUEST_WORKFLOW_PATH,
    LOCAL_PLAN_REQUEST_WORKFLOW_REF,
    TargetExecutionRegistry,
    TargetJobBinding,
    TargetWorkflowBinding,
    TrustedExecutionTarget,
    parse_target_execution_registry,
)
from ci_coordinator.kernel import canonical_json
from ci_coordinator.validation_contract import ValidationCatalog


def test_target_registry_round_trips_and_admits_exact_profile_authority() -> None:
    catalog = make_catalog()
    expected = make_target_registry(catalog)

    parsed = parse_target_execution_registry(
        json.dumps(expected.to_identity_mapping(), separators=(",", ":")).encode()
    )

    assert parsed == expected
    assert parsed is not None and parsed.admits(catalog)


def test_target_registry_rejects_authority_relabeling_and_noncanonical_source() -> None:
    catalog = make_catalog()
    registry = make_target_registry(catalog)
    profile = catalog.execution_profiles[0]
    broadened = replace(profile, credential_profile_id="production-secrets")
    broadened_catalog = ValidationCatalog(
        obligations=catalog.obligations,
        witnesses=catalog.witnesses,
        execution_profiles=(broadened,),
    )
    mapping = registry.to_identity_mapping()
    mapping["unexpected"] = True

    assert not registry.admits(broadened_catalog)
    assert parse_target_execution_registry(json.dumps(mapping).encode()) is None


def test_target_registry_cannot_add_an_execution_profile_outside_the_catalog() -> None:
    catalog = make_catalog()
    registry = make_target_registry(catalog)
    profile = registry.profiles[0]
    extra = replace(profile, profile_id="extra-profile", job_id="extra-job")
    workflow = registry.workflows[0]
    broadened = replace(
        registry,
        workflows=(
            replace(
                workflow,
                execution_jobs=(
                    TargetJobBinding("extra-job", ("plan",)),
                    *workflow.execution_jobs,
                ),
            ),
        ),
        profiles=(extra, *registry.profiles),
    )

    assert not broadened.admits(catalog)


def test_target_registry_rejects_duplicate_keys_and_duplicate_profiles() -> None:
    registry = make_target_registry(make_catalog()).to_identity_mapping()
    duplicate_profile = dict(registry)
    profiles = registry["profiles"]
    assert isinstance(profiles, list)
    duplicate_profile["profiles"] = [*profiles, *profiles]

    assert (
        parse_target_execution_registry(
            b'{"schemaVersion":"dynamic-ci-target-execution-registry/v1",'
            b'"schemaVersion":"dynamic-ci-target-execution-registry/v1",'
            b'"generator":{"id":"test","version":"1"},"workflows":[],"profiles":[]}'
        )
        is None
    )
    assert parse_target_execution_registry(json.dumps(duplicate_profile).encode()) is None


def test_target_registry_rejects_two_profiles_bound_to_one_static_job() -> None:
    registry = make_target_registry(make_catalog()).to_identity_mapping()
    profiles = registry["profiles"]
    assert isinstance(profiles, list)
    second_profile = {**profiles[0], "profileId": "python-other"}
    registry["profiles"] = [profiles[0], second_profile]

    assert parse_target_execution_registry(json.dumps(registry).encode()) is None


def test_target_registry_hash_seals_static_workflow_and_job_binding() -> None:
    registry = make_target_registry(make_catalog())
    binding = registry.profiles[0]
    workflow = registry.workflows[0]

    changed_workflow = replace(
        registry,
        adapter_files=make_adapter_files(".github/workflows/other.yml"),
        workflows=(
            replace(
                workflow,
                workflow_path=".github/workflows/other.yml",
            ),
        ),
        profiles=(replace(binding, workflow_path=".github/workflows/other.yml"),),
    )
    changed_job = replace(
        registry,
        workflows=(
            replace(
                workflow,
                execution_jobs=(TargetJobBinding("other-job", ("plan",)),),
            ),
        ),
        profiles=(replace(binding, job_id="other-job"),),
    )
    changed_requester = replace(
        registry,
        workflows=(
            replace(
                workflow,
                plan_request_workflow_ref=(
                    "example-org/ci-coordinator/"
                    ".github/workflows/trusted-plan-request.yml@" + "2" * 40
                ),
            ),
        ),
    )

    hashes = {
        registry.registry_hash,
        changed_workflow.registry_hash,
        changed_job.registry_hash,
        changed_requester.registry_hash,
    }
    assert len(hashes) == 4


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("repository", None),
        ("repository", "other/repository"),
        ("workflow_ref", None),
        ("workflow_ref", "example/repository/.github/workflows/other.yml@refs/heads/master"),
        ("workflow_ref", "example/repository/.github/workflows/ci-coordinator-bootstrap.yml@main"),
        ("workflow_sha", None),
        ("workflow_sha", "invalid"),
        ("workflow_sha", "2" * 40),
        ("job_workflow_ref", None),
        (
            "job_workflow_ref",
            "other/repository/.github/workflows/trusted-plan-request.yml@refs/heads/master",
        ),
        ("job_workflow_ref", "example/repository/.github/workflows/other.yml@refs/heads/master"),
        (
            "job_workflow_ref",
            "example/repository/.github/workflows/trusted-plan-request.yml@refs/heads/other",
        ),
        ("job_workflow_sha", None),
        ("job_workflow_sha", "2" * 40),
    ],
)
def test_local_requester_binds_every_independent_identity_operand(
    field: str,
    replacement: str | None,
) -> None:
    workflow = replace(
        make_target_registry(make_catalog()).workflows[0],
        plan_request_workflow_ref=LOCAL_PLAN_REQUEST_WORKFLOW_REF,
    )
    identity = {
        "repository": "example/repository",
        "workflow_ref": f"example/repository/{workflow.workflow_path}@refs/heads/master",
        "workflow_sha": "1" * 40,
        "job_workflow_ref": (
            f"example/repository/{LOCAL_PLAN_REQUEST_WORKFLOW_PATH}@refs/heads/master"
        ),
        "job_workflow_sha": "1" * 40,
    }
    assert workflow.admits_plan_request_identity(**identity)
    changed: dict[str, str | None] = {**identity, field: replacement}
    assert not workflow.admits_plan_request_identity(**changed)


def test_local_requester_requires_the_exact_adapter_path() -> None:
    registry = make_target_registry(make_catalog())
    workflow = replace(
        registry.workflows[0], plan_request_workflow_ref=LOCAL_PLAN_REQUEST_WORKFLOW_REF
    )
    with pytest.raises(ValueError, match="local plan requester"):
        replace(registry, workflows=(workflow,))
    current = replace(
        registry,
        workflows=(workflow,),
        adapter_files=make_adapter_files(workflow.workflow_path, LOCAL_PLAN_REQUEST_WORKFLOW_PATH),
    )
    assert (
        parse_target_execution_registry(canonical_json(current.to_identity_mapping()) + b"\n")
        == current
    )


def test_target_registry_rejects_mutable_requester_refs_and_binds_exact_identity() -> None:
    registry = make_target_registry(make_catalog())
    workflow = registry.workflows[0]

    assert workflow.plan_request_job_id == PLAN_REQUEST_JOB_ID
    assert workflow.plan_request_workflow_ref == PLAN_REQUEST_WORKFLOW_REF
    assert workflow.admits_plan_request_identity(
        job_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
        job_workflow_sha="1" * 40,
    )
    assert not workflow.admits_plan_request_identity(
        job_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
        job_workflow_sha="2" * 40,
    )
    with pytest.raises(ValueError, match="immutable workflow ref"):
        replace(
            workflow,
            plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF.rsplit("@", 1)[0] + "@main",
        )

    mapping = registry.to_identity_mapping()
    workflows = mapping["workflows"]
    assert isinstance(workflows, list)
    encoded_workflow = workflows[0]
    assert isinstance(encoded_workflow, dict)
    encoded_workflow["planRequestWorkflowRef"] = (
        PLAN_REQUEST_WORKFLOW_REF.rsplit("@", 1)[0] + "@main"
    )
    assert parse_target_execution_registry(json.dumps(mapping).encode()) is None


def test_target_registry_hash_seals_every_adapter_file_digest() -> None:
    registry = make_target_registry(make_catalog())
    changed_files = (
        replace(registry.adapter_files[0], sha256="f" * 64),
        *registry.adapter_files[1:],
    )

    changed = replace(registry, adapter_files=changed_files)

    assert changed.registry_hash != registry.registry_hash


def test_target_registry_requires_the_complete_validator_and_workflow_binding() -> None:
    registry = make_target_registry(make_catalog())

    with pytest.raises(ValueError, match="fixed control-file set"):
        replace(registry, adapter_files=registry.adapter_files[1:])
    with pytest.raises(ValueError, match="belong to the adapter bundle"):
        replace(
            registry,
            adapter_files=tuple(item for item in registry.adapter_files if not item.is_workflow),
        )


def test_target_registry_codec_matches_the_gate_signal_byte_bound() -> None:
    registry = make_target_registry(make_catalog())
    admitted = replace(
        registry,
        workflows=(replace(registry.workflows[0], gate_signal_name="g" * 256),),
    )
    rejected = admitted.to_identity_mapping()
    rejected_workflows = rejected["workflows"]
    assert isinstance(rejected_workflows, list)
    rejected_workflow = rejected_workflows[0]
    assert isinstance(rejected_workflow, dict)
    rejected_workflow["gateSignalName"] = "g" * 257

    parsed = parse_target_execution_registry(
        json.dumps(admitted.to_identity_mapping(), separators=(",", ":")).encode()
    )

    assert parsed == admitted
    assert (
        parse_target_execution_registry(json.dumps(rejected, separators=(",", ":")).encode())
        is None
    )


def test_target_registry_declares_native_gate_without_shard_coordinates() -> None:
    workflow_path = ".github/workflows/full-check.yml"
    registry = TargetWorkflowBinding(
        workflow_path=workflow_path,
        execution_kind="native-job-set",
        execution_jobs=(TargetJobBinding("backend", ("plan",)),),
        plan_request_job_id=PLAN_REQUEST_JOB_ID,
        plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
        plan_job_id="plan",
        fallback_job_id=None,
        gate_job_id="pr-gate",
        gate_signal_name="Pull Request Gate",
    )

    signal = registry.provider_signal

    assert signal.kind == "declared-native"
    assert signal.workflow_path == workflow_path
    assert signal.job_id == "pr-gate"
    assert signal.execution_profile_id is None
    assert signal.shard_id is None


@pytest.mark.parametrize(
    ("execution_kind", "fallback_job_id", "expected_gate_dependencies", "expected_topology"),
    [
        (
            "native-job-set",
            None,
            ("backend", "plan"),
            (
                ("backend", ("plan",)),
                (CONTROL_INVOCATION_JOB_ID, ()),
                ("plan", ("plan-request",)),
                ("plan-request", (CONTROL_INVOCATION_JOB_ID,)),
                ("pr-gate", ("backend", "plan")),
            ),
        ),
        (
            "witness-shards",
            "full-ci",
            ("backend", "full-ci", "plan"),
            (
                ("backend", ("plan",)),
                (CONTROL_INVOCATION_JOB_ID, ()),
                ("full-ci", ("plan",)),
                ("plan", ("plan-request",)),
                ("plan-request", (CONTROL_INVOCATION_JOB_ID,)),
                ("pr-gate", ("backend", "full-ci", "plan")),
            ),
        ),
    ],
)
def test_target_workflow_owns_its_complete_static_topology(
    execution_kind: Literal["witness-shards", "native-job-set"],
    fallback_job_id: str | None,
    expected_gate_dependencies: tuple[str, ...],
    expected_topology: tuple[tuple[str, tuple[str, ...]], ...],
) -> None:
    workflow = TargetWorkflowBinding(
        workflow_path=".github/workflows/full-check.yml",
        execution_kind=execution_kind,
        execution_jobs=(TargetJobBinding("backend", ("plan",)),),
        plan_request_job_id=PLAN_REQUEST_JOB_ID,
        plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
        plan_job_id="plan",
        fallback_job_id=fallback_job_id,
        gate_job_id="pr-gate",
        gate_signal_name="Pull Request Gate",
    )

    assert workflow.gate_dependencies == expected_gate_dependencies
    assert workflow.job_topology == expected_topology


@pytest.mark.parametrize(
    ("execution_kind", "fallback_job_id"),
    [
        ("witness-shards", None),
        ("native-job-set", "full-ci"),
    ],
)
def test_target_workflow_rejects_a_fallback_outside_its_execution_kind(
    execution_kind: Literal["witness-shards", "native-job-set"],
    fallback_job_id: str | None,
) -> None:
    with pytest.raises(ValueError, match="fallback"):
        TargetWorkflowBinding(
            workflow_path=".github/workflows/full-check.yml",
            execution_kind=execution_kind,
            execution_jobs=(TargetJobBinding("backend", ("plan",)),),
            plan_request_job_id=PLAN_REQUEST_JOB_ID,
            plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
            plan_job_id="plan",
            fallback_job_id=fallback_job_id,
            gate_job_id="gate",
            gate_signal_name="CI",
        )


def test_target_registry_scopes_duplicate_gate_names_by_workflow_identity() -> None:
    registry = make_target_registry(make_catalog())
    first_workflow = registry.workflows[0]
    first_profile = registry.profiles[0]
    second_path = ".github/workflows/release.yml"
    second_workflow = replace(
        first_workflow,
        workflow_path=second_path,
        execution_jobs=(TargetJobBinding("release-check", ("plan",)),),
        gate_job_id="release-gate",
    )
    second_profile = replace(
        first_profile,
        profile_id="release-profile",
        workflow_path=second_path,
        job_id="release-check",
    )
    combined = TargetExecutionRegistry(
        generator_id=registry.generator_id,
        generator_version=registry.generator_version,
        adapter_files=make_adapter_files(first_workflow.workflow_path, second_path),
        workflows=(first_workflow, second_workflow),
        profiles=(first_profile, second_profile),
    )

    parsed = parse_target_execution_registry(
        json.dumps(combined.to_identity_mapping(), separators=(",", ":")).encode()
    )

    assert parsed == combined
    assert first_workflow.gate_signal_name == second_workflow.gate_signal_name
    assert first_workflow.provider_signal.signal_id != second_workflow.provider_signal.signal_id


def test_target_workflow_computes_transitive_dependency_closure() -> None:
    workflow = TargetWorkflowBinding(
        workflow_path=".github/workflows/full-check.yml",
        execution_kind="native-job-set",
        execution_jobs=(
            TargetJobBinding("build", ("plan",)),
            TargetJobBinding("integration", ("package", "plan")),
            TargetJobBinding("package", ("build", "plan")),
        ),
        plan_request_job_id=PLAN_REQUEST_JOB_ID,
        plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
        plan_job_id="plan",
        fallback_job_id=None,
        gate_job_id="gate",
        gate_signal_name="CI",
    )

    assert workflow.dependency_closure(("integration",)) == (
        "build",
        "integration",
        "package",
    )


@pytest.mark.parametrize(
    ("execution_kind", "admitted"),
    [("native-job-set", True), ("witness-shards", False)],
)
def test_execution_target_requires_every_unconditional_static_job(
    execution_kind: Literal["witness-shards", "native-job-set"],
    admitted: bool,
) -> None:
    base = make_target_registry(make_catalog())
    profile = base.profiles[0]
    selected_job_id = profile.job_id
    required_job_id = "required-job"
    workflow = replace(
        base.workflows[0],
        execution_kind=execution_kind,
        execution_jobs=tuple(
            sorted(
                (
                    TargetJobBinding(
                        selected_job_id,
                        (
                            ("plan", required_job_id)
                            if execution_kind == "native-job-set"
                            else ("plan",)
                        ),
                    ),
                    TargetJobBinding(required_job_id, ("plan",)),
                ),
                key=lambda item: item.job_id,
            )
        ),
        fallback_job_id=("full-ci" if execution_kind == "witness-shards" else None),
        required_job_ids=(required_job_id,),
    )
    registry = replace(
        base,
        workflows=(workflow,),
        profiles=tuple(
            sorted(
                (
                    replace(profile, execution_kind=execution_kind),
                    replace(
                        profile,
                        profile_id="required-profile",
                        job_id=required_job_id,
                        execution_kind=execution_kind,
                    ),
                ),
                key=lambda item: item.profile_id,
            )
        ),
    )

    if admitted:
        target = TrustedExecutionTarget(
            verified_plan_id="verified-plan",
            workflow_path=workflow.workflow_path,
            selected_profile_ids=(profile.profile_id,),
            target_registry=registry,
        )
        assert target.execution_kind == "native-job-set"
    else:
        with pytest.raises(ValueError, match="required static job"):
            TrustedExecutionTarget(
                verified_plan_id="verified-plan",
                workflow_path=workflow.workflow_path,
                selected_profile_ids=(profile.profile_id,),
                target_registry=registry,
            )


def test_execution_target_rejects_an_unselected_witness_job_dependency() -> None:
    base = make_target_registry(make_catalog())
    selected_profile = base.profiles[0]
    dependency_profile = replace(
        selected_profile,
        profile_id="dependency-profile",
        job_id="dependency-job",
    )
    workflow = replace(
        base.workflows[0],
        execution_jobs=tuple(
            sorted(
                (
                    TargetJobBinding(
                        selected_profile.job_id,
                        (dependency_profile.job_id, "plan"),
                    ),
                    TargetJobBinding(dependency_profile.job_id, ("plan",)),
                ),
                key=lambda item: item.job_id,
            )
        ),
    )
    registry = replace(
        base,
        workflows=(workflow,),
        profiles=tuple(
            sorted(
                (selected_profile, dependency_profile),
                key=lambda item: item.profile_id,
            )
        ),
    )

    with pytest.raises(ValueError, match="dependency closed"):
        TrustedExecutionTarget(
            verified_plan_id="verified-plan",
            workflow_path=workflow.workflow_path,
            selected_profile_ids=(selected_profile.profile_id,),
            target_registry=registry,
        )


@pytest.mark.parametrize("job_id", ["_Build", "Test-Windows_2026"])
def test_target_job_binding_accepts_the_bounded_github_job_id_language(
    job_id: str,
) -> None:
    assert TargetJobBinding(job_id, ("plan",)).job_id == job_id


def test_target_workflow_rejects_job_ids_that_alias_in_github_expressions() -> None:
    with pytest.raises(ValueError, match="GitHub expressions"):
        TargetWorkflowBinding(
            workflow_path=".github/workflows/full-check.yml",
            execution_kind="native-job-set",
            execution_jobs=(
                TargetJobBinding("Lint", ("plan",)),
                TargetJobBinding("lint", ("plan",)),
            ),
            plan_request_job_id=PLAN_REQUEST_JOB_ID,
            plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
            plan_job_id="plan",
            fallback_job_id=None,
            gate_job_id="gate",
            gate_signal_name="CI",
        )


def test_target_workflow_rejects_the_fixed_classifier_job_as_an_execution_role() -> None:
    with pytest.raises(ValueError, match="GitHub expressions"):
        TargetWorkflowBinding(
            workflow_path=".github/workflows/full-check.yml",
            execution_kind="native-job-set",
            execution_jobs=(TargetJobBinding(CONTROL_INVOCATION_JOB_ID.upper(), ("plan",)),),
            plan_request_job_id=PLAN_REQUEST_JOB_ID,
            plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
            plan_job_id="plan",
            fallback_job_id=None,
            gate_job_id="gate",
            gate_signal_name="CI",
        )


@pytest.mark.parametrize(
    "jobs",
    [
        (TargetJobBinding("test"),),
        (TargetJobBinding("test", ("missing", "plan")),),
        (
            TargetJobBinding("build", ("plan", "test")),
            TargetJobBinding("test", ("build", "plan")),
        ),
    ],
    ids=["missing-plan", "dangling", "cycle"],
)
def test_target_workflow_rejects_non_executable_dependency_graph(
    jobs: tuple[TargetJobBinding, ...],
) -> None:
    with pytest.raises(ValueError):
        TargetWorkflowBinding(
            workflow_path=".github/workflows/full-check.yml",
            execution_kind="native-job-set",
            execution_jobs=jobs,
            plan_request_job_id=PLAN_REQUEST_JOB_ID,
            plan_request_workflow_ref=PLAN_REQUEST_WORKFLOW_REF,
            plan_job_id="plan",
            fallback_job_id=None,
            gate_job_id="gate",
            gate_signal_name="CI",
        )

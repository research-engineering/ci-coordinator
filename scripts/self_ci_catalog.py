from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from ci_coordinator.config_control import ValidatedEpochDraft, admit_policy_document
from ci_coordinator.config_control.planning_projection import (
    project_dynamic_ci_planning,
)
from ci_coordinator.kernel import canonical_json
from ci_coordinator.repo_context.freshness import validate_path_pattern
from ci_coordinator.validation_contract import (
    ExecutableWitness,
    ExecutionProfile,
    ShardingPolicy,
    ValidationCatalog,
    ValidationObligation,
)
from scripts.proofkit_common import JsonObject, as_object
from scripts.self_ci_source import (
    NATIVE_PATH,
    REQUESTER_ASSERTION_ID,
    REQUESTER_ID,
    admit_native_source,
    read_regular,
)

COVERAGE_JOBS = ("native-test-plan", "native-test-shards", "postgres-witness")
REQUESTER_JOBS = (REQUESTER_ASSERTION_ID, REQUESTER_ID)


@dataclass(frozen=True)
class FamilySettings:
    responsibility_paths: tuple[str, ...] = ("**",)
    omit_allowed: bool = False
    responsibility_risk_classes: tuple[str, ...] = ("source",)

    def __post_init__(self) -> None:
        if (
            type(self.omit_allowed) is not bool
            or not self.responsibility_paths
            or self.responsibility_paths != tuple(sorted(set(self.responsibility_paths)))
            or any(validate_path_pattern(path) is not None for path in self.responsibility_paths)
            or self.responsibility_risk_classes
            != tuple(sorted(set(self.responsibility_risk_classes)))
        ):
            raise ValueError("family applicability requires canonical admitted paths and omission")


def validation_catalog(
    jobs: dict[str, JsonObject],
    family_settings: Mapping[str, FamilySettings] | None = None,
) -> ValidationCatalog:
    profiles = tuple(_profile(job_id, job) for job_id, job in sorted(jobs.items()))
    groups = {
        "python-native-coverage": COVERAGE_JOBS,
        "requester-negative-contract": REQUESTER_JOBS,
        **{
            job_id: (job_id,)
            for job_id in sorted(jobs)
            if job_id not in {*COVERAGE_JOBS, *REQUESTER_JOBS}
        },
    }
    if {job_id for group in groups.values() for job_id in group} != jobs.keys():
        raise ValueError("semantic families do not cover the static execution jobs")
    settings = {} if family_settings is None else family_settings
    if set(settings) - groups.keys() or any(
        type(row) is not FamilySettings for row in settings.values()
    ):
        raise ValueError("family applicability references an unknown family or untyped settings")
    if any(
        row.omit_allowed and family not in {"utility-config", "utility-go-static"}
        for family, row in settings.items()
    ):
        raise ValueError("native, provider and vulnerability families must remain non-omittable")
    return ValidationCatalog(
        obligations=tuple(
            ValidationObligation(
                obligation_id=group_id,
                responsibility_paths=settings.get(group_id, FamilySettings()).responsibility_paths,
                responsibility_risk_classes=settings.get(
                    group_id, FamilySettings()
                ).responsibility_risk_classes,
                required_witness_ids=tuple(sorted(group)),
                default_depth="full",
                full_depth="full",
                omit_allowed=settings.get(group_id, FamilySettings()).omit_allowed,
            )
            for group_id, group in sorted(groups.items())
        ),
        witnesses=tuple(
            ExecutableWitness(
                witness_id=job_id,
                execution_profile_id=job_id,
                supported_depths=("full",),
            )
            for job_id in sorted(jobs)
        ),
        execution_profiles=profiles,
    )


def _profile(job_id: str, job: JsonObject) -> ExecutionProfile:
    runner = job.get("runs-on", "ubuntu-24.04")
    if runner not in {"ubuntu-24.04", "macos-15"}:
        raise ValueError(f"self CI runner profile is not owned: {job_id}")
    permissions = as_object(job.get("permissions", {"contents": "read"}), "job permissions")
    if permissions not in ({}, {"contents": "read"}, {"id-token": "write"}):
        raise ValueError(f"self CI permission profile is not owned: {job_id}")
    oidc = permissions == {"id-token": "write"}
    return ExecutionProfile(
        profile_id=job_id,
        runner_profile_id=runner.replace(".", "-"),
        permission_profile_id=(
            "oidc-write" if oidc else "contents-read" if permissions else "no-permissions"
        ),
        credential_profile_id="actions-oidc" if oidc else "no-credentials",
        fixture_profile_id=f"native-{job_id}",
        service_profile_ids=(),
        capacity_class_id="hosted-default",
        sharding_policy=ShardingPolicy(
            max_shards=1,
            max_parallel=1,
            max_items_per_shard=1000,
            setup_seconds_per_shard=0.0,
        ),
    )


def policy_document(
    catalog: ValidationCatalog,
    *,
    installation_id: int,
    repository_id: int,
    default_branch: str,
    dependency_graph: JsonObject | None = None,
) -> bytes:
    native = admit_native_source(read_regular(Path(__file__).resolve().parents[1], NATIVE_PATH))
    triggers = as_object(native.workflow["on"], "native workflow triggers")
    native_name = native.workflow.get("name")
    if (
        "pull_request" not in triggers
        or triggers["pull_request"] not in (None, {})
        or not isinstance(native_name, str)
        or not native_name.strip()
    ):
        raise ValueError("native policy observation requires an unfiltered named PR workflow")
    graph = (
        {"source": "configured", "globalRiskPaths": ["**"]}
        if dependency_graph is None
        else {
            "source": dependency_graph["source"],
            "globalRiskPaths": dependency_graph["globalRiskPaths"],
        }
    )
    value = {
        "schemaVersion": "ci-repository-policy/v1",
        "repository": {
            "installationId": installation_id,
            "repositoryId": repository_id,
            "owner": "research-engineering",
            "name": "ci-coordinator",
            "defaultBranch": default_branch,
            "dynamicCi": {
                "planningEnabled": True,
                "policyVersion": "self-ci-qualification-v1",
                "riskClasses": ["source"],
                "agentAdvice": {
                    "enabled": False,
                    "modelIdAllowlist": [],
                    "promptHashAllowlist": [],
                    "minConfidence": 0.7,
                    "promptInjectionEvalRequired": False,
                },
                "dependencyGraph": graph,
                "fallbackTimeoutSeconds": 60,
                **catalog.to_identity_mapping(),
            },
            "rules": [
                {
                    "name": "native-pr-full-check",
                    "on": {"event": "pull_request", "branches": [default_branch]},
                    "mode": "observe",
                    "timing": {
                        "expectedSignalTimeoutSeconds": 3600,
                        "absenceVerificationWindowSeconds": 300,
                        "absencePollLookbackSeconds": 3600,
                        "lateFindingWindowSeconds": 86400,
                        "mutableDecisionWindowSeconds": 300,
                    },
                    "expectedSignals": [
                        {
                            "kind": "workflow",
                            "name": native_name,
                            "workflowFile": Path(NATIVE_PATH).name,
                            "source": "native",
                            "requiredConclusion": "success",
                            "required": True,
                        }
                    ],
                    "omittedSignals": [],
                }
            ],
        },
    }
    content = canonical_json(value) + b"\n"
    admitted = admit_policy_document(content, "json")
    if not isinstance(admitted, ValidatedEpochDraft):
        diagnostic = admitted[0]
        detail = {
            "code": diagnostic.code[:256],
            "ruleId": diagnostic.rule_id[:256],
            "pointer": diagnostic.instance_pointer[:256],
        }
        raise ValueError(
            "self CI policy failed repository-owned admission: "
            + json.dumps(detail, sort_keys=True)
        )
    projection = project_dynamic_ci_planning(admitted)
    if projection is None or projection.validation_catalog != catalog:
        raise ValueError("self CI policy changed its validation catalog during compilation")
    return content

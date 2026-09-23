"""Closed independent-producer fixture."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from workflow_authority.factories import evidence as workflow_evidence

from ci_coordinator.config_control import (
    RepositoryScope,
    ValidatedEpochDraft,
    admit_policy_document,
)
from ci_coordinator.config_control.planning_projection import project_dynamic_ci_planning
from ci_coordinator.consumer_contract_lab.model import (
    ConsumerLabProfile,
    ConsumerLabScenario,
    ConsumerRepository,
    ExpectedOutcome,
    FileBinding,
    ScenarioChange,
    ScenarioCorpus,
)
from ci_coordinator.execution_orchestration import (
    LOCAL_PLAN_REQUEST_WORKFLOW_PATH,
    LOCAL_PLAN_REQUEST_WORKFLOW_REF,
    TARGET_CONTROL_FILE_PATHS,
    TargetAdapterFileBinding,
    TargetExecutionRegistry,
    TargetJobBinding,
    TargetProfileBinding,
    TargetWorkflowBinding,
)
from ci_coordinator.governance_observation import (
    EffectiveGovernanceRule,
    GovernanceRepository,
    GovernanceState,
)
from ci_coordinator.kernel import canonical_json, utf16_sort_key
from ci_coordinator.repo_context import (
    DefaultBranchWorkflow,
    ProviderWorkflowInventory,
    ProviderWorkflowInventoryEvidence,
    parse_workflow_capability,
)
from ci_coordinator.target_authority_producers import (
    ObservationCandidateSet,
    ObservationSources,
    ObservedCandidate,
    OwnerProjectionPolicy,
    ProjectionRule,
    ProviderAuthoritySources,
    RegistrationCandidateSet,
    TargetArtifactSources,
    enumerate_registration_candidates,
)
from ci_coordinator.target_authority_relation import (
    AuthorityField,
    AuthorityFieldEntry,
    AuthorityIntroduction,
    AuthorityTransitionDelta,
    BaselineDisposition,
    EpochComponent,
    ExpectedTargetAuthorityRelation,
    PhaseZeroBaseline,
    ProducerIdentity,
    TargetAuthorityEpoch,
    TargetAuthorityKey,
    TargetAuthorityRow,
    TargetAuthoritySubject,
    apply_transition,
    row_field_names,
)
from ci_coordinator.validation_contract import ExecutionProfile
from ci_coordinator.workflow_authority import WorkflowAuthorityEvidence, git_blob_oid
from ci_coordinator.workflow_discovery import DiscoveryReport, RepositoryIdentity, WorkflowSource
from ci_coordinator.workflow_discovery.graph import analyze_call_graph
from ci_coordinator.workflow_discovery.parser import parse_workflow
from ci_coordinator.workflow_discovery.summary import ParsedWorkflow

SCOPE = RepositoryScope(11, 22)
REVISION = "a" * 40
WORKFLOW_PATH = ".github/workflows/ci.yml"
WORKFLOW_DOCUMENT = """\
name: CI
on:
  push:
jobs:
  ci-invocation:
    runs-on: ubuntu-latest
    steps: []
  gate:
    name: Pull Request Gate
    needs: [plan, test]
    if: always()
    runs-on: ubuntu-latest
    steps: []
  plan:
    needs: plan-request
    runs-on: ubuntu-latest
    steps: []
  plan-request:
    needs: ci-invocation
    runs-on: ubuntu-latest
    steps: []
  test:
    needs: plan
    runs-on: ubuntu-latest
    steps: []
"""
REQUESTER_DOCUMENT = b"""\
name: Trusted plan request
on:
  workflow_call:
jobs:
  request:
    runs-on: ubuntu-latest
    steps: []
"""


def digest(label: str) -> str:
    return sha256(label.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ProducerFixture:
    subject: TargetAuthoritySubject
    baseline: PhaseZeroBaseline
    delta: AuthorityTransitionDelta
    expected: ExpectedTargetAuthorityRelation
    candidates: ObservationCandidateSet
    target_artifacts: TargetArtifactSources
    registration_candidates: RegistrationCandidateSet
    policy: OwnerProjectionPolicy
    registration_producer: ProducerIdentity
    observation_producer: ProducerIdentity
    workflow_evidence: WorkflowAuthorityEvidence


def producer_fixture(*, local_requester: bool = False) -> ProducerFixture:
    subject = TargetAuthoritySubject(SCOPE, "repository-validation")
    sources = observation_sources(local_requester=local_requester)
    manifest_digest = sources.workflow_evidence.manifest.manifest_digest
    target_artifacts = sources.target_artifacts
    registration_candidates = enumerate_registration_candidates(target_artifacts)
    target_artifact_epoch_digest = target_artifacts.artifact_epoch_digest
    # Live discovery retains safety unknowns; this closed success witness is synthetic.
    candidates = tuple(
        sorted(
            (
                *(
                    observed_candidate(
                        f"workflow:{entry.path}", "workflow", f"git:{entry.path}@{REVISION}"
                    )
                    for entry in sources.workflow_evidence.manifest.entries
                ),
                *registration_candidates.candidates,
            ),
            key=lambda item: utf16_sort_key(item.candidate_id),
        )
    )
    candidate_set = ObservationCandidateSet(
        scope=subject.scope,
        source_commit_id=sources.workflow_evidence.source_binding.source_commit_id,
        source_binding_digest=sources.workflow_evidence.source_binding.binding_digest,
        workflow_manifest_digest=manifest_digest,
        discovery_report_digest=sources.discovery_report.inventory_digest,
        provider_authority_digest=sources.provider_authority.authority_digest,
        target_artifact_epoch_digest=target_artifact_epoch_digest,
        target_policy_digest=target_artifacts.policy.epoch_hash,
        validation_catalog_digest=target_artifacts.validation_catalog.catalog_hash,
        target_registry_digest=target_artifacts.registry.registry_hash,
        candidates=candidates,
    )
    rules = tuple(
        ProjectionRule(
            candidate.candidate_id,
            candidate.evidence_digest,
            TargetAuthorityKey(
                candidate.projected_family,
                candidate.candidate_id.partition(":")[2],
            ),
            "authority",
            "target-owner",
            candidate.source_locator,
        )
        for candidate in candidates
    )
    policy = OwnerProjectionPolicy(
        subject,
        manifest_digest,
        target_artifact_epoch_digest,
        candidate_set.authority_domain_digest,
        registration_candidates.candidate_set_digest,
        rules,
    )
    target_epoch = TargetAuthorityEpoch(
        "adapted_target",
        EpochComponent.present(manifest_digest),
        EpochComponent.present(candidate_set.provider_authority_digest),
        EpochComponent.present(target_artifacts.policy.epoch_hash),
        EpochComponent.present(target_artifacts.validation_catalog.catalog_hash),
        EpochComponent.present(target_artifacts.registry.registry_hash),
        EpochComponent.present(policy.policy_digest),
    )
    target_rows = tuple(
        sorted(
            (
                TargetAuthorityRow(
                    rule.row_key,
                    rule.disposition,
                    rule.semantic_owner,
                    rule.source_locator,
                    candidate.fields,
                )
                for candidate, rule in zip(candidates, rules, strict=True)
            ),
            key=lambda item: item.key.sort_key,
        )
    )
    native_owner = digest("native-owner")
    native_epoch = TargetAuthorityEpoch(
        "native_baseline",
        EpochComponent.present(manifest_digest),
        EpochComponent.present(candidate_set.provider_authority_digest),
        EpochComponent.not_applicable(),
        EpochComponent.not_applicable(),
        EpochComponent.not_applicable(),
        EpochComponent.present(native_owner),
    )
    baseline_rows = tuple(
        TargetAuthorityRow(
            row.key,
            row.disposition,
            "native-owner",
            row.source_locator,
            row.fields,
        )
        for row in target_rows
        if row.key.family in {"job", "provider_repository", "workflow"}
    )
    baseline = PhaseZeroBaseline(
        subject,
        native_epoch,
        ProducerIdentity("native-owner-inventory", "1"),
        native_owner,
        baseline_rows,
    )
    baseline_keys = {row.key for row in baseline_rows}
    target_rows_by_key = {row.key: row for row in target_rows}
    delta = AuthorityTransitionDelta(
        subject,
        baseline.baseline_digest,
        target_epoch,
        ProducerIdentity("owner-transition", "1"),
        policy.policy_digest,
        tuple(
            BaselineDisposition(
                row.key,
                "replaced",
                (target_rows_by_key[row.key],),
                "owner-approved authority transfer",
            )
            for row in baseline_rows
        ),
        tuple(
            AuthorityIntroduction(row, "owner-approved target authority")
            for row in target_rows
            if row.key not in baseline_keys
        ),
    )
    expected = apply_transition(baseline, delta)
    if not isinstance(expected, ExpectedTargetAuthorityRelation):
        raise AssertionError("producer fixture transition did not create an expected relation")
    return ProducerFixture(
        subject=subject,
        baseline=baseline,
        delta=delta,
        expected=expected,
        candidates=candidate_set,
        target_artifacts=target_artifacts,
        registration_candidates=registration_candidates,
        policy=policy,
        registration_producer=ProducerIdentity("registration-producer", "1"),
        observation_producer=ProducerIdentity("observation-producer", "1"),
        workflow_evidence=sources.workflow_evidence,
    )


def observed_candidate(
    candidate_id: str,
    family: str,
    source_locator: str,
    *,
    revision: int = 1,
) -> ObservedCandidate:
    projected_family = "workflow" if family == "workflow_blob" else family
    fields = tuple(
        AuthorityFieldEntry(name, AuthorityField.present({"name": name, "revision": revision}))
        for name in row_field_names(projected_family)  # type: ignore[arg-type]
    )
    return ObservedCandidate(
        candidate_id,
        family,  # type: ignore[arg-type]
        source_locator,
        fields,
    )


def observation_sources(
    *,
    revision: str = REVISION,
    workflow_document: str = WORKFLOW_DOCUMENT,
    local_requester: bool = False,
) -> ObservationSources:
    policy = _admitted_policy(local_requester=local_requester)
    projection = project_dynamic_ci_planning(policy)
    if projection is None:
        raise AssertionError("producer fixture policy lost its dynamic projection")
    catalog = projection.validation_catalog
    registry = _registry(catalog.execution_profiles[0], local_requester=local_requester)
    if local_requester:
        workflow_document = workflow_document.replace(
            "  plan-request:\n    needs: ci-invocation\n    runs-on: ubuntu-latest\n    steps: []",
            "  plan-request:\n    needs: ci-invocation\n"
            "    uses: ./.github/workflows/trusted-plan-request.yml",
        )
    extra = ((LOCAL_PLAN_REQUEST_WORKFLOW_PATH, REQUESTER_DOCUMENT),) if local_requester else ()
    content = workflow_document.encode()
    evidence = workflow_evidence(
        revision=revision,
        workflow_content=content,
        workflow_path=WORKFLOW_PATH,
        scope=SCOPE,
        owner="example-org",
        name="consumer",
        default_branch="master",
        extra_workflows=extra,
    )
    report = _discovery_report(content, revision=revision, extra=extra)
    capability = parse_workflow_capability(
        content,
        path=WORKFLOW_PATH,
        revision_sha=revision,
    )
    if capability is None:
        raise AssertionError("producer fixture workflow capability did not parse")
    capabilities = [capability]
    for path, document in extra:
        additional = parse_workflow_capability(document, path=path, revision_sha=revision)
        assert additional is not None
        capabilities.append(additional)
    provider_inventory = ProviderWorkflowInventory(
        revision,
        tuple(
            DefaultBranchWorkflow(101 + index, item.path, True)
            for index, item in enumerate(capabilities)
        ),
        tuple(capabilities),
    )
    lab_profile, scenarios = _consumer_lab(policy)
    governance = _governance()
    provider_authority = ProviderAuthoritySources(
        ProviderWorkflowInventoryEvidence(
            SCOPE,
            "example-org",
            "consumer",
            "master",
            "2026-03-10",
            provider_inventory,
        ),
        governance,
    )
    target_artifacts = TargetArtifactSources(policy, registry, lab_profile, scenarios)
    return ObservationSources(
        workflow_evidence=evidence,
        discovery_report=report,
        provider_authority=provider_authority,
        target_artifacts=target_artifacts,
    )


def _admitted_policy(*, local_requester: bool = False) -> ValidatedEpochDraft:
    document = {
        "schemaVersion": "ci-repository-policy/v1",
        "repository": {
            "installationId": SCOPE.installation_id,
            "repositoryId": SCOPE.repository_id,
            "owner": "example-org",
            "name": "consumer",
            "defaultBranch": "master",
            "rules": [
                {
                    "name": "main",
                    "on": {"event": "push", "branches": ["master"]},
                    "mode": "observe",
                    "expectedSignals": [
                        {
                            "kind": "workflow",
                            "name": "CI",
                            "workflowFile": "ci.yml",
                            "source": "native",
                            "requiredConclusion": "success",
                            "required": True,
                        }
                    ],
                }
            ],
            "dynamicCi": {
                "planningEnabled": True,
                "policyVersion": "p1",
                "riskClasses": ["source"],
                "agentAdvice": {
                    "enabled": False,
                    "modelIdAllowlist": [],
                    "promptHashAllowlist": [],
                    "minConfidence": 0.7,
                    "promptInjectionEvalRequired": False,
                },
                "dependencyGraph": {
                    "source": "configured",
                    "globalRiskPaths": [".github/**"] if local_requester else ["**"],
                },
                "fallbackTimeoutSeconds": 60,
                "obligations": [
                    {
                        "obligationId": "repository-quality",
                        "responsibility": {"paths": ["src/**"], "riskClasses": ["source"]},
                        "requiredWitnessIds": ["python-quality"],
                        "defaultDepth": "standard",
                        "fullDepth": "full",
                        "omitAllowed": True,
                    }
                ],
                "witnesses": [
                    {
                        "witnessId": "python-quality",
                        "executionProfileId": "python-linux",
                        "supportedDepths": [
                            "smoke",
                            "targeted",
                            "standard",
                            "full",
                            "exhaustive",
                        ],
                    }
                ],
                "executionProfiles": [
                    {
                        "profileId": "python-linux",
                        "runnerProfileId": "ubuntu-24-04",
                        "permissionProfileId": "contents-read",
                        "credentialProfileId": "no-credentials",
                        "fixtureProfileId": "no-fixtures",
                        "serviceProfileIds": [],
                        "capacityClassId": "hosted-standard",
                        "shardingPolicy": {
                            "maxShards": 8,
                            "maxParallel": 8,
                            "maxItemsPerShard": 1000,
                            "setupSecondsPerShard": 15,
                            "cpuWeight": 1,
                            "wallWeight": 1,
                            "operatorWeight": 1,
                        },
                    }
                ],
            },
        },
    }
    result = admit_policy_document(canonical_json(document), "json")
    if not isinstance(result, ValidatedEpochDraft):
        raise AssertionError("producer fixture policy was not admitted")
    return result


def _registry(
    profile: ExecutionProfile, *, local_requester: bool = False
) -> TargetExecutionRegistry:
    extra_paths = (LOCAL_PLAN_REQUEST_WORKFLOW_PATH,) if local_requester else ()
    paths = tuple(
        sorted((*TARGET_CONTROL_FILE_PATHS, WORKFLOW_PATH, *extra_paths), key=utf16_sort_key)
    )
    return TargetExecutionRegistry(
        "producer-fixture",
        "b" * 40,
        tuple(TargetAdapterFileBinding(path, "0" * 64) for path in paths),
        (
            TargetWorkflowBinding(
                WORKFLOW_PATH,
                "native-job-set",
                (TargetJobBinding("test", ("plan",)),),
                "plan-request",
                LOCAL_PLAN_REQUEST_WORKFLOW_REF
                if local_requester
                else "example-org/ci-coordinator/"
                ".github/workflows/trusted-plan-request.yml@" + "1" * 40,
                "plan",
                None,
                "gate",
                "Pull Request Gate",
            ),
        ),
        (
            TargetProfileBinding.from_profile(
                profile,
                workflow_path=WORKFLOW_PATH,
                job_id="test",
                execution_kind="native-job-set",
            ),
        ),
    )


def _discovery_report(
    content: bytes, *, revision: str, extra: tuple[tuple[str, bytes], ...] = ()
) -> DiscoveryReport:
    sources = tuple(
        WorkflowSource(path, git_blob_oid(raw), len(raw), raw)
        for path, raw in ((WORKFLOW_PATH, content), *extra)
    )
    workflows = []
    for source in sources:
        parsed = parse_workflow(source, scope=SCOPE, revision=revision, default_branch="master")
        if not isinstance(parsed, ParsedWorkflow):
            raise AssertionError("producer fixture workflow did not parse")
        workflows.append(parsed)
    graph = analyze_call_graph(tuple(workflows))
    return DiscoveryReport.create(
        repository=RepositoryIdentity(SCOPE, "example-org", "consumer", "master"),
        revision=revision,
        sources=tuple(source.identity for source in sources),
        workflows=tuple(parsed.summary for parsed in workflows),
        call_edges=graph.edges,
        facts=tuple(
            sorted(
                (*[fact for parsed in workflows for fact in parsed.facts], *graph.facts),
                key=lambda item: utf16_sort_key(item.fact_id),
            )
        ),
        unknowns=tuple(
            sorted(
                (
                    *[unknown for parsed in workflows for unknown in parsed.unknowns],
                    *graph.unknowns,
                ),
                key=lambda item: utf16_sort_key(item.unknown_id),
            )
        ),
        complete=True,
        local_graph_closed=graph.local_graph_closed,
    )


def _consumer_lab(
    policy: ValidatedEpochDraft,
) -> tuple[ConsumerLabProfile, ScenarioCorpus]:
    repository = ConsumerRepository(
        SCOPE.installation_id,
        SCOPE.repository_id,
        "example-org",
        "consumer",
        "master",
    )
    scenarios = ScenarioCorpus(
        "native-consumer",
        (
            ConsumerLabScenario(
                "fallback-change",
                "push",
                (ScenarioChange("docs/readme.md", "modified", None),),
                ExpectedOutcome("fallback", ()),
            ),
            ConsumerLabScenario(
                "source-change",
                "push",
                (ScenarioChange("src/service.py", "modified", None),),
                ExpectedOutcome("selected", ("test",)),
            ),
        ),
    )
    scenario_bytes = canonical_json(scenarios.to_mapping()) + b"\n"
    profile = ConsumerLabProfile(
        "native-consumer",
        "b" * 40,
        repository,
        WORKFLOW_PATH,
        ("push",),
        ".ci-coordinator",
        FileBinding("dynamic-ci-policy.v1.json", sha256(policy.source_bytes).hexdigest()),
        FileBinding("local-lab-scenarios.v1.json", sha256(scenario_bytes).hexdigest()),
        (),
    )
    return profile, scenarios


def _governance() -> GovernanceState:
    value = {
        "parameters": {
            "required_status_checks": [{"context": "Pull Request Gate", "integration_id": 15368}]
        },
        "ruleset_id": 41,
        "ruleset_source": "example-org/consumer",
        "ruleset_source_type": "Repository",
        "type": "required_status_checks",
    }
    rule = EffectiveGovernanceRule(
        "required_status_checks",
        "Repository",
        "example-org/consumer",
        41,
        canonical_json(value),
    )
    repository = GovernanceRepository(
        SCOPE,
        101,
        "example-org",
        "consumer",
        "example-org/consumer",
        "master",
    )
    return GovernanceState(repository, "2026-03-10", (rule,))

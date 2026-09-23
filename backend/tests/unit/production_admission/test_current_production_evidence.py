from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, timezone

import pytest
from production_cutover_support import production_cutover_fixture
from target_authority_producers.factories import observation_sources
from workflow_authority.factories import evidence as workflow_evidence

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.kernel import canonical_json, hash_object
from ci_coordinator.production_admission.current_evidence import (
    CurrentActivationEvidence,
    CurrentProductionEvidence,
    admit_current_activation_evidence,
    admit_current_production_evidence,
)
from ci_coordinator.production_admission.cutover_state import ProductionScopeState
from ci_coordinator.production_admission.evidence_lookup import (
    ProductionEvidenceLookup,
    decode_production_lookup,
)
from ci_coordinator.production_admission.model import (
    ProductionCandidateSubject,
    ProductionScopeGrant,
)
from ci_coordinator.target_authority_producers.sources import ProviderAuthoritySources
from ci_coordinator.workflow_authority.evidence import WorkflowAuthorityEvidence

NOW = datetime(2026, 9, 6, tzinfo=UTC)
SECOND_SHA = "b" * 40


@dataclass(frozen=True)
class ObservationInput:
    candidate: ProductionCandidateSubject
    grant: ProductionScopeGrant
    sources: tuple[WorkflowAuthorityEvidence, ...]
    provider: ProviderAuthoritySources
    revision: int = 1
    started_at: datetime = NOW

    def admit(self) -> CurrentProductionEvidence:
        return admit_current_production_evidence(
            candidate=self.candidate,
            scope_grant=self.grant,
            workflow_sources=self.sources,
            provider_sources=self.provider,
            scope_revision=self.revision,
            database_started_at=self.started_at,
        )


@pytest.fixture(scope="module")
def observation_seed() -> ObservationInput:
    fixture = production_cutover_fixture(now=NOW)
    return ObservationInput(
        fixture.candidate,
        fixture.staged.scope_grant,
        fixture.sources.workflows,
        fixture.sources.provider,
    )


@pytest.fixture
def observed(observation_seed: ObservationInput) -> ObservationInput:
    return deepcopy(observation_seed)


@pytest.mark.parametrize("dimension", ("candidate", "workflow", "provider"))
def test_observation_fixture_copies_do_not_share_mutable_state(
    observation_seed: ObservationInput, observed: ObservationInput, dimension: str
) -> None:
    expected = observation_seed.admit()
    assert observed == observation_seed
    if dimension == "candidate":
        object.__setattr__(observed.candidate, "workflow_sha", SECOND_SHA)
        assert observation_seed.candidate.workflow_sha != SECOND_SHA
    elif dimension == "workflow":
        object.__setattr__(observed.sources[0].source_binding, "source_commit_id", SECOND_SHA)
        assert observation_seed.sources[0].source_binding.source_commit_id != SECOND_SHA
    else:
        object.__setattr__(observed.provider.workflows, "name", "mutated-copy")
        assert observation_seed.provider.workflows.name != "mutated-copy"
    assert observation_seed.admit() == expected
    assert deepcopy(observation_seed).admit() == expected


def test_current_observation_binds_real_replayed_and_signed_relation(
    observed: ObservationInput,
) -> None:
    result = observed.admit()
    assert result.candidate == observed.candidate
    assert result.scope_grant == observed.grant
    assert result.scope_revision == 1
    assert result.database_started_at == NOW
    assert result.source_binding_digests == (observed.sources[0].source_binding.binding_digest,)
    assert result.provider_evidence_digest == observed.provider.evidence_digest
    assert result.is_current_at(NOW)


def _lookup(observed: ObservationInput) -> ProductionEvidenceLookup:
    return ProductionEvidenceLookup(
        observed.sources[0].manifest.repository,
        observed.sources[0].source_binding.source_commit_id,
        (".github/workflows/ci.yml",),
    )


def _activation(
    observed: ObservationInput, lookup: ProductionEvidenceLookup
) -> CurrentActivationEvidence:
    return admit_current_activation_evidence(
        scope_grant=observed.grant,
        scope_revision=observed.revision,
        database_started_at=observed.started_at,
        lookup=lookup,
        workflow_source=observed.sources[0],
        provider_sources=observed.provider,
    )


@pytest.mark.parametrize(
    ("elapsed", "admitted"),
    [(-1, False), (0, True), (29_999_999, True), (30_000_000, False)],
)
def test_activation_observes_real_source_without_inventing_actions_identity(
    observed: ObservationInput, elapsed: int, admitted: bool
) -> None:
    lookup = _lookup(observed)
    assert decode_production_lookup(lookup.canonical_bytes) == lookup
    activation = _activation(observed, lookup)
    assert activation.scope_grant == observed.grant
    assert activation.scope_revision == observed.revision
    assert activation.database_started_at == observed.started_at
    assert activation.source_binding_digest == observed.sources[0].source_binding.binding_digest
    assert activation.provider_evidence_digest == observed.provider.evidence_digest
    assert activation.is_current_at(NOW + timedelta(microseconds=elapsed)) is admitted


@pytest.mark.parametrize("change", ["source", "repository", "scope", "provider", "revision"])
def test_activation_rejects_independent_binding_substitutions(
    observed: ObservationInput, change: str
) -> None:
    lookup = _lookup(observed)
    if change == "source":
        lookup = replace(lookup, source_commit=SECOND_SHA)
    elif change == "repository":
        lookup = replace(lookup, repository=replace(lookup.repository, name="another"))
    elif change == "scope":
        observed = replace(
            observed,
            grant=replace(
                observed.grant,
                subject=replace(observed.grant.subject, scope=RepositoryScope(11, 23)),
            ),
        )
    elif change == "provider":
        observed = replace(
            observed,
            grant=replace(
                observed.grant,
                relation=replace(observed.grant.relation, provider_authority_digest="f" * 64),
            ),
        )
    else:
        observed = replace(observed, revision=0)
    with pytest.raises(ValueError):
        _activation(observed, lookup)


@pytest.mark.parametrize("change", ["extra", "noncanonical", "empty-paths", "unsafe-path", "sha"])
def test_production_lookup_rejects_malformed_retained_projection(
    observed: ObservationInput, change: str
) -> None:
    mapping = _lookup(observed).to_mapping()
    if change == "extra":
        mapping["trusted"] = True
    elif change == "empty-paths":
        mapping["providerPaths"] = []
    elif change == "unsafe-path":
        mapping["providerPaths"] = [".github/workflows/../ci.yml"]
    elif change == "sha":
        mapping["sourceCommit"] = "master"
    content = canonical_json(mapping) + (b"\n" if change == "noncanonical" else b"")
    with pytest.raises(ValueError):
        decode_production_lookup(content)


def _active_scope(observed: ObservationInput) -> ProductionScopeState:
    return ProductionScopeState(
        scope=observed.candidate.scope,
        revision=observed.revision,
        generation=observed.grant.relation.generation,
        active_authority_id="production_admission_" + "a" * 32,
        active_subject_digest=observed.grant.admission_subject_digest,
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda state: replace(state, scope=RepositoryScope(11, 23)),
        lambda state: replace(state, revision=state.revision + 1),
        lambda state: replace(state, generation=state.generation + 1),
        lambda state: replace(state, revoked_through_generation=state.generation),
        lambda state: replace(state, active_authority_id="production_admission_" + "b" * 32),
        lambda state: replace(state, active_subject_digest="0" * 64),
        lambda state: replace(
            state,
            revoked_through_generation=state.generation,
            latch_override_id="override_" + "a" * 32,
            latch_applied_at=NOW,
        ),
    ],
    ids=["scope", "revision", "generation", "revocation", "receipt", "subject", "latch"],
)
def test_durable_scope_checks_each_current_authority_coordinate(
    observed: ObservationInput, mutate: Callable[[ProductionScopeState], ProductionScopeState]
) -> None:
    state = _active_scope(observed)
    current = observed.admit()
    authority_id = "production_admission_" + "a" * 32
    assert state.admits_current(current, authority_id=authority_id, database_now=NOW)
    assert not mutate(state).admits_current(current, authority_id=authority_id, database_now=NOW)


def test_staging_and_initial_state_never_supply_active_permission(
    observed: ObservationInput,
) -> None:
    initial = ProductionScopeState(
        observed.candidate.scope, revision=1, staged_authority_id="production_admission_" + "a" * 32
    )
    assert not initial.admits_current(
        observed.admit(), authority_id=initial.staged_authority_id or "", database_now=NOW
    )


@pytest.mark.parametrize(
    "offset,admitted", [(-1, False), (0, True), (29_999_999, True), (30_000_000, False)]
)
def test_durable_scope_uses_the_original_observation_interval(
    observed: ObservationInput, offset: int, admitted: bool
) -> None:
    state = _active_scope(observed)
    assert (
        state.admits_current(
            observed.admit(),
            authority_id="production_admission_" + "a" * 32,
            database_now=NOW + timedelta(microseconds=offset),
        )
        is admitted
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: replace(value, scope=RepositoryScope(11, 23)),
        lambda value: replace(value, config_epoch_id="0" * 64),
        lambda value: replace(value, compiled_policy_hash="0" * 64),
        lambda value: replace(value, policy_hash="0" * 64),
        lambda value: replace(value, catalog_hash="0" * 64),
        lambda value: replace(value, workflow_sha=None),
        lambda value: replace(value, job_workflow_sha=None),
        lambda value: replace(value, workflow_sha=SECOND_SHA),
        lambda value: replace(value, job_workflow_sha=SECOND_SHA),
        lambda value: replace(
            value, workflow_ref="other/repo/.github/workflows/ci.yml@refs/heads/main"
        ),
        lambda value: replace(
            value, job_workflow_ref="other/repo/.github/workflows/ci.yml@refs/heads/main"
        ),
    ],
    ids=[
        "scope",
        "config",
        "compiled",
        "policy",
        "catalog",
        "caller-sha-absent",
        "job-sha-absent",
        "caller-sha",
        "job-sha",
        "caller-ref",
        "job-ref",
    ],
)
def test_current_observation_rejects_each_candidate_substitution(
    observed: ObservationInput,
    mutate: Callable[[ProductionCandidateSubject], ProductionCandidateSubject],
) -> None:
    assert observed.admit().is_current_at(NOW)
    with pytest.raises(ValueError):
        replace(observed, candidate=mutate(observed.candidate)).admit()


@pytest.mark.parametrize("mode", ["missing", "duplicate", "extra", "changed", "foreign", "api"])
def test_current_observation_requires_exact_complete_sources(
    observed: ObservationInput, mode: str
) -> None:
    source = observed.sources[0]
    assert observed.admit().is_current_at(NOW)
    sources: tuple[WorkflowAuthorityEvidence, ...]
    match mode:
        case "missing":
            sources = ()
        case "duplicate":
            sources = (source, source)
        case "extra":
            sources = (source, observation_sources(revision=SECOND_SHA).workflow_evidence)
        case "changed":
            sources = (
                workflow_evidence(workflow_content=source.blobs[0].content + b"\n# changed\n"),
            )
        case "foreign":
            sources = (workflow_evidence(name="foreign"),)
        case "api":
            request = replace(source.source_binding.provider_request, api_version="2022-11-28")
            sources = (
                WorkflowAuthorityEvidence.create(
                    repository=source.manifest.repository,
                    provider_request=request,
                    source_commit_id=source.source_binding.source_commit_id,
                    root_tree_id=source.source_binding.root_tree_id,
                    commit_response=source.commit_response,
                    trees=source.trees,
                    blobs=source.blobs,
                ),
            )
        case _:
            raise AssertionError(mode)
    with pytest.raises(ValueError):
        replace(observed, sources=sources).admit()


@pytest.mark.parametrize(
    "mode", ["disabled", "inventory", "governance", "revision", "digest-domain"]
)
def test_current_observation_rejects_independent_provider_changes(
    observed: ObservationInput, mode: str
) -> None:
    provider = observed.provider
    inventory = provider.workflows.inventory
    grant = observed.grant
    assert observed.admit().is_current_at(NOW)
    match mode:
        case "disabled":
            inventory = replace(
                inventory,
                default_branch_workflows=tuple(
                    replace(item, active=False) for item in inventory.default_branch_workflows
                ),
            )
        case "inventory":
            inventory = replace(inventory, default_branch_workflows=())
        case "governance":
            assert provider.governance.rules
            provider = replace(provider, governance=replace(provider.governance, rules=()))
        case "revision":
            inventory = observation_sources(
                revision=SECOND_SHA
            ).provider_authority.workflows.inventory
        case "digest-domain":
            grant = replace(
                grant,
                relation=replace(
                    grant.relation,
                    provider_authority_digest=hash_object(provider.governance.identity_mapping()),
                ),
            )
        case _:
            raise AssertionError(mode)
    provider = replace(provider, workflows=replace(provider.workflows, inventory=inventory))
    with pytest.raises(ValueError):
        replace(observed, provider=provider, grant=grant).admit()


def test_new_application_commit_preserves_stable_authority(observed: ObservationInput) -> None:
    successor = observation_sources(revision=SECOND_SHA)
    current = replace(
        observed,
        candidate=replace(observed.candidate, workflow_sha=SECOND_SHA, job_workflow_sha=SECOND_SHA),
        sources=(successor.workflow_evidence,),
        provider=successor.provider_authority,
    )
    prior = observed.admit()
    result = current.admit()
    assert result.scope_grant.relation == prior.scope_grant.relation
    assert result.source_binding_digests != prior.source_binding_digests
    assert result.provider_evidence_digest != prior.provider_evidence_digest


def test_distinct_caller_and_job_sources_require_both_complete_manifests(
    observed: ObservationInput,
) -> None:
    second = observation_sources(revision=SECOND_SHA).workflow_evidence
    current = replace(
        observed,
        candidate=replace(observed.candidate, job_workflow_sha=SECOND_SHA),
        sources=(*observed.sources, second),
    )
    assert len(current.admit().source_binding_digests) == 2
    for source in current.sources:
        with pytest.raises(ValueError, match="every authenticated workflow SHA"):
            replace(current, sources=(source,)).admit()


@pytest.mark.parametrize(
    "offset,admitted",
    [(-1, False), (0, True), (29_999_999, True), (30_000_000, False), (30_000_001, False)],
)
def test_observation_age_is_closed_at_the_original_database_boundary(
    observed: ObservationInput, offset: int, admitted: bool
) -> None:
    evidence = observed.admit()
    now = NOW + timedelta(microseconds=offset)
    assert evidence.is_current_at(now) is admitted
    assert evidence.database_started_at == NOW
    assert evidence.is_current_at(now) is admitted


@pytest.mark.parametrize("revision", [0, -1, True, 9_007_199_254_740_992])
def test_observation_requires_a_bounded_exact_scope_revision(
    observed: ObservationInput, revision: int
) -> None:
    with pytest.raises(ValueError, match="scope revision"):
        replace(observed, revision=revision).admit()


@pytest.mark.parametrize(
    "invalid", [NOW.replace(tzinfo=None), NOW.astimezone(timezone(timedelta(hours=1)))]
)
def test_observation_never_converts_an_unadmitted_clock_domain(
    observed: ObservationInput, invalid: datetime
) -> None:
    with pytest.raises(ValueError, match="UTC"):
        replace(observed, started_at=invalid).admit()
    with pytest.raises(ValueError, match="UTC"):
        observed.admit().is_current_at(invalid)


def test_current_observation_cannot_be_rebound_by_dataclass_replace(
    observed: ObservationInput,
) -> None:
    with pytest.raises(TypeError, match="InitVar"):
        replace(observed.admit(), scope_revision=2)  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="owner admission"):
        replace(observed.admit(), scope_revision=2, token=object())

"""Real nonempty relation replay and signed cutover evidence shared by native witnesses."""

from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from production_admission_support import (
    ARTIFACT_DIGEST,
    PRODUCTION_KEY_ID,
    RELEASE_IDENTITY,
    ROLLOUT_PROFILE_ID,
    SOURCE_COMMIT,
    ProductionAdmissionFixture,
    make_production_admission_fixture,
)
from target_authority_evidence.factories import EvidenceFixture, evidence_fixture
from target_authority_producers.factories import observation_sources

from ci_coordinator.config_control import ValidatedEpochDraft
from ci_coordinator.config_control.planning_projection import project_dynamic_ci_planning
from ci_coordinator.execution_orchestration import LOCAL_PLAN_REQUEST_WORKFLOW_PATH
from ci_coordinator.kernel import FixedClock, canonical_json
from ci_coordinator.production_admission import (
    ProductionAdmissionGrant,
    admit_production_admission,
)
from ci_coordinator.production_admission.current_evidence import (
    CurrentActivationEvidence,
    admit_current_activation_evidence,
)
from ci_coordinator.production_admission.cutover_drain import (
    PRODUCTION_DRAIN_ENVELOPE_SCHEMA,
    ProductionDrainStatement,
)
from ci_coordinator.production_admission.cutover_state import ProductionScopeState
from ci_coordinator.production_admission.model import (
    ProductionCandidateSubject,
    ProductionScopeSubject,
)
from ci_coordinator.production_admission.ports import (
    CurrentProductionSources,
    ProductionReceiptVerifier,
    RetainedProductionAuthority,
)
from ci_coordinator.production_admission.relation_admission import (
    StagedProductionEvidence,
    admit_staged_production_evidence,
    bind_production_relation,
)
from ci_coordinator.target_authority_evidence.codec import encode_target_authority_evidence

CUTOVER_NOW = datetime(2026, 9, 6, tzinfo=UTC)


@dataclass(frozen=True)
class ProductionCutoverFixture:
    now: datetime
    producer: EvidenceFixture
    signed: ProductionAdmissionFixture
    signing_key: Ed25519PrivateKey
    candidate: ProductionCandidateSubject
    staged: StagedProductionEvidence
    sources: CurrentProductionSources

    def isolated_copy(self) -> ProductionCutoverFixture:
        grant = self.verifier()(self.signed.content)
        assert isinstance(grant, ProductionAdmissionGrant)
        return deepcopy(
            self,
            {id(self.signing_key): self.signing_key, id(self.signed.grant): grant},
        )

    @property
    def draft(self) -> ValidatedEpochDraft:
        return self.producer.producer.target_artifacts.policy

    def verifier(self, at: datetime | None = None) -> ProductionReceiptVerifier:
        return partial(
            admit_production_admission,
            public_key_pem=self.signed.public_key_pem,
            expected_key_id=PRODUCTION_KEY_ID,
            expected_artifact_digest=ARTIFACT_DIGEST,
            expected_release_identity=RELEASE_IDENTITY,
            expected_source_commit=SOURCE_COMMIT,
            expected_environment_id="production",
            expected_rollout_profile_id=ROLLOUT_PROFILE_ID,
            expected_repository_scopes=(self.draft.scope,),
            minimum_remaining_seconds=0,
            clock=FixedClock(self.now if at is None else at),
        )

    def retained(
        self, state: ProductionScopeState, *, at: datetime | None = None
    ) -> RetainedProductionAuthority:
        return RetainedProductionAuthority(
            state,
            self.signed.content,
            self.staged.lookup,
            self.now if at is None else at,
        )

    def activation(
        self, state: ProductionScopeState, *, at: datetime | None = None
    ) -> CurrentActivationEvidence:
        return admit_current_activation_evidence(
            scope_grant=self.staged.scope_grant,
            scope_revision=state.revision,
            database_started_at=self.now if at is None else at,
            lookup=self.staged.lookup,
            workflow_source=self.sources.workflows[0],
            provider_sources=self.sources.provider,
        )

    def drain_bytes(
        self,
        state: ProductionScopeState,
        *,
        at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> bytes:
        instant = self.now if at is None else at
        assert state.latch_override_id is not None
        statement = ProductionDrainStatement(
            scope=state.scope,
            authority_id=self.signed.grant.authority_id,
            admission_subject_digest=self.staged.scope_grant.admission_subject_digest,
            generation=self.staged.relation.generation,
            predecessor_generation=self.staged.relation.predecessor_generation,
            expected_scope_revision=state.revision,
            latch_override_id=state.latch_override_id,
            observed_at=instant,
            expires_at=instant + timedelta(minutes=4) if expires_at is None else expires_at,
            old_replicas_unroutable=True,
            old_replica_requests_completed=True,
            predecessor_executions_ended=True,
        )
        unsigned = {
            "schemaVersion": PRODUCTION_DRAIN_ENVELOPE_SCHEMA,
            "algorithm": "EdDSA",
            "keyId": PRODUCTION_KEY_ID,
            "statement": statement.to_mapping(),
        }
        signature = base64.urlsafe_b64encode(
            self.signing_key.sign(canonical_json(unsigned))
        ).rstrip(b"=")
        return canonical_json({**unsigned, "signature": signature.decode("ascii")}) + b"\n"


def production_cutover_fixture(
    *,
    now: datetime = CUTOVER_NOW,
    generation: int = 1,
    signing_key: Ed25519PrivateKey | None = None,
    local_requester: bool = False,
) -> ProductionCutoverFixture:
    retained = evidence_fixture(local_requester=local_requester)
    draft = retained.producer.target_artifacts.policy
    policy = project_dynamic_ci_planning(draft)
    assert policy is not None
    source = retained.producer.workflow_evidence
    path = f"{source.manifest.repository.full_name}/.github/workflows/ci.yml"
    requester_path = (
        f"{source.manifest.repository.full_name}/{LOCAL_PLAN_REQUEST_WORKFLOW_PATH}"
        if local_requester
        else path
    )
    subject = ProductionScopeSubject(
        scope=draft.scope,
        config_epoch_id=policy.epoch_id,
        compiled_policy_hash=policy.compiled_policy_hash,
        policy_hash=policy.policy_hash,
        catalog_hash=policy.validation_catalog.catalog_hash,
        target_registry_hash=retained.producer.target_artifacts.registry.registry_hash,
        workflow_refs=(),
        job_workflow_refs=(),
        workflow_paths=(path,),
        job_workflow_paths=(requester_path,),
    )
    relation = bind_production_relation(
        retained.admitted.bundle,
        subject=subject,
        active_epoch=draft,
        generation=generation,
        predecessor_generation=generation - 1,
    )
    key = Ed25519PrivateKey.generate() if signing_key is None else signing_key
    signed = make_production_admission_fixture(subject, now=now, relation=relation, signing_key=key)
    candidate = ProductionCandidateSubject(
        scope=subject.scope,
        config_epoch_id=subject.config_epoch_id,
        compiled_policy_hash=subject.compiled_policy_hash,
        policy_hash=subject.policy_hash,
        catalog_hash=subject.catalog_hash,
        execution_plan_id="current-production-test",
        workflow_ref=path + "@refs/heads/master",
        job_workflow_ref=requester_path + "@refs/heads/master",
        workflow_sha=source.source_binding.source_commit_id,
        job_workflow_sha=source.source_binding.source_commit_id,
    )
    staged = admit_staged_production_evidence(
        encode_target_authority_evidence(retained.admitted),
        scope_grant=signed.receipt.scope_grants[0],
        active_epoch=draft,
        provider_paths=tuple(entry.path for entry in source.manifest.entries),
    )
    return ProductionCutoverFixture(
        now,
        retained,
        signed,
        key,
        candidate,
        staged,
        CurrentProductionSources(
            (source,), observation_sources(local_requester=local_requester).provider_authority
        ),
    )

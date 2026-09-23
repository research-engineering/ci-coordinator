"""Cross-owner construction and replay for unactivated evidence."""

from __future__ import annotations

from collections.abc import Callable

from ci_coordinator.target_authority_producers.codec import (
    decode_observation_candidates,
    decode_owner_projection_policy,
    decode_registration_candidates,
    encode_observation_candidates,
    encode_owner_projection_policy,
    encode_registration_candidates,
)
from ci_coordinator.target_authority_producers.model import (
    OBSERVATION_CANDIDATE_SET_SCHEMA,
    OWNER_PROJECTION_POLICY_SCHEMA,
    REGISTRATION_CANDIDATE_SET_SCHEMA,
)
from ci_coordinator.target_authority_producers.production import (
    produce_observation,
    project_registration,
)
from ci_coordinator.target_authority_relation import (
    RelationRejected,
    apply_transition,
    close_target_authority_relation,
    decode_baseline,
    decode_expected_relation,
    decode_inventory,
    decode_projection_ledger,
    decode_raw_candidate_domain,
    decode_transition_delta,
    decode_unactivated_closure,
    encode_baseline,
    encode_expected_relation,
    encode_inventory,
    encode_projection_ledger,
    encode_raw_candidate_domain,
    encode_transition_delta,
    encode_unactivated_closure,
)
from ci_coordinator.target_authority_relation.inventory import (
    PROJECTION_LEDGER_SCHEMA,
    RAW_CANDIDATE_DOMAIN_SCHEMA,
    TARGET_AUTHORITY_INVENTORY_SCHEMA,
)
from ci_coordinator.target_authority_relation.outcomes import RELATION_CLOSURE_SCHEMA
from ci_coordinator.target_authority_relation.transition import (
    AUTHORITY_TRANSITION_DELTA_SCHEMA,
    EXPECTED_TARGET_RELATION_SCHEMA,
    PHASE_ZERO_BASELINE_SCHEMA,
)
from ci_coordinator.workflow_authority import (
    WORKFLOW_AUTHORITY_MANIFEST_SCHEMA,
    WORKFLOW_SOURCE_BINDING_SCHEMA,
    decode_manifest,
    decode_source_binding,
    encode_manifest,
    encode_source_binding,
)

from .model import (
    AdmittedTargetAuthorityEvidence,
    EvidenceArtifact,
    EvidenceRole,
    TargetAuthorityEvidenceError,
    TargetAuthorityEvidenceValues,
    UnactivatedEvidenceBundle,
)


def build_target_authority_evidence(
    values: TargetAuthorityEvidenceValues,
) -> AdmittedTargetAuthorityEvidence:
    if type(values) is not TargetAuthorityEvidenceValues:
        raise TypeError("evidence construction requires exact owner values")
    bundle = UnactivatedEvidenceBundle(
        subject=values.expected_relation.subject,
        epoch=values.expected_relation.epoch,
        artifacts=(
            _artifact(
                "phase_zero_baseline",
                PHASE_ZERO_BASELINE_SCHEMA,
                encode_baseline(values.phase_zero_baseline),
            ),
            _artifact(
                "transition_delta",
                AUTHORITY_TRANSITION_DELTA_SCHEMA,
                encode_transition_delta(values.transition_delta),
            ),
            _artifact(
                "expected_relation",
                EXPECTED_TARGET_RELATION_SCHEMA,
                encode_expected_relation(values.expected_relation),
            ),
            _artifact(
                "workflow_manifest",
                WORKFLOW_AUTHORITY_MANIFEST_SCHEMA,
                encode_manifest(values.workflow_manifest),
            ),
            _artifact(
                "source_binding",
                WORKFLOW_SOURCE_BINDING_SCHEMA,
                encode_source_binding(values.source_binding),
            ),
            _artifact(
                "registration_candidates",
                REGISTRATION_CANDIDATE_SET_SCHEMA,
                encode_registration_candidates(values.registration_candidates),
            ),
            _artifact(
                "observation_candidates",
                OBSERVATION_CANDIDATE_SET_SCHEMA,
                encode_observation_candidates(values.observation_candidates),
            ),
            _artifact(
                "owner_projection_policy",
                OWNER_PROJECTION_POLICY_SCHEMA,
                encode_owner_projection_policy(values.owner_projection_policy),
            ),
            _artifact(
                "registration_inventory",
                TARGET_AUTHORITY_INVENTORY_SCHEMA,
                encode_inventory(values.registration_inventory),
            ),
            _artifact(
                "observation_inventory",
                TARGET_AUTHORITY_INVENTORY_SCHEMA,
                encode_inventory(values.observation_inventory),
            ),
            _artifact(
                "raw_candidate_domain",
                RAW_CANDIDATE_DOMAIN_SCHEMA,
                encode_raw_candidate_domain(values.raw_candidate_domain),
            ),
            _artifact(
                "projection_ledger",
                PROJECTION_LEDGER_SCHEMA,
                encode_projection_ledger(values.projection_ledger),
            ),
            _artifact(
                "relation_closure",
                RELATION_CLOSURE_SCHEMA,
                encode_unactivated_closure(values.relation_closure),
            ),
        ),
    )
    admitted = replay_target_authority_evidence(bundle)
    if admitted.values != values:
        raise TargetAuthorityEvidenceError(
            "owner_round_trip_mismatch",
            "evidence construction did not preserve exact owner values",
        )
    return admitted


def replay_target_authority_evidence(
    bundle: UnactivatedEvidenceBundle,
) -> AdmittedTargetAuthorityEvidence:
    if type(bundle) is not UnactivatedEvidenceBundle:
        raise TypeError("evidence replay requires an exact bundle")
    artifacts = {artifact.role: artifact for artifact in bundle.artifacts}
    try:
        values = TargetAuthorityEvidenceValues(
            phase_zero_baseline=_decode(
                artifacts["phase_zero_baseline"],
                PHASE_ZERO_BASELINE_SCHEMA,
                decode_baseline,
            ),
            transition_delta=_decode(
                artifacts["transition_delta"],
                AUTHORITY_TRANSITION_DELTA_SCHEMA,
                decode_transition_delta,
            ),
            expected_relation=_decode(
                artifacts["expected_relation"],
                EXPECTED_TARGET_RELATION_SCHEMA,
                decode_expected_relation,
            ),
            workflow_manifest=_decode(
                artifacts["workflow_manifest"],
                WORKFLOW_AUTHORITY_MANIFEST_SCHEMA,
                decode_manifest,
            ),
            source_binding=_decode(
                artifacts["source_binding"],
                WORKFLOW_SOURCE_BINDING_SCHEMA,
                decode_source_binding,
            ),
            registration_candidates=_decode(
                artifacts["registration_candidates"],
                REGISTRATION_CANDIDATE_SET_SCHEMA,
                decode_registration_candidates,
            ),
            observation_candidates=_decode(
                artifacts["observation_candidates"],
                OBSERVATION_CANDIDATE_SET_SCHEMA,
                decode_observation_candidates,
            ),
            owner_projection_policy=_decode(
                artifacts["owner_projection_policy"],
                OWNER_PROJECTION_POLICY_SCHEMA,
                decode_owner_projection_policy,
            ),
            registration_inventory=_decode(
                artifacts["registration_inventory"],
                TARGET_AUTHORITY_INVENTORY_SCHEMA,
                decode_inventory,
            ),
            observation_inventory=_decode(
                artifacts["observation_inventory"],
                TARGET_AUTHORITY_INVENTORY_SCHEMA,
                decode_inventory,
            ),
            raw_candidate_domain=_decode(
                artifacts["raw_candidate_domain"],
                RAW_CANDIDATE_DOMAIN_SCHEMA,
                decode_raw_candidate_domain,
            ),
            projection_ledger=_decode(
                artifacts["projection_ledger"],
                PROJECTION_LEDGER_SCHEMA,
                decode_projection_ledger,
            ),
            relation_closure=_decode(
                artifacts["relation_closure"],
                RELATION_CLOSURE_SCHEMA,
                decode_unactivated_closure,
            ),
        )
    except TargetAuthorityEvidenceError:
        raise
    except (TypeError, ValueError) as error:
        raise TargetAuthorityEvidenceError(
            "owner_codec_rejected",
            "an evidence artifact failed its owner codec",
        ) from error

    _require_bundle_identity(bundle, values)
    _require_source_binding(values)
    _require_transition_replay(values)
    _require_producer_replay(values)
    _require_relation_replay(values)
    return AdmittedTargetAuthorityEvidence(bundle, values)


def _require_bundle_identity(
    bundle: UnactivatedEvidenceBundle,
    values: TargetAuthorityEvidenceValues,
) -> None:
    expected = values.expected_relation
    if bundle.subject != expected.subject or bundle.epoch != expected.epoch:
        raise TargetAuthorityEvidenceError(
            "bundle_identity_mismatch",
            "evidence bundle crosses its retained subject or epoch",
        )


def _require_source_binding(values: TargetAuthorityEvidenceValues) -> None:
    manifest = values.workflow_manifest
    binding = values.source_binding
    expected = values.expected_relation
    observation = values.observation_candidates
    registration = values.registration_candidates
    policy = values.owner_projection_policy
    source_manifest_digest = expected.epoch.source_manifest.digest
    provider_digest = expected.epoch.provider_governance.digest
    if (
        binding.repository != manifest.repository
        or binding.workflows_tree_id != manifest.workflows_tree_id
        or binding.manifest_digest != manifest.manifest_digest
        or manifest.repository.scope != expected.subject.scope
        or source_manifest_digest != manifest.manifest_digest
        or observation.scope != expected.subject.scope
        or observation.source_commit_id != binding.source_commit_id
        or observation.source_binding_digest != binding.binding_digest
        or observation.workflow_manifest_digest != manifest.manifest_digest
        or registration.scope != expected.subject.scope
        or policy.subject != expected.subject
        or policy.workflow_manifest_digest != manifest.manifest_digest
        or policy.target_artifact_epoch_digest != observation.target_artifact_epoch_digest
        or policy.target_artifact_epoch_digest != registration.target_artifact_epoch_digest
        or observation.provider_authority_digest != provider_digest
        or observation.target_policy_digest != registration.target_policy_digest
        or observation.validation_catalog_digest != registration.validation_catalog_digest
        or observation.target_registry_digest != registration.target_registry_digest
    ):
        raise TargetAuthorityEvidenceError(
            "source_or_epoch_mismatch",
            "retained workflow, producer, subject, or epoch identities do not close",
        )


def _require_transition_replay(values: TargetAuthorityEvidenceValues) -> None:
    derived = apply_transition(values.phase_zero_baseline, values.transition_delta)
    if isinstance(derived, RelationRejected) or derived != values.expected_relation:
        raise TargetAuthorityEvidenceError(
            "transition_replay_mismatch",
            "retained transition does not reproduce the expected relation",
        )


def _require_producer_replay(values: TargetAuthorityEvidenceValues) -> None:
    closure = values.relation_closure
    try:
        registration = project_registration(
            values.expected_relation,
            values.registration_candidates,
            values.owner_projection_policy,
            closure.registration_producer,
        )
        observation = produce_observation(
            values.observation_candidates,
            values.owner_projection_policy,
            closure.observation_producer,
            values.expected_relation.subject,
            values.expected_relation.epoch,
        )
    except (TypeError, ValueError) as error:
        raise TargetAuthorityEvidenceError(
            "producer_replay_rejected",
            "retained candidate domains do not reproduce producer evidence",
        ) from error
    if (
        registration.inventory != values.registration_inventory
        or observation.inventory != values.observation_inventory
        or observation.raw_domain != values.raw_candidate_domain
        or observation.projection_ledger != values.projection_ledger
    ):
        raise TargetAuthorityEvidenceError(
            "producer_replay_mismatch",
            "retained producer evidence differs from its replay",
        )


def _require_relation_replay(values: TargetAuthorityEvidenceValues) -> None:
    derived = close_target_authority_relation(
        values.phase_zero_baseline,
        values.transition_delta,
        values.registration_inventory,
        values.observation_inventory,
        values.raw_candidate_domain,
        values.projection_ledger,
    )
    if isinstance(derived, RelationRejected) or derived != values.relation_closure:
        raise TargetAuthorityEvidenceError(
            "relation_replay_mismatch",
            "retained relation evidence does not reproduce its unactivated closure",
        )


def _artifact(role: EvidenceRole, owner_schema: str, content: bytes) -> EvidenceArtifact:
    return EvidenceArtifact(role, owner_schema, content)


def _decode[T](
    artifact: EvidenceArtifact,
    owner_schema: str,
    decoder: Callable[[bytes], T],
) -> T:
    if artifact.owner_schema != owner_schema:
        raise TargetAuthorityEvidenceError(
            "artifact_schema_mismatch",
            "an evidence role is bound to the wrong owner schema",
        )
    return decoder(artifact.content)

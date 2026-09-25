from __future__ import annotations

from dataclasses import replace

import pytest

from ci_coordinator.persistence.compatibility_contracts import (
    CapabilityDeclaration,
    CompatibilityContractError,
    assert_exact_replay,
    build_declaration,
    capabilities_cover,
    declaration_hash,
    required_capabilities,
    validate_declaration,
    validate_successor,
)
from ci_coordinator.persistence.compatibility_profile import load_bundled_profile
from ci_coordinator.persistence.schema_capabilities import (
    AUDIT_LEDGER,
    CONFIG_EPOCH_LIFECYCLE,
    CONTROL_PLANE_IDENTITY_STATE,
    DATABASE_COMPATIBILITY_PROTOCOL,
    PRODUCTION_GENERATION_CUTOVER,
    PROPOSAL_REVIEW_REGISTRATION,
    SHADOW_RECONCILIATION_STATE_V2,
    CapabilityDefinition,
    capability_descriptor_hash,
    proposal_review_registration_requirements,
    shadow_reconciliation_state_requirements,
)

_BASELINE_REVISION = "20260716_0001"
_SUCCESSOR_REVISION = "20260717_0002"
_SECOND_SUCCESSOR_REVISION = "20260718_0003"
_UNRELATED_REVISION = "20260715_0000"


def test_bootstrap_and_expand_are_canonical_and_hash_bound() -> None:
    profile = load_bundled_profile()
    bootstrap = build_declaration(
        profile,
        generation=1,
        revision_id=_BASELINE_REVISION,
        parent_revision_id=None,
        transition_kind="bootstrap",
        capabilities=(AUDIT_LEDGER.declaration(), DATABASE_COMPATIBILITY_PROTOCOL.declaration()),
    )
    expand_capability = CapabilityDeclaration("workflow-observation/v1", "1" * 64)
    successor = build_declaration(
        profile,
        generation=2,
        revision_id=_SUCCESSOR_REVISION,
        parent_revision_id=bootstrap.revision_id,
        transition_kind="expand",
        capabilities=(*bootstrap.capabilities, expand_capability),
    )

    validate_successor(profile, None, bootstrap)
    validate_successor(profile, bootstrap, successor)
    assert successor.capabilities == tuple(
        sorted(
            successor.capabilities,
            key=lambda capability: capability.capability_id.encode("utf-8"),
        )
    )
    assert capabilities_cover(bootstrap.capabilities, successor.capabilities)


@pytest.mark.parametrize(
    "candidate",
    (
        "generation-gap",
        "parent-mismatch",
        "mixed-transition",
        "descriptor-rewrite",
        "contract-without-removal",
    ),
)
def test_successor_laws_reject_the_smallest_invalid_transition(candidate: str) -> None:
    profile = load_bundled_profile()
    bootstrap = build_declaration(
        profile,
        generation=1,
        revision_id=_BASELINE_REVISION,
        parent_revision_id=None,
        transition_kind="bootstrap",
        capabilities=(AUDIT_LEDGER.declaration(), DATABASE_COMPATIBILITY_PROTOCOL.declaration()),
    )
    if candidate == "generation-gap":
        successor = build_declaration(
            profile,
            generation=3,
            revision_id=_SUCCESSOR_REVISION,
            parent_revision_id=bootstrap.revision_id,
            transition_kind="expand",
            capabilities=(
                *bootstrap.capabilities,
                CapabilityDeclaration("workflow-observation/v1", "1" * 64),
            ),
        )
    elif candidate == "parent-mismatch":
        successor = build_declaration(
            profile,
            generation=2,
            revision_id=_SUCCESSOR_REVISION,
            parent_revision_id=_UNRELATED_REVISION,
            transition_kind="expand",
            capabilities=(
                *bootstrap.capabilities,
                CapabilityDeclaration("workflow-observation/v1", "1" * 64),
            ),
        )
    elif candidate == "mixed-transition":
        successor = build_declaration(
            profile,
            generation=2,
            revision_id=_SUCCESSOR_REVISION,
            parent_revision_id=bootstrap.revision_id,
            transition_kind="expand",
            capabilities=(
                DATABASE_COMPATIBILITY_PROTOCOL.declaration(),
                CapabilityDeclaration("workflow-observation/v1", "1" * 64),
            ),
        )
    elif candidate == "descriptor-rewrite":
        successor = build_declaration(
            profile,
            generation=2,
            revision_id=_SUCCESSOR_REVISION,
            parent_revision_id=bootstrap.revision_id,
            transition_kind="expand",
            capabilities=(
                CapabilityDeclaration("audit-ledger/v1", "2" * 64),
                DATABASE_COMPATIBILITY_PROTOCOL.declaration(),
                CapabilityDeclaration("workflow-observation/v1", "1" * 64),
            ),
        )
    else:
        successor = build_declaration(
            profile,
            generation=2,
            revision_id=_SUCCESSOR_REVISION,
            parent_revision_id=bootstrap.revision_id,
            transition_kind="contract",
            capabilities=bootstrap.capabilities,
        )

    with pytest.raises(CompatibilityContractError):
        validate_successor(profile, bootstrap, successor)


def test_exact_replay_and_required_capabilities_are_fail_closed() -> None:
    profile = load_bundled_profile()
    required = required_capabilities(profile, (AUDIT_LEDGER.declaration(),))
    assert required == (AUDIT_LEDGER.declaration(),)
    with pytest.raises(CompatibilityContractError, match="at least one capability"):
        required_capabilities(profile, ())

    declaration = build_declaration(
        profile,
        generation=1,
        revision_id=_BASELINE_REVISION,
        parent_revision_id=None,
        transition_kind="bootstrap",
        capabilities=(AUDIT_LEDGER.declaration(),),
    )
    assert_exact_replay(declaration, declaration)
    with pytest.raises(CompatibilityContractError, match="differs"):
        assert_exact_replay(declaration, replace(declaration, declaration_hash="0" * 64))


def test_shadow_reconciliation_unit_of_work_requires_its_exact_capability_set() -> None:
    profile = load_bundled_profile()

    assert shadow_reconciliation_state_requirements(profile) == required_capabilities(
        profile,
        (
            DATABASE_COMPATIBILITY_PROTOCOL.declaration(),
            AUDIT_LEDGER.declaration(),
            SHADOW_RECONCILIATION_STATE_V2.declaration(),
            PRODUCTION_GENERATION_CUTOVER.declaration(),
        ),
    )


def test_proposal_review_unit_of_work_requires_the_atomic_dependency_closure() -> None:
    profile = load_bundled_profile()

    assert proposal_review_registration_requirements(profile) == required_capabilities(
        profile,
        (
            DATABASE_COMPATIBILITY_PROTOCOL.declaration(),
            AUDIT_LEDGER.declaration(),
            CONFIG_EPOCH_LIFECYCLE.declaration(),
            PROPOSAL_REVIEW_REGISTRATION.declaration(),
            CONTROL_PLANE_IDENTITY_STATE.declaration(),
        ),
    )


@pytest.mark.parametrize(
    "mutant",
    (
        replace(PROPOSAL_REVIEW_REGISTRATION, capability_id="proposal-review-registration/v2"),
        replace(
            PROPOSAL_REVIEW_REGISTRATION,
            schema_requirements=("ci_coordinator.workflow_proposal_reviews/v2",),
        ),
        replace(PROPOSAL_REVIEW_REGISTRATION, read_domain_id="workflow-proposal-review/v2"),
        replace(
            PROPOSAL_REVIEW_REGISTRATION,
            write_domain_id="append-only-workflow-proposal-review/v2",
        ),
        replace(
            PROPOSAL_REVIEW_REGISTRATION,
            codec_id="workflow-proposal-review-canonical-diff/v2",
        ),
        replace(PROPOSAL_REVIEW_REGISTRATION, backfill_state_id="complete/v1"),
        replace(
            PROPOSAL_REVIEW_REGISTRATION,
            bridge_constraint_ids=(
                *PROPOSAL_REVIEW_REGISTRATION.bridge_constraint_ids,
                "ci_coordinator.ck_workflow_proposal_reviews_target_epoch/v1",
            ),
        ),
        replace(
            PROPOSAL_REVIEW_REGISTRATION,
            routine_requirements=("ci_coordinator.reject_proposal_review_mutation/v2",),
        ),
        replace(
            PROPOSAL_REVIEW_REGISTRATION,
            privilege_requirements=("runtime-proposal-review-select-insert/v2",),
        ),
    ),
)
def test_proposal_review_capability_hash_binds_every_descriptor_coordinate(
    mutant: CapabilityDefinition,
) -> None:
    assert capability_descriptor_hash(mutant) != capability_descriptor_hash(
        PROPOSAL_REVIEW_REGISTRATION
    )


def test_declaration_hash_binds_every_identity_coordinate() -> None:
    profile = load_bundled_profile()
    capabilities = (AUDIT_LEDGER.declaration(), DATABASE_COMPATIBILITY_PROTOCOL.declaration())
    baseline = declaration_hash(
        generation=2,
        lineage_id=profile.lineage_id,
        revision_id=_SUCCESSOR_REVISION,
        parent_revision_id=_BASELINE_REVISION,
        transition_kind="expand",
        protocol_version=profile.protocol_version,
        capabilities=capabilities,
    )
    assert baseline != declaration_hash(
        generation=2,
        lineage_id=profile.lineage_id,
        revision_id=_SUCCESSOR_REVISION,
        parent_revision_id=_UNRELATED_REVISION,
        transition_kind="expand",
        protocol_version=profile.protocol_version,
        capabilities=capabilities,
    )
    declaration = build_declaration(
        profile,
        generation=2,
        revision_id=_SUCCESSOR_REVISION,
        parent_revision_id=_BASELINE_REVISION,
        transition_kind="expand",
        capabilities=capabilities,
    )
    with pytest.raises(CompatibilityContractError, match="hash"):
        validate_declaration(profile, replace(declaration, declaration_hash="0" * 64))
    assert baseline != declaration_hash(
        generation=2,
        lineage_id=profile.lineage_id,
        revision_id=_SECOND_SUCCESSOR_REVISION,
        parent_revision_id=_BASELINE_REVISION,
        transition_kind="expand",
        protocol_version=profile.protocol_version,
        capabilities=capabilities,
    )
    assert baseline != declaration_hash(
        generation=2,
        lineage_id=profile.lineage_id,
        revision_id=_SUCCESSOR_REVISION,
        parent_revision_id=_BASELINE_REVISION,
        transition_kind="expand",
        protocol_version=profile.protocol_version + 1,
        capabilities=capabilities,
    )

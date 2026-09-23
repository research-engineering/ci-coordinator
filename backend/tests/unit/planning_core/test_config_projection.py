from __future__ import annotations

from dataclasses import replace

import pytest

import ci_coordinator.config_control as config_control
from ci_coordinator.config_control.planning_projection import (
    __all__ as planning_projection_public_symbols,
)
from ci_coordinator.config_control.planning_projection import project_dynamic_ci_planning
from ci_coordinator.planning_core import PlanningPolicy

from ._epoch_support import (
    admitted_dynamic_epoch as _admitted_dynamic_epoch,
)


def test_admitted_projection_binds_closed_catalog_to_planning_policy() -> None:
    draft = _admitted_dynamic_epoch()

    projection = project_dynamic_ci_planning(draft)
    assert projection is not None
    policy = PlanningPolicy.from_projection(projection)

    assert policy.config_epoch_id == draft.epoch_id
    assert policy.compiled_policy_hash == draft.epoch_hash
    assert policy.policy_hash == projection.policy_hash
    assert policy.catalog is projection.validation_catalog
    assert [item.obligation_id for item in policy.catalog.obligations] == ["repository-quality"]
    assert [item.witness_id for item in policy.catalog.witnesses] == ["python-quality"]
    assert policy.agent_advice.enabled is False


def test_projection_has_a_bounded_typed_public_contract() -> None:
    assert config_control.__all__ == [
        "PolicySourceFormat",
        "PolicyPhase",
        "PolicyDiagnostic",
        "RepositoryScope",
        "ValidatedEpochDraft",
        "PolicyAdmissionResult",
        "admit_policy_document",
    ]
    assert {
        "DynamicCiPlanningProjection",
        "ExecutableWitness",
        "ExecutionProfile",
        "ShardingPolicy",
        "ValidationCatalog",
        "ValidationDepth",
        "ValidationObligation",
        "project_dynamic_ci_planning",
    } == set(planning_projection_public_symbols)


def test_projection_rejects_compiled_catalog_with_copied_epoch_identity() -> None:
    draft = _admitted_dynamic_epoch()
    altered = replace(
        draft,
        compiled_policy_bytes=draft.compiled_policy_bytes.replace(
            b"repository-quality", b"repository-changed"
        ),
    )

    with pytest.raises(ValueError, match="compiled policy bytes do not match epoch hash"):
        project_dynamic_ci_planning(altered)


def test_policy_rejects_projection_facts_changed_after_epoch_admission() -> None:
    projection = project_dynamic_ci_planning(_admitted_dynamic_epoch())
    assert projection is not None
    obligation = projection.validation_catalog.obligations[0]
    altered = replace(
        projection,
        validation_catalog=replace(
            projection.validation_catalog,
            obligations=(replace(obligation, omit_allowed=False),),
        ),
    )

    with pytest.raises(ValueError, match="planning projection facts"):
        PlanningPolicy.from_projection(altered)

from __future__ import annotations

from dataclasses import replace

import pytest

from ci_coordinator.target_authority_relation import (
    AuthorityField,
    AuthorityFieldEntry,
    EpochComponent,
    TargetAuthorityEpoch,
    TargetAuthorityKey,
    TargetAuthorityRowFamily,
    row_field_names,
)

from .factories import digest, row, target_epoch

ROW_FAMILIES: tuple[TargetAuthorityRowFamily, ...] = (
    "consumer_contract_scenario",
    "job",
    "provider_gate",
    "provider_repository",
    "target_policy",
    "target_registry_adapter",
    "target_registry_metadata",
    "target_registry_profile",
    "target_registry_workflow",
    "validation_obligation",
    "validation_profile",
    "validation_witness",
    "workflow",
)


@pytest.mark.parametrize("family", ROW_FAMILIES)
def test_every_row_family_has_one_closed_field_schema(family: TargetAuthorityRowFamily) -> None:
    admitted = row(family, f"member-{family}")

    assert admitted.key.family == family
    assert tuple(entry.name for entry in admitted.fields) == row_field_names(family)

    with pytest.raises(ValueError, match="family schema"):
        replace(admitted, fields=admitted.fields[:-1])


def test_every_row_component_changes_its_content_identity() -> None:
    baseline = row()
    field_mutants = tuple(
        replace(
            baseline,
            fields=tuple(
                replace(entry, field=AuthorityField.present({"mutated": entry.name}))
                if entry.name == selected.name
                else entry
                for entry in baseline.fields
            ),
        )
        for selected in baseline.fields
    )
    mutants = (
        replace(baseline, key=TargetAuthorityKey("workflow", ".github/workflows/other.yml")),
        replace(baseline, disposition="owner_approved_non_authority"),
        replace(baseline, semantic_owner="other-owner"),
        replace(baseline, source_locator="target://workflow/other"),
        *field_mutants,
    )

    assert all(mutant.row_digest != baseline.row_digest for mutant in mutants)


def test_unknown_and_not_applicable_are_distinct_and_never_truthy_aliases() -> None:
    unknown = AuthorityField.unknown("not observed")
    not_applicable = AuthorityField.not_applicable()

    assert unknown.to_mapping() == {"state": "unknown", "reason": "not observed"}
    assert not_applicable.to_mapping() == {"state": "not_applicable"}
    assert unknown != not_applicable

    workflow = row(unknown_field="activeState")
    assert workflow.has_unknown
    with pytest.raises(ValueError, match=r"required.*not applicable"):
        replace(
            workflow,
            fields=tuple(
                AuthorityFieldEntry(
                    entry.name,
                    AuthorityField.not_applicable() if entry.name == "activeState" else entry.field,
                )
                for entry in workflow.fields
            ),
        )


def test_adapted_epoch_requires_every_component_and_preserves_component_identity() -> None:
    epoch = target_epoch()
    changed = replace(epoch, registry=EpochComponent.present(digest("other-registry")))

    assert changed.epoch_digest != epoch.epoch_digest
    with pytest.raises(ValueError, match="requires every component"):
        TargetAuthorityEpoch(
            phase="adapted_target",
            source_manifest=epoch.source_manifest,
            provider_governance=epoch.provider_governance,
            policy=epoch.policy,
            catalog=epoch.catalog,
            registry=EpochComponent.not_applicable(),
            owner=epoch.owner,
        )

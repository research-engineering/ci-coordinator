"""Construction of catalog-bound test manifests."""

from __future__ import annotations

from collections.abc import Iterable

from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.runner_capacity.model import ManifestTest, TestManifest
from ci_coordinator.validation_contract import ValidationCatalog


def build_test_manifest(
    *,
    verified_plan_id: str,
    target_registry_hash: str,
    selected_witness_ids: Iterable[str],
    tests: Iterable[ManifestTest],
    catalog: ValidationCatalog,
) -> TestManifest:
    selected = tuple(sorted(set(selected_witness_ids), key=utf16_sort_key))
    complete_inventory = tuple(tests)
    if any(type(item) is not ManifestTest for item in complete_inventory):
        raise TypeError("manifest inventory must contain exact ManifestTest values")
    test_ids = tuple(item.test_id for item in complete_inventory)
    if len(set(test_ids)) != len(test_ids):
        raise ValueError("manifest inventory test identities must be unique")
    known_witness_ids = {item.witness_id for item in catalog.witnesses}
    if any(item.witness_id not in known_witness_ids for item in complete_inventory):
        raise ValueError("manifest inventory witness must resolve in the validation catalog")
    selected_set = set(selected)
    materialized = tuple(
        sorted(
            (item for item in complete_inventory if item.witness_id in selected_set),
            key=lambda item: utf16_sort_key(item.test_id),
        )
    )
    return TestManifest(
        verified_plan_id=verified_plan_id,
        target_registry_hash=target_registry_hash,
        selected_witness_ids=selected,
        tests=materialized,
        catalog=catalog,
    )

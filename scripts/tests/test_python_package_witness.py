from __future__ import annotations

import json

import pytest
from scripts.python_package_witness import PackageWitnessError, _inspection_payload

_VALID_INSPECTION = {
    "capacityQualificationResourceCount": 1,
    "ciEconomicsResourceCount": 1,
    "consumerContractLabResourceCount": 2,
    "githubIngestionResourceCount": 1,
    "operatorUiNestedAssetCount": 1,
    "persistenceResourceCount": 3,
    "productionAdmissionResourceCount": 1,
    "resourceCount": 8,
    "runtimeSettingsResourceCount": 4,
    "sitePackages": "/isolated/site-packages",
    "targetArtifactsResourceCount": 7,
}


def test_package_inspection_accepts_complete_exact_resource_inventory() -> None:
    assert _inspection_payload(json.dumps(_VALID_INSPECTION)) == {
        "sitePackages": "/isolated/site-packages"
    }


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("capacityQualificationResourceCount", None),
        ("capacityQualificationResourceCount", 0),
        ("ciEconomicsResourceCount", None),
        ("ciEconomicsResourceCount", 0),
        ("unexpectedField", 1),
    ),
)
def test_package_inspection_rejects_missing_or_inexact_owned_resource_count(
    field: str,
    replacement: int | None,
) -> None:
    payload = dict(_VALID_INSPECTION)
    if replacement is None:
        del payload[field]
    else:
        payload[field] = replacement

    with pytest.raises(PackageWitnessError, match="invalid result"):
        _inspection_payload(json.dumps(payload))

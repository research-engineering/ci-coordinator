from __future__ import annotations

import pytest
from scripts.proofkit_retirements import (
    normalized_owner_rows,
    retired_proof_owner_paths,
)


def test_owner_rows_normalize_historical_set_order_without_weakening_manifest() -> None:
    document = {
        "bindings": [
            {
                "requirementId": "REQ-1",
                "scenarioId": "SCN-1",
                "witnessId": "WIT-1",
                "witnessKind": "test",
                "witnessPath": "scripts/retired.py",
                "commandIds": ["test", "lint", "test"],
                "environmentClasses": ["python-3.14", "local"],
            }
        ]
    }

    assert normalized_owner_rows(document, ["scripts/retired.py"]) == [
        {
            "requirementId": "REQ-1",
            "scenarioId": "SCN-1",
            "witnessId": "WIT-1",
            "witnessKind": "test",
            "witnessPath": "scripts/retired.py",
            "commandIds": ["lint", "test"],
            "environmentClasses": ["local", "python-3.14"],
        }
    ]


def test_retirement_manifest_still_rejects_noncanonical_current_sets() -> None:
    manifest = {
        "schemaVersion": 2,
        "requirementReplacements": [],
        "transitions": [
            {
                "transitionId": "retirement",
                "baselineVariants": [
                    {
                        "bindingState": "unbound",
                        "bindingSetSha256": "0" * 64,
                        "affectedRequirementIds": [],
                    }
                ],
                "paths": ["scripts/retired.py"],
                "disposition": {
                    "kind": "obsolete",
                    "authorityRefs": ["REQ-Z", "REQ-A"],
                    "rationale": "The owner is obsolete.",
                },
            }
        ],
    }

    with pytest.raises(ValueError, match="authority refs canonical order"):
        retired_proof_owner_paths(manifest)

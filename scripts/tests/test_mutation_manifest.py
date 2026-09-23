from __future__ import annotations

# The repository's test assertion exception is scoped to backend/tests.
from pathlib import Path
from typing import cast

import pytest
from scripts.mutation.mutation_manifest import (
    Mutant,
    MutationManifest,
    assert_manifest_execution_contract,
    assert_manifest_fits_execution_envelope,
    collect_manifest_applicability_failures,
)


def _manifest(relative_path: str) -> MutationManifest:
    mutant: Mutant = {
        "command": ["witness"],
        "file": relative_path,
        "id": "probe",
        "operator": "replace",
        "original": "SAFE",
        "replacement": "CHANGED",
        "requirementIds": ["REQ-PROBE-001"],
        "witnessId": "probe",
    }
    return {
        "expectedKilled": 1,
        "expectedMutantIds": [mutant["id"]],
        "mutants": [mutant],
        "outerTimeoutMs": 62_000,
        "timeoutMs": 1_000,
    }


@pytest.mark.parametrize("relative_path", ["../target.txt", "/target.txt", "target\\.txt"])
def test_applicability_preflight_rejects_noncanonical_paths(
    tmp_path: Path,
    relative_path: str,
) -> None:
    (tmp_path / "target.txt").write_text("SAFE\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="path is not canonical"):
        collect_manifest_applicability_failures(
            _manifest(relative_path),
            source_root=tmp_path,
        )


def test_applicability_preflight_rejects_symlinked_target(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("SAFE\n", encoding="utf-8")
    (tmp_path / "linked.txt").symlink_to(target)

    with pytest.raises(RuntimeError, match="contains a symlink"):
        collect_manifest_applicability_failures(
            _manifest("linked.txt"),
            source_root=tmp_path,
        )


def test_applicability_preflight_counts_overlapping_targets(tmp_path: Path) -> None:
    (tmp_path / "target.txt").write_text("AAA", encoding="utf-8")
    manifest = _manifest("target.txt")
    manifest["mutants"][0]["original"] = "AA"

    assert collect_manifest_applicability_failures(
        manifest,
        source_root=tmp_path,
    ) == ({"file": "target.txt", "id": "probe", "occurrenceCount": 2},)


def test_manifest_budget_and_requirement_guards() -> None:
    manifest = _manifest("target.txt")
    assert_manifest_execution_contract(manifest)
    assert_manifest_fits_execution_envelope(manifest, timeout_ms=62_000)

    with pytest.raises(RuntimeError, match="execution envelope is invalid"):
        assert_manifest_fits_execution_envelope(manifest, timeout_ms=0)

    with pytest.raises(RuntimeError, match="exceeds its command envelope"):
        assert_manifest_fits_execution_envelope(manifest, timeout_ms=61_999)

    over_budget = cast(MutationManifest, {**manifest, "outerTimeoutMs": 61_999})
    with pytest.raises(RuntimeError, match="execution budget"):
        assert_manifest_execution_contract(over_budget)

    mutant = manifest["mutants"][0]
    duplicate_requirements = cast(
        MutationManifest,
        {
            **manifest,
            "mutants": [
                cast(
                    Mutant,
                    {**mutant, "requirementIds": ["REQ-PROBE-001", "REQ-PROBE-001"]},
                )
            ],
        },
    )
    with pytest.raises(RuntimeError, match="execution contract"):
        assert_manifest_execution_contract(duplicate_requirements)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("mutants", []),
        ("timeoutMs", 0),
        ("timeoutMs", -1),
        ("outerTimeoutMs", 0),
    ],
)
def test_manifest_execution_contract_rejects_vacuous_or_unbounded_values(
    field: str,
    value: object,
) -> None:
    manifest = cast(MutationManifest, {**_manifest("target.txt"), field: value})

    with pytest.raises(RuntimeError, match="execution contract"):
        assert_manifest_execution_contract(manifest)


@pytest.mark.parametrize(
    "override",
    [
        {"command": []},
        {"environment": {"INVALID=NAME": "value"}},
        {"environment": {"UNDECLARED": "value"}},
        {"environment": {"PYTHONDONTWRITEBYTECODE": "0"}},
        {"original": ""},
        {"replacement": "SAFE"},
        {"requirementIds": []},
        {"unexpected": True},
    ],
)
def test_manifest_execution_contract_rejects_inert_or_unowned_mutants(
    override: dict[str, object],
) -> None:
    manifest = _manifest("target.txt")
    manifest["mutants"][0].update(cast(Mutant, override))

    with pytest.raises(RuntimeError, match="execution contract"):
        assert_manifest_execution_contract(manifest)

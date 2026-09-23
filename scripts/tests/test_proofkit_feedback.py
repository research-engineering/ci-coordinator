from __future__ import annotations

from pathlib import Path

import pytest
from scripts.proofkit_feedback import validate_proofkit_feedback_ledger


def test_feedback_evidence_must_be_a_tracked_regular_current_file(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence.md"
    evidence.write_text("evidence\n", encoding="utf-8")

    summary = validate_proofkit_feedback_ledger(
        _feedback_ledger("evidence.md"),
        "agentic-proofkit==test",
        repo_root=tmp_path,
        tracked_regular_paths=frozenset({"evidence.md"}),
    )

    assert summary.record_count == 5


@pytest.mark.parametrize(
    ("kind", "message"),
    (
        ("untracked", "not tracked"),
        ("missing", "unavailable"),
        ("directory", "not a regular file"),
        ("symlink", "not a regular file"),
    ),
)
def test_feedback_evidence_rejects_non_current_or_non_regular_paths(
    tmp_path: Path,
    kind: str,
    message: str,
) -> None:
    evidence = tmp_path / "evidence.md"
    tracked_regular_paths = frozenset({"evidence.md"})
    if kind == "untracked":
        evidence.write_text("evidence\n", encoding="utf-8")
        tracked_regular_paths = frozenset()
    elif kind == "directory":
        evidence.mkdir()
    elif kind == "symlink":
        target = tmp_path / "target.md"
        target.write_text("target\n", encoding="utf-8")
        evidence.symlink_to(target)
    elif kind != "missing":  # pragma: no cover - closed parameter set
        raise AssertionError("unknown evidence mutation")

    with pytest.raises(ValueError, match=message):
        validate_proofkit_feedback_ledger(
            _feedback_ledger("evidence.md"),
            "agentic-proofkit==test",
            repo_root=tmp_path,
            tracked_regular_paths=tracked_regular_paths,
        )


@pytest.mark.parametrize("evidence_ref", ("../evidence.md", "/evidence.md", "dir\\evidence.md"))
def test_feedback_evidence_rejects_noncanonical_paths(
    tmp_path: Path,
    evidence_ref: str,
) -> None:
    with pytest.raises(ValueError, match="not canonical"):
        validate_proofkit_feedback_ledger(
            _feedback_ledger(evidence_ref),
            "agentic-proofkit==test",
            repo_root=tmp_path,
            tracked_regular_paths=frozenset({evidence_ref}),
        )


def _feedback_ledger(evidence_ref: str) -> dict[str, object]:
    categories = (
        "adoption_friction",
        "consumer_integration_defect",
        "missing_primitive",
        "schema_discovery_gap",
        "unclear_prompt",
    )
    return {
        "evaluatedDependency": "agentic-proofkit==test",
        "ledgerId": "ci-coordinator.agentic-proofkit-adoption-feedback",
        "records": [
            {
                "category": category,
                "closureOracle": "The exact closure oracle is executable.",
                "evidenceRefs": [evidence_ref],
                "id": f"APF-CI-{index:03d}",
                "observedOn": "2026-09-05",
                "status": "open",
                "summary": "The exact bounded observation remains open.",
            }
            for index, category in enumerate(categories, start=1)
        ],
        "schemaVersion": 1,
    }

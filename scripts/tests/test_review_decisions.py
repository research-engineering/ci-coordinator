from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from scripts.review_decisions import REGISTER_PATH, validate_decision_register


@pytest.fixture
def register_source(tmp_path: Path) -> dict[str, Any]:
    owner = tmp_path / "docs/owner.md"
    owner.parent.mkdir()
    owner.write_text("One authoritative contract.\n", encoding="utf-8")
    decision = {
        "decisionId": "RD-001",
        "kind": "conditional-tradeoff",
        "rejectedArgument": "One implementation alone proves an interface is unnecessary.",
        "scope": "One named provider boundary, not arbitrary interfaces.",
        "ownerReferences": [
            {
                "path": "docs/owner.md",
                "sha256": hashlib.sha256(owner.read_bytes()).hexdigest(),
            }
        ],
        "assumptions": ["The owner still requires substitution at this boundary."],
        "protectedObservations": ["Remote failure remains explicit."],
        "simplestAlternative": "Call the concrete provider directly.",
        "rationale": "The port enforces a required independent provider boundary.",
        "acceptedCost": "One small protocol remains to maintain.",
        "falsifiers": ["The boundary no longer isolates any required behavior."],
        "revisionTriggers": ["Provider ownership changes."],
        "residualReview": "Continue checking its actual error algebra and runtime contract.",
    }
    return {
        "schemaVersion": 1,
        "registerId": "ci-coordinator.review-decisions/v1",
        "decisions": [decision],
    }


def _write(root: Path, source: dict[str, Any]) -> None:
    path = root / REGISTER_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(source), encoding="utf-8")


def test_matching_owner_bytes_prove_only_reference_freshness(
    tmp_path: Path, register_source: dict[str, Any]
) -> None:
    _write(tmp_path, register_source)
    (result,) = validate_decision_register(tmp_path)
    assert result.decision_id == "RD-001"
    assert result.state == "reference-current"
    assert result.changed_owner_paths == ()


def test_owner_drift_requires_review_without_refreshing_the_register(
    tmp_path: Path, register_source: dict[str, Any]
) -> None:
    _write(tmp_path, register_source)
    original = (tmp_path / REGISTER_PATH).read_bytes()
    (tmp_path / "docs/owner.md").write_text("A changed owner.\n", encoding="utf-8")
    (result,) = validate_decision_register(tmp_path)
    assert result.state == "review-required"
    assert result.changed_owner_paths == ("docs/owner.md",)
    assert (tmp_path / REGISTER_PATH).read_bytes() == original


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("kind", "permanently-safe"),
        ("decisionId", "RD-001\n"),
        ("scope", 1),
        ("scope", " "),
        ("assumptions", []),
        ("protectedObservations", "all"),
        ("falsifiers", []),
        ("revisionTriggers", None),
        ("ownerReferences", []),
        ("safe", True),
        ("suppressedPaths", ["backend/**"]),
    ],
)
def test_wrong_or_suppression_shaped_fields_are_rejected(
    tmp_path: Path, register_source: dict[str, Any], field: str, value: object
) -> None:
    register_source["decisions"][0][field] = value
    _write(tmp_path, register_source)
    with pytest.raises(ValueError):
        validate_decision_register(tmp_path)


@pytest.mark.parametrize("version", [True, "1", 1.0, 2, None])
def test_schema_version_is_not_coerced(
    tmp_path: Path, register_source: dict[str, Any], version: object
) -> None:
    register_source["schemaVersion"] = version
    _write(tmp_path, register_source)
    with pytest.raises(ValueError):
        validate_decision_register(tmp_path)


@pytest.mark.parametrize("duplicate", ["id", "owner", "json-key"])
def test_duplicates_are_not_silently_discarded(
    tmp_path: Path, register_source: dict[str, Any], duplicate: str
) -> None:
    if duplicate == "id":
        register_source["decisions"] *= 2
    if duplicate == "owner":
        register_source["decisions"][0]["ownerReferences"] *= 2
    _write(tmp_path, register_source)
    if duplicate == "json-key":
        path = tmp_path / REGISTER_PATH
        path.write_text(
            path.read_text().replace(
                '"schemaVersion": 1', '"schemaVersion": 1, "schemaVersion": 1'
            ),
            encoding="utf-8",
        )
    with pytest.raises(ValueError):
        validate_decision_register(tmp_path)


@pytest.mark.parametrize(
    "path",
    [
        "../outside.md",
        "/outside/owner.md",
        "docs/./owner.md",
        "docs//owner.md",
        "docs/missing.md",
    ],
)
def test_owner_reference_must_be_present_confined_and_canonical(
    tmp_path: Path, register_source: dict[str, Any], path: str
) -> None:
    register_source["decisions"][0]["ownerReferences"][0]["path"] = path
    _write(tmp_path, register_source)
    with pytest.raises(ValueError):
        validate_decision_register(tmp_path)


def test_owner_symlink_is_not_followed(tmp_path: Path, register_source: dict[str, Any]) -> None:
    (tmp_path / "docs/alias.md").symlink_to(tmp_path / "docs/owner.md")
    register_source["decisions"][0]["ownerReferences"][0]["path"] = "docs/alias.md"
    _write(tmp_path, register_source)
    with pytest.raises(ValueError, match="symlink"):
        validate_decision_register(tmp_path)


@pytest.mark.parametrize("malformation", ["digest", "missing-field", "register-extra", "oversize"])
def test_incomplete_or_oversized_register_cannot_be_admitted(
    tmp_path: Path, register_source: dict[str, Any], malformation: str
) -> None:
    if malformation == "digest":
        register_source["decisions"][0]["ownerReferences"][0]["sha256"] = "0" * 63
    elif malformation == "missing-field":
        del register_source["decisions"][0]["rationale"]
    elif malformation == "register-extra":
        register_source["skipReviews"] = True
    _write(tmp_path, register_source)
    if malformation == "oversize":
        (tmp_path / REGISTER_PATH).write_bytes(b" " * (128 * 1024 + 1))
    with pytest.raises(ValueError):
        validate_decision_register(tmp_path)

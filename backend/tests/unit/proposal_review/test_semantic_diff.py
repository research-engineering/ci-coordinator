from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import replace

import pytest

from ci_coordinator.config_control import ValidatedEpochDraft, admit_policy_document
from ci_coordinator.kernel import canonical_json
from ci_coordinator.proposal_review import (
    MAX_CHANGED_POINTERS,
    MAX_SEMANTIC_DIFF_BYTES,
    PolicySemanticDiff,
    SemanticDiffLimitExceeded,
    compare_policy_drafts,
    decode_policy_semantic_diff,
)


def test_absent_baseline_is_one_root_replacement() -> None:
    target = _draft()

    result = compare_policy_drafts(None, target)

    assert result.changed_pointers == ("",)
    assert result.base_epoch_id is None
    assert result.target_epoch_id == target.epoch_id
    assert decode_policy_semantic_diff(result.canonical_bytes) == result


def test_source_formatting_is_not_a_semantic_change() -> None:
    document = _document()
    compact = _admit(canonical_json(document))
    formatted = _admit(json.dumps(document, indent=2).encode())

    result = compare_policy_drafts(compact, formatted)

    assert compact.epoch_id != formatted.epoch_id
    assert compact.document_hash == formatted.document_hash
    assert result.changed_pointers == ()


@pytest.mark.parametrize(
    ("base_branch", "base_signal", "target_branch", "target_signal", "expected"),
    [
        (
            "master",
            "Full CI",
            "main",
            "Full CI",
            ("/repository/defaultBranch", "/repository/rules"),
        ),
        ("master", "Full CI", "master", "Verified CI", ("/repository/rules",)),
    ],
)
def test_diff_reports_policy_semantics_without_array_false_precision(
    base_branch: str,
    base_signal: str,
    target_branch: str,
    target_signal: str,
    expected: tuple[str, ...],
) -> None:
    base = _draft(default_branch=base_branch, signal=base_signal)
    target = _draft(default_branch=target_branch, signal=target_signal)
    result = compare_policy_drafts(base, target)

    assert result.changed_pointers == expected
    assert all(
        "Full CI" not in pointer and "Verified CI" not in pointer
        for pointer in result.changed_pointers
    )


def test_pointer_and_byte_contract_fail_closed() -> None:
    target = _draft()
    pointers = tuple(f"/repository/key{index}" for index in range(MAX_CHANGED_POINTERS + 1))

    with pytest.raises(SemanticDiffLimitExceeded, match="pointer limit"):
        PolicySemanticDiff.create(
            scope=target.scope,
            base_epoch_id=None,
            target_epoch_id=target.epoch_id,
            changed_pointers=pointers,
        )

    byte_limit_pointers = tuple(
        f"/{index:04d}-" + "x" * 64 for index in range(MAX_CHANGED_POINTERS)
    )
    with pytest.raises(SemanticDiffLimitExceeded, match="byte limit"):
        PolicySemanticDiff.create(
            scope=target.scope,
            base_epoch_id=None,
            target_epoch_id=target.epoch_id,
            changed_pointers=byte_limit_pointers,
        )

    valid = compare_policy_drafts(None, target)
    malformed = valid.canonical_bytes.replace(b'"changedPointers":[""]', b'"changedPointers":["~"]')
    with pytest.raises(ValueError, match=r"canonical versioned projection|JSON Pointer"):
        decode_policy_semantic_diff(malformed)


def test_pointer_canonicalization_uses_utf16_order_and_rfc6901_escapes() -> None:
    target = _draft()
    diff = PolicySemanticDiff.create(
        scope=target.scope,
        base_epoch_id=None,
        target_epoch_id=target.epoch_id,
        changed_pointers=("/\ue000", "/a~1b/~0", "/\U0001f600"),
    )

    assert diff.changed_pointers == ("/a~1b/~0", "/\U0001f600", "/\ue000")
    assert len(diff.canonical_bytes) <= MAX_SEMANTIC_DIFF_BYTES


@pytest.mark.parametrize("pointer", ["missing-root", "/~", "/~2", "/\ud800"])
def test_invalid_json_pointer_is_rejected_before_projection(pointer: str) -> None:
    target = _draft()

    with pytest.raises(ValueError, match=r"JSON Pointer|invalid escape|Unicode scalar"):
        PolicySemanticDiff.create(
            scope=target.scope,
            base_epoch_id=None,
            target_epoch_id=target.epoch_id,
            changed_pointers=(pointer,),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: replace(value, canonical_bytes=value.canonical_bytes + b" "),
        lambda value: replace(value, diff_hash="0" * 64),
    ],
    ids=["canonical-bytes", "digest"],
)
def test_diff_identity_rejects_each_noncanonical_coordinate(
    mutation: Callable[[PolicySemanticDiff], PolicySemanticDiff],
) -> None:
    valid = compare_policy_drafts(None, _draft())

    with pytest.raises(ValueError, match=r"canonical projection|hash"):
        mutation(valid)


def _draft(
    *,
    default_branch: str = "master",
    signal: str = "Full CI",
) -> ValidatedEpochDraft:
    return _admit(canonical_json(_document(default_branch=default_branch, signal=signal)))


def _admit(source: bytes) -> ValidatedEpochDraft:
    result = admit_policy_document(source, "json")
    assert isinstance(result, ValidatedEpochDraft)
    return result


def _document(
    *,
    default_branch: str = "master",
    signal: str = "Full CI",
) -> dict[str, object]:
    return {
        "schemaVersion": "ci-repository-policy/v1",
        "repository": {
            "installationId": 7,
            "repositoryId": 11,
            "owner": "example",
            "name": "repo",
            "defaultBranch": default_branch,
            "rules": [
                {
                    "name": "default-ci",
                    "on": {"event": "push", "branches": [default_branch]},
                    "mode": "observe",
                    "expectedSignals": [
                        {
                            "kind": "workflow",
                            "name": signal,
                            "workflowFile": ".github/workflows/ci.yml",
                            "source": "native",
                            "requiredConclusion": "success",
                            "required": True,
                        }
                    ],
                    "omittedSignals": [],
                }
            ],
            "dynamicCi": None,
        },
    }

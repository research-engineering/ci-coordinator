from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from ci_coordinator.config_control import (
    PolicyDiagnostic,
    ValidatedEpochDraft,
    admit_policy_document,
)
from ci_coordinator.config_control._compiler import _Compilation, compile_policy
from ci_coordinator.config_control._rules import normalize_policy_document
from ci_coordinator.config_control.epoch_integrity import (
    EpochDraftIntegrityError,
    assert_admitted_epoch_draft,
)
from ci_coordinator.config_control.planning_projection import project_dynamic_ci_planning
from ci_coordinator.planning_core.policy import PlanningPolicy
from ci_coordinator.repo_context.freshness import matches_path_pattern
from ci_coordinator.repo_context.planning_input import PolicySnapshot

_GLOBAL_POINTER = "/repository/dynamicCi/dependencyGraph/globalRiskPaths/0"
_RESPONSIBILITY_POINTER = "/repository/dynamicCi/obligations/0/responsibility/paths/0"
type PathField = Literal["global", "responsibility"]

_SOURCE = b"""{
  "schemaVersion": "ci-repository-policy/v1",
  "repository": {
    "installationId": 1, "repositoryId": 2,
    "owner": "example", "name": "paths", "defaultBranch": "main",
    "rules": [{
      "name": "main", "on": {"event": "push", "branches": ["main"]},
      "mode": "observe", "expectedSignals": [{
        "kind": "workflow", "name": "CI", "workflowFile": "ci.yml",
        "source": "native", "requiredConclusion": "success", "required": true
      }]
    }],
    "dynamicCi": {
      "planningEnabled": true, "policyVersion": "path-v1", "riskClasses": [],
      "dependencyGraph": {"source": "configured", "globalRiskPaths": ["GLOBAL"]},
      "obligations": [{
        "obligationId": "quality", "responsibility": {"paths": ["RESPONSIBILITY"]},
        "requiredWitnessIds": ["quality"], "defaultDepth": "standard",
        "fullDepth": "full", "omitAllowed": true
      }],
      "witnesses": [{
        "witnessId": "quality", "executionProfileId": "linux",
        "supportedDepths": ["standard", "full"]
      }],
      "executionProfiles": [{
        "profileId": "linux", "runnerProfileId": "linux",
        "permissionProfileId": "read", "credentialProfileId": "none",
        "fixtureProfileId": "none", "capacityClassId": "hosted",
        "shardingPolicy": {
          "maxShards": 1, "maxParallel": 1, "maxItemsPerShard": 100,
          "setupSecondsPerShard": 1
        }
      }]
    }
  }
}"""


def _source(field: PathField, raw_pattern: bytes) -> bytes:
    global_pattern = raw_pattern if field == "global" else b'"metadata/**"'
    responsibility_pattern = raw_pattern if field == "responsibility" else b'"src/**"'
    return _SOURCE.replace(b'"GLOBAL"', global_pattern).replace(
        b'"RESPONSIBILITY"', responsibility_pattern
    )


def test_config_admission_does_not_import_repository_or_planning_contexts() -> None:
    source_root = Path(__file__).resolve().parents[3] / "src"
    program = """
import importlib.abc
import sys

sys.path.insert(0, sys.argv[1])
blocked = ("ci_coordinator.repo_context", "ci_coordinator.planning_core")

def forbidden(name):
    return any(name == prefix or name.startswith(prefix + ".") for prefix in blocked)

assert not any(forbidden(name) for name in sys.modules)

class RejectContextImports(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if forbidden(fullname):
            raise AssertionError("config admission imported a higher-level context: " + fullname)
        return None

sys.meta_path.insert(0, RejectContextImports())
from ci_coordinator.config_control import ValidatedEpochDraft, admit_policy_document

result = admit_policy_document(sys.stdin.buffer.read(), "json")
assert isinstance(result, ValidatedEpochDraft), result
assert not any(forbidden(name) for name in sys.modules)
"""
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program, str(source_root)],
        input=_source("responsibility", b'"src/**"'),
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", errors="replace")


@pytest.mark.parametrize("source_format", ["json", "yaml-1.2"])
@pytest.mark.parametrize("field", ["global", "responsibility"])
@pytest.mark.parametrize(
    "raw_pattern",
    [
        b'"src/\\u0000file"',
        b'"' + b"a" * 513 + b'"',
        b'"' + b"\\ud83d\\ude00" * 513 + b'"',
        b'"src/"',
        b'"src/**/"',
        b'"**/"',
        b'".github/workflows/"',
        b'"\\u0085"',
        b'"../private"',
        b'"/absolute"',
        b'"src/[ab].py"',
        b'"src/{a,b*}"',
    ],
)
def test_literal_policy_rejects_unsafe_path_at_its_exact_pointer(
    source_format: str,
    field: PathField,
    raw_pattern: bytes,
) -> None:
    result = admit_policy_document(_source(field, raw_pattern), source_format)

    assert result == (
        PolicyDiagnostic(
            code="semantics.invalid",
            phase="semantics",
            rule_id="path.valid-pattern",
            instance_pointer=_GLOBAL_POINTER if field == "global" else _RESPONSIBILITY_POINTER,
            parameters={},
        ),
    )


@pytest.mark.parametrize("source_format", ["json", "yaml-1.2"])
@pytest.mark.parametrize("field", ["global", "responsibility"])
@pytest.mark.parametrize(
    ("pattern", "matching_path", "nonmatching_path"),
    [
        ("src", "src", "src/a.py"),
        ("future/new-file.py", "future/new-file.py", "future/other.py"),
        ("src/**", "src/a/b.py", "other/a.py"),
        ("src/{api,app}/?.py", "src/api/a.py", "src/api/ab.py"),
        ("caf\u00e9/**", "caf\u00e9/a.py", "cafe\u0301/a.py"),
        ("cafe\u0301/**", "cafe\u0301/a.py", "caf\u00e9/a.py"),
        ("A" * 512, "A" * 512, "A" * 511),
        ("\U0001f600" * 512, "\U0001f600" * 512, "\U0001f600" * 511),
    ],
)
def test_literal_policy_paths_survive_both_consumers_without_reinterpretation(
    source_format: str,
    field: PathField,
    pattern: str,
    matching_path: str,
    nonmatching_path: str,
) -> None:
    source = _source(field, json.dumps(pattern).encode("ascii"))
    draft = admit_policy_document(source, source_format)
    assert isinstance(draft, ValidatedEpochDraft)
    assert draft.source_bytes == source
    projection = project_dynamic_ci_planning(draft)
    assert projection is not None
    policy = PlanningPolicy.from_projection(projection)
    snapshot = PolicySnapshot.from_projection(projection)

    paths = (
        snapshot.global_risk_paths
        if field == "global"
        else policy.catalog.obligations[0].responsibility_paths
    )
    assert paths == (pattern,)
    assert snapshot.epoch_id == policy.config_epoch_id == draft.epoch_id
    assert snapshot.compiled_policy_hash == policy.compiled_policy_hash == draft.epoch_hash
    assert matches_path_pattern(paths[0], matching_path)
    assert not matches_path_pattern(paths[0], nonmatching_path)


@pytest.mark.parametrize("field", ["global", "responsibility"])
def test_unicode_path_forms_keep_distinct_compiled_and_policy_identities(field: PathField) -> None:
    drafts = [
        admit_policy_document(_source(field, raw), "json")
        for raw in (
            b'"caf\\u00e9/**"',
            b'"cafe\\u0301/**"',
        )
    ]
    first, second = drafts
    assert isinstance(first, ValidatedEpochDraft) and isinstance(second, ValidatedEpochDraft)
    assert first.document_hash != second.document_hash
    assert first.epoch_hash != second.epoch_hash
    assert first.epoch_id != second.epoch_id
    first_projection, second_projection = (
        project_dynamic_ci_planning(first),
        project_dynamic_ci_planning(second),
    )
    assert first_projection is not None and second_projection is not None
    assert first_projection.policy_hash != second_projection.policy_hash


@pytest.mark.parametrize("field", ["global", "responsibility"])
@pytest.mark.parametrize("raw_pattern", [b'"src/\\u0000file"', b'"' + b"a" * 513 + b'"', b'"src/"'])
def test_historical_compiled_epoch_cannot_bypass_new_source_admission(
    field: PathField,
    raw_pattern: bytes,
) -> None:
    baseline = admit_policy_document(_source(field, b'"src/**"'), "json")
    assert isinstance(baseline, ValidatedEpochDraft)
    source = _source(field, raw_pattern)
    normalized = normalize_policy_document(json.loads(source))
    assert isinstance(normalized, dict)
    compiled = compile_policy(normalized, raw_source=source, source_format="json")
    assert isinstance(compiled, _Compilation)
    historical = replace(
        baseline,
        source_bytes=source,
        normalized_document_bytes=compiled.normalized_document_bytes,
        compiled_policy_bytes=compiled.compiled_policy_bytes,
        source_hash=compiled.source_hash,
        document_hash=compiled.document_hash,
        epoch_hash=compiled.epoch_hash,
        epoch_id=compiled.epoch_id,
    )

    with pytest.raises(EpochDraftIntegrityError, match="does not match policy admission"):
        assert_admitted_epoch_draft(historical)
    assert historical.source_bytes == source
    assert historical.compiled_policy_bytes == compiled.compiled_policy_bytes


@pytest.mark.parametrize("raw_pattern", [b'" ../private"', b'"src/\\u0000 "', b'"src/ "'])
def test_new_path_rejection_preserves_existing_whitespace_diagnostic_priority(
    raw_pattern: bytes,
) -> None:
    result = admit_policy_document(_source("global", raw_pattern), "json")
    assert result == (
        PolicyDiagnostic(
            code="semantics.non_canonical_string",
            phase="semantics",
            rule_id="string.canonical-whitespace",
            instance_pointer=_GLOBAL_POINTER,
            parameters={},
        ),
    )

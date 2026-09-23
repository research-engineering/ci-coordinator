from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
import scripts.proofkit_route_sources as route_sources
from scripts.proofkit_common import JsonObject
from scripts.proofkit_route_contract import (
    assert_legacy_relation_preserved,
    legacy_binding_projection,
)


def _requirement_source() -> JsonObject:
    return {
        "requirements": [
            {
                "requirementId": "REQ-CI-CORE-001",
                "ownerId": "ci-coordinator.core",
                "claimLevel": "blocking",
                "nonClaims": ["The fixture does not prove native execution."],
            }
        ]
    }


def _witness_plan() -> JsonObject:
    return {
        "commands": [
            {
                "id": "verify",
                "argv": ["python", "-m", "verify"],
                "environment": {"classes": ["local-python"]},
            }
        ]
    }


def _route_source() -> JsonObject:
    return {
        "schemaVersion": 2,
        "contractId": "ci-coordinator.requirement-binding-route-source.v2",
        "sourceId": "ci-coordinator.routes.core",
        "requirementIdPrefix": "REQ-CI-CORE-",
        "scenarioIdPrefix": "ci-coordinator.binding.scenario.",
        "witnessIdPrefix": "ci-coordinator.binding.witness.",
        "bindingColumns": [
            "requirementIdSuffix",
            "scenarioIdSuffix",
            "witnessIdSuffix",
            "witnessKind",
            "witnessPath",
            "commandIds",
            "environmentClasses",
        ],
        "bindings": [
            [
                "001",
                "route",
                "route",
                "contract",
                "docs/witness.md",
                ["verify"],
                ["local-python"],
            ]
        ],
        "nonClaims": ["The fixture declares routes only."],
    }


def _projection(source: JsonObject | None = None) -> JsonObject:
    selected = _route_source() if source is None else source
    return legacy_binding_projection(
        [("ci-coordinator.routes.core", "proofkit/routes/core.v2.json", selected)],
        [("docs/specs/core/requirements.v1.json", _requirement_source())],
        _witness_plan(),
    )


def test_compact_route_projects_the_legacy_relation() -> None:
    projection = _projection()

    assert projection["requirements"] == [
        {
            "requirementId": "REQ-CI-CORE-001",
            "ownerId": "ci-coordinator.core",
            "specPath": "docs/specs/core/requirements.v1.json",
            "claimLevel": "blocking",
            "proofState": "witness_backed",
            "nonClaims": ["The fixture does not prove native execution."],
        }
    ]
    assert projection["bindings"] == [
        {
            "requirementId": "REQ-CI-CORE-001",
            "scenarioId": "ci-coordinator.binding.scenario.route",
            "witnessId": "ci-coordinator.binding.witness.route",
            "witnessKind": "contract",
            "witnessPath": "docs/witness.md",
            "commandIds": ["verify"],
            "environmentClasses": ["local-python"],
        }
    ]


def _unknown_command(source: JsonObject) -> None:
    cast(list[object], cast(list[object], source["bindings"])[0])[5] = ["unknown"]


def _missing_environment(source: JsonObject) -> None:
    cast(list[object], cast(list[object], source["bindings"])[0])[6] = ["other"]


def _complete_identity(source: JsonObject) -> None:
    cast(list[object], cast(list[object], source["bindings"])[0])[1] = (
        "ci-coordinator.binding.scenario.route"
    )


def _unsorted_commands(source: JsonObject) -> None:
    cast(list[object], cast(list[object], source["bindings"])[0])[5] = ["verify", "verify"]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_unknown_command, "references unknown command"),
        (_missing_environment, "omits command environment"),
        (_complete_identity, "must be a suffix"),
        (_unsorted_commands, "must be unique and sorted"),
    ],
)
def test_compact_route_rejects_non_bijective_facts(
    mutate: Callable[[JsonObject], None], message: str
) -> None:
    source = _route_source()
    mutate(source)

    with pytest.raises(ValueError, match=message):
        _projection(source)


def test_compact_route_rejects_global_identity_collisions() -> None:
    second = _route_source()
    second.update(
        {
            "sourceId": "ci-coordinator.routes.developer-environment",
            "requirementIdPrefix": "REQ-CI-DEV-",
        }
    )
    requirements = _requirement_source()
    cast(list[JsonObject], requirements["requirements"]).append(
        {
            "requirementId": "REQ-CI-DEV-001",
            "ownerId": "ci-coordinator.developer-environment",
            "claimLevel": "blocking",
            "nonClaims": ["The fixture does not prove native execution."],
        }
    )

    with pytest.raises(ValueError, match="identities must be globally unique"):
        legacy_binding_projection(
            [
                (
                    "ci-coordinator.routes.core",
                    "proofkit/routes/core.v2.json",
                    _route_source(),
                ),
                (
                    "ci-coordinator.routes.developer-environment",
                    "proofkit/routes/developer-environment.v2.json",
                    second,
                ),
            ],
            [("docs/specs/requirements.v1.json", requirements)],
            _witness_plan(),
        )


def test_migration_relation_allows_only_routes_for_declared_added_paths() -> None:
    baseline = _projection()
    candidate = copy.deepcopy(baseline)
    added = copy.deepcopy(cast(list[JsonObject], candidate["bindings"])[0])
    added.update(
        {
            "scenarioId": "ci-coordinator.binding.scenario.added",
            "witnessId": "ci-coordinator.binding.witness.added",
            "witnessPath": "docs/added.md",
        }
    )
    cast(list[JsonObject], candidate["bindings"]).append(added)

    parity = assert_legacy_relation_preserved(
        baseline,
        candidate,
        allowed_added_paths=["docs/added.md"],
    )
    assert parity.extra_binding_count == 1
    with pytest.raises(ValueError, match="introduced undeclared routes"):
        assert_legacy_relation_preserved(baseline, candidate, allowed_added_paths=[])
    with pytest.raises(ValueError, match="removed legacy routes"):
        assert_legacy_relation_preserved(
            candidate,
            baseline,
            allowed_added_paths=[],
        )


def test_migration_reports_canonicalized_requirement_non_claims() -> None:
    baseline = _projection()
    candidate = copy.deepcopy(baseline)
    cast(list[JsonObject], baseline["requirements"])[0]["nonClaims"] = [
        "Legacy binding-local proof prose."
    ]

    parity = assert_legacy_relation_preserved(
        baseline,
        candidate,
        allowed_added_paths=[],
    )

    assert parity.canonicalized_requirement_non_claim_count == 1


def _write_route_fixture(root: Path, *, digest_override: str | None = None) -> None:
    route_path = root / "proofkit/routes/core.v2.json"
    route_path.parent.mkdir(parents=True)
    route_text = json.dumps(_route_source(), separators=(",", ":")) + "\n"
    route_path.write_text(route_text, encoding="utf-8")
    digest = hashlib.sha256(route_text.encode()).hexdigest()
    index = {
        "schemaVersion": 2,
        "bindingId": "ci-coordinator.requirement-bindings",
        "contractId": "ci-coordinator.requirement-binding-route-index.v2",
        "sourceColumns": ["sourceId", "path", "sha256"],
        "sources": [
            [
                "ci-coordinator.routes.core",
                "proofkit/routes/core.v2.json",
                digest if digest_override is None else digest_override,
            ]
        ],
        "nonClaims": ["The fixture indexes route bytes only."],
    }
    (root / "proofkit/requirement-bindings.json").write_text(
        json.dumps(index),
        encoding="utf-8",
    )
    (root / "proofkit/witness-plan-input.json").write_text(
        json.dumps(_witness_plan()),
        encoding="utf-8",
    )


@pytest.mark.parametrize("digest_override", [None, "0" * 64])
def test_route_source_hash_is_authoritative(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    digest_override: str | None,
) -> None:
    _write_route_fixture(tmp_path, digest_override=digest_override)
    monkeypatch.setattr(
        route_sources,
        "_requirement_sources",
        lambda _root, *, commit: [("docs/specs/core/requirements.v1.json", _requirement_source())],
    )

    if digest_override is not None:
        with pytest.raises(ValueError, match="source digest drift"):
            route_sources.load_route_authority(repo_root=tmp_path)
        return
    authority = route_sources.load_route_authority(repo_root=tmp_path)
    assert authority.compact is True
    assert authority.source_count == 1
    assert len(cast(list[object], authority.binding_projection["bindings"])) == 1


@pytest.mark.parametrize(
    ("limit_name", "limit_value", "message"),
    [
        ("MAX_REQUIREMENT_SOURCE_COUNT", 0, "source count"),
        ("MAX_TOTAL_REQUIREMENT_SOURCE_BYTES", 1, "aggregate byte bound"),
    ],
)
def test_requirement_source_inventory_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    limit_value: int,
    message: str,
) -> None:
    requirement_path = "docs/specs/core/requirements.v1.json"
    target = tmp_path / requirement_path
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(_requirement_source()), encoding="utf-8")
    monkeypatch.setattr(route_sources, limit_name, limit_value)
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(
        route_sources,
        "capture_git_text",
        lambda _root, _args: requirement_path + "\n",
    )

    with pytest.raises(ValueError, match=message):
        route_sources._requirement_sources(tmp_path, commit=None)

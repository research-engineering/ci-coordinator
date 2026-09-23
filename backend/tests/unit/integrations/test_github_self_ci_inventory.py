from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Literal

import pytest

from ci_coordinator.integrations.github import recursive_git_tree, self_ci_inventory
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import (
    GitHubPaginationEvidence,
    GitHubQueryParameter,
    GitHubRequest,
    GitHubResponse,
    GitHubTransportResult,
)
from ci_coordinator.integrations.github.installation_request_admission import (
    installation_request_is_admitted,
)
from ci_coordinator.integrations.github.repository_context import GitHubRepositoryContextProvider
from ci_coordinator.kernel import canonical_json
from ci_coordinator.plan_issuance import PlanRequest
from ci_coordinator.planning_core import DeterministicPlan, PlanningPolicy, plan
from ci_coordinator.repo_context import PolicySnapshot
from ci_coordinator.validation_contract import (
    ExecutableWitness,
    ExecutionProfile,
    ShardingPolicy,
    ValidationCatalog,
    ValidationObligation,
)
from ci_coordinator.verification_core import verify
from ci_coordinator.workflow_authority.git_objects import git_blob_oid, git_tree_oid
from ci_coordinator.workflow_authority.model import GitTreeChild

_HEAD = "b" * 40
_GRAPH = ".ci-coordinator/dependency-graph.v1.json"
_REPORT = ".ci-coordinator/self-ci-inventory.v1.json"
_DOC = "docs/guide.md"


def _response(value: object, *, status: int = 200) -> GitHubResponse:
    return GitHubResponse(
        status=status,
        api_version=GITHUB_API_VERSION,
        headers=(),
        body=json.dumps(value).encode(),
        pagination=GitHubPaginationEvidence.not_paginated(),
    )


@dataclass
class _Transport:
    mutation: str = "unchanged"
    additional_directories: int = 0
    requests: list[GitHubRequest] = field(default_factory=list)
    objects: dict[str, object] = field(default_factory=dict)
    tree_sha: str = field(init=False)
    graph: bytes = field(init=False)

    def __post_init__(self) -> None:
        extra_files = {
            f"directory-{index}/guide.md": b"documentation\n"
            for index in range(self.additional_directories)
        }
        paths = sorted((_GRAPH, _REPORT, _DOC, "config.json", "src/main.go", *extra_files))
        report = {
            "schemaVersion": 1,
            "profileId": "ci-coordinator.self-ci-qualification",
            "responsibilityInventory": {
                "scope": "git-observed-regular-paths-and-modes",
                "pathCount": len(paths),
                "pathInventorySha256": hashlib.sha256(
                    canonical_json([[path, 0o100644] for path in paths])
                ).hexdigest(),
            },
        }
        if self.mutation == "wrong-report-version":
            report["schemaVersion"] = 2
        graph_paths = paths if self.mutation != "stale-graph-only" else [*paths, "deleted.md"]
        self.graph = (
            canonical_json(
                {
                    "provenance": {
                        "source": "configured",
                        "schemaVersion": "dependency-graph/v1",
                        "generator": "self-ci@2"
                        if self.mutation == "unknown-profile"
                        else "self-ci@1",
                    },
                    "invalidatesWhenChanged": [".ci-coordinator/**"],
                    "globalRiskPaths": [".ci-coordinator/**"],
                    "nodes": [
                        {"path": path, "dependents": [], "riskClasses": ["source"]}
                        for path in sorted(graph_paths)
                    ],
                }
            )
            + b"\n"
        )
        files = {
            _GRAPH: self.graph,
            _REPORT: canonical_json(report) + b"\n",
            _DOC: b"changed documentation\n",
            "config.json": b"{}\n",
            "src/main.go": b"package main\n",
            **extra_files,
        }
        if self.mutation == "deletion":
            del files[_DOC]
        if self.mutation == "missing-report":
            del files[_REPORT]
        modes = dict.fromkeys(files, "100644")
        if self.mutation == "mode-only":
            modes[_DOC] = "100755"
        if self.mutation == "symlink":
            modes[_DOC] = "120000"
        if self.mutation == "submodule":
            modes[_DOC] = "160000"
        if self.mutation == "graph-content-substitution":
            files[_GRAPH] = self.graph + b" "
        self.tree_sha = self._tree("", files, modes)

    def _tree(self, prefix: str, files: dict[str, bytes], modes: dict[str, str]) -> str:
        names = sorted(
            {path[len(prefix) :].split("/", 1)[0] for path in files if path.startswith(prefix)}
        )
        entries = []
        rows: list[dict[str, object]] = []
        for name in names:
            path = prefix + name
            kind: Literal["blob", "commit", "tree"]
            size: int | None
            if path in files:
                mode = modes[path]
                kind = "commit" if mode == "160000" else "blob"
                content = files[path]
                oid = git_blob_oid(content)
                size = None if kind == "commit" else len(content)
                self.objects[oid] = {
                    "sha": oid,
                    "size": len(content),
                    "encoding": "base64",
                    "content": base64.b64encode(content).decode(),
                }
            else:
                mode, kind, size = "040000", "tree", None
                oid = self._tree(path + "/", files, modes)
            entries.append(GitTreeChild(name, mode, kind, oid, size))
            row: dict[str, object] = {"path": name, "mode": mode, "type": kind, "sha": oid}
            if size is not None:
                row["size"] = size
            rows.append(row)
        oid = git_tree_oid(entries)
        self.objects[oid] = {"sha": oid, "tree": rows, "truncated": False}
        return oid

    def _recursive_rows(self, tree_sha: str, prefix: str = "") -> list[dict[str, object]]:
        tree = self.objects[tree_sha]
        assert isinstance(tree, dict) and isinstance(tree["tree"], list)
        rows: list[dict[str, object]] = []
        for child in tree["tree"]:
            row = {**child, "path": prefix + child["path"]}
            rows.append(row)
            if child["type"] == "tree":
                rows.extend(self._recursive_rows(child["sha"], str(row["path"]) + "/"))
        return rows

    async def send(self, request: GitHubRequest) -> GitHubTransportResult:
        self.requests.append(request)
        assert installation_request_is_admitted(request)
        if request.operation == "repositories.get_by_id":
            return _response(
                {
                    "id": 202,
                    "name": "repository",
                    "full_name": "acme/repository",
                    "owner": {"login": "acme"},
                }
            )
        if request.operation == "diff.compare":
            mode_only = self.mutation == "mode-only"
            return _response(
                {
                    "files": [
                        {
                            "filename": _DOC,
                            "status": "removed" if self.mutation == "deletion" else "modified",
                            "additions": 0 if mode_only else 1,
                            "deletions": 0,
                            **({} if mode_only else {"patch": "changed documentation"}),
                        }
                    ]
                }
            )
        if request.operation == "workflow_catalog.get_content":
            return _response(
                {
                    "type": "file",
                    "encoding": "base64",
                    "path": _GRAPH,
                    "size": len(self.graph),
                    "content": base64.b64encode(self.graph).decode(),
                }
            )
        if request.operation == "workflow_discovery.get_commit":
            if self.mutation == "cancelled":
                raise asyncio.CancelledError
            if self.mutation == "unavailable":
                return _response({}, status=503)
            if self.mutation == "timeout":
                await asyncio.sleep(1)
            return _response(
                {
                    "sha": "a" * 40 if self.mutation == "wrong-head" else _HEAD,
                    "tree": {"sha": self.tree_sha},
                }
            )
        if request.operation == "workflow_discovery.get_recursive_tree":
            assert request.path.endswith(self.tree_sha)
            assert request.query == (GitHubQueryParameter("recursive", "1"),)
            rows = self._recursive_rows(self.tree_sha)
            if self.mutation == "missing-nested-tree":
                rows = [row for row in rows if row["path"] != "docs"]
            if self.mutation == "missing-nested-children":
                rows = [row for row in rows if row["path"] != _DOC]
            if self.mutation == "missing-entire-subtree":
                rows = [row for row in rows if row["path"] not in {"docs", _DOC}]
            if self.mutation == "duplicate-recursive-path":
                rows.append(rows[-1])
            if self.mutation == "forged-mode":
                rows = [{**row, "mode": "100755"} if row["path"] == _DOC else row for row in rows]
            if self.mutation == "wrong-subtree-oid":
                rows = [{**row, "sha": "a" * 40} if row["path"] == "docs" else row for row in rows]
            response = _response(
                {
                    "sha": "a" * 40 if self.mutation == "wrong-root" else self.tree_sha,
                    "truncated": self.mutation == "truncated-tree",
                    "tree": rows,
                }
            )
            if self.mutation == "paginated-tree":
                return replace(
                    response,
                    pagination=GitHubPaginationEvidence(False, 1, None, "next", "next_page"),
                )
            return response
        if request.operation == "workflow_discovery.get_blob":
            return _response(self.objects[request.path.rsplit("/", 1)[1]])
        raise AssertionError(f"unexpected provider operation {request.operation}")


@dataclass
class _Factory:
    transport: _Transport

    def for_installation(self, installation_id: int) -> _Transport:
        assert installation_id == 101
        return self.transport


def _policies() -> tuple[PolicySnapshot, PlanningPolicy]:
    catalog = ValidationCatalog(
        obligations=(
            ValidationObligation(
                "native", ("**",), ("source",), ("native",), "full", "full", False
            ),
            ValidationObligation(
                "utility-config", ("**/*.json",), (), ("utility-config",), "full", "full", True
            ),
            ValidationObligation(
                "utility-go-static", ("**/*.go",), (), ("utility-go-static",), "full", "full", True
            ),
        ),
        witnesses=tuple(
            ExecutableWitness(identity, identity, ("full",))
            for identity in ("native", "utility-config", "utility-go-static")
        ),
        execution_profiles=tuple(
            ExecutionProfile(
                identity,
                "ubuntu",
                "read",
                "none",
                "unit",
                (),
                "hosted",
                ShardingPolicy(1, 1, 1000, 0.0),
            )
            for identity in ("native", "utility-config", "utility-go-static")
        ),
    )
    return (
        PolicySnapshot(
            "0" * 64, "0" * 64, "1" * 64, "configured", (".ci-coordinator/**",), ("source",)
        ),
        PlanningPolicy("0" * 64, "0" * 64, "1" * 64, catalog, 60),
    )


def _request() -> PlanRequest:
    return PlanRequest(
        "dynamic-ci-plan-request/v2",
        "request-1",
        101,
        202,
        "acme",
        "repository",
        "push",
        "refs/heads/qualification",
        "a" * 40,
        _HEAD,
        7001,
        1,
        execution_sha=_HEAD,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "mode-only",
        "deletion",
        "stale-graph-only",
        "missing-report",
        "wrong-report-version",
        "symlink",
        "submodule",
        "truncated-tree",
        "paginated-tree",
        "missing-nested-tree",
        "missing-nested-children",
        "missing-entire-subtree",
        "duplicate-recursive-path",
        "forged-mode",
        "wrong-subtree-oid",
        "wrong-root",
        "wrong-head",
        "unavailable",
        "graph-content-substitution",
        "unknown-profile",
    ],
)
def test_provider_inventory_failure_forces_full_selection_before_omission(mutation: str) -> None:
    transport = _Transport(mutation)
    snapshot, policy = _policies()
    context = asyncio.run(
        GitHubRepositoryContextProvider(_Factory(transport)).load(_request(), snapshot)
    )
    assert _DOC in {node["path"] for node in json.loads(transport.graph)["nodes"]}
    assert not context.diff.full_ci_invalidating
    assert context.diff.changed_paths == (_DOC,)
    assert context.dependency_graph.fresh is False
    assert "graph_provenance_untrusted" in context.planning_input.fallback_reasons
    candidate = plan(context.planning_input, policy)
    assert isinstance(candidate, DeterministicPlan)
    verified = verify(context.planning_input, policy, candidate)
    assert verified.fallback.triggered
    assert verified.omitted_obligations == ()
    assert {row.obligation_id for row in verified.selected_obligations} == {
        "native",
        "utility-config",
        "utility-go-static",
    }
    assert all(row.depth == "full" for row in verified.selected_obligations)


@pytest.mark.parametrize("additional_directories", [0, 194], ids=["small", "198-trees"])
def test_content_only_docs_change_preserves_qualified_utility_omission(
    additional_directories: int,
) -> None:
    transport = _Transport(additional_directories=additional_directories)
    snapshot, policy = _policies()
    context = asyncio.run(
        GitHubRepositoryContextProvider(_Factory(transport)).load(_request(), snapshot)
    )
    assert context.dependency_graph.fresh
    candidate = plan(context.planning_input, policy)
    assert isinstance(candidate, DeterministicPlan)
    verified = verify(context.planning_input, policy, candidate)
    assert not verified.fallback.triggered
    assert {row.obligation_id for row in verified.omitted_obligations} == {
        "utility-config",
        "utility-go-static",
    }
    git_requests = [
        request
        for request in transport.requests
        if request.operation.startswith("workflow_discovery.")
    ]
    assert [request.operation for request in git_requests] == [
        "workflow_discovery.get_commit",
        "workflow_discovery.get_recursive_tree",
        "workflow_discovery.get_blob",
    ]
    assert git_requests[0].path.endswith(_HEAD)
    assert git_requests[0].query == git_requests[2].query == ()
    assert git_requests[1].path.endswith(transport.tree_sha)
    assert git_requests[1].query == (GitHubQueryParameter("recursive", "1"),)


@pytest.mark.parametrize(
    "limit", ["_MAX_FILES", "_MAX_TREES", "_MAX_DEPTH", "_MAX_TOTAL_BYTES", "_MAX_FILE_BYTES"]
)
def test_inventory_resource_exhaustion_preserves_full_fallback(
    monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    monkeypatch.setattr(self_ci_inventory, limit, 0 if limit == "_MAX_DEPTH" else 1)
    transport = _Transport()
    snapshot, policy = _policies()
    context = asyncio.run(
        GitHubRepositoryContextProvider(_Factory(transport)).load(_request(), snapshot)
    )
    candidate = plan(context.planning_input, policy)
    assert isinstance(candidate, DeterministicPlan)
    assert verify(context.planning_input, policy, candidate).fallback.triggered


def test_inventory_timeout_falls_back_but_caller_cancellation_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(self_ci_inventory, "_TIMEOUT_SECONDS", 0.001)
    snapshot, policy = _policies()
    context = asyncio.run(
        GitHubRepositoryContextProvider(_Factory(_Transport("timeout"))).load(_request(), snapshot)
    )
    candidate = plan(context.planning_input, policy)
    assert isinstance(candidate, DeterministicPlan)
    assert verify(context.planning_input, policy, candidate).fallback.triggered
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            GitHubRepositoryContextProvider(_Factory(_Transport("cancelled"))).load(
                _request(), snapshot
            )
        )


@pytest.mark.parametrize(
    "limit", ["_MAX_JSON_BYTES", "_MAX_DIRECT_ENTRIES", "_MAX_PATH_BYTES", "_MAX_PATH_COMPONENTS"]
)
def test_recursive_response_limits_force_full_selection(
    monkeypatch: pytest.MonkeyPatch, limit: str
) -> None:
    monkeypatch.setattr(recursive_git_tree, limit, 1)
    snapshot, policy = _policies()
    context = asyncio.run(
        GitHubRepositoryContextProvider(_Factory(_Transport())).load(_request(), snapshot)
    )
    candidate = plan(context.planning_input, policy)
    assert isinstance(candidate, DeterministicPlan)
    verified = verify(context.planning_input, policy, candidate)
    assert verified.fallback.triggered
    assert verified.omitted_obligations == ()
    assert {row.obligation_id for row in verified.selected_obligations} == {
        "native",
        "utility-config",
        "utility-go-static",
    }

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Mapping

from ci_coordinator.execution_orchestration import TARGET_EXECUTION_REGISTRY_PATH
from ci_coordinator.integrations.github.app_transport_profile import GITHUB_API_VERSION
from ci_coordinator.integrations.github.contracts import (
    GitHubPaginationEvidence,
    GitHubRequest,
    GitHubResponse,
)
from ci_coordinator.kernel import utf16_sort_key
from ci_coordinator.workflow_discovery.source import git_blob_sha1

REVISION_SHA = "1" * 40
CAPACITY_MANIFEST_PATH = ".ci-coordinator/test-manifest.v1.json"
ROOT_TREE_SHA = "2" * 40
CONTROL_TREE_SHA = "3" * 40
GITHUB_TREE_SHA = "4" * 40
WORKFLOWS_TREE_SHA = "5" * 40


class AdapterSnapshotTransport:
    def __init__(
        self,
        *,
        registry: bytes,
        workflows: Mapping[str, bytes],
        control_files: Mapping[str, bytes],
        mode_overrides: Mapping[str, str] | None = None,
        manifest: bytes | None = None,
        failing_blob_content: bytes | None = None,
        rejected_blob_content: bytes | None = None,
    ) -> None:
        self.requests: list[GitHubRequest] = []
        self.maximum_concurrent_blobs = 0
        self.cancelled_blob_reads = 0
        self._active_blobs = 0
        self._blocked_blob_reads = asyncio.Event()
        self._failure_started = asyncio.Event()
        self._four_blob_reads_active = asyncio.Event()
        self._failing_blob_sha = (
            None if failing_blob_content is None else git_blob_sha1(failing_blob_content)
        )
        self._rejected_blob_sha = (
            None if rejected_blob_content is None else git_blob_sha1(rejected_blob_content)
        )
        self._mode_overrides = dict(mode_overrides or {})
        self._files = {
            TARGET_EXECUTION_REGISTRY_PATH: registry,
            **control_files,
            **workflows,
            **({} if manifest is None else {CAPACITY_MANIFEST_PATH: manifest}),
        }
        self._blobs = {git_blob_sha1(content): content for content in self._files.values()}

    @property
    def active_blob_reads(self) -> int:
        return self._active_blobs

    async def send(self, request: GitHubRequest) -> GitHubResponse:
        self.requests.append(request)
        if request.operation == "workflow_discovery.get_commit":
            commit_sha = request.path.rsplit("/", 1)[-1]
            return _response(_json({"sha": commit_sha, "tree": {"sha": ROOT_TREE_SHA}}))
        if request.operation == "workflow_discovery.get_tree":
            return self._tree_response(request.path)
        if request.operation == "workflow_discovery.get_blob":
            return await self._blob_response(request.path)
        raise AssertionError(f"unexpected GitHub request: {request.operation}")

    def _tree_response(self, path: str) -> GitHubResponse:
        if path.endswith(ROOT_TREE_SHA):
            return _tree(
                ROOT_TREE_SHA,
                (
                    _tree_entry(".ci-coordinator", CONTROL_TREE_SHA),
                    _tree_entry(".github", GITHUB_TREE_SHA),
                ),
            )
        if path.endswith(CONTROL_TREE_SHA):
            paths = tuple(
                sorted(
                    (path for path in self._files if path.startswith(".ci-coordinator/")),
                    key=utf16_sort_key,
                )
            )
            return _tree(
                CONTROL_TREE_SHA,
                tuple(self._blob_entry(path) for path in paths),
            )
        if path.endswith(GITHUB_TREE_SHA):
            return _tree(
                GITHUB_TREE_SHA,
                (_tree_entry("workflows", WORKFLOWS_TREE_SHA),),
            )
        if path.endswith(WORKFLOWS_TREE_SHA):
            paths = tuple(
                sorted(
                    (path for path in self._files if path.startswith(".github/workflows/")),
                    key=utf16_sort_key,
                )
            )
            return _tree(
                WORKFLOWS_TREE_SHA,
                tuple(self._blob_entry(path) for path in paths),
            )
        raise AssertionError(f"unexpected Git tree request: {path}")

    async def _blob_response(self, path: str) -> GitHubResponse:
        sha = path.rsplit("/", 1)[-1]
        content = self._blobs.get(sha)
        if content is None:
            raise AssertionError(f"unexpected Git blob request: {path}")
        self._active_blobs += 1
        self.maximum_concurrent_blobs = max(
            self.maximum_concurrent_blobs,
            self._active_blobs,
        )
        if self._active_blobs >= 4:
            self._four_blob_reads_active.set()
        try:
            if sha in {self._failing_blob_sha, self._rejected_blob_sha}:
                self._failure_started.set()
                await self._four_blob_reads_active.wait()
                if sha == self._failing_blob_sha:
                    raise RuntimeError("injected Git blob failure")
                return _response(b"provider unavailable", status=503)
            if (
                self._failing_blob_sha is None and self._rejected_blob_sha is None
            ) or not self._failure_started.is_set():
                await asyncio.sleep(0)
            else:
                await self._blocked_blob_reads.wait()
            return _response(
                _json(
                    {
                        "sha": sha,
                        "size": len(content),
                        "encoding": "base64",
                        "content": base64.b64encode(content).decode(),
                    }
                )
            )
        except asyncio.CancelledError:
            self.cancelled_blob_reads += 1
            raise
        finally:
            self._active_blobs -= 1

    def _blob_entry(self, path: str) -> dict[str, object]:
        content = self._files[path]
        return {
            "path": path.rsplit("/", 1)[-1],
            "mode": self._mode_overrides.get(path, "100644"),
            "type": "blob",
            "sha": git_blob_sha1(content),
            "size": len(content),
        }


def _tree(sha: str, entries: object) -> GitHubResponse:
    return _response(_json({"sha": sha, "truncated": False, "tree": entries}))


def _tree_entry(name: str, sha: str) -> dict[str, object]:
    return {
        "path": name,
        "mode": "040000",
        "type": "tree",
        "sha": sha,
    }


def _response(body: bytes, *, status: int = 200) -> GitHubResponse:
    return GitHubResponse(
        status=status,
        api_version=GITHUB_API_VERSION,
        headers=(),
        body=body,
        pagination=GitHubPaginationEvidence.not_paginated(),
    )


def _json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()

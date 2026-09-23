from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from scripts.bounded_process import spawn
from scripts.module_ownership_profile import ModuleOwnershipProfile, ReviewMetric
from scripts.module_ownership_python_signals import signal_runtime_id
from scripts.module_ownership_source_signals import metric_values
from scripts.repository_paths import (
    read_repository_regular_file,
    repository_path_matches,
)

_GIT_OUTPUT_OVERHEAD_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class RepositoryPathSnapshot:
    deleted_tracked_count: int
    paths: tuple[str, ...]
    tracked_count: int
    untracked_count: int

    def __post_init__(self) -> None:
        if self.deleted_tracked_count < 0 or self.tracked_count < 0 or self.untracked_count < 0:
            raise ValueError("module ownership path counts must be non-negative")
        if self.paths != tuple(sorted(set(self.paths))):
            raise ValueError("module ownership paths must be unique and canonically ordered")
        if self.tracked_count + self.untracked_count != len(self.paths):
            raise ValueError("module ownership path counts do not match observed paths")


def candidate_inventory(
    profile: ModuleOwnershipProfile,
    repo_root: Path,
) -> dict[str, object]:
    repo_root = repo_root.resolve(strict=True)
    limits = profile.traversal_limits
    snapshot = repository_path_snapshot(
        repo_root,
        maximum_output_bytes=(
            limits.maximum_total_path_bytes + limits.maximum_entries + _GIT_OUTPUT_OVERHEAD_BYTES
        ),
        timeout_seconds=limits.git_path_discovery_timeout_seconds,
    )
    return _evaluate_candidate_snapshot(
        profile,
        repo_root,
        snapshot,
        candidate_queue_complete=True,
        inventory_scope="git-worktree",
    )


def fixture_candidate_inventory(
    profile: ModuleOwnershipProfile,
    repo_root: Path,
    snapshot: RepositoryPathSnapshot,
) -> dict[str, object]:
    return _evaluate_candidate_snapshot(
        profile,
        repo_root,
        snapshot,
        candidate_queue_complete=False,
        inventory_scope="caller-supplied-fixture",
    )


def _evaluate_candidate_snapshot(
    profile: ModuleOwnershipProfile,
    repo_root: Path,
    snapshot: RepositoryPathSnapshot,
    *,
    candidate_queue_complete: bool,
    inventory_scope: str,
) -> dict[str, object]:
    limits = profile.traversal_limits
    runtime_id = signal_runtime_id()
    if runtime_id not in profile.signal_runtime_ids:
        raise ValueError(f"module ownership signal runtime is not admitted: {runtime_id}")
    if len(snapshot.paths) > limits.maximum_entries:
        raise ValueError("module ownership candidate inventory exceeds its entry bound")

    dispositions: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []
    cohort_counts = {
        "production": {"candidateCount": 0, "evaluatedCount": 0},
        "special": {"candidateCount": 0, "evaluatedCount": 0},
        "test": {"candidateCount": 0, "evaluatedCount": 0},
    }
    total_path_bytes = 0
    total_content_bytes = 0
    for relative in snapshot.paths:
        if len(Path(relative).parts) > limits.maximum_depth:
            raise ValueError(f"candidate path exceeds its depth bound: {relative}")
        path_bytes = len(relative.encode("utf-8"))
        if path_bytes > limits.maximum_relative_path_bytes:
            raise ValueError(f"candidate path exceeds its byte bound: {relative}")
        total_path_bytes += path_bytes
        if total_path_bytes > limits.maximum_total_path_bytes:
            raise ValueError("module ownership candidate paths exceed their aggregate byte bound")

        payload = read_repository_regular_file(
            repo_root,
            Path(relative),
            "module ownership candidate file",
            maximum_bytes=limits.maximum_candidate_file_bytes,
        )
        total_content_bytes += len(payload)
        if total_content_bytes > limits.maximum_total_candidate_bytes:
            raise ValueError(
                "module ownership candidate contents exceed their aggregate byte bound"
            )
        file_kind = classify_file_kind(profile, relative)
        cohort = _cohort(file_kind)
        measured_values = metric_values(relative, payload)
        applicable_values: dict[str, int | None] = {}
        reasons: list[str] = []
        suffix = Path(relative).suffix
        for metric in profile.review_metrics:
            if not _metric_applies(metric, file_kind, suffix):
                continue
            value = measured_values.get(metric.metric_id)
            applicable_values[metric.metric_id] = value
            if value is None:
                reasons.append(f"{metric.metric_id}:unknown")
            elif metric.matches(value):
                reasons.append(f"{metric.metric_id}:{metric.operator}:{metric.threshold}")

        content_digest = hashlib.sha256(payload).hexdigest()
        disposition = {
            "candidate": bool(reasons),
            "cohort": cohort,
            "contentDigest": content_digest,
            "fileKind": file_kind,
            "metricValues": applicable_values,
            "path": relative,
            "selectionReasons": reasons,
        }
        dispositions.append(disposition)
        cohort_counts[cohort]["evaluatedCount"] += 1
        if reasons:
            cohort_counts[cohort]["candidateCount"] += 1
            candidates.append(disposition)

    inventory_digest = hashlib.sha256(_canonical_json_bytes(dispositions)).hexdigest()
    candidate_evidence_digest = hashlib.sha256(_canonical_json_bytes(candidates)).hexdigest()
    return {
        "schemaVersion": 1,
        "profileDigest": profile.profile_digest,
        "signalGrammarId": profile.signal_grammar_id,
        "signalRuntimeId": runtime_id,
        "inventoryScope": inventory_scope,
        "candidateQueueComplete": candidate_queue_complete,
        "candidateTruncatedCount": 0,
        "defaultMaximumCandidates": None,
        "candidateCount": len(candidates),
        "candidateEvidenceDigest": candidate_evidence_digest,
        "inventoryDigest": inventory_digest,
        "observedPathCount": len(snapshot.paths),
        "evaluatedContentBytes": total_content_bytes,
        "trackedPathCount": snapshot.tracked_count,
        "untrackedPathCount": snapshot.untracked_count,
        "deletedTrackedPathCount": snapshot.deleted_tracked_count,
        "dispositionCount": len(dispositions),
        "cohorts": cohort_counts,
        "candidates": candidates,
        "nonClaims": [
            (
                "Candidate completeness is relative to the exact profile, worktree paths, "
                "deterministic signal grammar, and parser runtime."
            ),
            (
                "Only the Git-discovered worktree inventory scope can claim candidate-queue "
                "completeness."
            ),
            (
                "Candidate metrics and unknown analysis select review only; they do not "
                "prove semantic concentration or a blockable violation."
            ),
            (
                "The inventory does not claim complete coupling, complexity, churn, or "
                "history analysis."
            ),
        ],
    }


def repository_path_snapshot(
    repo_root: Path,
    *,
    maximum_output_bytes: int = 4 * 1024 * 1024,
    timeout_seconds: int = 30,
) -> RepositoryPathSnapshot:
    repo_root = repo_root.resolve(strict=True)
    _assert_git_toplevel(
        repo_root,
        maximum_output_bytes=maximum_output_bytes,
        timeout_seconds=timeout_seconds,
    )
    tracked = set(
        _git_paths(
            repo_root,
            ("ls-files", "--cached", "-z"),
            maximum_output_bytes=maximum_output_bytes,
            timeout_seconds=timeout_seconds,
        )
    )
    untracked = set(
        _git_paths(
            repo_root,
            ("ls-files", "--others", "--exclude-standard", "-z"),
            maximum_output_bytes=maximum_output_bytes,
            timeout_seconds=timeout_seconds,
        )
    )
    deleted = set(
        _git_paths(
            repo_root,
            ("ls-files", "--deleted", "-z"),
            maximum_output_bytes=maximum_output_bytes,
            timeout_seconds=timeout_seconds,
        )
    )
    if tracked & untracked:
        raise ValueError("module ownership path is both tracked and untracked")
    paths = tuple(sorted((tracked | untracked) - deleted))
    return RepositoryPathSnapshot(
        deleted_tracked_count=len(deleted),
        paths=paths,
        tracked_count=len(tracked - deleted),
        untracked_count=len(untracked),
    )


def classify_file_kind(profile: ModuleOwnershipProfile, path: str) -> str:
    for rule in profile.file_kind_rules:
        if any(repository_path_matches(pattern, path) for pattern in rule.path_patterns):
            return rule.kind
    return profile.default_file_kind


def _metric_applies(metric: ReviewMetric, file_kind: str, suffix: str) -> bool:
    return file_kind in metric.applicable_file_kinds and (
        metric.applicable_suffixes == ("*",) or suffix in metric.applicable_suffixes
    )


def _cohort(file_kind: str) -> str:
    if file_kind == "test":
        return "test"
    if file_kind in {
        "composition-root",
        "production-authority",
        "production-like-script",
    }:
        return "production"
    return "special"


def _git_paths(
    repo_root: Path,
    args: tuple[str, ...],
    *,
    maximum_output_bytes: int,
    timeout_seconds: int,
) -> list[str]:
    executable = shutil.which("git")
    if executable is None:
        raise OSError("git executable was not found on PATH")
    result = spawn(
        executable,
        args,
        cwd=repo_root,
        env=_git_environment(),
        max_buffer=maximum_output_bytes,
        timeout_seconds=timeout_seconds,
        decode_errors="surrogateescape",
    )
    if result.error is not None:
        raise OSError(f"module ownership git path discovery failed: {result.error}")
    if result.status != 0:
        raise ValueError(
            f"module ownership git path discovery exited with status {result.status}: "
            f"{result.stderr.strip()}"
        )
    if any("\udc80" <= character <= "\udcff" for character in result.stdout):
        raise ValueError("module ownership git path is not valid UTF-8")
    if result.stdout and not result.stdout.endswith("\0"):
        raise ValueError("module ownership git path output is not NUL terminated")
    return [segment for segment in result.stdout.split("\0") if segment]


def _assert_git_toplevel(
    repo_root: Path,
    *,
    maximum_output_bytes: int,
    timeout_seconds: int,
) -> None:
    executable = shutil.which("git")
    if executable is None:
        raise OSError("git executable was not found on PATH")
    result = spawn(
        executable,
        ("rev-parse", "--path-format=absolute", "--show-toplevel"),
        cwd=repo_root,
        env=_git_environment(),
        max_buffer=maximum_output_bytes,
        timeout_seconds=timeout_seconds,
        decode_errors="surrogateescape",
    )
    if result.error is not None:
        raise OSError(f"module ownership git root discovery failed: {result.error}")
    if result.status != 0:
        raise ValueError(
            f"module ownership git root discovery exited with status {result.status}: "
            f"{result.stderr.strip()}"
        )
    if any("\udc80" <= character <= "\udcff" for character in result.stdout):
        raise ValueError("module ownership git root is not valid UTF-8")
    if not result.stdout.endswith("\n"):
        raise ValueError("module ownership git root output is not newline terminated")
    observed_root = Path(result.stdout[:-1]).resolve(strict=True)
    if observed_root != repo_root:
        raise ValueError("module ownership repository root differs from the Git toplevel")


def _git_environment() -> dict[str, str]:
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")

from __future__ import annotations

import base64
import hashlib
import os
import re
import stat
import sys
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.bounded_git import DEFAULT_GIT_TIMEOUT_SECONDS, capture_git_text, run_git
from scripts.proofkit_changed_paths import (
    ChangedPathContext,
    changed_path_context_from_git_context,
)
from scripts.proofkit_common import (
    JsonObject,
    as_array,
    as_object,
    js_json_dumps,
    read_json_object,
    write_json,
)
from scripts.proofkit_git_range import ProofkitGitRange
from scripts.proofkit_selective_plan import selective_gate_plan_input

REPO_ROOT = Path(__file__).resolve().parent.parent
TEXT_POLICY_MAX_INPUT_BYTES = 32 * 1024 * 1024
TEXT_POLICY_REPORT_ID = "ci-coordinator.text-policy"

GENERATED_ARTIFACTS: tuple[JsonObject, ...] = (
    {
        "generator": "scripts/frontend_contract.py",
        "path": "frontend/openapi/workbench.openapi.json",
        "sourceOfTruth": [
            "backend/src/ci_coordinator/api/http/activity_contracts.py",
            "backend/src/ci_coordinator/api/http/analytics_configuration_contracts.py",
            "backend/src/ci_coordinator/api/http/app.py",
            "backend/src/ci_coordinator/api/http/body_limits.py",
            "backend/src/ci_coordinator/api/http/ci_economics_budget_contracts.py",
            "backend/src/ci_coordinator/api/http/ci_economics_catalog_contracts.py",
            "backend/src/ci_coordinator/api/http/ci_economics_contracts.py",
            "backend/src/ci_coordinator/api/http/ci_economics_measurement_contracts.py",
            "backend/src/ci_coordinator/api/http/ci_economics_source_contracts.py",
            "backend/src/ci_coordinator/api/http/ci_history_analytics_contracts.py",
            "backend/src/ci_coordinator/api/http/ci_history_contracts.py",
            "backend/src/ci_coordinator/api/http/ci_history_read_contracts.py",
            "backend/src/ci_coordinator/api/http/ci_measurement_report_contracts.py",
            "backend/src/ci_coordinator/api/http/ci_measurement_report_submission.py",
            "backend/src/ci_coordinator/api/http/ci_observation_contracts.py",
            "backend/src/ci_coordinator/api/http/config_lifecycle_contracts.py",
            "backend/src/ci_coordinator/api/http/contracts.py",
            "backend/src/ci_coordinator/api/http/control_plane_authentication.py",
            "backend/src/ci_coordinator/api/http/control_plane_security.py",
            "backend/src/ci_coordinator/api/http/dependencies.py",
            "backend/src/ci_coordinator/api/http/governance_baseline_contracts.py",
            "backend/src/ci_coordinator/api/http/governance_comparison_contracts.py",
            "backend/src/ci_coordinator/api/http/governance_observation_contracts.py",
            "backend/src/ci_coordinator/api/http/governance_state_contracts.py",
            "backend/src/ci_coordinator/api/http/model_contracts.py",
            "backend/src/ci_coordinator/api/http/public_request_limits.py",
            "backend/src/ci_coordinator/api/http/routers/activity.py",
            "backend/src/ci_coordinator/api/http/routers/analytics_configuration.py",
            "backend/src/ci_coordinator/api/http/routers/ci_economics.py",
            "backend/src/ci_coordinator/api/http/routers/ci_economics_budgets.py",
            "backend/src/ci_coordinator/api/http/routers/ci_economics_sources.py",
            "backend/src/ci_coordinator/api/http/routers/ci_history.py",
            "backend/src/ci_coordinator/api/http/routers/ci_history_analytics.py",
            "backend/src/ci_coordinator/api/http/routers/ci_history_read.py",
            "backend/src/ci_coordinator/api/http/routers/ci_measurement_report_ingestion.py",
            "backend/src/ci_coordinator/api/http/routers/ci_measurement_reports.py",
            "backend/src/ci_coordinator/api/http/routers/ci_observation.py",
            "backend/src/ci_coordinator/api/http/routers/config_lifecycle_queries.py",
            "backend/src/ci_coordinator/api/http/routers/config_management.py",
            "backend/src/ci_coordinator/api/http/routers/control_plane_identity.py",
            "backend/src/ci_coordinator/api/http/routers/governance_baselines.py",
            "backend/src/ci_coordinator/api/http/routers/governance_comparisons.py",
            "backend/src/ci_coordinator/api/http/routers/governance_observation.py",
            "backend/src/ci_coordinator/api/http/routers/provider_inventory.py",
            "backend/src/ci_coordinator/api/http/routers/repository_attestations.py",
            "backend/src/ci_coordinator/api/http/routers/workbench.py",
            "backend/src/ci_coordinator/api/http/routers/workflow_discovery.py",
            "backend/src/ci_coordinator/api/http/security.py",
            "backend/src/ci_coordinator/api/http/workflow_discovery_contracts.py",
            "backend/src/ci_coordinator/audit_replay/event.py",
            "backend/src/ci_coordinator/ci_economics/_observation_values.py",
            "backend/src/ci_coordinator/ci_economics/analytics_configuration.py",
            "backend/src/ci_coordinator/ci_economics/archive_analytics_models.py",
            "backend/src/ci_coordinator/ci_economics/archive_detail.py",
            "backend/src/ci_coordinator/ci_economics/archive_retention.py",
            "backend/src/ci_coordinator/ci_economics/archive_retention_payload.py",
            "backend/src/ci_coordinator/ci_economics/archive_statistics.py",
            "backend/src/ci_coordinator/ci_economics/budget.py",
            "backend/src/ci_coordinator/ci_economics/budget_payload.py",
            "backend/src/ci_coordinator/ci_economics/budget_policy.py",
            "backend/src/ci_coordinator/ci_economics/budget_signal.py",
            "backend/src/ci_coordinator/ci_economics/catalog.py",
            "backend/src/ci_coordinator/ci_economics/collection.py",
            "backend/src/ci_coordinator/ci_economics/comparison.py",
            "backend/src/ci_coordinator/ci_economics/discovery.py",
            "backend/src/ci_coordinator/ci_economics/history_administration.py",
            "backend/src/ci_coordinator/ci_economics/history_commands.py",
            "backend/src/ci_coordinator/ci_economics/history_configuration.py",
            "backend/src/ci_coordinator/ci_economics/history_payload.py",
            "backend/src/ci_coordinator/ci_economics/history_read.py",
            "backend/src/ci_coordinator/ci_economics/history_rechecks.py",
            "backend/src/ci_coordinator/ci_economics/history_retention_commands.py",
            "backend/src/ci_coordinator/ci_economics/history_scan.py",
            "backend/src/ci_coordinator/ci_economics/model.py",
            "backend/src/ci_coordinator/ci_economics/observation.py",
            "backend/src/ci_coordinator/ci_economics/observation_payload.py",
            "backend/src/ci_coordinator/ci_economics/payload_model.py",
            "backend/src/ci_coordinator/ci_economics/read_models.py",
            "backend/src/ci_coordinator/ci_economics/report_payload.py",
            "backend/src/ci_coordinator/ci_economics/reports.py",
            "backend/src/ci_coordinator/ci_economics/sources.py",
            "backend/src/ci_coordinator/config_control/limits.py",
            "backend/src/ci_coordinator/config_epochs/contracts.py",
            "backend/src/ci_coordinator/config_epochs/queries.py",
            "backend/src/ci_coordinator/control_plane_identity/model.py",
            "backend/src/ci_coordinator/governance_baseline/model.py",
            "backend/src/ci_coordinator/governance_comparison/model.py",
            "backend/src/ci_coordinator/governance_observation/model.py",
            "backend/src/ci_coordinator/kernel/canonical_json.py",
            "backend/src/ci_coordinator/kernel/git_reference.py",
            "backend/src/ci_coordinator/proposal_review/model.py",
            "backend/src/ci_coordinator/provider_inventory/model.py",
            "backend/src/ci_coordinator/workbench_read_models/model.py",
            "docs/specs/ci-coordinator-control-plane/config-lifecycle-http-profile.v1.json",
            "scripts/config_lifecycle_http_profile.py",
            "scripts/frontend_contract.py",
        ],
    },
    {
        "generator": "frontend/package.json",
        "path": "frontend/src/api/generated.ts",
        "sourceOfTruth": [
            "frontend/openapi/workbench.openapi.json",
            "frontend/package.json",
            "pnpm-lock.yaml",
        ],
    },
)


def build_input(
    mode: str | None,
    args: Sequence[str],
    *,
    env: Mapping[str, str],
    repo_root: Path = REPO_ROOT,
) -> JsonObject:
    if mode == "changed-path-set":
        return changed_path_set_input(_changed_path_context(args, env, repo_root).paths)
    if mode == "repo-profile":
        _reject_unexpected_args(mode, args)
        return repo_profile_input(repo_root)
    if mode == "selective-gate-plan":
        context = _changed_path_context(args, env, repo_root)
        return selective_gate_plan_input(
            base_ref=context.base_ref, paths=context.paths, repo_root=repo_root
        )
    if mode == "text-policy":
        _reject_unexpected_args(mode, args)
        return text_policy_input(repo_root)
    visible = mode if mode is not None else "<missing>"
    raise ValueError(f"unknown proofkit input mode: {visible}")


def repo_profile_input(repo_root: Path = REPO_ROOT) -> JsonObject:
    profile = read_json_object(repo_root / "proofkit/repo-profile.json")
    witness_input = read_json_object(repo_root / "proofkit/witness-plan-input.json")
    with (repo_root / "backend/pyproject.toml").open("rb") as source:
        pyproject = tomllib.load(source)
    project = as_object(pyproject.get("project"), "Python project")
    command_environment_pairs: list[JsonObject] = []
    for raw_command in as_array(witness_input.get("commands"), "witness commands"):
        command = as_object(raw_command, "witness command")
        argv = _string_array(command.get("argv"), "witness command argv")
        environment = as_object(command.get("environment"), "witness command environment")
        command_environment_pairs.append(
            {
                "command": " ".join(argv),
                "environmentClasses": environment.get("classes"),
            }
        )
    environment_class_policies: list[JsonObject] = []
    vocabulary = as_object(witness_input.get("vocabulary"), "witness vocabulary")
    for raw_policy in as_array(
        vocabulary.get("environmentClassPolicies"), "environment class policies"
    ):
        policy = as_object(raw_policy, "environment class policy")
        for network_policy in _string_array(policy.get("networkPolicies"), "network policies"):
            environment_class_policies.extend(
                [
                    {
                        "credentialClass": credential_class,
                        "environmentClass": policy.get("environmentClass"),
                        "networkPolicy": network_policy,
                    }
                    for credential_class in _string_array(
                        policy.get("credentialClasses"), "credential classes"
                    )
                ]
            )
    scripts_value = project.get("scripts", {})
    scripts = as_object(scripts_value, "Python project scripts")
    return {
        "schemaVersion": 1,
        "facts": {
            "commandEnvironmentPairs": command_environment_pairs,
            "docsPolicyGeneratedArtifacts": [dict(artifact) for artifact in GENERATED_ARTIFACTS],
            "packageScripts": [],
            "rootPackageName": project.get("name"),
            "rootScripts": sorted(scripts),
            "trackedFiles": repo_profile_tracked_files(repo_root),
        },
        "policy": {
            "environmentPolicy": {
                "environmentClassPolicies": environment_class_policies,
                "liveGithubRequiredClasses": [],
                "localSecretRequiredClasses": [],
            },
            "packageNamePattern": None,
        },
        "profile": profile,
    }


def changed_path_set_input(changed_paths: Sequence[str]) -> JsonObject:
    return {
        "schemaVersion": 1,
        "reportId": "ci-coordinator.changed-path-set",
        "nonClaims": [
            "CI Coordinator supplies changed paths from git state or explicit CLI arguments.",
            (
                "This input does not prove git source freshness, source completeness, "
                "or gate adequacy."
            ),
        ],
        "preexistingFailures": [],
        "sources": [
            {
                "sourceId": "ci-coordinator.git-working-tree",
                "paths": list(changed_paths),
            }
        ],
    }


def text_policy_input(repo_root: Path = REPO_ROOT) -> JsonObject:
    return text_policy_document(text_file_inventory(repo_root), TEXT_POLICY_REPORT_ID)


def text_policy_document(files: Sequence[JsonObject], report_id: str) -> JsonObject:
    return {
        "schemaVersion": 1,
        "reportId": report_id,
        "nonClaims": [
            "CI Coordinator supplies an explicit tracked and untracked file inventory.",
            "Text policy is not a repository-wide secret scanner.",
        ],
        "policy": {
            "allowTab": True,
            "asciiOnly": True,
            "binarySuffixes": [
                ".avif",
                ".gif",
                ".gz",
                ".ico",
                ".jpeg",
                ".jpg",
                ".pdf",
                ".png",
                ".tgz",
                ".webp",
                ".zip",
            ],
            "rejectTrailingWhitespace": True,
            "requireFinalNewline": True,
        },
        "files": list(files),
    }


def repo_files(repo_root: Path = REPO_ROOT) -> list[str]:
    paths = sorted(
        set(
            _git_lines(repo_root, ("ls-files",))
            + _git_lines(repo_root, ("ls-files", "--others", "--exclude-standard"))
        )
    )
    return [path for path in paths if (repo_root / path).exists()]


def repo_profile_tracked_files(repo_root: Path = REPO_ROOT) -> list[str]:
    secret_pattern = re.compile(r"sk-(?:proj-)?[A-Za-z0-9_-]{10,}", re.IGNORECASE)
    return [path for path in repo_files(repo_root) if secret_pattern.search(path) is None]


def text_file_inventory(repo_root: Path = REPO_ROOT) -> list[JsonObject]:
    entries = (_text_policy_entry(repo_root, path) for path in repo_files(repo_root))
    return [entry.row() for entry in entries if entry.exclusion is None]


@dataclass(frozen=True)
class TextPolicyEntry:
    path: str
    mode: int | None
    size: int | None
    exclusion: str | None
    content_base64: str | None = None
    content_sha256: str | None = None

    def row(self) -> JsonObject:
        if self.exclusion is not None or self.content_base64 is None:
            raise ValueError(f"excluded text-policy entry cannot become a row: {self.path}")
        return {"contentBase64": self.content_base64, "path": self.path, "state": "present"}

    def manifest(self) -> JsonObject:
        return {
            "path": self.path,
            "mode": self.mode,
            "bytes": self.size,
            "exclusion": self.exclusion,
            "sha256": self.content_sha256,
        }


@dataclass(frozen=True)
class TextPolicyBatch:
    start: int
    stop: int
    report_id: str
    input_bytes: int


def capture_text_policy_inventory(
    repo_root: Path, remaining: Callable[[], float]
) -> tuple[TextPolicyEntry, ...]:
    output = run_git(
        repo_root,
        ("ls-files", "--cached", "--others", "--exclude-standard", "-z"),
        timeout_seconds=min(DEFAULT_GIT_TIMEOUT_SECONDS, remaining()),
    ).stdout
    if output and not output.endswith("\0"):
        raise ValueError("text-policy Git inventory is not NUL terminated")
    paths = output[:-1].split("\0") if output else []
    if len(paths) != len(set(paths)):
        raise ValueError("text-policy Git inventory contains duplicate paths")
    entries: list[TextPolicyEntry] = []
    for path in sorted(paths):
        remaining()
        if _safe_repo_path(path) != path:
            raise ValueError("text-policy Git inventory contains a noncanonical path")
        entries.append(_text_policy_entry(repo_root, path))
        remaining()
    remaining()
    return tuple(entries)


def _text_policy_entry(repo_root: Path, path: str) -> TextPolicyEntry:
    full_path = repo_root / path
    if not full_path.exists():
        return TextPolicyEntry(path, None, None, "missing")
    info = full_path.lstat()
    reason = (
        "dist"
        if path.startswith("dist/")
        else "directory"
        if stat.S_ISDIR(info.st_mode)
        else "non-regular"
        if not stat.S_ISREG(info.st_mode)
        else "over-size-limit"
        if info.st_size > 1_000_000
        else None
    )
    if reason is not None:
        return TextPolicyEntry(path, info.st_mode, info.st_size, reason)
    with full_path.open("rb") as stream:
        content = stream.read(1_000_001)
    after = full_path.lstat()
    identity_fields = ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(info, field) != getattr(after, field) for field in identity_fields) or (
        len(content) != info.st_size
    ):
        raise ValueError(f"text-policy source changed while reading: {path}")
    if b"\0" in content:
        return TextPolicyEntry(path, info.st_mode, info.st_size, "nul-content")
    return TextPolicyEntry(
        path,
        info.st_mode,
        info.st_size,
        None,
        base64.b64encode(content).decode("ascii"),
        hashlib.sha256(content).hexdigest(),
    )


def plan_text_policy_batches(
    files: Sequence[TextPolicyEntry],
    remaining: Callable[[], float],
    *,
    maximum_bytes: int = TEXT_POLICY_MAX_INPUT_BYTES,
) -> tuple[TextPolicyBatch, ...]:
    if type(maximum_bytes) is not int or not 0 < maximum_bytes <= TEXT_POLICY_MAX_INPUT_BYTES:
        raise ValueError("text-policy input bound must be within the pinned CLI limit")
    paths = [entry.path for entry in files]
    if paths != sorted(set(paths)):
        raise ValueError("text-policy file paths must be globally sorted and unique")
    sizes: list[int] = []
    for entry in files:
        remaining()
        sizes.append(len(js_json_dumps(entry.row()).encode("utf-8")))
    whole_bytes = _text_policy_envelope_bytes(TEXT_POLICY_REPORT_ID) + sum(sizes)
    whole_bytes += max(0, len(files) - 1)
    if whole_bytes <= maximum_bytes:
        remaining()
        return (TextPolicyBatch(0, len(files), TEXT_POLICY_REPORT_ID, whole_bytes),)
    if not files:
        raise ValueError("text-policy envelope exceeds the input bound")
    batches: list[TextPolicyBatch] = []
    start = 0
    while start < len(files):
        remaining()
        report_id = f"{TEXT_POLICY_REPORT_ID}.batch-{len(batches) + 1}"
        size = _text_policy_envelope_bytes(report_id)
        stop = start
        while stop < len(files):
            remaining()
            extra = sizes[stop] + (stop > start)
            if size + extra > maximum_bytes:
                break
            size += extra
            stop += 1
        if stop == start:
            raise ValueError(f"text-policy row exceeds the input bound: {files[start].path}")
        batches.append(TextPolicyBatch(start, stop, report_id, size))
        start = stop
    remaining()
    return tuple(batches)


def _text_policy_envelope_bytes(report_id: str) -> int:
    return len(js_json_dumps(text_policy_document([], report_id)).encode("utf-8"))


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    mode = arguments[0] if arguments else None
    try:
        write_json(build_input(mode, arguments[1:], env=os.environ))
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


def _changed_path_context(
    args: Sequence[str], env: Mapping[str, str], repo_root: Path
) -> ChangedPathContext:
    git_range = ProofkitGitRange(repo_root)
    return changed_path_context_from_git_context(
        args=args,
        env=env,
        normalize_path=_safe_repo_path,
        git_paths=git_range.git_paths,
        committed_paths_since=git_range.committed_paths_since,
    )


def _reject_unexpected_args(mode: str, args: Sequence[str]) -> None:
    if args:
        raise ValueError(f"{mode} does not accept trailing arguments")


def _git_lines(repo_root: Path, args: Sequence[str]) -> list[str]:
    output = capture_git_text(repo_root, args)
    return [_safe_repo_path(path) for path in output.split("\n") if path]


def _safe_repo_path(value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"unsafe repository path: {value}")
    normalized = value.replace("\\", "/")
    if (
        not normalized
        or normalized.startswith("/")
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise ValueError(f"unsafe repository path: {value}")
    return normalized


def _string_array(value: object, context: str) -> list[str]:
    values = as_array(value, context)
    if any(not isinstance(item, str) for item in values):
        raise ValueError(f"{context} must contain only strings")
    return [item for item in values if isinstance(item, str)]


if __name__ == "__main__":
    raise SystemExit(main())

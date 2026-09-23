"""Exact coordinator and target contract source-epoch admission."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, cast

from ci_coordinator.consumer_contract_lab.codec import (
    parse_consumer_lab_profile,
    parse_scenario_corpus,
)
from ci_coordinator.consumer_contract_lab.git_source import (
    GitSourceError,
    coordinator_package_snapshot,
    git_snapshot,
    read_opaque_regular,
    read_regular,
    relative_path,
    repository_root,
    require_directory,
    require_loaded_source,
)
from ci_coordinator.consumer_contract_lab.model import (
    ConsumerLabProfile,
    FileBinding,
    GitSourceSnapshot,
    ManifestEntry,
    ScenarioCorpus,
)
from ci_coordinator.consumer_contract_lab.workflow_admission import (
    admits_target_workflow_authority,
)
from ci_coordinator.execution_orchestration import (
    TargetExecutionRegistry,
    digest_adapter_file,
    parse_target_execution_registry,
)
from ci_coordinator.kernel import (
    StrictJsonError,
    canonical_json,
    hash_object,
    load_strict_json,
    utf16_sort_key,
)
from ci_coordinator.repo_context.freshness import is_safe_relative_path
from ci_coordinator.target_artifacts import (
    TargetArtifactsAdmissionError,
    TargetArtifactsRenderError,
    TargetArtifactsSource,
    parse_target_artifacts_source,
    render_target_artifacts,
)

_LOCK_SCHEMA: Final = "ci-coordinator-target-artifact-lock/v1"
_POLICY_SCHEMA: Final = "dynamic-ci-policy-fragment/v1"
_LOCK_FILENAME: Final = "target-artifacts.lock.v1.json"
_MAX_FILE_BYTES: Final = 4_194_304
_COORDINATOR_PACKAGE_PATH: Final = "backend/src/ci_coordinator"


class ConsumerContractSourceError(ValueError):
    """Coordinator or target bytes do not form one exact contract epoch."""


@dataclass(frozen=True, slots=True)
class LabExecutionAuthority:
    kind: Literal["target-git-commit", "sealed-worktree-manifest"]
    coordinate: str

    def __post_init__(self) -> None:
        if len(self.coordinate) != 40 or any(
            character not in "0123456789abcdef" for character in self.coordinate
        ):
            raise ValueError("lab execution authority coordinate must be 40 hexadecimal characters")

    def to_mapping(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "coordinate": self.coordinate,
            "providerEvidence": False,
        }


class _ContractFiles:
    def __init__(self, root: Path) -> None:
        self._root = root
        self._contents: dict[str, bytes] = {}
        self._sealed = False

    def read(self, relative: str, *, opaque: bool = False) -> bytes:
        if self._sealed:
            raise ConsumerContractSourceError("contract file set is already sealed")
        content = _read_regular(self._root, relative, opaque=opaque)
        previous = self._contents.setdefault(relative, content)
        if previous != content:
            raise ConsumerContractSourceError("contract file changed between observations")
        return content

    def read_bound(self, binding: FileBinding, *, opaque: bool = False) -> bytes:
        content = self.read(binding.path, opaque=opaque)
        if hashlib.sha256(content).hexdigest() != binding.sha256:
            raise ConsumerContractSourceError(
                f"target-owned binding digest mismatch: {binding.path}"
            )
        return content

    def seal(self) -> tuple[ManifestEntry, ...]:
        if self._sealed or not self._contents:
            raise ConsumerContractSourceError("contract file set cannot be sealed")
        paths = tuple(sorted(self._contents, key=utf16_sort_key))
        entries = tuple(
            ManifestEntry(
                path=path,
                sha256=hashlib.sha256(self._contents[path]).hexdigest(),
                size_bytes=len(self._contents[path]),
            )
            for path in paths
        )
        if _manifest(self._root, paths) != entries:
            raise ConsumerContractSourceError("contract files changed before source-epoch closure")
        self._sealed = True
        return entries

    def relevant_files(self) -> tuple[tuple[str, bytes], ...]:
        if not self._sealed:
            raise ConsumerContractSourceError("contract file set is not sealed")
        return tuple(
            (path, self._contents[path]) for path in sorted(self._contents, key=utf16_sort_key)
        )


@dataclass(frozen=True, slots=True)
class PreparedConsumerContract:
    coordinator_root: Path
    coordinator_package_root: Path
    target_root: Path
    profile_path: str
    profile: ConsumerLabProfile
    corpus: ScenarioCorpus
    coordinator_source: GitSourceSnapshot
    target_source: GitSourceSnapshot
    manifest_entries: tuple[ManifestEntry, ...]
    contract_files: tuple[tuple[str, bytes], ...]
    manifest_sha256: str
    source_epoch_id: str
    dynamic_policy: dict[str, object]
    target_artifacts_source: TargetArtifactsSource
    target_registry: TargetExecutionRegistry

    def __post_init__(self) -> None:
        if (
            not self.coordinator_root.is_absolute()
            or not self.coordinator_package_root.is_absolute()
            or not self.target_root.is_absolute()
        ):
            raise ValueError("consumer contract roots must be absolute")
        if type(self.profile) is not ConsumerLabProfile:
            raise TypeError("prepared contract requires an exact profile")
        if type(self.corpus) is not ScenarioCorpus:
            raise TypeError("prepared contract requires an exact scenario corpus")
        if type(self.coordinator_source) is not GitSourceSnapshot:
            raise TypeError("prepared contract requires an exact coordinator source")
        if type(self.target_source) is not GitSourceSnapshot:
            raise TypeError("prepared contract requires an exact target source")
        if (
            type(self.manifest_entries) is not tuple
            or not self.manifest_entries
            or any(type(item) is not ManifestEntry for item in self.manifest_entries)
        ):
            raise TypeError("prepared contract requires exact manifest entries")
        paths = tuple(item.path for item in self.manifest_entries)
        if paths != tuple(sorted(set(paths), key=utf16_sort_key)):
            raise ValueError("prepared contract manifest must be canonical")
        if (
            type(self.contract_files) is not tuple
            or not self.contract_files
            or any(
                type(item) is not tuple
                or len(item) != 2
                or type(item[0]) is not str
                or type(item[1]) is not bytes
                for item in self.contract_files
            )
            or tuple(path for path, _ in self.contract_files) != paths
            or _manifest_entries(self.contract_files) != self.manifest_entries
        ):
            raise ValueError("prepared contract files do not match the sealed manifest")
        if self.manifest_sha256 != _manifest_digest(self.manifest_entries):
            raise ValueError("prepared contract manifest digest does not match entries")
        expected_epoch = hash_object(
            {
                "coordinator": self.coordinator_source.to_mapping(),
                "target": self.target_source.to_mapping(),
                "manifestSha256": self.manifest_sha256,
            }
        )
        if self.source_epoch_id != expected_epoch:
            raise ValueError("prepared contract source epoch identity does not match")
        if type(self.target_artifacts_source) is not TargetArtifactsSource:
            raise TypeError("prepared contract requires exact target artifacts")
        if type(self.target_registry) is not TargetExecutionRegistry:
            raise TypeError("prepared contract requires an exact target registry")

    def file_bytes(self, relative: str) -> bytes:
        for path, content in self.contract_files:
            if path == relative:
                return content
        raise ConsumerContractSourceError(f"sealed contract file is unavailable: {relative}")

    @property
    def execution_authority(self) -> LabExecutionAuthority:
        return _execution_authority(self.target_source, self.source_epoch_id)


def prepare_consumer_contract(
    *,
    coordinator_root: Path,
    coordinator_commit: str,
    coordinator_package_root: Path,
    target_root: Path,
    profile_path: Path,
) -> PreparedConsumerContract:
    try:
        coordinator = repository_root(coordinator_root)
        target = repository_root(target_root)
        relative_profile = relative_path(target, profile_path)
        coordinator_source = coordinator_package_snapshot(
            coordinator,
            _COORDINATOR_PACKAGE_PATH,
            coordinator_commit,
            coordinator_package_root,
        )
    except GitSourceError as error:
        raise ConsumerContractSourceError(str(error)) from error
    try:
        require_loaded_source(
            coordinator_package_root,
            Path(__file__).resolve().parents[1],
        )
    except GitSourceError as error:
        raise ConsumerContractSourceError(str(error)) from error

    files = _ContractFiles(target)
    profile_content = files.read(relative_profile)
    profile = parse_consumer_lab_profile(profile_content)
    if coordinator_source.head != profile.expected_coordinator_commit:
        raise ConsumerContractSourceError("coordinator head does not match the target profile")

    corpus_content = files.read_bound(profile.scenario_corpus)
    corpus = parse_scenario_corpus(corpus_content)
    _validate_corpus(profile, corpus)
    dynamic_policy_content = files.read_bound(profile.dynamic_policy)
    dynamic_policy = _admit_dynamic_policy(dynamic_policy_content, profile)
    for binding in profile.target_bindings:
        files.read_bound(binding, opaque=True)

    try:
        require_directory(target, profile.target_artifacts_directory)
    except GitSourceError as error:
        raise ConsumerContractSourceError(str(error)) from error
    lock = _target_lock(
        files.read(
            _join_relative(profile.target_artifacts_directory, _LOCK_FILENAME),
        )
    )
    _require_lock_commit(lock, profile.expected_coordinator_commit)
    source_path, source_digest = _lock_source(lock)
    source_relative = _join_relative(profile.target_artifacts_directory, source_path)
    source_content = files.read(source_relative)
    if hashlib.sha256(source_content).hexdigest() != source_digest:
        raise ConsumerContractSourceError("target artifact source digest does not match lock")
    try:
        target_artifacts_source = parse_target_artifacts_source(source_content)
        if target_artifacts_source.generator.version != profile.expected_coordinator_commit:
            raise ConsumerContractSourceError("target artifact generator revision is stale")
        rendered = render_target_artifacts(target_artifacts_source)
    except (TargetArtifactsAdmissionError, TargetArtifactsRenderError) as error:
        raise ConsumerContractSourceError("target artifact source is not admitted") from error
    lock_artifacts = _lock_artifacts(lock)
    expected_filenames = tuple(filename for filename, _ in rendered.by_filename())
    if tuple(path for path, _ in lock_artifacts) != expected_filenames:
        raise ConsumerContractSourceError("target artifact lock inventory is incomplete")
    for (filename, expected), (_, locked_digest) in zip(
        rendered.by_filename(),
        lock_artifacts,
        strict=True,
    ):
        relative = _join_relative(profile.target_artifacts_directory, filename)
        current = files.read(relative)
        digest = hashlib.sha256(current).hexdigest()
        if current != expected or digest != locked_digest:
            raise ConsumerContractSourceError("generated target artifact bytes have drifted")

    registry_relative = _join_relative(
        profile.target_artifacts_directory,
        "execution-registry.v1.json",
    )
    registry = parse_target_execution_registry(files.read(registry_relative))
    if (
        registry is None
        or registry.generator_version != profile.expected_coordinator_commit
        or registry.workflow(profile.workflow_path) is None
    ):
        raise ConsumerContractSourceError("target execution registry is not profile-admitted")
    adapter_contents = tuple(
        (adapter_binding, files.read(adapter_binding.path))
        for adapter_binding in registry.adapter_files
    )
    for adapter_binding, content in adapter_contents:
        if digest_adapter_file(content) != adapter_binding.sha256:
            raise ConsumerContractSourceError("target adapter bytes do not match registry")

    entries = files.seal()
    contract_files = files.relevant_files()
    try:
        target_source = git_snapshot(target, relevant_files=files.relevant_files())
    except GitSourceError as error:
        raise ConsumerContractSourceError(str(error)) from error
    manifest_sha256 = _manifest_digest(entries)
    source_epoch_id = hash_object(
        {
            "coordinator": coordinator_source.to_mapping(),
            "target": target_source.to_mapping(),
            "manifestSha256": manifest_sha256,
        }
    )
    execution_authority = _execution_authority(target_source, source_epoch_id)
    workflow_contents = tuple(
        (binding.path, content) for binding, content in adapter_contents if binding.is_workflow
    )
    if not admits_target_workflow_authority(
        registry,
        workflow_contents=workflow_contents,
        revision_sha=execution_authority.coordinate,
    ):
        raise ConsumerContractSourceError(
            "target workflow semantics do not match the execution registry"
        )
    return PreparedConsumerContract(
        coordinator_root=coordinator,
        coordinator_package_root=coordinator_package_root,
        target_root=target,
        profile_path=relative_profile,
        profile=profile,
        corpus=corpus,
        coordinator_source=coordinator_source,
        target_source=target_source,
        manifest_entries=entries,
        contract_files=contract_files,
        manifest_sha256=manifest_sha256,
        source_epoch_id=source_epoch_id,
        dynamic_policy=dynamic_policy,
        target_artifacts_source=target_artifacts_source,
        target_registry=registry,
    )


def _execution_authority(
    target_source: GitSourceSnapshot,
    source_epoch_id: str,
) -> LabExecutionAuthority:
    if target_source.source_kind == "commit":
        return LabExecutionAuthority("target-git-commit", target_source.head)
    coordinate = hash_object(
        {
            "kind": "consumer-contract-lab-worktree-authority",
            "sourceEpochId": source_epoch_id,
        }
    )[:40]
    return LabExecutionAuthority("sealed-worktree-manifest", coordinate)


def assert_consumer_contract_unchanged(contract: PreparedConsumerContract) -> None:
    if type(contract) is not PreparedConsumerContract:
        raise TypeError("source-epoch verification requires an exact prepared contract")
    paths = tuple(item.path for item in contract.manifest_entries)
    files = _ContractFiles(contract.target_root)
    for path in paths:
        files.read(path, opaque=True)
    entries = files.seal()
    if entries != contract.manifest_entries:
        raise ConsumerContractSourceError("target contract bytes changed during execution")
    try:
        coordinator_source = coordinator_package_snapshot(
            contract.coordinator_root,
            _COORDINATOR_PACKAGE_PATH,
            contract.coordinator_source.head,
            contract.coordinator_package_root,
        )
        target_source = git_snapshot(
            contract.target_root,
            relevant_files=files.relevant_files(),
        )
    except GitSourceError as error:
        raise ConsumerContractSourceError(str(error)) from error
    if coordinator_source != contract.coordinator_source:
        raise ConsumerContractSourceError("coordinator source changed during laboratory execution")
    if target_source != contract.target_source:
        raise ConsumerContractSourceError("target source changed during laboratory execution")


def _validate_corpus(profile: ConsumerLabProfile, corpus: ScenarioCorpus) -> None:
    if corpus.profile_id != profile.profile_id:
        raise ConsumerContractSourceError("scenario corpus does not belong to the profile")
    scenario_events = {scenario.event_name for scenario in corpus.scenarios}
    if scenario_events != set(profile.event_surface):
        raise ConsumerContractSourceError("scenario corpus does not cover the exact event surface")
    modes = {scenario.expected_outcome.mode for scenario in corpus.scenarios}
    if modes != {"fallback", "selected"}:
        raise ConsumerContractSourceError("scenario corpus requires selected and fallback routes")


def _admit_dynamic_policy(
    content: bytes,
    profile: ConsumerLabProfile,
) -> dict[str, object]:
    try:
        value = load_strict_json(content, max_bytes=_MAX_FILE_BYTES)
        if canonical_json(value) + b"\n" != content:
            raise ValueError("dynamic policy is not canonical")
        root = _exact_object(
            value,
            {"schemaVersion", "generator", "workflowPath", "dynamicCi"},
        )
        generator = _exact_object(root["generator"], {"id", "version"})
        if (
            root["schemaVersion"] != _POLICY_SCHEMA
            or root["workflowPath"] != profile.workflow_path
            or generator["version"] != profile.expected_coordinator_commit
            or type(root["dynamicCi"]) is not dict
        ):
            raise ValueError("dynamic policy identity is stale")
        return root
    except (KeyError, StrictJsonError, TypeError, ValueError) as error:
        raise ConsumerContractSourceError("dynamic policy is not admitted") from error


def _target_lock(content: bytes) -> dict[str, object]:
    try:
        value = load_strict_json(content, max_bytes=131_072)
        if canonical_json(value) + b"\n" != content:
            raise ValueError("target artifact lock is not canonical")
        root = _exact_object(
            value,
            {"schemaVersion", "coordinatorCommit", "source", "artifacts"},
        )
        if root["schemaVersion"] != _LOCK_SCHEMA:
            raise ValueError("target artifact lock schema is unsupported")
        return root
    except (StrictJsonError, TypeError, ValueError) as error:
        raise ConsumerContractSourceError("target artifact lock is invalid") from error


def _require_lock_commit(lock: dict[str, object], expected: str) -> None:
    if lock["coordinatorCommit"] != expected:
        raise ConsumerContractSourceError("target artifact lock uses another coordinator commit")


def _lock_source(lock: dict[str, object]) -> tuple[str, str]:
    source = _exact_object(lock["source"], {"path", "sha256"})
    return _simple_filename(source["path"]), _sha256(source["sha256"])


def _lock_artifacts(lock: dict[str, object]) -> tuple[tuple[str, str], ...]:
    raw = lock["artifacts"]
    if type(raw) is not list or not raw:
        raise ConsumerContractSourceError("target artifact lock inventory is empty")
    result = tuple(
        (
            _simple_filename(record["path"]),
            _sha256(record["sha256"]),
        )
        for item in raw
        for record in (_exact_object(item, {"path", "sha256"}),)
    )
    paths = tuple(path for path, _ in result)
    if len(paths) != len(set(paths)):
        raise ConsumerContractSourceError("target artifact lock paths are duplicated")
    return result


def _manifest(root: Path, paths: tuple[str, ...]) -> tuple[ManifestEntry, ...]:
    canonical_paths = tuple(sorted(set(paths), key=utf16_sort_key))
    return tuple(
        ManifestEntry(
            path=path,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )
        for path in canonical_paths
        for content in (_read_regular(root, path, opaque=True),)
    )


def _manifest_digest(entries: tuple[ManifestEntry, ...]) -> str:
    return hash_object({"files": [entry.to_mapping() for entry in entries]})


def _manifest_entries(files: tuple[tuple[str, bytes], ...]) -> tuple[ManifestEntry, ...]:
    return tuple(
        ManifestEntry(
            path=path,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )
        for path, content in files
    )


def _read_regular(root: Path, relative: str, *, opaque: bool = False) -> bytes:
    try:
        reader = read_opaque_regular if opaque else read_regular
        return reader(root, relative)
    except GitSourceError as error:
        raise ConsumerContractSourceError(str(error)) from error


def _join_relative(directory: str, filename: str) -> str:
    value = f"{directory.rstrip('/')}/{filename}"
    if not is_safe_relative_path(value):
        raise ConsumerContractSourceError("target artifact path is unsafe")
    return value


def _simple_filename(value: object) -> str:
    if (
        type(value) is not str
        or not value
        or "/" in value
        or "\\" in value
        or not is_safe_relative_path(value)
    ):
        raise ConsumerContractSourceError("target artifact lock filename is invalid")
    return value


def _sha256(value: object) -> str:
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ConsumerContractSourceError("target artifact lock digest is invalid")
    return value


def _exact_object(value: object, fields: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != fields:
        raise ConsumerContractSourceError("consumer contract object shape is invalid")
    return cast(dict[str, object], value)

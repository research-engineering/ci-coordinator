"""Provider witness for reversible Compose Watch synchronization."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import secrets
import socket
import sys
import time
import tomllib
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager, ExitStack, contextmanager, suppress
from dataclasses import dataclass
from io import StringIO
from pathlib import Path
from typing import Final, Protocol
from urllib.parse import urlsplit

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.events import (
    AliasEvent,
    CollectionEndEvent,
    CollectionStartEvent,
    DocumentEndEvent,
    DocumentStartEvent,
)

from scripts.bounded_process import CommandResult, InteractiveResult, spawn
from scripts.dev_environment.compose import (
    ComposeError,
    LocalEndpoints,
    ProviderCommandFailed,
    ProviderInvocation,
    ServiceAbsent,
    ServiceRuntimeIdentity,
    ServiceStatus,
)
from scripts.dev_environment.identity import InstanceIdentity
from scripts.dev_environment.watch_session import (
    CancelResult,
    WatchClientContract,
    cancel_watch,
    owned_watch_session,
)

_PROBE_NAME: Final = "ci-coordinator-watch-probe.txt"
_POLL_INTERVAL_SECONDS: Final = 0.2
_TRANSITION_TIMEOUT_SECONDS: Final = 30.0
_WATCH_GRACE_SECONDS: Final = 10.0
_WATCH_KILL_SECONDS: Final = 5.0
# CI_COORDINATOR_* is a closed runtime-settings namespace, not fixture metadata.
_IMAGE_WITNESS_ENV: Final = "DEVEX_IMAGE_WITNESS"
_ENTRYPOINT_WITNESS_ENV: Final = "DEVEX_ENTRYPOINT_WITNESS"


class WatchError(RuntimeError):
    """A stable source-watch witness failure."""

    def __init__(
        self,
        message: str,
        *,
        process: InteractiveResult | None = None,
        cancellation: CancelResult | None = None,
    ) -> None:
        super().__init__(message)
        self.process = process
        self.cancellation = cancellation


class ModelFeedbackError(WatchError):
    """Keep the first model failure when ordinary-mode restoration also fails."""

    def __init__(self, phase: str, error: BaseException) -> None:
        super().__init__(
            f"compose-model/{phase}: {_model_error_detail(error)}",
            process=error.process if isinstance(error, WatchError) else None,
            cancellation=error.cancellation if isinstance(error, WatchError) else None,
        )
        self.phase = phase
        self.primary_error = error
        self.restoration_error: ModelFeedbackError | None = None

    def __str__(self) -> str:
        message = super().__str__()
        if self.restoration_error is not None:
            message += f"; restoration_failure={self.restoration_error}"
        return message


def _model_error_detail(error: BaseException) -> str:
    # Only existing closed diagnostics may contribute text. OS/parser errors
    # can contain source values; retain their objects without projecting them.
    if isinstance(error, (ComposeError, WatchError)):
        return str(error)[:2_048]
    return type(error).__name__


class WatchProcess(Protocol):
    def poll(self) -> int | None: ...

    def stop(self) -> None: ...


class WatchProject(Protocol):
    @property
    def repo_root(self) -> Path: ...

    def watch_invocation(self) -> ProviderInvocation: ...

    def service_runtime_identity(self, service: str) -> ServiceRuntimeIdentity: ...

    def service_file(self, service: str, path: str) -> bytes | None: ...


class FeedbackProject(WatchProject, Protocol):
    def endpoints(self) -> LocalEndpoints: ...

    def diagnostic_status(self) -> tuple[ServiceStatus, ...]: ...

    def observation_budget(self, seconds: float = 30.0) -> AbstractContextManager[None]: ...

    def assert_runtime_boundary(
        self, service: str, *, expected_uid: int, protected_path: str | None
    ) -> None: ...


class MaterialFeedbackProject(FeedbackProject, Protocol):
    def validate(self) -> None: ...


@dataclass(frozen=True, slots=True)
class MaterialInputClass:
    name: str
    inputs: tuple[str, ...]
    mechanism: str
    oracle: str


# A watch action is shared only where Compose dispatches the same action.
# Consumption remains separately witnessed: uv graph, pnpm graph/patch,
# image environment, executable entrypoint, build context, and service model.
MATERIAL_INPUT_CLASSES: Final = (
    MaterialInputClass(
        "backend-source",
        ("backend/src",),
        "sync+restart",
        "create/update/delete projection, runtime restart, restored HTTP response",
    ),
    MaterialInputClass(
        "frontend-source",
        ("frontend/src", "frontend/index.html"),
        "sync",
        "rendered module/style updates without document or runtime replacement",
    ),
    MaterialInputClass(
        "frontend-config",
        (
            "frontend/vite.config.ts",
            "frontend/src/api/development",
            "frontend/src/api/workbench/limits.ts",
        ),
        "sync+restart",
        "direct/transitive Vite configuration effect and runtime restart",
    ),
    MaterialInputClass(
        "frontend-compiler-config",
        ("frontend/tsconfig.json", "frontend/tsconfig.app.json", "frontend/tsconfig.node.json"),
        "sync+restart",
        "tsconfig-driven class field define/assign/define browser behavior",
    ),
    MaterialInputClass(
        "backend-dependencies",
        ("backend/pyproject.toml", "backend/uv.lock"),
        "rebuild",
        "pinned third-party distribution absent/present/absent in replaced backend",
    ),
    MaterialInputClass(
        "frontend-dependencies",
        (
            "package.json",
            "frontend/package.json",
            "pnpm-lock.yaml",
            "pnpm-workspace.yaml",
            "patches",
        ),
        "rebuild",
        "new direct alias and workspace-selected patched third-party function",
    ),
    MaterialInputClass(
        "image-recipe",
        ("docker/development/backend.Dockerfile", "frontend/Dockerfile.dev"),
        "rebuild",
        "distinct image environment values consumed by both running services",
    ),
    MaterialInputClass(
        "entrypoint",
        ("docker/development/secret-entrypoint.sh",),
        "rebuild",
        "entrypoint-exported runtime value with preserved UID and private secret boundary",
    ),
    MaterialInputClass(
        "build-context",
        (".dockerignore",),
        "rebuild",
        "COPY inputs excluded and restored in replaced backend and frontend images",
    ),
    MaterialInputClass(
        "compose-model",
        ("compose.yaml",),
        "stop/validate/up",
        "environment response, exact published port, stricter health model, "
        "invalid-model rejection",
    ),
)

type ProcessFactory = Callable[[ProviderInvocation], WatchProcess]
type Wait = Callable[[float], None]


def verify_source_watch(
    project: WatchProject,
    *,
    identity: InstanceIdentity | None = None,
    process_factory: ProcessFactory | None = None,
    wait: Wait = time.sleep,
    effect_verifier: Callable[[WatchProcess], None] | None = None,
) -> None:
    backend_host = project.repo_root / "backend" / "src" / _PROBE_NAME
    frontend_host = project.repo_root / "frontend" / "src" / _PROBE_NAME
    backend_container = f"/workspace/backend/src/{_PROBE_NAME}"
    frontend_container = f"/workspace/frontend/src/{_PROBE_NAME}"
    probes = (
        ("backend", backend_host, backend_container),
        ("frontend", frontend_host, frontend_container),
    )
    if any(host.exists() or host.is_symlink() for _, host, _ in probes):
        raise WatchError("source watch probe path is already allocated")

    if process_factory is None:
        if identity is None:
            raise WatchError("native feedback requires an owned instance identity")
        process: WatchProcess = _SubprocessWatch(project, identity)
    else:
        process = process_factory(project.watch_invocation())
    try:
        _source_transitions(project, probes, process=process, wait=wait)
        if effect_verifier is not None:
            effect_verifier(process)
    finally:
        for _, host, _ in probes:
            with suppress(FileNotFoundError):
                host.unlink()
        process.stop()


def _source_transitions(
    project: WatchProject,
    probes: Sequence[tuple[str, Path, str]],
    *,
    process: WatchProcess,
    wait: Wait,
) -> None:
    initial = project.service_runtime_identity("backend")
    frontend = project.service_runtime_identity("frontend")
    # Retry a changing sentinel until both services acknowledge it; elapsed startup
    # time and Compose's pre-watcher log line cannot establish watch readiness.
    deadline = time.monotonic() + _TRANSITION_TIMEOUT_SECONDS
    attempt = 0
    next_write = 0.0
    expected = b""
    while True:
        _require_running(process)
        now = time.monotonic()
        if now >= next_write:
            attempt += 1
            expected = f"barrier-{attempt}\n".encode()
            for _, host, _ in probes:
                host.write_bytes(expected)
            next_write = now + 2.0
        if _projected(project, probes, expected):
            break
        if time.monotonic() >= deadline:
            raise WatchError("source watch startup acknowledgement timed out")
        wait(_POLL_INTERVAL_SECONDS)
    initial = _await_runtime_transition(project, initial, process=process, wait=wait)
    for expected_value in (b"updated\n", None):
        for _, host, _ in probes:
            if expected_value is None:
                host.unlink()
            else:
                host.write_bytes(expected_value)
        _await_projection(project, probes, expected_value, process=process, wait=wait)
        initial = _await_runtime_transition(project, initial, process=process, wait=wait)
    if project.service_runtime_identity("frontend") != frontend:
        raise WatchError("frontend source sync unexpectedly restarted the runtime")


def _await_projection(
    project: WatchProject,
    probes: Sequence[tuple[str, Path, str]],
    expected: bytes | None,
    *,
    process: WatchProcess,
    wait: Wait,
) -> None:
    deadline = time.monotonic() + _TRANSITION_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        _require_running(process)
        if _projected(project, probes, expected):
            return
        wait(_POLL_INTERVAL_SECONDS)
    raise WatchError("source watch transition timed out")


def _projected(
    project: WatchProject,
    probes: Sequence[tuple[str, Path, str]],
    expected: bytes | None,
) -> bool:
    try:
        return all(project.service_file(service, path) == expected for service, _, path in probes)
    except (ServiceAbsent, ProviderCommandFailed):
        return False


def _await_runtime_transition(
    project: WatchProject,
    initial: ServiceRuntimeIdentity,
    *,
    process: WatchProcess,
    wait: Wait,
) -> ServiceRuntimeIdentity:
    deadline = time.monotonic() + _TRANSITION_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        _require_running(process)
        try:
            current = project.service_runtime_identity("backend")
            if current.container_id != initial.container_id:
                raise WatchError("backend source sync unexpectedly recreated the container")
            if current.started_at != initial.started_at:
                return current
        except (ServiceAbsent, ProviderCommandFailed):
            pass
        wait(_POLL_INTERVAL_SECONDS)
    raise WatchError("backend source watch did not restart the runtime")


def verify_feedback_effects(
    project: FeedbackProject,
    *,
    identity: InstanceIdentity | None = None,
    browser_runtime_root: Path | None = None,
    effect_verifier: Callable[[WatchProcess], None] | None = None,
) -> None:
    """Run effects in a caller-owned disposable checkout with a prepared browser.

    The optional runtime root reuses read-only dependencies from an explicitly
    trusted CI checkout at the same source epoch, admitted by the caller. No
    dependency installation occurs here; all fixture writes target project.

    This is representative effect evidence, not qualification of every lock,
    patch, platform, model reconciliation, or in-flight daemon transaction.
    """
    _admit_frontend_root(project.repo_root)
    runtime_root = project.repo_root if browser_runtime_root is None else browser_runtime_root
    runtime_frontend = _admit_frontend_root(runtime_root)
    if not (runtime_frontend / "node_modules/@playwright/test/package.json").is_file():
        raise WatchError("feedback browser dependencies must be explicitly prepared")
    prepared = spawn(
        "node",
        ["--input-type=module", "-e", _BROWSER_PREFLIGHT],
        cwd=runtime_frontend,
        max_buffer=65_536,
        timeout_seconds=30,
    )
    if (
        prepared.status != 0
        or prepared.failure_kind is not None
        or prepared.stdout.strip() != "browser_runtime_available"
    ):
        raise WatchError("feedback Chromium must be explicitly prepared")

    def verify(process: WatchProcess) -> None:
        with _watch_phase("frontend-feedback"):
            _verify_frontend_effects(project, process, browser_runtime_root=runtime_root)
        with _watch_phase("backend-feedback"):
            _verify_backend_effects(project, process)
        if effect_verifier is not None:
            effect_verifier(process)

    with _watch_phase("source-watch"):
        verify_source_watch(project, identity=identity, effect_verifier=verify)
    _await_ordinary_health(project)


def verify_material_feedback(
    project: MaterialFeedbackProject,
    *,
    identity: InstanceIdentity,
    browser_runtime_root: Path,
    preservation_check: Callable[[], None],
    reconcile: Callable[[], LocalEndpoints],
) -> tuple[str, ...]:
    """Run the complete material cohort in an isolated source checkout.

    The caller fixes the source epoch and seeds the preservation oracle for
    database data, credential identity and a separate running root. reconcile
    owns a fresh finite mutation lease and the explicit stop/validate/up route.
    No host dependencies are installed or repaired by this verifier.
    """
    preservation_check()

    def material(process: WatchProcess) -> None:
        preservation_check()
        with _watch_phase("frontend-compiler"):
            _verify_frontend_compiler_effect(
                project, process, browser_runtime_root=browser_runtime_root
            )
        preservation_check()
        for name, verify in (
            ("backend-dependencies", _verify_backend_dependency_effect),
            ("frontend-dependencies", _verify_frontend_dependency_effect),
            ("image-recipe/entrypoint", _verify_image_and_entrypoint_effects),
            ("build-context", _verify_build_context_effect),
        ):
            with _watch_phase(name):
                verify(project, process)
            preservation_check()

    verify_feedback_effects(
        project,
        identity=identity,
        browser_runtime_root=browser_runtime_root,
        effect_verifier=material,
    )
    preservation_check()
    with _watch_phase("compose-model"):
        _verify_compose_model_effect(
            project, reconcile=reconcile, preservation_check=preservation_check
        )
    return tuple(item.name for item in MATERIAL_INPUT_CLASSES)


def _yaml_bytes(value: object) -> bytes:
    output = StringIO()
    YAML(typ="safe").dump(value, output)
    return output.getvalue().encode()


def _frontend_lock_document(source: bytes) -> tuple[bytes, dict[str, object]]:
    """Preserve pnpm 12's bootstrap document while editing only the project graph."""
    if not source or len(source) > 16 * 1024 * 1024:
        raise WatchError("pnpm lock stream exceeds its source bound or is empty")
    try:
        text = source.decode("utf-8", errors="strict")
        starts: list[int] = []
        depth = 0
        for count, event in enumerate(YAML(typ="safe").parse(text), start=1):
            if isinstance(event, CollectionStartEvent):
                depth += 1
            elif isinstance(event, CollectionEndEvent):
                depth -= 1
            if count > 250_000 or depth > 128 or isinstance(event, AliasEvent):
                raise WatchError("pnpm lock stream has aliases or exceeds its structure bound")
            if isinstance(event, DocumentStartEvent):
                index = event.start_mark.index
                if event.version or event.tags or text[index : index + 4] != "---\n":
                    raise WatchError("pnpm lock stream has unsupported document framing")
                starts.append(index)
                if len(starts) > 2:
                    raise WatchError(
                        "pnpm lock stream must contain bootstrap and project documents"
                    )
            elif isinstance(event, DocumentEndEvent) and event.explicit:
                raise WatchError("pnpm lock stream has unsupported document framing")
        if len(starts) != 2 or starts[0] != 0:
            raise WatchError("pnpm lock stream must contain bootstrap and project documents")
        bootstrap, project = YAML(typ="safe").load_all(text)
    except (UnicodeError, YAMLError) as error:
        raise WatchError("pnpm lock stream is not unambiguous UTF-8 YAML") from error
    if (
        not isinstance(bootstrap, dict)
        or set(bootstrap) != {"lockfileVersion", "importers", "packages", "snapshots"}
        or bootstrap["lockfileVersion"] != "9.0"
        or not isinstance(bootstrap["importers"], dict)
        or set(bootstrap["importers"]) != {"."}
        or not isinstance(bootstrap["importers"]["."], dict)
        or set(bootstrap["importers"]["."]) != {"configDependencies", "packageManagerDependencies"}
        or not isinstance(bootstrap["importers"]["."]["configDependencies"], dict)
        or not isinstance(bootstrap["importers"]["."]["packageManagerDependencies"], dict)
        or "pnpm" not in bootstrap["importers"]["."]["packageManagerDependencies"]
        or any(
            not isinstance(dependency, dict)
            or set(dependency) != {"specifier", "version"}
            or any(not isinstance(value, str) or not value for value in dependency.values())
            for group in bootstrap["importers"]["."].values()
            for dependency in group.values()
        )
        or not isinstance(project, dict)
        or project.get("lockfileVersion") != "9.0"
        or not isinstance(project.get("importers"), dict)
        or not isinstance(project["importers"].get("frontend"), dict)
        or any(
            not isinstance(importer, dict)
            or {"configDependencies", "packageManagerDependencies"}.intersection(importer)
            for importer in project["importers"].values()
        )
        or any(
            not isinstance(document.get(key), dict)
            for document in (bootstrap, project)
            for key in ("packages", "snapshots")
        )
    ):
        raise WatchError("pnpm lock stream has ambiguous bootstrap or project roles")
    # Parser offsets are character indices. Encoding the untouched prefix keeps
    # bootstrap bytes and the explicit second-document marker exactly as read.
    return text[: starts[1] + 4].encode("utf-8"), project


@contextmanager
def _watch_phase(name: str) -> Iterator[None]:
    started = time.monotonic_ns()
    outcome = "failed"
    try:
        yield
        outcome = "passed"
    except (ComposeError, WatchError) as error:
        raise WatchError(
            f"{name}: {_model_error_detail(error)}",
            process=error.process if isinstance(error, WatchError) else None,
            cancellation=error.cancellation if isinstance(error, WatchError) else None,
        ) from error
    finally:
        phase = (
            name
            if name
            in (
                "frontend-feedback",
                "backend-feedback",
                "source-watch",
                "frontend-compiler",
                "backend-dependencies",
                "frontend-dependencies",
                "image-recipe/entrypoint",
                "build-context",
                "compose-model",
            )
            else "other"
        )
        with suppress(OSError):
            print(
                json.dumps(
                    {
                        "code": "material_phase_timing",
                        "phase": phase,
                        "outcome": outcome,
                        "elapsedMs": min(
                            3_600_000, max(0, (time.monotonic_ns() - started) // 1_000_000)
                        ),
                    },
                    separators=(",", ":"),
                ),
                file=sys.stderr,
            )


def _header_source(original: bytes, expression: str, *, imports: str = "") -> bytes:
    marker = b"    server: {\n"
    if original.count(marker) != 1:
        raise WatchError("Vite configuration witness anchor changed")
    return imports.encode() + original.replace(
        marker, marker + f'      headers: {{"X-Coordinator-Watch": {expression}}},\n'.encode()
    )


def _health_source(original: bytes, expression: str, *, imports: str) -> bytes:
    marker = b'    state: Literal["alive"] = "alive"'
    anchor = b"from dataclasses import dataclass"
    if original.count(marker) != 1 or original.count(anchor) != 1:
        raise WatchError("backend response witness anchor changed")
    return original.replace(anchor, anchor + b"\n" + imports.encode()).replace(
        marker, f"    state: str = {expression}".encode()
    )


def _recreated(project: WatchProject, service: str, before: ServiceRuntimeIdentity) -> bool:
    return project.service_runtime_identity(service).container_id != before.container_id


def _await_rebuild_baseline(
    project: FeedbackProject, process: WatchProcess
) -> dict[str, ServiceRuntimeIdentity]:
    deadline = time.monotonic() + _TRANSITION_TIMEOUT_SECONDS

    def observed() -> dict[str, ServiceRuntimeIdentity] | None:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        with project.observation_budget(min(30, remaining)):
            return {
                service: project.service_runtime_identity(service)
                for service in ("backend", "frontend")
            }

    return _await_effect(observed, process=process, timeout_seconds=_TRANSITION_TIMEOUT_SECONDS)


def _await_runtime_effect(
    project: WatchProject,
    observed: Callable[[], bool],
    *,
    before: Mapping[str, ServiceRuntimeIdentity],
    process: WatchProcess,
    recreate: bool = False,
    timeout_seconds: float = 45,
) -> dict[str, ServiceRuntimeIdentity]:
    # Capture identities in the successful effect round. A later unguarded
    # Compose ps can observe the next watch batch's stop/recreate gap.
    def ready() -> dict[str, ServiceRuntimeIdentity] | None:
        if not observed():
            return None
        current = {service: project.service_runtime_identity(service) for service in before}
        for service, previous in before.items():
            if recreate:
                if current[service].container_id == previous.container_id:
                    return None
            else:
                if current[service].container_id != previous.container_id:
                    raise WatchError(
                        "configuration/source effect unexpectedly recreated the container"
                    )
                if current[service].started_at == previous.started_at:
                    return None
        return current

    return _await_effect(ready, process=process, timeout_seconds=timeout_seconds)


def _verify_frontend_compiler_effect(
    project: FeedbackProject, process: WatchProcess, *, browser_runtime_root: Path
) -> None:
    root = project.repo_root
    source = root / "frontend/src/ci-coordinator-compiler-probe.ts"
    config = root / "frontend/tsconfig.app.json"
    document = root / "frontend/index.html"
    configuration = json.loads(config.read_bytes())
    configuration["compilerOptions"]["useDefineForClassFields"] = True
    defining = (json.dumps(configuration, indent=2) + "\n").encode()
    configuration["compilerOptions"]["useDefineForClassFields"] = False
    assigning = (json.dumps(configuration, indent=2) + "\n").encode()
    code = (
        b'let observed = "define";\nclass Parent {}\n'
        b'Object.defineProperty(Parent.prototype, "value", {set() { observed = "assign"; }});\n'
        b"class Child extends Parent { value = 7; }\nnew Child();\n"
        b'document.querySelector("#watch-value")!.textContent = observed;\n'
    )
    html = (
        b'<!doctype html><title>compiler-probe</title><p id="watch-value"></p>'
        b'<script type="module" src="/src/ci-coordinator-compiler-probe.ts"></script>'
    )

    def observed(expected: str, previous: ServiceRuntimeIdentity) -> ServiceRuntimeIdentity:
        _await_effect(
            lambda: b"compiler-probe" in _http(project.endpoints().ui, "/")[1],
            process=process,
        )
        deadline = time.monotonic() + 45
        result = spawn(
            "node",
            ["--input-type=module", "-e", _COMPILER_WITNESS, project.endpoints().ui, expected],
            cwd=browser_runtime_root / "frontend",
            max_buffer=65_536,
            timeout_seconds=45,
        )
        if (
            result.status != 0
            or result.failure_kind is not None
            or result.stdout.strip() != expected
        ):
            raise WatchError("tsconfig compiler effect was not observed in the browser")
        return _await_runtime_effect(
            project,
            lambda: True,
            before={"frontend": previous},
            process=process,
            timeout_seconds=max(0, deadline - time.monotonic()),
        )["frontend"]

    initial = project.service_runtime_identity("frontend")
    with _created(source, code), _replaced(document, html), _replaced(config, defining):
        _await_projection(
            project,
            (("frontend", source, "/workspace/frontend/src/" + source.name),),
            code,
            process=process,
            wait=time.sleep,
        )
        _await_runtime_effect(
            project,
            lambda: (
                project.service_file("frontend", "/workspace/frontend/tsconfig.app.json")
                == defining
            ),
            before={"frontend": initial},
            process=process,
        )
        before = observed("define", initial)
        with _replaced(config, assigning):
            _await_runtime_effect(
                project,
                lambda: (
                    project.service_file("frontend", "/workspace/frontend/tsconfig.app.json")
                    == assigning
                ),
                before={"frontend": before},
                process=process,
            )
            assigned = observed("assign", before)
        _await_runtime_effect(
            project,
            lambda: (
                project.service_file("frontend", "/workspace/frontend/tsconfig.app.json")
                == defining
            ),
            before={"frontend": assigned},
            process=process,
        )
        observed("define", assigned)
    _await_effect(
        lambda: b"CI Coordinator" in _http(project.endpoints().ui, "/")[1], process=process
    )


def _verify_backend_dependency_effect(project: FeedbackProject, process: WatchProcess) -> None:
    root = project.repo_root
    source = root / "backend/src/ci_coordinator/observability/health.py"
    manifest, lock = root / "backend/pyproject.toml", root / "backend/uv.lock"
    manifest_bytes, lock_bytes = manifest.read_bytes(), lock.read_bytes()
    package_anchor = b'[[package]]\nname = "ci-coordinator-backend"\n'
    if lock_bytes.count(package_anchor) != 1:
        raise WatchError("uv package graph witness anchor changed")
    start = lock_bytes.index(package_anchor)
    end = lock_bytes.find(b"\n[[package]]", start + len(package_anchor))
    end = len(lock_bytes) if end < 0 else end
    package = lock_bytes[start:end]
    if (
        manifest_bytes.count(b"dependencies = [\n") != 1
        or package.count(b"dependencies = [\n") != 1
        or package.count(b"requires-dist = [\n") != 1
    ):
        raise WatchError("uv package graph witness shape changed")
    candidate_manifest = manifest_bytes.replace(
        b"dependencies = [\n", b'dependencies = [\n    "debugpy==1.8.22",\n', 1
    )
    candidate_package = package.replace(
        b"dependencies = [\n", b'dependencies = [\n    { name = "debugpy" },\n', 1
    ).replace(
        b"requires-dist = [\n",
        b'requires-dist = [\n    { name = "debugpy", specifier = "==1.8.22" },\n',
        1,
    )
    candidate_lock = lock_bytes[:start] + candidate_package + lock_bytes[end:]
    candidate_source = _health_source(
        source.read_bytes(),
        '"absent" if find_spec("debugpy") is None else version("debugpy")',
        imports="from importlib.util import find_spec\nfrom importlib.metadata import version",
    )
    with _replaced(source, candidate_source):
        before = _await_effect(
            lambda: (
                project.service_runtime_identity("backend")
                if _health_status(project) == "absent"
                else None
            ),
            process=process,
        )
        with _replaced(manifest, candidate_manifest), _replaced(lock, candidate_lock):
            installed = _await_runtime_effect(
                project,
                lambda: _health_status(project) == "1.8.22",
                before={"backend": before},
                recreate=True,
                process=process,
                timeout_seconds=300,
            )
        _await_runtime_effect(
            project,
            lambda: _health_status(project) == "absent",
            before=installed,
            recreate=True,
            process=process,
            timeout_seconds=300,
        )
    _await_effect(lambda: _health_status(project) == "alive", process=process)


def _verify_frontend_dependency_effect(project: FeedbackProject, process: WatchProcess) -> None:
    root = project.repo_root
    manifest = root / "frontend/package.json"
    workspace, lock = root / "pnpm-workspace.yaml", root / "pnpm-lock.yaml"
    config = root / "frontend/vite.config.ts"
    original_patch = (root / "patches/minimatch@5.1.9.patch").read_bytes().replace(b"\r\n", b"\n")
    token = secrets.token_hex(12)
    patch = original_patch.replace(b"@@ -18,2 +18,2 @@", b"@@ -18,2 +18,3 @@")
    if patch == original_patch:
        raise WatchError("pnpm patch witness anchor changed")
    patch += f"+minimatch.coordinatorWatchProbe = () => '{token}'\n".encode()
    old_hash, new_hash = (
        hashlib.sha256(original_patch).hexdigest(),
        hashlib.sha256(patch).hexdigest(),
    )
    workspace_data = YAML(typ="safe").load(workspace)
    lock_prefix, lock_data = _frontend_lock_document(lock.read_bytes())
    patched_dependencies = lock_data.get("patchedDependencies")
    if (
        not isinstance(patched_dependencies, dict)
        or patched_dependencies.get("minimatch@5.1.9") != old_hash
    ):
        raise WatchError("pnpm patch hash contract changed")
    patch_path = "patches/ci-coordinator-watch-minimatch.patch"
    workspace_data["patchedDependencies"]["minimatch@5.1.9"] = patch_path
    # pnpm hashes UTF-8 patch text after CRLF normalization and records that
    # SHA-256 in both the selector and snapshot key (crypto/hash + calcPatchHashes).
    lock_data = json.loads(json.dumps(lock_data).replace(old_hash, new_hash))
    alias = "coordinator-watch-dependency"
    manifest_data = json.loads(manifest.read_bytes())
    if alias in manifest_data["dependencies"]:
        raise WatchError("pnpm alias witness is already allocated")
    manifest_data["dependencies"][alias] = "npm:minimatch@5.1.9"
    lock_data["importers"]["frontend"]["dependencies"][alias] = {
        "specifier": "npm:minimatch@5.1.9",
        "version": f"minimatch@5.1.9(patch_hash={new_hash})",
    }
    candidate_config = _header_source(
        config.read_bytes(),
        'watchDependency.coordinatorWatchProbe() + ":" + '
        'watchDependency.braceExpand("{left,right}").join("|")',
        imports=f'import watchDependency from "{alias}";\n',
    )
    before = project.service_runtime_identity("frontend")
    with (
        _created(root / patch_path, patch),
        _replaced(workspace, _yaml_bytes(workspace_data)),
        _replaced(lock, lock_prefix + _yaml_bytes(lock_data)),
        _replaced(manifest, (json.dumps(manifest_data, indent=2) + "\n").encode()),
        _replaced(config, candidate_config),
    ):
        installed = _await_runtime_effect(
            project,
            lambda: _http(project.endpoints().ui, "/")[2] == f"{token}:left|right",
            before={"frontend": before},
            recreate=True,
            process=process,
            timeout_seconds=300,
        )
    _await_runtime_effect(
        project,
        lambda: _http(project.endpoints().ui, "/")[2] == "",
        before=installed,
        recreate=True,
        process=process,
        timeout_seconds=300,
    )


def _verify_image_and_entrypoint_effects(project: FeedbackProject, process: WatchProcess) -> None:
    root = project.repo_root
    backend_recipe = root / "docker/development/backend.Dockerfile"
    frontend_recipe = root / "frontend/Dockerfile.dev"
    entrypoint = root / "docker/development/secret-entrypoint.sh"
    health = root / "backend/src/ci_coordinator/observability/health.py"
    config = root / "frontend/vite.config.ts"
    image_token, entrypoint_token = secrets.token_hex(12), secrets.token_hex(12)
    entrypoint_bytes = entrypoint.read_bytes()
    marker = b"exec setpriv \\\n"
    if entrypoint_bytes.count(marker) != 1:
        raise WatchError("entrypoint witness anchor changed")
    candidate_entrypoint = entrypoint_bytes.replace(
        marker, f"export {_ENTRYPOINT_WITNESS_ENV}={entrypoint_token}\n\n".encode() + marker
    )
    candidate_health = _health_source(
        health.read_bytes(),
        f'os.environ.get("{_IMAGE_WITNESS_ENV}", "") + ":" + '
        f'os.environ.get("{_ENTRYPOINT_WITNESS_ENV}", "")',
        imports="import os",
    )
    before = _await_rebuild_baseline(project, process)
    with (
        _replaced(health, candidate_health),
        _replaced(
            config,
            _header_source(config.read_bytes(), f'process.env["{_IMAGE_WITNESS_ENV}"] ?? ""'),
        ),
        _replaced(entrypoint, candidate_entrypoint),
        _replaced(
            backend_recipe,
            backend_recipe.read_bytes() + f"\nENV {_IMAGE_WITNESS_ENV}={image_token}\n".encode(),
        ),
        _replaced(
            frontend_recipe,
            frontend_recipe.read_bytes() + f"\nENV {_IMAGE_WITNESS_ENV}={image_token}\n".encode(),
        ),
    ):
        installed = _await_image_transition(
            project, process, before=before, tokens=(image_token, entrypoint_token)
        )
    _await_image_transition(project, process, before=installed, tokens=None)


def _await_image_transition(
    project: FeedbackProject,
    process: WatchProcess,
    *,
    before: Mapping[str, ServiceRuntimeIdentity],
    tokens: tuple[str, str] | None,
) -> dict[str, ServiceRuntimeIdentity]:
    phase = "restoration" if tokens is None else "install"
    backend_checks = ("health_restored",) if tokens is None else ("health_image", "entrypoint")
    checks = (*backend_checks, "frontend_header", "recreated_backend", "recreated_frontend")
    observed: dict[str, bool | None] = dict.fromkeys((*checks, "uid_secret_boundary"))
    deadline = time.monotonic() + 300

    def probe(name: str, predicate: Callable[[], bool]) -> None:
        try:
            observed[name] = predicate()
        except (ServiceAbsent, ProviderCommandFailed, OSError, http.client.HTTPException):
            observed[name] = None

    def ready() -> dict[str, ServiceRuntimeIdentity] | None:
        # Each vector is one polling round; prior matches cannot accumulate
        # into success. Only booleans leave the fixture, never marker values.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        current: dict[str, ServiceRuntimeIdentity] = {}

        def recreated(service: str) -> bool:
            current[service] = project.service_runtime_identity(service)
            return current[service].container_id != before[service].container_id

        observed.update(dict.fromkeys((*checks, "uid_secret_boundary")))
        with project.observation_budget(min(30, remaining)):
            try:
                status = _health_status(project)
            except (ServiceAbsent, ProviderCommandFailed, OSError, http.client.HTTPException):
                pass
            else:
                if tokens is None:
                    observed["health_restored"] = status == "alive"
                else:
                    image, separator, entrypoint = status.partition(":")
                    observed["health_image"] = separator == ":" and image == tokens[0]
                    observed["entrypoint"] = separator == ":" and entrypoint == tokens[1]
            probe(
                "frontend_header",
                lambda: (
                    _http(project.endpoints().ui, "/")[2] == ("" if tokens is None else tokens[0])
                ),
            )
            probe("recreated_backend", lambda: recreated("backend"))
            probe("recreated_frontend", lambda: recreated("frontend"))
            if not all(observed[name] is True for name in checks):
                return None
            observed["uid_secret_boundary"] = False
            try:
                project.assert_runtime_boundary(
                    "backend",
                    expected_uid=65_532,
                    protected_path="/run/ci-coordinator-secrets/runtime-dsn",
                )
            except ComposeError as error:
                raise WatchError("runtime UID/secret boundary failed") from error
            observed["uid_secret_boundary"] = True
            return current

    try:
        return _await_effect(ready, process=process, timeout_seconds=300)
    except WatchError as error:
        vector = json.dumps(observed, sort_keys=True, separators=(",", ":"))
        raise WatchError(
            f"{phase}: {error}; observed={vector}",
            process=error.process,
            cancellation=error.cancellation,
        ) from error


def _verify_build_context_effect(project: FeedbackProject, process: WatchProcess) -> None:
    root = project.repo_root
    name = "ci-coordinator-build-context-probe.txt"
    payload = secrets.token_hex(12).encode()
    probes = tuple(
        (service, root / service / "src" / name, f"/workspace/{service}/src/{name}")
        for service in ("backend", "frontend")
    )
    ignore = root / ".dockerignore"
    baseline = _await_rebuild_baseline(project, process)
    before_restart, frontend = baseline["backend"], baseline["frontend"]
    with _created(probes[0][1], payload), _created(probes[1][1], payload):
        _await_projection(project, probes, payload, process=process, wait=time.sleep)
        backend = _await_runtime_transition(
            project, before_restart, process=process, wait=time.sleep
        )
        before = {"backend": backend, "frontend": frontend}
        candidate = ignore.read_bytes() + f"\nbackend/src/{name}\nfrontend/src/{name}\n".encode()
        with _replaced(ignore, candidate):
            excluded = _await_runtime_effect(
                project,
                lambda: _projected(project, probes, None),
                before=before,
                recreate=True,
                process=process,
                timeout_seconds=300,
            )
        _await_runtime_effect(
            project,
            lambda: _projected(project, probes, payload),
            before=excluded,
            recreate=True,
            process=process,
            timeout_seconds=300,
        )
    _await_projection(project, probes, None, process=process, wait=time.sleep)


def _verify_compose_model_effect(
    project: MaterialFeedbackProject,
    *,
    reconcile: Callable[[], LocalEndpoints],
    preservation_check: Callable[[], None],
) -> None:
    root = project.repo_root
    model, config = root / "compose.yaml", root / "frontend/vite.config.ts"
    ordinary = model.read_bytes()
    before = {
        service: project.service_runtime_identity(service) for service in ("backend", "frontend")
    }
    invalid = YAML(typ="safe").load(ordinary)
    invalid["services"]["frontend"]["coordinator-invalid-model-field"] = True
    phase = "invalid-schema"
    try:
        with _replaced(model, _yaml_bytes(invalid)):
            _require_invalid_model(project)
            phase = "invalid-reconcile"
            try:
                reconcile()
            except ComposeError:
                # The schema diagnostic above distinguishes this case from a
                # generic provider failure. Re-admission and identity checks below
                # still have to prove that the failed route preserved the stack.
                pass
            else:
                raise WatchError("invalid Compose model was admitted by reconciliation")
        phase = "invalid-preservation"
        project.validate()
        if any(
            project.service_runtime_identity(service) != previous
            for service, previous in before.items()
        ):
            raise WatchError("invalid model changed the existing runtime")
        preservation_check()
    except Exception as error:
        raise ModelFeedbackError(phase, error) from error
    token = secrets.token_hex(12)
    candidate = YAML(typ="safe").load(ordinary)
    frontend = candidate["services"]["frontend"]
    frontend["environment"]["CI_COORDINATOR_MODEL_WITNESS"] = token
    original_probe = frontend["healthcheck"]["test"][-1]
    frontend["healthcheck"]["test"][-1] = (
        f"if(process.env.CI_COORDINATOR_MODEL_WITNESS !== '{token}')process.exit(1);"
        + original_probe
    )
    # Reserve an OS-selected loopback port through validation. The explicit
    # published value, rather than a coincidental new random port, is the oracle.
    attempted = False
    primary_error: BaseException | None = None
    phase = "install-inputs"
    try:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
            frontend["ports"] = [f"127.0.0.1:{port}:5173"]
            with (
                _replaced(
                    config,
                    _header_source(
                        config.read_bytes(), 'process.env["CI_COORDINATOR_MODEL_WITNESS"] ?? ""'
                    ),
                ),
                _replaced(model, _yaml_bytes(candidate)),
            ):
                phase = "install-validate"
                project.validate()
                reservation.close()
                attempted = True
                phase = "install-reconcile"
                endpoints = reconcile()
                phase = "install-health"
                _await_ordinary_health(project)
                phase = "install-effects"
                if urlsplit(endpoints.ui).port != port or _http(endpoints.ui, "/")[2] != token:
                    raise WatchError("valid Compose model did not apply its environment and port")
                if not _recreated(project, "frontend", before["frontend"]):
                    raise WatchError("Compose model did not replace the frontend")
                _require_healthcheck_model(project, frontend["healthcheck"]["test"])
                phase = "install-preservation"
                preservation_check()
    except Exception as error:
        primary_error = ModelFeedbackError(phase, error)
        raise primary_error from error
    except BaseException as error:
        primary_error = error
        raise
    finally:
        if attempted:
            # The callback must re-admit ownership before restoration too. A
            # blocked owner propagates retention instead of performing teardown.
            phase = "restoration-reconcile"
            try:
                restored = reconcile()
                phase = "restoration-health"
                _await_ordinary_health(project)
                phase = "restoration-effects"
                if _http(restored.ui, "/")[2] != "":
                    raise WatchError("ordinary Compose model was not restored")
                _require_healthcheck_model(
                    project,
                    YAML(typ="safe").load(ordinary)["services"]["frontend"]["healthcheck"]["test"],
                )
                phase = "restoration-preservation"
                preservation_check()
            except BaseException as error:
                if primary_error is None:
                    if isinstance(error, Exception):
                        raise ModelFeedbackError(phase, error) from error
                    raise
                restoration = ModelFeedbackError(phase, error)
                if isinstance(primary_error, ModelFeedbackError):
                    primary_error.restoration_error = restoration
                else:
                    primary_error.add_note(str(restoration))


def _require_invalid_model(project: FeedbackProject) -> None:
    invocation = project.watch_invocation()
    if invocation.argv[-2:] != ("watch", "--no-up"):
        raise WatchError("invalid-model witness has no admitted Compose binding")
    observed = spawn(
        invocation.argv[0],
        [*invocation.argv[1:-2], "config", "--quiet"],
        cwd=invocation.cwd,
        env=invocation.environment,
        max_buffer=65_536,
        timeout_seconds=30,
    )
    diagnostic = observed.stderr.lower()
    if (
        observed.status is None
        or observed.status == 0
        or observed.failure_kind is not None
        or "coordinator-invalid-model-field" not in diagnostic
        or not any(reason in diagnostic for reason in ("not allowed", "additional propert"))
    ):
        raise WatchError("invalid Compose model lacks an observed schema rejection")


def _require_healthcheck_model(project: FeedbackProject, expected: object) -> None:
    before = project.service_runtime_identity("frontend")
    invocation = project.watch_invocation()
    observed = spawn(
        "docker",
        ["inspect", "--format", "{{json .Config.Healthcheck.Test}}", before.container_id],
        cwd=invocation.cwd,
        env=invocation.environment,
        max_buffer=65_536,
        timeout_seconds=30,
    )
    if observed.status != 0 or observed.failure_kind is not None:
        raise WatchError("installed healthcheck model is unavailable")
    try:
        value = json.loads(observed.stdout)
    except ValueError as error:
        raise WatchError("installed healthcheck model is invalid") from error
    if value != expected or project.service_runtime_identity("frontend") != before:
        raise WatchError("installed healthcheck model does not match the admitted input")


def _await_ordinary_health(project: FeedbackProject, *, timeout_seconds: float = 60) -> None:
    # The final HTTP response proves restored source behavior, but Docker's
    # periodic health probe can still report starting after that restart.
    # Observe this postcondition only after the watch client has stopped cleanly.
    services = ("backend", "postgres", "frontend")
    deadline = time.monotonic() + timeout_seconds
    summary = "not_observed"
    while (remaining := deadline - time.monotonic()) > 0:
        with project.observation_budget(min(10.0, remaining)):
            statuses = {item.service: item for item in project.diagnostic_status()}
        if all(
            service in statuses
            and statuses[service].state == "running"
            and statuses[service].health == "healthy"
            for service in services
        ):
            return
        summary = ",".join(
            f"{service}={statuses[service].state}/{statuses[service].health or 'none'}"
            if service in statuses
            else f"{service}=missing"
            for service in services
        )
        time.sleep(min(_POLL_INTERVAL_SECONDS, max(0.0, deadline - time.monotonic())))
    raise WatchError(f"ordinary services did not become healthy after feedback; services={summary}")


def _admit_frontend_root(repo_root: Path) -> Path:
    frontend = repo_root / "frontend"
    if not repo_root.is_absolute() or not frontend.is_dir() or frontend.resolve() != frontend:
        raise WatchError("feedback frontend root must be absolute, canonical and existing")
    return frontend


def _verify_frontend_effects(
    project: FeedbackProject,
    process: WatchProcess,
    *,
    browser_runtime_root: Path | None = None,
) -> None:
    root = project.repo_root
    runtime_root = root if browser_runtime_root is None else browser_runtime_root
    initial = project.service_runtime_identity("frontend")
    token = secrets.token_hex(12)
    config = root / "frontend/vite.config.ts"
    original = config.read_bytes()
    marker = b"    server: {\n"
    if original.count(marker) != 1:
        raise WatchError("Vite configuration witness anchor changed")
    candidate = original.replace(
        marker, marker + f'      headers: {{"X-Coordinator-Watch": "{token}"}},\n'.encode()
    )
    with _replaced(config, candidate):
        configured = _await_runtime_effect(
            project,
            lambda: _http(project.endpoints().ui, "/")[2] == token,
            before={"frontend": initial},
            process=process,
        )
    restored = _await_runtime_effect(
        project,
        lambda: _http(project.endpoints().ui, "/")[2] == "",
        before=configured,
        process=process,
    )

    limits = root / "frontend/src/api/workbench/limits.ts"
    original = limits.read_bytes()
    if original.count(b" = 20;") != 1:
        raise WatchError("Vite transitive config witness anchor changed")
    request_path = "/api/v1/workbench/repositories/1/1?limit=20"
    _await_effect(
        lambda: _http(project.endpoints().ui, request_path)[0] == 401,
        process=process,
    )
    with _replaced(limits, original.replace(b" = 20;", b" = 19;")):
        configured = _await_runtime_effect(
            project,
            lambda: _http(project.endpoints().ui, request_path)[0] == 404,
            before=restored,
            process=process,
        )
    frontend = _await_runtime_effect(
        project,
        lambda: _http(project.endpoints().ui, request_path)[0] == 401,
        before=configured,
        process=process,
    )["frontend"]

    module = root / "frontend/src/ci-coordinator-hmr-probe.js"
    style = root / "frontend/src/ci-coordinator-hmr-probe.css"
    if any(path.exists() or path.is_symlink() for path in (module, style)):
        raise WatchError("render witness probe path is already allocated")
    document = root / "frontend/index.html"
    probe_document = (
        b'<!doctype html><title>watch-document</title><p id="watch-value"></p>'
        b'<script type="module" src="/src/ci-coordinator-hmr-probe.js"></script>'
    )
    try:
        with _replaced(document, probe_document):
            _await_effect(
                lambda: b"watch-document" in _http(project.endpoints().ui, "/")[1], process=process
            )
            result = spawn(
                "node",
                [
                    "--input-type=module",
                    "-e",
                    _RENDER_BUDGET + _RENDER_DOCUMENT_LIFETIME + _RENDER_WITNESS,
                    project.endpoints().ui,
                    str(root / "frontend"),
                ],
                cwd=runtime_root / "frontend",
                max_buffer=65_536,
                timeout_seconds=90,
            )
            _require_render_result(result)
            if project.service_runtime_identity("frontend") != frontend:
                raise WatchError("rendered HMR unexpectedly restarted the runtime")
    finally:
        for path in (module, style):
            with suppress(FileNotFoundError):
                path.unlink()
    _await_effect(
        lambda: b"CI Coordinator" in _http(project.endpoints().ui, "/")[1], process=process
    )


def _verify_backend_effects(project: FeedbackProject, process: WatchProcess) -> None:
    root = project.repo_root
    source = root / "backend/src/ci_coordinator/observability/health.py"
    original = source.read_bytes()
    marker = b'    state: Literal["alive"] = "alive"'
    if original.count(marker) != 1:
        raise WatchError("backend response witness anchor changed")
    token = secrets.token_hex(12)
    initial = project.service_runtime_identity("backend")
    with _replaced(source, original.replace(marker, f'    state: str = "{token}"'.encode())):
        _await_runtime_effect(
            project,
            lambda: _health_status(project) == token,
            before={"backend": initial},
            process=process,
        )
    _await_effect(lambda: _health_status(project) == "alive", process=process)

    manifest = root / "backend/pyproject.toml"
    lock = root / "backend/uv.lock"
    manifest_bytes, lock_bytes = manifest.read_bytes(), lock.read_bytes()
    version = tomllib.loads(manifest_bytes.decode())["project"]["version"]
    next_version = f"{version}+watch.{token}"
    version_line = f'version = "{version}"'.encode()
    lock_anchor = b'name = "ci-coordinator-backend"\n' + version_line
    if manifest_bytes.count(version_line) != 1 or lock_bytes.count(lock_anchor) != 1:
        raise WatchError("installed package witness anchor changed")
    installed_version_source = original.replace(
        b"from dataclasses import dataclass",
        b"from dataclasses import dataclass\nfrom importlib.metadata import version",
    ).replace(marker, b'    state: str = version("ci-coordinator-backend")')
    with _replaced(source, installed_version_source):
        initial = _await_effect(
            lambda: (
                project.service_runtime_identity("backend")
                if _health_status(project) == version
                else None
            ),
            process=process,
        )
        with (
            _replaced(
                manifest,
                manifest_bytes.replace(version_line, f'version = "{next_version}"'.encode()),
            ),
            _replaced(
                lock,
                lock_bytes.replace(
                    lock_anchor,
                    f'name = "ci-coordinator-backend"\nversion = "{next_version}"'.encode(),
                ),
            ),
        ):
            _await_runtime_effect(
                project,
                lambda: _health_status(project) == next_version,
                before={"backend": initial},
                recreate=True,
                process=process,
                timeout_seconds=300,
            )
        _await_effect(
            lambda: _health_status(project) == version, process=process, timeout_seconds=300
        )
    _await_effect(lambda: _health_status(project) == "alive", process=process)


def _health_status(project: FeedbackProject) -> str:
    status, body, _ = _http(project.endpoints().api, "/healthz")
    if status != 200:
        return ""
    value = json.loads(body)
    if (
        not isinstance(value, dict)
        or value.get("ok") is not True
        or not isinstance(value.get("status"), str)
    ):
        raise WatchError("backend response shape changed")
    return str(value["status"])


def _await_effect[T](
    observed: Callable[[], T | None],
    *,
    process: WatchProcess,
    timeout_seconds: float = 45,
) -> T:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        _require_running(process)
        try:
            result = observed()
            if result:
                _require_running(process)
                return result
        except (ServiceAbsent, ProviderCommandFailed, OSError, http.client.HTTPException):
            pass
        time.sleep(_POLL_INTERVAL_SECONDS)
    raise WatchError("native watch effect deadline exceeded")


def _http(endpoint: str, path: str) -> tuple[int, bytes, str]:
    address = urlsplit(endpoint)
    if address.scheme != "http" or address.hostname != "127.0.0.1" or address.port is None:
        raise WatchError("feedback endpoint escaped loopback")
    connection = http.client.HTTPConnection("127.0.0.1", address.port, timeout=3)
    try:
        connection.request("GET", path, headers={"Cache-Control": "no-cache"})
        response = connection.getresponse()
        body = response.read(1_048_577)
        if len(body) > 1_048_576:
            raise WatchError("feedback response exceeded the byte bound")
        return response.status, body, response.getheader("X-Coordinator-Watch", "")
    finally:
        connection.close()


@contextmanager
def _replaced(path: Path, candidate: bytes) -> Iterator[None]:
    if path.is_symlink() or not path.is_file():
        raise WatchError("feedback input is not a regular owned file")
    original = path.read_bytes()
    path.write_bytes(candidate)
    try:
        yield
    finally:
        if path.is_symlink() or path.read_bytes() != candidate:
            raise WatchError("feedback input changed outside the witness")
        path.write_bytes(original)


@contextmanager
def _created(path: Path, content: bytes) -> Iterator[None]:
    if path.exists() or path.is_symlink():
        raise WatchError("material feedback probe path is already allocated")
    with path.open("xb") as output:
        output.write(content)
    try:
        yield
    finally:
        if path.is_symlink() or not path.is_file() or path.read_bytes() != content:
            raise WatchError("material feedback probe changed outside the witness")
        path.unlink()


_BROWSER_PREFLIGHT: Final = r"""
import { chromium } from '@playwright/test';
import { constants } from 'node:fs';
import { access } from 'node:fs/promises';
await access(chromium.executablePath(), constants.X_OK);
console.log('browser_runtime_available');
"""


_COMPILER_WITNESS: Final = r"""
import { chromium, expect } from '@playwright/test';
const browser = await chromium.launch();
try {
  const page = await browser.newPage();
  await page.goto(process.argv[1]);
  await expect(page.locator('#watch-value')).toHaveText(process.argv[2], {timeout: 30000});
  console.log(process.argv[2]);
} finally {
  await browser.close();
}
"""


_RENDER_PHASES: Final = (
    "fixture-root",
    "create-style",
    "create-module",
    "browser-launch",
    "page-create",
    "transport-observer",
    "source-projection",
    "navigate",
    "ws-connected",
    "initial-text",
    "initial-style",
    "module-ready",
    "prime-module",
    "prime-receipt",
    "primed-text",
    "document-mark",
    "update-module",
    "updated-text",
    "update-style",
    "updated-style",
    "document-retained",
    "ws-retained",
    "browser-close",
    "remove-module",
    "remove-style",
)

_RENDER_TRANSPORT_LIMITS: Final = {
    "attempts": 16,
    "same-host": 16,
    "same-port": 16,
    "same-endpoint": 16,
    "frames": 16,
    "connected": 16,
    "close": 16,
    "error": 16,
    "present": 1,
    "closed": 1,
    "changed": 1,
    "http-client-status": 599,
    "cdp-attempts": 16,
    "cdp-connected": 16,
    "handshake-status": 599,
    "client-connected": 16,
    "full-reload": 16,
    "main-navigation": 16,
    "epoch": 16,
    "baseline-epoch": 16,
    "prime-epoch": 16,
    "prime-update": 1,
    "prime-reload": 1,
    "prime-requests": 16,
    "prime-responses": 16,
    "prime-status": 599,
    "prime-request-failed": 16,
    "prime-fetch-truncated": 1,
    "pre-pin-request-failed": 16,
    "pre-pin-page-error": 16,
    "pre-pin-console-error": 16,
    "pre-pin-vite-error": 16,
    "prime-dom": 4,
    "prime-accept": 4,
    "bootstrap-complete": 1,
    "lifetime-invalid": 1,
    "post-update": 16,
    "post-reload": 16,
    "post-error": 16,
    "module-status": 599,
}


def _require_render_result(result: CommandResult) -> None:
    # Only closed witness codes cross the process boundary. Browser errors,
    # URLs (including the HMR token), source text and provider output do not.
    phase = "bootstrap"
    failures: list[str] = []
    completed: set[str] = set()
    transport: dict[str, int] = {}
    durations: dict[str, int] = {}
    lines = result.stdout.splitlines()
    for line in lines:
        event, _, code = line.partition("=")
        if event == "hmr_ms":
            name, _, number = code.partition(":")
            if (
                name in _RENDER_PHASES
                and 1 <= len(number) <= 5
                and number.isascii()
                and number.isdecimal()
                and int(number) <= 90_000
            ):
                durations[name] = int(number)
            continue
        if event == "hmr_transport":
            name, _, number = code.partition(":")
            if (
                name in _RENDER_TRANSPORT_LIMITS
                and 1 <= len(number) <= 3
                and number.isascii()
                and number.isdecimal()
                and int(number) <= _RENDER_TRANSPORT_LIMITS[name]
            ):
                transport[name] = int(number)
            continue
        if code not in _RENDER_PHASES:
            continue
        if event == "hmr_phase":
            phase = code
        elif event == "hmr_ok":
            completed.add(code)
        elif event == "hmr_failed" and code not in failures:
            failures.append(code)
    if (
        result.status == 0
        and result.failure_kind is None
        and lines
        and lines[-1] == "rendered_hmr_and_style"
        and completed == set(_RENDER_PHASES)
        and not failures
    ):
        return
    checks = ",".join(f"{code}={int(code in completed)}" for code in _RENDER_PHASES)
    reason = result.failure_kind or ("exit" if result.status != 0 else "incomplete-evidence")
    primary = failures[0] if failures else phase
    secondary = ",".join(failures[1:]) or "none"
    transport_details = ",".join(
        f"{name}={transport.get(name, 'unknown')}" for name in _RENDER_TRANSPORT_LIMITS
    )
    elapsed = ",".join(f"{name}={duration}" for name, duration in durations.items()) or "unknown"
    raise WatchError(
        "rendered source/style HMR witness failed; "
        f"check={primary}; last_phase={phase}; process={reason}; "
        f"secondary_failures={secondary}; checks={checks}; transport={transport_details}; "
        f"elapsed_ms={elapsed}"
    )


_RENDER_BUDGET: Final = r"""
function createRenderBudget(workMs, cleanupMs, now = () => performance.now()) {
  let deadline = now() + workMs;
  const finalDeadline = deadline + cleanupMs;
  let cleaning = false;
  const remaining = () => Math.max(1, deadline - now());
  return {
    remaining,
    cleanup() {
      if (cleaning) throw new Error('cleanup already started');
      cleaning = true;
      deadline = Math.min(now() + cleanupMs, finalDeadline);
    },
    async run(action) {
      if (now() >= deadline) throw new Error('phase budget expired');
      let timer;
      try {
        const timeout = new Promise((_, reject) => {
          timer = setTimeout(() => reject(new Error('phase budget expired')), remaining());
        });
        const result = await Promise.race([Promise.resolve().then(() => {
          if (now() >= deadline) throw new Error('phase budget expired');
          return action();
        }), timeout]);
        if (now() >= deadline) throw new Error('phase returned after budget');
        return result;
      } finally {
        clearTimeout(timer);
      }
    },
  };
}
"""


_RENDER_DOCUMENT_LIFETIME: Final = r"""
function isPreparedHmrProjection(source) {
  // Vite 8.3 initializes this controlled module before its source. A raw 200
  // can precede the delayed add event and must not admit browser navigation.
  const initialization =
    'import { createHotContext as __vite__createHotContext } from "/@vite/client";' +
    'import.meta.hot = __vite__createHotContext("/src/ci-coordinator-hmr-probe.js");';
  return source.startsWith(initialization) &&
    source.includes('window.watchHmrAcceptReady = "created";');
}

function observePrimedText(expected) {
  const text = document.querySelector('#watch-value')?.textContent;
  const category = value => value === 'created' ? 1 : value === expected ? 2 :
    value === 'updated' ? 3 : 4;
  const state = `${category(text)}:${category(window.watchHmrAcceptReady)}`;
  const diagnostic = window.watchHmrDiagnosticState ??= {last: null, emitted: 0};
  if (diagnostic.last !== state && diagnostic.emitted < 16) {
    diagnostic.last = state;
    diagnostic.emitted += 1;
    console.debug(`[watch-hmr-state] ${state}`);
  }
  return text === expected;
}

function parsePrimingState(text) {
  const match = /^\[watch-hmr-state\] ([1-4]):([1-4])$/.exec(text);
  return match && match[0] === text ? [Number(match[1]), Number(match[2])] : null;
}

function createDocumentBoundHmr(modulePath = '/src/ci-coordinator-hmr-probe.js') {
  const documentState = epoch => ({
    epoch, socket: null, duplicate: false, pendingReload: false, primedUpdate: false,
  });
  let current = documentState(0);
  let baseline;
  let priming;
  let reloadedAfterPrime = false;
  let invalid = false;
  const connected = () => Boolean(!invalid && !current.pendingReload &&
    current.socket && !current.socket.isClosed() && !current.duplicate);
  const bootstrapComplete = () => Boolean(priming &&
    (current === priming.document ? current.primedUpdate : reloadedAfterPrime));
  const ready = () => connected() && bootstrapComplete();
  return {
    get document() { return current; },
    navigate() {
      if (baseline || (current.epoch > 0 && !current.pendingReload)) invalid = true;
      if (priming && current.pendingReload) reloadedAfterPrime = true;
      current = documentState(current.epoch + 1);
    },
    socketCreated(socket) {
      const document = current;
      if (baseline && socket !== baseline.socket) invalid = true;
      return payload => {
        if (document !== current) return;
        if (payload.type === 'connected') {
          if (document.socket && document.socket !== socket) {
            document.duplicate = true;
            invalid = true;
          }
          document.socket ??= socket;
        } else if (document.socket === socket &&
          payload.type === 'full-reload' && !payload.ifFallback &&
          (!payload.path || payload.path === '*' || payload.path === '/index.html')) {
          document.pendingReload = true;
          if (baseline) invalid = true;
        } else if (!baseline && priming?.document === document &&
          priming.socket === socket && document.socket === socket && payload.type === 'update' &&
          Array.isArray(payload.updates) && payload.updates.some(update =>
            update?.type === 'js-update' && update.path === modulePath &&
            update.acceptedPath === modulePath)) {
          document.primedUpdate = true;
        }
      };
    },
    connected,
    prime(document, prepared) {
      if (priming || baseline || document !== current || !connected() || prepared !== true) {
        throw new Error('invalid HMR priming admission');
      }
      priming = {document, socket: document.socket};
    },
    ready,
    pin(document, rendered) {
      if (baseline || document !== current || !ready() || rendered !== true) {
        throw new Error('invalid HMR baseline');
      }
      baseline = {document, socket: document.socket};
    },
    retained() {
      return Boolean(baseline && current === baseline.document &&
        current.socket === baseline.socket && ready());
    },
    facts() {
      return {
        epoch: current.epoch, 'baseline-epoch': baseline?.document.epoch ?? 0,
        present: Number(Boolean(current.socket)),
        closed: Number(Boolean(current.socket?.isClosed())),
        changed: Number(current.duplicate ||
          Boolean(baseline && current.socket !== baseline.socket)),
        'prime-epoch': priming?.document.epoch ?? 0,
        'prime-update': Number(current.primedUpdate), 'prime-reload': Number(reloadedAfterPrime),
        'bootstrap-complete': Number(bootstrapComplete()), 'lifetime-invalid': Number(invalid),
      };
    },
  };
}
"""


_RENDER_WITNESS: Final = r"""
import { chromium, expect } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import { writeFile, unlink } from 'node:fs/promises';
import { isAbsolute, join } from 'node:path';
const targetFrontend = process.argv[2];
const budget = createRenderBudget(70000, 10000);
const remaining = budget.remaining;
const check = async (code, action) => {
  console.log(`hmr_phase=${code}`);
  const started = performance.now();
  try {
    const result = await budget.run(action);
    console.log(`hmr_ok=${code}`);
    return result;
  } catch {
    console.log(`hmr_failed=${code}`);
    throw new Error('witness check failed');
  } finally {
    console.log(`hmr_ms=${code}:${Math.min(90000, Math.max(0,
      Math.trunc(performance.now() - started)))}`);
  }
};
const primed = `primed-${randomUUID()}`;
const moduleSource = value => `import './ci-coordinator-hmr-probe.css';
if (import.meta.hot) {
  import.meta.hot.accept();
  window.watchHmrAcceptReady = ${JSON.stringify(value)};
}
document.querySelector('#watch-value').textContent = ${JSON.stringify(value)};`;
let browser;
let modulePath;
let stylePath;
let failed = false;
let baselinePinned = false;
let primingStarted = false;
let observeTransport = true;
let snapshotTransport = () => {};
const transport = {
  attempts: 0, 'same-host': 0, 'same-port': 0, 'same-endpoint': 0,
  frames: 0, connected: 0, close: 0, error: 0,
  present: 0, closed: 0, changed: 0, 'http-client-status': 0,
  'cdp-attempts': 0, 'cdp-connected': 0, 'handshake-status': 0, 'client-connected': 0,
  'full-reload': 0, 'main-navigation': 0, epoch: 0, 'baseline-epoch': 0,
  'prime-epoch': 0, 'prime-update': 0, 'prime-reload': 0,
  'prime-requests': 0, 'prime-responses': 0, 'prime-status': 0,
  'prime-request-failed': 0, 'prime-fetch-truncated': 0,
  'pre-pin-request-failed': 0, 'pre-pin-page-error': 0,
  'pre-pin-console-error': 0, 'pre-pin-vite-error': 0,
  'prime-dom': 0, 'prime-accept': 0,
  'bootstrap-complete': 0, 'lifetime-invalid': 0,
  'post-update': 0, 'post-reload': 0, 'post-error': 0, 'module-status': 0,
};
const recordTransport = (name, value) => {
  if (!observeTransport || transport[name] === value) return;
  transport[name] = value;
  console.log(`hmr_transport=${name}:${value}`);
};
const countTransport = name => recordTransport(name, Math.min(16, transport[name] + 1));
try {
  await check('fixture-root', () => {
    if (!targetFrontend || !isAbsolute(targetFrontend)) throw new Error('invalid fixture root');
    modulePath = join(targetFrontend, 'src/ci-coordinator-hmr-probe.js');
    stylePath = join(targetFrontend, 'src/ci-coordinator-hmr-probe.css');
  });
  await check('create-style', () =>
    writeFile(stylePath, '#watch-value { color: rgb(1, 2, 3) }', {flag: 'wx'}));
  await check('create-module', () => writeFile(modulePath, moduleSource('created'), {flag: 'wx'}));
  browser = await check('browser-launch', () => chromium.launch({timeout: remaining()}));
  const page = await check('page-create', () => browser.newPage());
  const lifetime = createDocumentBoundHmr();
  const expectedEndpoint = new URL(process.argv[1]);
  snapshotTransport = () => {
    for (const [name, value] of Object.entries(lifetime.facts())) {
      recordTransport(name, Math.min(16, value));
    }
  };
  // Observe before navigation: a module's DOM render does not await Vite's
  // transport.connect(). A late hot.on('vite:ws:connect') can miss that event.
  await check('transport-observer', async () => {
    const primingRequests = new WeakSet();
    let primingStateMessages = 0;
    for (const [name, value] of Object.entries(transport)) {
      console.log(`hmr_transport=${name}:${value}`);
    }
    page.on('framenavigated', frame => {
      if (frame !== page.mainFrame()) return;
      countTransport('main-navigation');
      lifetime.navigate();
      snapshotTransport();
    });
    page.on('request', request => {
      const url = new URL(request.url());
      if (!observeTransport || !primingStarted || baselinePinned ||
          url.host !== expectedEndpoint.host ||
          url.pathname !== '/src/ci-coordinator-hmr-probe.js') return;
      if (transport['prime-requests'] === 16) {
        recordTransport('prime-fetch-truncated', 1);
        return;
      }
      primingRequests.add(request);
      countTransport('prime-requests');
    });
    page.on('requestfailed', request => {
      if (!baselinePinned && new URL(request.url()).host === expectedEndpoint.host) {
        countTransport('pre-pin-request-failed');
      }
      if (primingRequests.has(request)) countTransport('prime-request-failed');
    });
    page.on('pageerror', () => {
      if (!baselinePinned) countTransport('pre-pin-page-error');
    });
    page.on('response', response => {
      if (primingRequests.has(response.request())) {
        countTransport('prime-responses');
        recordTransport('prime-status', response.status());
      }
      const url = new URL(response.url());
      if (url.host === expectedEndpoint.host && url.pathname === '/@vite/client') {
        recordTransport('http-client-status', response.status());
      }
      if (baselinePinned && url.host === expectedEndpoint.host &&
          url.pathname === '/src/ci-coordinator-hmr-probe.js') {
        recordTransport('module-status', response.status());
      }
    });
    page.on('console', message => {
      if (!baselinePinned && message.type() === 'error') {
        countTransport('pre-pin-console-error');
      }
      const state = message.type() === 'debug' ? parsePrimingState(message.text()) : null;
      if (state && primingStarted && !baselinePinned && primingStateMessages < 16) {
        primingStateMessages += 1;
        recordTransport('prime-dom', state[0]);
        recordTransport('prime-accept', state[1]);
      }
      if (message.type() === 'debug' && message.text() === '[vite] connected.') {
        countTransport('client-connected');
      }
    });
    page.on('websocket', socket => {
      countTransport('attempts');
      const url = new URL(socket.url());
      if (url.hostname === expectedEndpoint.hostname) countTransport('same-host');
      if (url.port === expectedEndpoint.port) countTransport('same-port');
      if (url.host !== expectedEndpoint.host) return;
      countTransport('same-endpoint');
      const receive = lifetime.socketCreated(socket);
      snapshotTransport();
      socket.on('close', () => { countTransport('close'); snapshotTransport(); });
      socket.on('socketerror', () => countTransport('error'));
      socket.on('framereceived', ({payload}) => {
        countTransport('frames');
        try {
          const message = JSON.parse(String(payload));
          if (message.type === 'connected') countTransport('connected');
          if (message.type === 'full-reload') countTransport('full-reload');
          if (!baselinePinned && message.type === 'error') countTransport('pre-pin-vite-error');
          if (baselinePinned) {
            if (message.type === 'full-reload') countTransport('post-reload');
            if (message.type === 'error') countTransport('post-error');
            if (message.type === 'update' && Array.isArray(message.updates) &&
                message.updates.some(update => update?.type === 'js-update' &&
                  update.path === '/src/ci-coordinator-hmr-probe.js' &&
                  update.acceptedPath === '/src/ci-coordinator-hmr-probe.js')) {
              countTransport('post-update');
            }
          }
          receive(message);
          snapshotTransport();
        } catch { /* Non-JSON frames do not establish the Vite connection. */ }
      });
    });
    // CDP observes upgrade status and received frames independently of the
    // public WebSocket event. It is diagnostic only, never a readiness bypass.
    const cdp = await page.context().newCDPSession(page);
    const connections = new Set();
    cdp.on('Network.webSocketCreated', event => {
      countTransport('cdp-attempts');
      if (new URL(event.url).host === expectedEndpoint.host && connections.size < 16) {
        connections.add(event.requestId);
      }
    });
    cdp.on('Network.webSocketHandshakeResponseReceived', event => {
      if (connections.has(event.requestId)) {
        recordTransport('handshake-status', event.response.status);
      }
    });
    cdp.on('Network.webSocketFrameReceived', event => {
      if (!connections.has(event.requestId)) return;
      try {
        if (JSON.parse(event.response.payloadData).type === 'connected') {
          countTransport('cdp-connected');
        }
      } catch { /* Only the closed message kind contributes a count. */ }
    });
    await cdp.send('Network.enable');
  });
  await check('source-projection', () => expect.poll(async () => {
    const response = await page.request.get(`${process.argv[1]}/src/ci-coordinator-hmr-probe.js`);
    return response.ok() && isPreparedHmrProjection(await response.text());
  }, {timeout: Math.min(30000, remaining())}).toBe(true));
  await check('navigate', () =>
    page.goto(process.argv[1], {timeout: Math.min(30000, remaining())}));
  await check('ws-connected', () => expect.poll(() => {
    snapshotTransport();
    return lifetime.connected();
  }, {timeout: remaining()}).toBe(true));
  await check('initial-text', () => page.waitForFunction(() =>
    document.querySelector('#watch-value')?.textContent === 'created', null,
    {timeout: remaining()}));
  await check('initial-style', () => page.waitForFunction(() => {
    const value = document.querySelector('#watch-value');
    return value && getComputedStyle(value).color === 'rgb(1, 2, 3)';
  }, null, {timeout: remaining()}));
  await check('module-ready', () => page.waitForFunction(() =>
    window.watchHmrAcceptReady === 'created', null, {timeout: remaining()}));
  // The owned priming update orders any connection-time buffered reload
  // before baseline, without requiring a reload to exist.
  await check('prime-module', async () => {
    await expect.poll(async () => {
      const candidate = lifetime.document;
      if (!lifetime.connected()) return false;
      let prepared;
      try {
        prepared = await page.evaluate(() => {
          const value = document.querySelector('#watch-value');
          return value?.textContent === 'created' &&
            getComputedStyle(value).color === 'rgb(1, 2, 3)' &&
            window.watchHmrAcceptReady === 'created';
        });
      } catch (error) {
        // An observed announced reload may destroy this evaluation context.
        if (candidate.pendingReload) return false;
        throw error;
      }
      if (candidate !== lifetime.document || !lifetime.connected() || !prepared) return false;
      lifetime.prime(candidate, prepared);
      return true;
    }, {timeout: remaining()}).toBe(true);
    primingStarted = true;
    await writeFile(modulePath, moduleSource(primed));
    snapshotTransport();
  });
  await check('prime-receipt', () => expect.poll(() => {
    snapshotTransport();
    return lifetime.ready();
  }, {timeout: remaining()}).toBe(true));
  await check('primed-text', () => page.waitForFunction(observePrimedText, primed,
    {timeout: remaining()}));
  await check('document-mark', async () => {
    const candidate = lifetime.document;
    const rendered = await page.evaluate(expected => {
      const value = document.querySelector('#watch-value');
      if (value?.textContent !== expected || getComputedStyle(value).color !== 'rgb(1, 2, 3)' ||
        window.watchHmrAcceptReady !== expected) {
        return false;
      }
      window.watchDocumentIdentity = document;
      return true;
    }, primed);
    lifetime.pin(candidate, rendered);
    baselinePinned = true;
    snapshotTransport();
  });
  await check('update-module', () => writeFile(modulePath, moduleSource('updated')));
  await check('updated-text', () => page.waitForFunction(() =>
    document.querySelector('#watch-value')?.textContent === 'updated', null,
    {timeout: remaining()}));
  await check('update-style', () => writeFile(stylePath, '#watch-value { color: rgb(4, 5, 6) }'));
  await check('updated-style', () => page.waitForFunction(() =>
    getComputedStyle(document.querySelector('#watch-value')).color === 'rgb(4, 5, 6)', null,
    {timeout: remaining()}));
  await check('document-retained', async () => {
    if (!await page.evaluate(() => window.watchDocumentIdentity === document)) {
      throw new Error('full reload');
    }
  });
  await check('ws-retained', () => {
    if (!lifetime.retained()) throw new Error('changed HMR client');
  });
} catch {
  failed = true;
} finally {
  snapshotTransport();
  observeTransport = false;
  budget.cleanup();
  for (const [code, action] of [
    ['browser-close', () => browser?.close()],
    ['remove-module', () => modulePath && unlink(modulePath)],
    ['remove-style', () => stylePath && unlink(stylePath)],
  ]) {
    try { await check(code, action); } catch { failed = true; }
  }
}
if (failed) process.exitCode = 1;
else console.log('rendered_hmr_and_style');
"""


def _require_running(process: WatchProcess) -> None:
    if process.poll() is not None:
        raise WatchError("Compose source watch exited before witness completion")


class _SubprocessWatch:
    def __init__(self, project: WatchProject, identity: InstanceIdentity) -> None:
        if project.repo_root != identity.repo_root:
            raise WatchError("feedback instance identity does not match its source")
        self._identity = identity
        self._resources = ExitStack()
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="feedback-watch")
        try:
            self._session = self._resources.enter_context(owned_watch_session(identity))
            # Re-admit after publishing the session; the earlier up belongs to
            # a distinct operation and does not authorize this invocation.
            invocation = project.watch_invocation()
            if invocation.cwd != identity.repo_root:
                raise WatchError("feedback invocation escaped its owned source")
            self._future = self._executor.submit(self._run, invocation)
        except BaseException:
            self._resources.close()
            self._executor.shutdown(wait=False, cancel_futures=True)
            raise

    def _run(self, invocation: ProviderInvocation) -> InteractiveResult:
        # The main-thread fixture owns the session and handles interruption in
        # its finally block. The existing owner records the provider's exact
        # nonce, inherited mutation descriptor and terminal process facts.
        with Path(os.devnull).open("wb") as output:
            return self._session.run(
                invocation.argv,
                cwd=invocation.cwd,
                env=invocation.environment,
                client_contract=WatchClientContract.COMPOSE_JOINED_WATCH,
                timeout_seconds=1_800,
                graceful_seconds=_WATCH_GRACE_SECONDS,
                kill_seconds=_WATCH_KILL_SECONDS,
                stdout_fd=output.fileno(),
            )

    def poll(self) -> int | None:
        if not self._future.done():
            return None
        result = self._result(timeout_seconds=0)
        if result.returncode is None:
            raise WatchError("watch client has no physical exit status", process=result)
        return result.returncode

    def stop(self) -> None:
        requester = self._executor.submit(
            cancel_watch,
            self._identity,
            expected_nonce=self._session.nonce,
            timeout_seconds=_WATCH_GRACE_SECONDS + _WATCH_KILL_SECONDS + 2,
        )
        result: InteractiveResult | None = None
        try:
            try:
                result = self._result(
                    timeout_seconds=_WATCH_GRACE_SECONDS + _WATCH_KILL_SECONDS + 2
                )
            finally:
                # Release mutation before awaiting the requester, which holds
                # control while waiting for that release. An ambiguous result
                # leaves the existing durable session fence in place.
                self._resources.close()
            cancellation = requester.result(timeout=_WATCH_GRACE_SECONDS + _WATCH_KILL_SECONDS + 2)
            if self._session.outcome.state != "quiescent" or cancellation.state != "quiescent":
                raise WatchError(
                    "watch client clean stop is unproven",
                    process=result,
                    cancellation=(
                        self._session.outcome
                        if self._session.outcome.state != "quiescent"
                        else cancellation
                    ),
                )
        except (OSError, ValueError, TimeoutError, WatchError) as error:
            if isinstance(error, WatchError) and error.cancellation is not None:
                raise
            raise WatchError(
                "watch client bounded stop did not complete",
                process=result,
                cancellation=self._session.outcome,
            ) from error
        finally:
            self._executor.shutdown(wait=False, cancel_futures=True)

    def _result(self, *, timeout_seconds: float) -> InteractiveResult:
        try:
            return self._future.result(timeout=timeout_seconds)
        except TimeoutError as error:
            raise WatchError("watch client bounded stop did not complete") from error
        except Exception as error:
            raise WatchError("watch client supervision failed") from error

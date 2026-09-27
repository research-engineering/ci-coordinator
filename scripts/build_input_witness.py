"""Finite native Corepack and Docker-context witnesses for the GitHub container lane."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import sys
import tarfile
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from scripts.bounded_process import CommandResult, spawn
from scripts.container_smoke_runner import JsonValue
from scripts.dev_environment.toolchain import _PNPM_SPEC, _instructions

_MAX_SOURCE_BYTES = 128 * 1024 * 1024
_MAX_MEMBERS = 10_000
_TOTAL_SECONDS = 570.0
_CLEANUP_RESERVE_SECONDS = 60.0
_TERMINATION_RESERVE_SECONDS = 3.0
_HOST_CLEANUP = (
    "import shutil,sys; from pathlib import Path; "
    "root=Path(sys.argv[1]); shutil.rmtree(root); "
    "assert not root.exists() and not root.is_symlink()"
)
_NODE_RECIPES = ("Dockerfile", "frontend/Dockerfile.dev", "docker/ci/connected-browser.Dockerfile")
_CONTEXT_MARKERS = {
    "**/.env": "frontend/src/build-input-fixture/.env",
    "**/.env.*": "frontend/src/build-input-fixture/.env.witness",
    "**/.DS_Store": "frontend/src/build-input-fixture/.DS_Store",
    "frontend/**/*.tsbuildinfo": "frontend/build-input-fixture/state.tsbuildinfo",
    "frontend/test-results": "frontend/test-results/build-input-marker",
    "frontend/playwright-report": "frontend/playwright-report/build-input-marker",
    "frontend/.stryker-tmp": "frontend/.stryker-tmp/build-input-marker",
    "frontend/reports/mutation": "frontend/reports/mutation/build-input-marker",
}
_REQUIRED_FILES = (
    "LICENSE",
    "NOTICE",
    "frontend/index.html",
    "frontend/dev/production.vite.config.ts",
    "patches/minimatch@5.1.9.patch",
    "backend/pyproject.toml",
    "backend/uv.lock",
    "backend/alembic.ini",
    "frontend/src/assets/fonts/OFL.txt",
    "frontend/src/assets/fonts/lato-latin-400-normal.woff2",
    "frontend/src/assets/fonts/lato-latin-700-normal.woff2",
    "frontend/src/assets/fonts/lato-latin-ext-400-normal.woff2",
    "frontend/src/assets/fonts/lato-latin-ext-700-normal.woff2",
)


def _emitted_failure(result: CommandResult, message: str) -> bool:
    # Do not accept a guard merely echoed as part of Docker's RUN command.
    pattern = r"(?m)^(?:#\d+ [0-9.]+ )?(?:Internal Error: |Error: )?" + re.escape(message) + r"\r?$"
    return re.search(pattern, result.stdout + "\n" + result.stderr) is not None


def _before(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise TimeoutError("build-input completion was late for its finite budget")


def _retain_secondary(primary: BaseException, secondary: BaseException) -> None:
    name = type(secondary).__name__[:64]
    BaseException.add_note(primary, f"build-input cleanup uncertain: {name}")


class _Commands:
    def __init__(self, root: Path, home: Path, *, deadline: float | None = None) -> None:
        self.root = root
        self.home = home
        self.docker = os.environ.get("CI_COORDINATOR_DOCKER_BIN", "docker")
        self.environment = {
            "PATH": os.environ.get("PATH", os.defpath),
            "HOME": str(home),
            "PYTHONDONTWRITEBYTECODE": "1",
            "GIT_OPTIONAL_LOCKS": "0",
        }
        self.deadline = time.monotonic() + _TOTAL_SECONDS if deadline is None else deadline
        self.suffix = f"{os.getpid()}-{secrets.token_hex(8)}"
        self.images: list[str] = []
        self.containers: list[str] = []
        self.sequence = 0
        self.owns_home = False

    def create_home(self) -> None:
        self.ensure_work_time()
        self.owns_home = True
        try:
            self.home.mkdir(mode=0o700)
        except FileExistsError:
            self.owns_home = False
            raise

    def ensure_work_time(self) -> None:
        _before(self.deadline - _CLEANUP_RESERVE_SECONDS)

    def _run(
        self,
        command: str,
        args: Sequence[str],
        *,
        deadline: float,
        cap: float,
        max_buffer: int,
    ) -> CommandResult:
        # spawn has ordinary termination delays, not a hard OS-drain guarantee.
        remaining = deadline - time.monotonic() - _TERMINATION_RESERVE_SECONDS
        if remaining <= 0:
            raise TimeoutError("build-input command cannot spend its finite budget reserve")
        result = spawn(
            command,
            args,
            cwd=self.root,
            env=self.environment,
            max_buffer=max_buffer,
            timeout_seconds=min(cap, remaining),
        )
        _before(deadline)
        if result.error is not None or result.failure_kind is not None or result.status is None:
            raise RuntimeError("build-input command did not complete cleanly")
        return result

    def execute(
        self, command: str, args: Sequence[str], *, failure: str | None = None
    ) -> CommandResult:
        result = self._run(
            command,
            args,
            deadline=self.deadline - _CLEANUP_RESERVE_SECONDS,
            cap=180,
            max_buffer=2 * 1024 * 1024,
        )
        if failure is None:
            if result.status != 0:
                detail = (result.stderr or result.stdout).strip()[-2000:]
                raise RuntimeError(f"build-input positive failed: {detail}")
        elif result.status == 0 or not _emitted_failure(result, failure):
            raise RuntimeError("build-input negative did not isolate its expected guard")
        return result

    def build(self, context: Path, recipe: str, *, failure: str | None = None) -> str:
        self.sequence += 1
        image = f"ci-coordinator:build-input-{self.suffix}-{self.sequence}"
        self.images.append(image)
        path = self.home / f"recipe-{self.sequence}.Dockerfile"
        path.write_text(recipe, encoding="utf-8")
        self.execute(
            self.docker,
            (
                "build",
                "--no-cache",
                "--progress=plain",
                "--file",
                str(path),
                "--tag",
                image,
                str(context),
            ),
            failure=failure,
        )
        return image

    def context_files(self, context: Path, destination: Path) -> None:
        image = self.build(context, "FROM scratch\nCOPY . /context/\n")
        self.sequence += 1
        container = f"ci-coordinator-build-input-{self.suffix}-{self.sequence}"
        self.containers.append(container)
        self.execute(self.docker, ("create", "--name", container, image, "/not-executed"))
        destination.mkdir()
        self.execute(self.docker, ("cp", f"{container}:/context/.", str(destination)))

    def cleanup(self) -> None:
        failures: list[BaseException] = []
        deadline = min(time.monotonic() + 30, self.deadline - 25)
        for kind, names in (("container", self.containers), ("image", self.images)):
            for name in reversed(names):
                try:
                    result = self._run(
                        self.docker,
                        (kind, "rm", "--force", name),
                        deadline=deadline,
                        cap=5,
                        max_buffer=65_536,
                    )
                    missing = (
                        result.status == 1
                        and re.fullmatch(
                            r"(?:Error response from daemon: )?No such "
                            + kind
                            + ": "
                            + re.escape(name),
                            result.stderr.strip(),
                        )
                        is not None
                    )
                    if result.status != 0 and not missing:
                        raise RuntimeError("build-input Docker cleanup was not established")
                except BaseException as error:
                    failures.append(error)
        if self.owns_home:
            try:
                result = self._run(
                    sys.executable,
                    ("-I", "-B", "-c", _HOST_CLEANUP, str(self.home)),
                    deadline=min(time.monotonic() + 20, self.deadline - 5),
                    cap=20,
                    max_buffer=65_536,
                )
                if result.status != 0:
                    raise RuntimeError("build-input host cleanup was not established")
            except BaseException as error:
                failures.append(error)
        if failures:
            primary = failures[0]
            for secondary in failures[1:4]:
                _retain_secondary(primary, secondary)
            raise primary
        _before(self.deadline)


def _source_members(archive: tarfile.TarFile) -> list[tarfile.TarInfo]:
    members: list[tarfile.TarInfo] = []
    total = 0
    for member in archive:
        path = PurePosixPath(member.name)
        total += member.size
        if (
            len(members) >= _MAX_MEMBERS
            or total > _MAX_SOURCE_BYTES
            or path.is_absolute()
            or ".." in path.parts
            or not (member.isfile() or member.isdir())
        ):
            raise RuntimeError("build-input source exceeds its closed member boundary")
        members.append(member)
    return members


def _snapshot(commands: _Commands, destination: Path) -> str:
    if commands.execute("git", ("status", "--porcelain")).stdout:
        raise RuntimeError("build-input witness requires a clean tracked source snapshot")
    head = commands.execute("git", ("rev-parse", "HEAD")).stdout.strip()
    archive_path = commands.home / "source.tar"
    commands.execute("git", ("archive", "--format=tar", "--output", str(archive_path), head))
    if archive_path.stat().st_size > _MAX_SOURCE_BYTES:
        raise RuntimeError("build-input source archive is oversized")
    destination.mkdir()
    with tarfile.open(archive_path) as archive:
        members = _source_members(archive)
        archive.extractall(destination, members=members, filter="data")
    return head


def _node_stage(root: Path, path: str, manager: str) -> tuple[str, str]:
    prefix: list[str] = []
    for _line, instruction, argument in _instructions((root / path).read_text(encoding="utf-8")):
        if instruction == "FROM":
            if prefix:
                break
            if argument.startswith("node:"):
                prefix.append(f"FROM {argument}\n")
        elif prefix:
            if instruction == "RUN" and f"corepack prepare {manager} --activate" in argument:
                if argument.count(manager) != 1:
                    break
                return "".join(prefix), argument
            if instruction not in {"ENV", "WORKDIR"}:
                break
            prefix.append(f"{instruction} {argument}\n")
    raise RuntimeError("build-input Node bootstrap is outside its admitted literal shape")


def _corepack_checks(commands: _Commands, snapshot: Path, empty: Path) -> dict[str, JsonValue]:
    manager = json.loads((snapshot / "package.json").read_text(encoding="utf-8"))["packageManager"]
    if not isinstance(manager, str) or _PNPM_SPEC.fullmatch(manager) is None:
        raise RuntimeError("build-input manager spec is not admitted")
    version, digest = manager.split("+sha512.")
    wrong_digest = ("0" if digest[0] != "0" else "1") + digest[1:]
    wrong = f"{version}+sha512.{wrong_digest}"
    stage, bootstrap = _node_stage(snapshot, "Dockerfile", manager)
    positives: list[str] = []
    for path in _NODE_RECIPES:
        prefix, command = _node_stage(snapshot, path, manager)
        positives.append(
            commands.build(
                empty, prefix + f'RUN {command}\nRUN test "$(pnpm --version)" = 12.5.1\n'
            )
        )
    commands.build(
        empty,
        stage + "RUN " + bootstrap.replace(manager, wrong) + "\n",
        failure=f"Mismatch hashes. Expected {wrong_digest}, got {digest}",
    )
    commands.build(
        empty,
        stage + "RUN rm /usr/local/bin/corepack\nRUN " + bootstrap + "\n",
        failure="Bundled Corepack version unavailable",
    )
    commands.build(
        empty,
        stage + "RUN mv /usr/local/bin/corepack /usr/local/bin/corepack-real "
        "&& printf '#!/bin/sh\\nprintf 0.0.0\\n' > /usr/local/bin/corepack "
        "&& chmod 755 /usr/local/bin/corepack\nRUN " + bootstrap + "\n",
        failure="Bundled Corepack version unavailable",
    )
    commands.build(
        empty,
        stage + "RUN rm /usr/local/bin/node\nRUN " + bootstrap + "\n",
        failure="Bundled Node version unavailable",
    )
    # The real positive contains an installed manager, not a pretend cache marker.
    cached = f"FROM {positives[0]}\nRUN " + bootstrap.replace(manager, wrong) + "\n"
    commands.build(empty, cached, failure="Corepack home must be fresh")
    if cached.count('mkdir "$COREPACK_HOME"') != 1:
        raise RuntimeError("build-input fresh-home countercontrol is ambiguous")
    commands.build(empty, cached.replace('mkdir "$COREPACK_HOME"', 'mkdir -p "$COREPACK_HOME"'))
    commands.build(
        empty,
        stage + "RUN ln -s /absent-build-input-home /opt/corepack\nRUN " + bootstrap + "\n",
        failure="Corepack home must be fresh",
    )
    return {
        "recipes": list(_NODE_RECIPES),
        "manager": manager,
        "coldHashMismatch": "rejected",
        "existingHome": "rejected",
        "unprotectedCacheCountercontrol": "accepted",
        "proofScope": "original archive; changed expected hash; not signed same-version tampering",
    }


def _required_files(snapshot: Path) -> dict[str, str]:
    paths = {snapshot / name for name in _REQUIRED_FILES}
    for directory in ("backend/src", "backend/alembic"):
        paths.update(path for path in (snapshot / directory).rglob("*") if path.is_file())
    if len(paths) > _MAX_MEMBERS:
        raise RuntimeError("build-input required-file population is oversized")
    return {
        path.relative_to(snapshot).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(paths)
    }


def _context_checks(commands: _Commands, snapshot: Path) -> dict[str, JsonValue]:
    required = _required_files(snapshot)
    ignore = snapshot / ".dockerignore"
    original = ignore.read_text(encoding="utf-8")
    for pattern, marker in _CONTEXT_MARKERS.items():
        if original.splitlines().count(pattern) != 1:
            raise RuntimeError("build-input exclusion is not uniquely declared")
        path = snapshot / marker
        if path.exists() or path.is_symlink():
            raise RuntimeError("build-input synthetic marker collides with tracked source")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("synthetic build-input marker\n", encoding="ascii")
    copied = commands.home / "context-positive"
    commands.context_files(snapshot, copied)
    for marker in _CONTEXT_MARKERS.values():
        if (copied / marker).exists() or (copied / marker).is_symlink():
            raise RuntimeError("Docker context admitted an excluded synthetic marker")
    for relative_path, expected in required.items():
        actual = copied / relative_path
        if not actual.is_file() or hashlib.sha256(actual.read_bytes()).hexdigest() != expected:
            raise RuntimeError(f"Docker context lost required bytes: {relative_path}")
    for number, (pattern, marker) in enumerate(_CONTEXT_MARKERS.items()):
        ignore.write_text(
            "\n".join(line for line in original.splitlines() if line != pattern) + "\n",
            encoding="utf-8",
        )
        control = commands.home / f"context-control-{number}"
        commands.context_files(snapshot, control)
        if (control / marker).read_text(encoding="ascii") != "synthetic build-input marker\n":
            raise RuntimeError("removed-rule control did not expose its exact marker")
    return {"excludedPopulations": len(_CONTEXT_MARKERS), "requiredFiles": len(required)}


def qualify_build_inputs(root: Path) -> dict[str, JsonValue]:
    """Return only after native positives, isolated negatives and owned cleanup."""
    deadline = time.monotonic() + _TOTAL_SECONDS
    if root.resolve() != Path(__file__).resolve().parents[1]:
        raise RuntimeError("build-input witness requires its exact repository root")
    if sys.platform != "linux":
        raise RuntimeError("build-input native qualification requires the owned Linux CI lane")
    home = Path(tempfile.gettempdir()) / f"ci-build-input-{os.getpid()}-{secrets.token_hex(16)}"
    commands = _Commands(root, home, deadline=deadline)
    snapshot = home / "source"
    empty = home / "empty"
    try:
        commands.create_home()
        empty.mkdir()
        head = _snapshot(commands, snapshot)
        commands.ensure_work_time()
        corepack = _corepack_checks(commands, snapshot, empty)
        commands.ensure_work_time()
        context = _context_checks(commands, snapshot)
        commands.ensure_work_time()
        if commands.execute("git", ("rev-parse", "HEAD")).stdout.strip() != head:
            raise RuntimeError("build-input source HEAD changed during qualification")
        if commands.execute("git", ("status", "--porcelain")).stdout:
            raise RuntimeError("build-input source changed during qualification")
    except BaseException as primary:
        try:
            commands.cleanup()
        except BaseException as secondary:
            _retain_secondary(primary, secondary)
        raise
    commands.cleanup()
    _before(deadline)
    return {
        "sourceCommit": head,
        "corepack": corepack,
        "context": context,
        "pythonBuildHashGuard": "source-counterguard; adversarial binary test not performed",
    }


def main() -> int:
    deadline = time.monotonic() + _TOTAL_SECONDS
    try:
        result = qualify_build_inputs(Path(__file__).resolve().parents[1])
        encoded = json.dumps(result, sort_keys=True)
        _before(deadline)
        sys.stdout.write(encoded + "\n")
        _before(deadline)
    except Exception as error:
        sys.stderr.write(f"build-input qualification failed: {error}\n")
        for note in getattr(error, "__notes__", ())[-4:]:
            if re.fullmatch(r"build-input cleanup uncertain: [A-Za-z_][A-Za-z_0-9]{0,63}", note):
                sys.stderr.write(note + "\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

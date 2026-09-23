from __future__ import annotations

import shlex
from collections.abc import Sequence
from pathlib import Path

from scripts.command_sequence import Command, run_commands

ACTIONLINT_VERSION = "1.7.12"
WORKFLOW_DIRECTORIES = (
    ".github/workflows",
    "fixtures/native-target-repository/.github/workflows",
    "fixtures/target-repository/.github/workflows",
)
SHELL_SOURCES = (
    (".github/actions/secret-scan/install.sh", "bash"),
    (".github/actions/secret-scan/run.sh", "bash"),
    (".devcontainer/post-create.sh", "bash"),
    (".githooks/pre-push", "sh"),
    (".qodana/preflight.sh", "sh"),
    ("docker/development/secret-entrypoint.sh", "sh"),
    ("docker/runtime/assemble.sh", "bash"),
    ("docker/runtime/install.sh", "sh"),
    ("docker/runtime/security/build.sh", "bash"),
)
_DOLLAR_ROOT_FALSE_POSITIVE = (
    r'^(?:reusable workflow call "\$/\.github/workflows/'
    r'(trusted-plan-request|api-contract|source-assurance)\.yml" '
    r'at "uses" is not following the format|'
    r'specifying action "\$/\.github/actions/secret-scan" in invalid format '
    r"because ref is missing\.)"
)


def commands(repo_root: Path) -> tuple[Command, ...]:
    image = f"ci-coordinator-actionlint:{ACTIONLINT_VERSION}"
    workflows = workflow_paths(repo_root)
    for source, _dialect in SHELL_SOURCES:
        path = repo_root / source
        _reject_symlink_components(repo_root, path)
        if not path.is_file():
            raise ValueError(f"shell lint source is not a regular file: {source}")
    container = (
        "docker",
        "run",
        "--rm",
        "--network",
        "none",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--volume",
        f"{repo_root}:/repo:ro",
        "--workdir",
        "/repo",
    )
    return (
        Command(
            (
                "docker",
                "build",
                "--pull",
                "--tag",
                image,
                "--file",
                "Dockerfile.workflow-lint",
                ".",
            ),
            repo_root,
        ),
        Command(
            (
                *container,
                image,
                "-color",
                "-ignore",
                _DOLLAR_ROOT_FALSE_POSITIVE,
                "--",
                *workflows,
            ),
            repo_root,
        ),
        *(
            Command(
                (
                    *container,
                    "--entrypoint",
                    "/usr/local/bin/shellcheck",
                    image,
                    "--norc",
                    f"--shell={dialect}",
                    "--",
                    source,
                ),
                repo_root,
            )
            for source, dialect in SHELL_SOURCES
        ),
        Command(
            (
                str(repo_root / "backend" / ".venv" / "bin" / "zizmor"),
                "--offline",
                "--persona",
                "pedantic",
                ".github",
                "fixtures/native-target-repository/.github",
                "fixtures/target-repository/.github",
            ),
            repo_root,
        ),
    )


def workflow_paths(repo_root: Path) -> tuple[str, ...]:
    paths: list[str] = []
    for directory in WORKFLOW_DIRECTORIES:
        root = repo_root / directory
        _reject_symlink_components(repo_root, root)
        if not root.is_dir():
            raise ValueError(f"workflow lint directory is unavailable: {directory}")
        candidates = sorted(path for path in root.iterdir() if path.suffix in {".yml", ".yaml"})
        if not candidates:
            raise ValueError(f"workflow lint directory is empty: {directory}")
        for path in candidates:
            _reject_symlink_components(repo_root, path)
            if not path.is_file():
                raise ValueError(f"workflow lint source is not a regular file: {path.name}")
            paths.append(path.relative_to(repo_root).as_posix())
    return tuple(sorted(paths))


def _reject_symlink_components(repo_root: Path, path: Path) -> None:
    current = repo_root
    for part in path.relative_to(repo_root).parts:
        current /= part
        if current.is_symlink():
            raise ValueError(f"lint input contains a symlink: {current.relative_to(repo_root)}")


def shell_source_paths(repo_root: Path, tracked_paths: Sequence[str]) -> tuple[str, ...]:
    shells = {"sh", "bash", "dash", "ash", "ksh", "zsh", "fish", "csh", "tcsh"}
    sources: list[str] = []
    for name in tracked_paths:
        path = repo_root / name
        if path.suffix.removeprefix(".") in shells:
            sources.append(name)
            continue
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as source:
            prefix = source.readline(513)
        if not prefix.startswith(b"#!"):
            continue
        if len(prefix) > 512:
            raise ValueError(f"shell inventory shebang exceeds its bound: {name}")
        tokens = shlex.split(prefix[2:].decode("utf-8"))
        if tokens and (
            Path(tokens[0]).name in shells
            or (
                Path(tokens[0]).name == "env"
                and any(Path(token).name in shells for token in tokens[1:])
            )
        ):
            sources.append(name)
    return tuple(sorted(sources))


def main() -> int:
    repo_root = Path(__file__).resolve().parent.parent
    return run_commands(commands(repo_root))


if __name__ == "__main__":
    raise SystemExit(main())

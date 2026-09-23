from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Protocol, TypedDict

VERSION = "8.30.1"
MAX_FILES = 32_768
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TREE_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024 * 1024
MAX_HISTORY_BYTES = 128 * 1024 * 1024
MAX_COMMITS = 1024
MAX_REPORT_BYTES = 16 * 1024 * 1024
SCAN_TIMEOUT = 180
FINDINGS_EXIT = 23
SHA = re.compile(r"[0-9a-f]{40}")
POLICY_ROOT = Path(__file__).resolve().parent
LOG_OPTIONS = ("--full-history", "--no-ext-diff", "--no-textconv", "--no-renames", "--text", "-m")


class ScanError(RuntimeError):
    def __init__(self, message: str, *, stage: str, code: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.code = code


class FindingCoordinate(TypedDict):
    file: str
    rule: str
    line: int
    scope: str
    commit: str


class ScanSummary(TypedDict):
    scope: str
    tree_files: int
    tree_bytes: int
    history_commits: int
    findings: int
    locations: list[FindingCoordinate]


class Runner(Protocol):
    def __call__(
        self, argv: list[str], cwd: Path, environment: dict[str, str], limit: int, timeout: int
    ) -> tuple[int, bytes]: ...


class Reader(Protocol):
    def __call__(self, root: Path, relative: Path, maximum: int) -> bytes: ...


def child_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_ALLOW_PROTOCOL": "",
    }


def git(
    runner: Runner, root: Path, arguments: list[str], *, limit: int = MAX_MANIFEST_BYTES
) -> bytes:
    executable = shutil.which("git", path=child_environment()["PATH"])
    if executable is None:
        raise ScanError("Git executable unavailable", stage="git", code="git_unavailable")
    status, output = runner(
        [executable, "--no-replace-objects", *arguments], root, child_environment(), limit, 30
    )
    if status != 0:
        raise ScanError("Git scope command failed", stage="git", code="git_command_failed")
    return output


def manifest(runner: Runner, root: Path) -> tuple[Path, ...]:
    discovered = git(runner, root, ["rev-parse", "--show-toplevel"])
    try:
        top = Path(discovered.decode("utf-8").removesuffix("\n"))
    except UnicodeError as error:
        raise ScanError(
            "repository root encoding is unsupported", stage="tree_manifest", code="root_encoding"
        ) from error
    if top.resolve() != root.resolve():
        raise ScanError(
            "source must be the complete repository root",
            stage="tree_manifest",
            code="partial_root",
        )
    payload = git(runner, root, ["ls-files", "--cached", "--others", "--exclude-standard", "-z"])
    if not payload or not payload.endswith(b"\0"):
        raise ScanError(
            "missing or empty Git-observed tree", stage="tree_manifest", code="empty_tree"
        )
    names = payload[:-1].split(b"\0")
    if len(names) > MAX_FILES or len(set(names)) != len(names):
        raise ScanError(
            "tree manifest count or identity is invalid",
            stage="tree_manifest",
            code="invalid_population",
        )
    result: list[Path] = []
    for name in names:
        try:
            text = name.decode("utf-8")
        except UnicodeError as error:
            raise ScanError(
                "tree path encoding is unsupported", stage="tree_manifest", code="path_encoding"
            ) from error
        parts = text.split("/")
        if (
            len(name) > 2048
            or len(parts) > 32
            or any(part in {"", ".", "..", ".git"} for part in parts)
            or any(ord(character) < 32 or ord(character) == 127 for character in text)
            or Path(text).is_absolute()
        ):
            raise ScanError(
                "tree path is outside the admitted grammar",
                stage="tree_manifest",
                code="path_grammar",
            )
        result.append(Path(text))
    return tuple(result)


def stage_tree(
    root: Path, destination: Path, paths: tuple[Path, ...], reader: Reader
) -> tuple[int, dict[str, str]]:
    total = 0
    projected_paths: dict[str, str] = {}
    ignored_policy: bytes | None = None
    files = destination / "files"
    files.mkdir(parents=True)
    for relative in paths:
        try:
            payload = reader(root, relative, MAX_FILE_BYTES)
        except (OSError, ValueError) as error:
            raise ScanError(
                "tree requires stable bounded regular files and real parents",
                stage="tree_snapshot",
                code="unsafe_file",
            ) from error
        total += len(payload)
        if total > MAX_TREE_BYTES:
            raise ScanError(
                "tree content exceeds its bound", stage="tree_snapshot", code="content_limit"
            )
        if relative == Path(".gitleaksignore"):
            ignored_policy = payload
            continue
        target = files / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payload)
        projected_paths[relative.as_posix()] = relative.as_posix()
    if ignored_policy is not None:
        # Gitleaks loads source/.gitleaksignore even with an explicit ignore path.
        holder = Path(tempfile.mkdtemp(prefix="policy-data-", dir=files))
        target = holder / ".gitleaksignore"
        target.write_bytes(ignored_policy)
        projected_paths[target.relative_to(files).as_posix()] = ".gitleaksignore"
    if total == 0:
        raise ScanError("tree contains no content", stage="tree_snapshot", code="empty_content")
    if len(projected_paths) != len(paths) or set(projected_paths.values()) != {
        path.as_posix() for path in paths
    }:
        raise ScanError(
            "tree snapshot does not preserve the manifest",
            stage="tree_snapshot",
            code="projection_mismatch",
        )
    return total, projected_paths


def history_scope(runner: Runner, root: Path, base: str, head: str) -> tuple[str, ...]:
    if (
        SHA.fullmatch(base) is None
        or SHA.fullmatch(head) is None
        or base == "0" * 40
        or head == "0" * 40
        or base == head
    ):
        raise ScanError(
            "required history needs distinct full nonzero commit identities",
            stage="history_identity",
            code="invalid_range",
        )
    if git(runner, root, ["rev-parse", "--is-shallow-repository"]).strip() != b"false":
        raise ScanError(
            "required history rejects shallow repositories",
            stage="history_identity",
            code="shallow_repository",
        )
    for identity in (base, head):
        if git(runner, root, ["rev-parse", "--verify", f"{identity}^{{commit}}"]).strip() != (
            identity.encode()
        ):
            raise ScanError(
                "history commit identity mismatch", stage="history_identity", code="commit_mismatch"
            )
    if git(runner, root, ["rev-parse", "--verify", "HEAD"]).strip() != head.encode():
        raise ScanError(
            "history head must equal the checked-out subject",
            stage="history_identity",
            code="checkout_mismatch",
        )
    git(runner, root, ["merge-base", "--is-ancestor", base, head])
    commits = (
        git(runner, root, ["rev-list", f"--max-count={MAX_COMMITS + 1}", f"{base}..{head}", "--"])
        .decode("ascii")
        .splitlines()
    )
    if not commits or len(commits) > MAX_COMMITS or any(not SHA.fullmatch(v) for v in commits):
        raise ScanError(
            "required history commit population is empty or exceeds its bound",
            stage="history_identity",
            code="commit_population",
        )
    git(
        runner,
        root,
        ["log", "-p", "-U0", *LOG_OPTIONS, f"{base}..{head}", "--"],
        limit=MAX_HISTORY_BYTES,
    )
    return tuple(commits)


def isolated_history(runner: Runner, root: Path, destination: Path) -> None:
    objects = git(runner, root, ["rev-parse", "--path-format=absolute", "--git-path", "objects"])
    try:
        object_path = Path(objects.decode("utf-8").removesuffix("\n"))
    except UnicodeError as error:
        raise ScanError(
            "Git object path encoding is unsupported",
            stage="history_projection",
            code="object_path_encoding",
        ) from error
    if (
        not object_path.is_absolute()
        or not object_path.is_dir()
        or any(character in str(object_path) for character in "\r\n\0")
    ):
        raise ScanError(
            "Git object path is invalid", stage="history_projection", code="invalid_object_path"
        )
    git(runner, root, ["init", "--bare", "--quiet", "--template=", str(destination)])
    (destination / "objects" / "info" / "alternates").write_text(
        str(object_path) + "\n", encoding="utf-8"
    )


def admit_report(
    payload: bytes, status: int, commits: tuple[str, ...] = ()
) -> list[FindingCoordinate]:
    try:
        findings = json.loads(payload)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise ScanError(
            "scanner report is malformed", stage="report_admission", code="malformed_report"
        ) from error
    if not isinstance(findings, list):
        raise ScanError(
            "scanner report must be an explicit finding array",
            stage="report_admission",
            code="invalid_report_shape",
        )
    coordinates: list[FindingCoordinate] = []
    for finding in findings:
        if (
            not isinstance(finding, dict)
            or finding.get("Secret") != "REDACTED"
            or not isinstance(finding.get("RuleID"), str)
            or re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", finding["RuleID"]) is None
            or not isinstance(finding.get("File"), str)
            or not finding["File"]
            or type(finding.get("StartLine")) is not int
            or finding["StartLine"] < 1
            or (commits and finding.get("Commit") not in commits)
            or (not commits and finding.get("Commit", "") != "")
        ):
            raise ScanError(
                "scanner finding schema, redaction or scope is invalid",
                stage="report_admission",
                code="invalid_finding",
            )
        coordinates.append(
            {
                "file": finding["File"],
                "rule": finding["RuleID"],
                "line": finding["StartLine"],
                "scope": "history" if commits else "tree",
                "commit": finding.get("Commit", ""),
            }
        )
    if status != (FINDINGS_EXIT if findings else 0):
        raise ScanError(
            "scanner exit status and report disagree",
            stage="report_admission",
            code="status_mismatch",
        )
    return coordinates


def bind_coordinates(
    coordinates: list[FindingCoordinate], paths: dict[str, str], mode: str
) -> list[FindingCoordinate]:
    for coordinate in coordinates:
        name = coordinate["file"]
        if mode == "dir":
            if name not in paths:
                raise ScanError(
                    "scanner report refers to a file outside the admitted tree",
                    stage="coordinate_admission",
                    code="foreign_file",
                )
            coordinate["file"] = paths[name]
        elif (
            Path(name).is_absolute()
            or len(name.encode("utf-8")) > 2048
            or any(part in {"", ".", "..", ".git"} for part in name.split("/"))
            or any(ord(character) < 32 or ord(character) == 127 for character in name)
        ):
            raise ScanError(
                "scanner history coordinate is outside the admitted grammar",
                stage="coordinate_admission",
                code="invalid_history_path",
            )
    return coordinates


def scan_scope(
    runner: Runner,
    reader: Reader,
    executable: Path,
    source: Path,
    scratch: Path,
    mode: str,
    *,
    base: str = "",
    head: str = "",
    commits: tuple[str, ...] = (),
    paths: dict[str, str] | None = None,
) -> list[FindingCoordinate]:
    report = scratch / f"{mode}.json"
    working_directory = source if mode == "dir" else scratch
    arguments = [
        str(executable),
        mode,
        "." if mode == "dir" else str(source),
        "--config",
        str(POLICY_ROOT / "policy.toml"),
        "--gitleaks-ignore-path",
        str(POLICY_ROOT / "empty.ignore"),
        "--ignore-gitleaks-allow",
        "--redact=100",
        "--no-banner",
        "--no-color",
        "--log-level=error",
        f"--exit-code={FINDINGS_EXIT}",
        "--report-format=json",
        "--report-path",
        str(report),
        "--max-target-megabytes=0",
        "--max-decode-depth=5",
        "--max-archive-depth=0",
    ]
    if mode == "git":
        arguments.extend(["--log-opts", " ".join((*LOG_OPTIONS, f"{base}..{head}", "--"))])
    status, diagnostics = runner(
        arguments, working_directory, child_environment(), MAX_REPORT_BYTES, SCAN_TIMEOUT
    )
    # Gitleaks can log fragment errors without returning a nonzero process status.
    if diagnostics or status not in {0, FINDINGS_EXIT}:
        raise ScanError(
            "scanner execution failed or emitted error diagnostics",
            stage="scanner_execution",
            code="tool_failure",
        )
    try:
        payload = reader(scratch, report.relative_to(scratch), MAX_REPORT_BYTES)
    except (OSError, ValueError) as error:
        raise ScanError(
            "scanner report is missing or not a bounded regular file",
            stage="report_read",
            code="missing_or_unbounded_report",
        ) from error
    return bind_coordinates(admit_report(payload, status, commits), paths or {}, mode)


def scan(
    root: Path,
    executable: Path,
    runner: Runner,
    reader: Reader,
    *,
    history: bool = False,
    base: str = "",
    head: str = "",
) -> ScanSummary:
    root = Path(os.path.abspath(root))
    try:
        executable = executable.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise ScanError(
            "scanner executable cannot be resolved", stage="tool_identity", code="tool_unavailable"
        ) from error
    if not root.is_dir() or stat.S_ISLNK(root.lstat().st_mode):
        raise ScanError(
            "source root must be a real repository directory",
            stage="source_admission",
            code="invalid_source_root",
        )
    if not executable.is_file() or stat.S_ISLNK(executable.lstat().st_mode):
        raise ScanError(
            "scanner executable must be a regular file",
            stage="tool_identity",
            code="invalid_tool_file",
        )
    status, version = runner(
        [str(executable), "version"], POLICY_ROOT, child_environment(), 4096, 10
    )
    if status or version.strip() != VERSION.encode():
        raise ScanError(
            "scanner version does not match the admitted pin",
            stage="tool_identity",
            code="version_mismatch",
        )
    if not history and (base or head):
        raise ScanError(
            "history identities require explicit history scope",
            stage="scope_selection",
            code="ambiguous_history",
        )
    paths = manifest(runner, root)
    commits = history_scope(runner, root, base, head) if history else ()
    with tempfile.TemporaryDirectory(prefix="ci-secret-scan-") as temporary:
        scratch = Path(temporary)
        tree = scratch / "tree"
        size, projected_paths = stage_tree(root, tree, paths, reader)
        findings = scan_scope(
            runner, reader, executable, tree / "files", scratch, "dir", paths=projected_paths
        )
        if history:
            history_root = scratch / "history.git"
            isolated_history(runner, root, history_root)
            findings += scan_scope(
                runner,
                reader,
                executable,
                history_root,
                scratch,
                "git",
                base=base,
                head=head,
                commits=commits,
            )
    return {
        "scope": "tree-and-history" if history else "tree-only",
        "tree_files": len(paths),
        "tree_bytes": size,
        "history_commits": len(commits),
        "findings": len(findings),
        "locations": findings,
    }


def main(runner: Runner, reader: Reader, *, default_root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded tree and exact-history secret scanning")
    parser.add_argument("--root", type=Path, default=default_root or Path.cwd())
    parser.add_argument("--gitleaks", type=Path)
    parser.add_argument("--history", choices=("none", "required"), default="none")
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    options = parser.parse_args()
    executable = options.gitleaks
    if executable is None:
        configured = os.environ.get("GITLEAKS_TEST_BINARY") or shutil.which(
            "gitleaks", path=child_environment()["PATH"]
        )
        if configured is None:
            print(
                json.dumps(
                    {"status": "error", "stage": "tool_identity", "reason": "tool_unavailable"}
                )
            )
            return 2
        executable = Path(configured)
    try:
        result = scan(
            options.root,
            executable,
            runner,
            reader,
            history=options.history == "required",
            base=options.base,
            head=options.head,
        )
    except ScanError as error:
        print(json.dumps({"status": "error", "stage": error.stage, "reason": error.code}))
        return 2
    except OSError:
        print(json.dumps({"status": "error", "stage": "filesystem", "reason": "io_failure"}))
        return 2
    except ValueError:
        print(json.dumps({"status": "error", "stage": "input", "reason": "invalid_value"}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 1 if result["findings"] else 0

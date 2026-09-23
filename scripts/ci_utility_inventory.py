from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from scripts.bounded_git import git_stdout_bytes, run_git
from scripts.repository_source_admission import read_bounded_repository_text

MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_PATHS = 50_000
MAX_PATH_BYTES = 4_096
MAX_TOTAL_SOURCE_BYTES = 256 * 1024 * 1024
QUALITY_DIRECTORY = "tooling/quality"
DEVCONTAINER_SCHEMA = f"{QUALITY_DIRECTORY}/schemas/devcontainers/devContainer.base.schema.json"
SCHEMA_EXCLUSIONS = {
    "docker/development/compose.debug.yaml": (
        "Compose !reset overlay; scripts/dev_environment/debug_witness.py and "
        "scripts/dev_environment/compose.py own native Compose config validation"
    ),
}
SPELLING_LINE_FILES = {
    "backend/tests/unit/config_control/test_semantics.py": "semantic-fixtures.txt",
    "scripts/dev_environment/log_transport.py": "http-header.txt",
    "scripts/dev_environment/watch_witness.py": "compose-diagnostic.txt",
    "scripts/tests/test_dev_environment_log_transport.py": "transport-fixtures.txt",
    "scripts/tests/test_documentation_graph.py": "documentation-fixtures.txt",
}
_SPDX_SOURCE_DIGESTS = {
    "docs/specs/ci-coordinator-release/spdx-2.3.schema.json": (
        "23b238cde51ad35021a61eb79639814c91a436b1d62061a1122aba6107b1c927"
    ),
    "docs/specs/ci-coordinator-release/SPDX-LICENSE.txt": (
        "a69d068ec0e987513259d3d355f10c1b39cae1bfb275e8a6ed250b8c1d17531f"
    ),
}
_BINARY_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".pdf"})
_GENERATED_SPELLING_PATHS = frozenset(
    {
        "pnpm-lock.yaml",
        "mise.lock",
        "backend/uv.lock",
        "backend/requirements-dev.lock",
        "tooling/quality/uv.lock",
        "tooling/actionlint/go.sum",
    }
)
_GO_BUILD_SUFFIX = re.compile(
    r"_(?:aix|android|darwin|dragonfly|freebsd|illumos|ios|js|linux|netbsd|openbsd|plan9|"
    r"solaris|wasip1|windows|386|amd64|arm|arm64|loong64|mips|mipsle|mips64|mips64le|"
    r"ppc64|ppc64le|riscv64|s390x|wasm)(?:_test)?\.go$"
)
_METASCHEMAS = frozenset(
    f"{scheme}://json-schema.org/{draft}/schema{fragment}"
    for scheme in ("http", "https")
    for draft in ("draft-04", "draft-06", "draft-07", "draft/2019-09", "draft/2020-12")
    for fragment in ("", "#")
)


def repository_paths(root: Path) -> tuple[str, ...]:
    """Git-observed tracked plus nonignored untracked paths; caps fail closed."""
    result = run_git(
        root,
        ("ls-files", "--cached", "--others", "--exclude-standard", "-z"),
        decode_errors="surrogateescape",
    )
    raw = git_stdout_bytes(result)
    if raw and not raw.endswith(b"\0"):
        raise ValueError("utility Git inventory is not NUL terminated")
    names = tuple(
        sorted(set(value.decode("utf-8", "strict") for value in raw.split(b"\0") if value))
    )
    if not names or len(names) > MAX_PATHS:
        raise ValueError("utility Git inventory is empty or exceeds its path bound")
    for name in names:
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or not path.parts
            or path.as_posix() != name
            or ".." in path.parts
            or len(name.encode()) > MAX_PATH_BYTES
            or any(ord(character) < 32 or ord(character) == 127 for character in name)
        ):
            raise ValueError(f"unsupported utility input path: {name!r}")
    return names


def source_text(root: Path, name: str) -> str:
    return read_bounded_repository_text(root, PurePosixPath(name), maximum_bytes=MAX_SOURCE_BYTES)


def admitted_sources(root: Path, names: tuple[str, ...]) -> tuple[str, ...]:
    if not names:
        raise ValueError("utility check has no applicable input files")
    total = 0
    for name in names:
        content = source_text(root, name)
        total += len(content.encode("utf-8"))
        if "\0" in content:
            raise ValueError(f"utility input is not text: {name}")
        if total > MAX_TOTAL_SOURCE_BYTES:
            raise ValueError("utility inputs exceed their aggregate source bound")
    return names


def is_dockerfile(name: str) -> bool:
    basename = PurePosixPath(name).name
    return (
        basename == "Dockerfile"
        or basename.startswith("Dockerfile.")
        or basename.endswith(".Dockerfile")
    )


def spelling_exclusion(name: str) -> str | None:
    if PurePosixPath(name).suffix.lower() in _BINARY_SUFFIXES:
        return "binary asset"
    if name in _GENERATED_SPELLING_PATHS:
        return "generated dependency resolution data"
    if name in {DEVCONTAINER_SCHEMA, f"{QUALITY_DIRECTORY}/schemas/devcontainers/LICENSE-CODE"}:
        return "immutable upstream source; core schema provenance is checked separately"
    if name in _SPDX_SOURCE_DIGESTS:
        return "immutable SPDX source; release specification owns exact upstream provenance"
    if name in {f"{QUALITY_DIRECTORY}/spelling/{path}" for path in SPELLING_LINE_FILES.values()}:
        return "exact spelling exclusion lines; validated against their owning source file"
    return None


def spelling_line_file(root: Path, name: str) -> str | None:
    if name not in SPELLING_LINE_FILES:
        return None
    excluded = f"{QUALITY_DIRECTORY}/spelling/{SPELLING_LINE_FILES[name]}"
    lines = [line.rstrip() for line in source_text(root, excluded).splitlines()]
    source = [line.rstrip() for line in source_text(root, name).splitlines()]
    if (
        not lines
        or len(lines) != len(set(lines))
        or any(not line or source.count(line) != 1 for line in lines)
    ):
        raise ValueError(f"stale, duplicate or widened spelling line exception: {name}")
    return excluded


def admit_spelling_vendor_exclusions(root: Path, names: tuple[str, ...]) -> None:
    for source, expected in _SPDX_SOURCE_DIGESTS.items():
        if (
            source in names
            and hashlib.sha256(source_text(root, source).encode()).hexdigest() != expected
        ):
            raise ValueError("vendored SPDX source spelling exclusion has stale provenance")
    for source, excluded in SPELLING_LINE_FILES.items():
        exception = f"{QUALITY_DIRECTORY}/spelling/{excluded}"
        if exception in names and source not in names:
            raise ValueError(f"orphan spelling exception: {exception}")


def go_modules(root: Path, names: tuple[str, ...]) -> tuple[str, ...]:
    """Admit declared Go paths without running the native package-selection owner."""
    modules = tuple(
        PurePosixPath(name).parent for name in names if PurePosixPath(name).name == "go.mod"
    )
    go_files = tuple(name for name in names if name.endswith(".go"))
    admitted_sources(root, go_files)
    if not modules:
        raise ValueError("Go inputs have no module owner")
    populated = set()
    for name in go_files:
        path = PurePosixPath(name)
        owners = [module for module in modules if path.is_relative_to(module)]
        if not owners:
            raise ValueError(f"Go input has no module owner: {name}")
        owner = max(owners, key=lambda module: len(module.parts))
        populated.add(owner)
        relative = path.relative_to(owner)
        if any(
            part.startswith((".", "_")) or part in {"testdata", "vendor"} for part in relative.parts
        ):
            raise ValueError(f"Go input is excluded by ./... package discovery: {name}")
        source = source_text(root, name)
        if _GO_BUILD_SUFFIX.search(path.name) or re.search(
            r"(?m)^//\s*(?:go:build|\+build)\b", source
        ):
            raise ValueError(f"Go input needs an explicit build-configuration owner: {name}")
    for module in modules:
        admitted_sources(root, ((module / "go.mod").as_posix(),))
        if module not in populated:
            raise ValueError(f"Go module has no owned source files: {module}")
    if any(PurePosixPath(name).name in {"go.work", "staticcheck.conf"} for name in names):
        raise ValueError(
            "Go workspace or Staticcheck config requires explicit utility policy admission"
        )
    return tuple(sorted(module.as_posix() for module in modules))


def go_module_sources(names: tuple[str, ...], module: str) -> tuple[str, ...]:
    """Project the complete declared Go cohort onto its nearest module owner."""
    modules = tuple(
        PurePosixPath(name).parent for name in names if PurePosixPath(name).name == "go.mod"
    )
    selected = PurePosixPath(module)
    sources = []
    for name in names:
        path = PurePosixPath(name)
        if path.suffix != ".go" or not path.is_relative_to(selected):
            continue
        owner = max(
            (candidate for candidate in modules if path.is_relative_to(candidate)),
            key=lambda candidate: len(candidate.parts),
        )
        if owner == selected:
            sources.append(path.relative_to(selected).as_posix())
    if not sources:
        raise ValueError(f"Go module has no declared source cohort: {module}")
    return tuple(sorted(sources))


def admit_go_effective_inputs(module_root: Path, expected: tuple[str, ...], stdout: str) -> None:
    """Require native go-list selection to cover exactly the admitted Git cohort."""
    if not expected or len(expected) != len(set(expected)):
        raise ValueError("native Go admission requires a nonempty unique declared cohort")
    decoder = json.JSONDecoder(object_pairs_hook=_unique_go_keys)
    offset = 0
    directories: set[str] = set()
    effective: set[str] = set()
    fields = (
        "GoFiles",
        "TestGoFiles",
        "XTestGoFiles",
        "CgoFiles",
        "IgnoredGoFiles",
        "InvalidGoFiles",
    )
    while offset < len(stdout):
        if stdout[offset].isspace():
            offset += 1
            continue
        package, offset = decoder.raw_decode(stdout, offset)
        if not isinstance(package, dict) or "Dir" not in package:
            raise ValueError("native Go inventory has an incomplete package record")
        # Go 1.27.1 marshals PackagePublic with omitempty even for requested
        # -json fields. Absent lists and error flags therefore carry zero values;
        # the exact source-cohort equality below still rejects lost input files.
        if package.get("Error") is not None or package.get("Incomplete", False) is not False:
            raise ValueError("native Go package discovery reported an error")
        directory = package["Dir"]
        if not isinstance(directory, str) or not Path(directory).is_absolute():
            raise ValueError("native Go package directory is not absolute")
        relative = Path(directory).relative_to(module_root)
        if ".." in relative.parts or str(module_root / relative) != directory:
            raise ValueError("native Go package directory escaped its module")
        if directory in directories or len(directories) >= MAX_PATHS:
            raise ValueError("native Go package inventory is duplicate or exceeds its bound")
        directories.add(directory)
        for field in fields:
            files = package.get(field, [])
            if files is None:
                files = []
            if not isinstance(files, list) or any(
                not isinstance(name, str) or Path(name).name != name or not name.endswith(".go")
                for name in files
            ):
                raise ValueError(f"native Go inventory has invalid {field}")
            if field in {"CgoFiles", "IgnoredGoFiles", "InvalidGoFiles"} and files:
                raise ValueError(f"native Go inventory contains unsupported {field}: {files!r}")
            for name in files:
                source = (relative / name).as_posix()
                if source in effective or len(effective) >= MAX_PATHS:
                    raise ValueError("native Go source inventory is duplicate or exceeds its bound")
                effective.add(source)
    if effective != set(expected):
        raise ValueError(
            "native Go source selection differs from the declared Git cohort: "
            f"missing={sorted(set(expected) - effective)!r}; "
            f"extra={sorted(effective - set(expected))!r}"
        )


def _unique_go_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    values: dict[str, object] = {}
    for key, value in pairs:
        if key in values:
            raise ValueError(f"native Go inventory has duplicate JSON key: {key}")
        values[key] = value
    return values


def schema_groups(root: Path, names: tuple[str, ...]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    groups: dict[str, list[str]] = {}
    for name in names:
        path = PurePosixPath(name)
        schema = None
        if path.name in {"dependabot.yml", "dependabot.yaml"} and path.parent.name == ".github":
            schema = "vendor.dependabot"
        elif path.name == "devcontainer.json":
            schema = DEVCONTAINER_SCHEMA
        elif (
            path.suffix in {".yaml", ".yml"}
            and name not in SCHEMA_EXCLUSIONS
            and (
                path.stem in {"compose", "docker-compose"}
                or path.stem.startswith(("compose.", "docker-compose."))
            )
        ):
            schema = "vendor.compose-spec"
        if schema is not None:
            groups.setdefault(schema, []).append(name)
        if path.suffix == ".json" and ".schema." in path.name:
            payload = json.loads(source_text(root, name))
            if not isinstance(payload, dict) or payload.get("$schema") not in _METASCHEMAS:
                raise ValueError(f"JSON Schema needs an admitted offline metaschema: {name}")
            groups.setdefault("metaschema", []).append(name)
    if not groups:
        raise ValueError("schema check has no applicable input files")
    return tuple(
        (schema, admitted_sources(root, tuple(paths))) for schema, paths in sorted(groups.items())
    )


def admit_devcontainer_schema(root: Path) -> None:
    source = source_text(root, DEVCONTAINER_SCHEMA)
    provenance = json.loads(
        source_text(root, f"{QUALITY_DIRECTORY}/schemas/devcontainers/provenance.json")
    )
    if hashlib.sha256(source.encode("utf-8")).hexdigest() != provenance["sha256"]:
        raise ValueError("vendored devcontainer core schema differs from its provenance")
    pending = [json.loads(source)]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            reference = node.get("$ref", "#")
            if not isinstance(reference, str) or not reference.startswith("#"):
                raise ValueError("vendored devcontainer schema has a nonlocal reference")
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)

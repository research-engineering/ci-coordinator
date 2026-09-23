from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scripts.python_import_authority_scanner import (
    _module_object_authority,
    scan_imported_modules,
)


@dataclass(frozen=True, slots=True)
class ImportRule:
    applies: Callable[[str], bool]
    forbidden: tuple[str, ...]
    rule_id: str
    allowed_first_party_prefixes: tuple[str, ...] | None = None
    allowed_import_prefixes: tuple[str, ...] | None = None
    allowed_external_prefixes: tuple[str, ...] | None = None
    allowed_private_first_party_prefixes: tuple[str, ...] | None = None

    def violates(self, imported: str) -> bool:
        allowed_import = self.allowed_import_prefixes is not None and matches_import_prefix(
            imported, self.allowed_import_prefixes
        )
        explicit_violation = not allowed_import and matches_import_prefix(imported, self.forbidden)
        first_party_violation = (
            self.allowed_first_party_prefixes is not None
            and is_forbidden_first_party_import(imported, self.allowed_first_party_prefixes)
        )
        external_violation = (
            self.allowed_external_prefixes is not None
            and not is_first_party_import(imported)
            and not matches_import_prefix(imported, self.allowed_external_prefixes)
        )
        private_violation = (
            self.allowed_private_first_party_prefixes is not None
            and is_first_party_import(imported)
            and any(part.startswith("_") for part in imported.split(".")[1:])
            and not matches_import_prefix(imported, self.allowed_private_first_party_prefixes)
        )
        return (
            first_party_violation or explicit_violation or external_violation or private_violation
        )


@dataclass(frozen=True, slots=True)
class PythonSourceInventory:
    files: tuple[Path, ...]
    symlinks: tuple[Path, ...]


def module_object_authority(module: str) -> str:
    return _module_object_authority(module)


def inventory_python_sources(root: Path) -> PythonSourceInventory:
    files: list[Path] = []
    symlinks: list[Path] = []

    def visit(directory: Path) -> None:
        for entry in sorted(directory.iterdir()):
            if entry.is_symlink():
                symlinks.append(entry)
            elif entry.is_dir():
                visit(entry)
            elif entry.is_file() and entry.suffix == ".py":
                files.append(entry)

    if root.is_symlink():
        symlinks.append(root)
    elif root.is_dir():
        visit(root)
    return PythonSourceInventory(tuple(sorted(files)), tuple(sorted(symlinks)))


def imported_modules(
    path: Path,
    source_root: Path,
    *,
    ambient_authority_prefixes: tuple[str, ...] = (),
    restricted_module_objects: tuple[str, ...] = (),
) -> tuple[str, ...]:
    resolved_path = path.resolve()
    resolved_source_root = source_root.resolve()
    relative = resolved_path.relative_to(resolved_source_root)
    module_parts = ("ci_coordinator", *relative.with_suffix("").parts)
    current_package = ".".join(module_parts[:-1])

    return scan_imported_modules(
        resolved_path.read_text(encoding="utf-8"),
        filename=str(resolved_path),
        current_package=current_package,
        ambient_authority_prefixes=ambient_authority_prefixes,
        restricted_module_objects=restricted_module_objects,
    )


def is_forbidden_first_party_import(imported: str, allowed_prefixes: tuple[str, ...]) -> bool:
    if imported == "ci_coordinator":
        return False
    if not imported.startswith("ci_coordinator."):
        return False
    return not matches_import_prefix(imported, allowed_prefixes)


def is_first_party_import(imported: str) -> bool:
    return imported == "ci_coordinator" or imported.startswith("ci_coordinator.")


def matches_import_prefix(imported: str, prefixes: tuple[str, ...]) -> bool:
    return any(imported == prefix or imported.startswith(f"{prefix}.") for prefix in prefixes)

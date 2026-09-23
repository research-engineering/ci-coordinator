"""Filesystem workflow for deterministic target artifacts."""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ci_coordinator.target_artifacts.model import RenderedTargetArtifacts


def check_artifacts(
    output_directory: Path,
    rendered: RenderedTargetArtifacts,
) -> tuple[str, ...]:
    """Return canonical filenames that are absent or byte-different."""
    _require_inputs(output_directory, rendered)
    drift: list[str] = []
    for filename, expected in rendered.by_filename():
        if not check_exact_file(output_directory / filename, expected):
            drift.append(filename)
    return tuple(drift)


def write_artifacts(output_directory: Path, rendered: RenderedTargetArtifacts) -> None:
    """Publish a complete set or restore the prior set after a caught failure."""
    _require_inputs(output_directory, rendered)
    output_directory.mkdir(parents=True, exist_ok=True)
    replacements = _prepare_replacements(output_directory, rendered)
    try:
        for replacement in replacements:
            if replacement.backup is not None:
                replacement.target.replace(replacement.backup)
            replacement.staged.replace(replacement.target)
    except BaseException as error:
        recovery_errors = _rollback(replacements)
        cleanup_errors = _cleanup_replacements(
            replacements,
            preserve_recovery_backups=bool(recovery_errors),
        )
        if recovery_errors or cleanup_errors:
            raise OSError("target artifact publication recovery failed") from error
        raise
    cleanup_errors = _cleanup_replacements(replacements)
    if cleanup_errors:
        raise OSError("target artifact publication cleanup failed")


def check_exact_file(path: Path, expected: bytes) -> bool:
    if not isinstance(path, Path) or type(expected) is not bytes or not expected:
        raise TypeError("exact-file check requires a Path and non-empty bytes")
    try:
        return not path.is_symlink() and path.read_bytes() == expected
    except OSError:
        return False


def write_exact_file(path: Path, content: bytes) -> None:
    if not isinstance(path, Path) or type(content) is not bytes or not content:
        raise TypeError("exact-file write requires a Path and non-empty bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = _stage_file(path, content)
    try:
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _require_inputs(output_directory: Path, rendered: RenderedTargetArtifacts) -> None:
    if not isinstance(output_directory, Path):
        raise TypeError("output directory must be a Path")
    if type(rendered) is not RenderedTargetArtifacts:
        raise TypeError("target artifact workflow requires exact rendered artifacts")


@dataclass(slots=True)
class _StagedReplacement:
    target: Path
    staged: Path
    backup: Path | None


def _prepare_replacements(
    output_directory: Path,
    rendered: RenderedTargetArtifacts,
) -> tuple[_StagedReplacement, ...]:
    replacements: list[_StagedReplacement] = []
    try:
        for filename, content in rendered.by_filename():
            target = output_directory / filename
            if check_exact_file(target, content):
                continue
            if target.exists() and not target.is_file() and not target.is_symlink():
                raise OSError("target artifact path is not replaceable")
            staged = _stage_file(target, content)
            try:
                backup = (
                    _reserve_backup_path(target) if target.exists() or target.is_symlink() else None
                )
            except BaseException:
                staged.unlink(missing_ok=True)
                raise
            replacements.append(_StagedReplacement(target, staged, backup))
    except BaseException:
        _cleanup_replacements(tuple(replacements))
        raise
    return tuple(replacements)


def _stage_file(target: Path, content: bytes) -> Path:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=target.parent,
            prefix=f".{target.name}.staged.",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        return temporary_path
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise


def _reserve_backup_path(target: Path) -> Path:
    descriptor, name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.backup.")
    os.close(descriptor)
    path = Path(name)
    path.unlink()
    return path


def _rollback(replacements: tuple[_StagedReplacement, ...]) -> tuple[OSError, ...]:
    errors: list[OSError] = []
    for replacement in reversed(replacements):
        try:
            if replacement.backup is not None and _entry_exists(replacement.backup):
                replacement.backup.replace(replacement.target)
            elif replacement.backup is None and not _entry_exists(replacement.staged):
                replacement.target.unlink(missing_ok=True)
        except OSError as error:
            errors.append(error)
    return tuple(errors)


def _entry_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _cleanup_replacements(
    replacements: tuple[_StagedReplacement, ...],
    *,
    preserve_recovery_backups: bool = False,
) -> tuple[OSError, ...]:
    errors: list[OSError] = []
    for replacement in replacements:
        for path in (replacement.staged, replacement.backup):
            if path is None:
                continue
            if preserve_recovery_backups and path == replacement.backup:
                continue
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                errors.append(error)
    return tuple(errors)

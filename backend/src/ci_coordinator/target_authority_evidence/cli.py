"""Offline publication command for verified unactivated evidence."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from ci_coordinator.kernel.canonical_json import canonical_json

from .file import publish_target_authority_evidence, read_target_authority_evidence
from .model import TargetAuthorityEvidenceError


class _ArgumentsRejected(ValueError):
    pass


class _RedactedArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise _ArgumentsRejected


def main(argv: Sequence[str] | None = None) -> int:
    parser = _RedactedArgumentParser(
        prog="ci-coordinator-target-authority-evidence",
        description="Verify and atomically publish one unactivated evidence bundle.",
    )
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    try:
        arguments = parser.parse_args(argv)
        evidence = read_target_authority_evidence(arguments.input)
        published = publish_target_authority_evidence(
            evidence,
            arguments.output_directory,
        )
    except _ArgumentsRejected:
        _write_result({"status": "rejected", "code": "arguments_rejected"})
        return 2
    except TargetAuthorityEvidenceError as error:
        _write_result({"status": "rejected", "code": error.code})
        return 2
    except Exception:
        _write_result({"status": "rejected", "code": "internal_error"})
        return 70
    _write_result(
        {
            "status": "published",
            "code": "evidence_published",
            "bundleDigest": published.bundle_digest,
            "path": str(published.path),
        }
    )
    return 0


def _write_result(value: dict[str, object]) -> None:
    sys.stdout.buffer.write(canonical_json(value) + b"\n")

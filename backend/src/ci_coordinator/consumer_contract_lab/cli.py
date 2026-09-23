"""Stable command boundary for provider-independent consumer contract proof."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from tempfile import NamedTemporaryFile

from ci_coordinator.consumer_contract_lab.codec import ConsumerLabAdmissionError
from ci_coordinator.consumer_contract_lab.composition import ConsumerLabExecutionError
from ci_coordinator.consumer_contract_lab.node_runtime import ConsumerControlError
from ci_coordinator.consumer_contract_lab.runner import run_consumer_contract_lab
from ci_coordinator.consumer_contract_lab.source_epoch import ConsumerContractSourceError
from ci_coordinator.runtime_settings import admit_python_runtime


class ConsumerLabCommandError(ValueError):
    """The local command boundary cannot safely emit its receipt."""


def main(argv: Sequence[str] | None = None) -> int:
    if admit_python_runtime() is not None:
        return _reject("unsupported_python_runtime")
    arguments = _parser().parse_args(argv)
    coordinator_root = Path(arguments.coordinator_root).expanduser().resolve()
    coordinator_package_root = Path(arguments.coordinator_package_root).resolve()
    target_root = Path(arguments.target_root).expanduser().resolve()
    output = Path(arguments.output).expanduser().resolve()
    try:
        _require_external_output(output, coordinator_root, target_root)
        receipt = run_consumer_contract_lab(
            coordinator_root=coordinator_root,
            coordinator_commit=arguments.coordinator_commit,
            coordinator_package_root=coordinator_package_root,
            target_root=target_root,
            profile_path=Path(arguments.profile),
        )
        try:
            _write_atomic(output, receipt.canonical_bytes())
        except OSError as error:
            raise ConsumerLabCommandError("consumer lab receipt could not be written") from error
    except (
        ConsumerControlError,
        ConsumerContractSourceError,
        ConsumerLabAdmissionError,
        ConsumerLabCommandError,
        ConsumerLabExecutionError,
    ) as error:
        return _reject("consumer_contract_lab_failed", detail=type(error).__name__)
    return _report(
        "consumer_contract_lab_passed",
        receiptId=receipt.receipt_id,
        output=str(output),
        scenarioCount=len(receipt.results),
        providerEnforcement=False,
        targetJobsExecuted=False,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ci-coordinator-consumer-lab-internal")
    parser.add_argument("--coordinator-root", required=True)
    parser.add_argument("--coordinator-commit", required=True)
    parser.add_argument("--coordinator-package-root", required=True)
    parser.add_argument("--target-root", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", required=True)
    return parser


def _require_external_output(output: Path, *roots: Path) -> None:
    if not output.parent.is_dir():
        raise ConsumerLabCommandError("consumer lab output parent is unavailable")
    for root in roots:
        try:
            output.relative_to(root)
        except ValueError:
            continue
        raise ConsumerLabCommandError("consumer lab output must be outside source repositories")


def _write_atomic(output: Path, content: bytes) -> None:
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb",
            dir=output.parent,
            prefix=f".{output.name}.",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            temporary_path = Path(temporary.name)
        temporary_path.replace(output)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _report(code: str, **details: object) -> int:
    print(json.dumps({"code": code, **details}, separators=(",", ":"), sort_keys=True))
    return 0


def _reject(code: str, **details: object) -> int:
    print(
        json.dumps({"code": code, **details}, separators=(",", ":"), sort_keys=True),
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

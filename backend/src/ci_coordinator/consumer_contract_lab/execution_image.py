"""Private execution image built only from sealed target contract bytes."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from ci_coordinator.consumer_contract_lab.source_epoch import PreparedConsumerContract


@contextmanager
def target_execution_image(contract: PreparedConsumerContract) -> Iterator[Path]:
    if type(contract) is not PreparedConsumerContract:
        raise TypeError("target execution image requires an exact prepared contract")
    with TemporaryDirectory(prefix="ci-consumer-target-") as temporary:
        root = Path(temporary).resolve()
        for relative, content in contract.contract_files:
            destination = root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as output:
                output.write(content)
        yield root

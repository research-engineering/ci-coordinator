"""End-to-end orchestration and content-addressed local receipt."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ci_coordinator.consumer_contract_lab.composition import issue_scenario
from ci_coordinator.consumer_contract_lab.model import RECEIPT_SCHEMA, ManifestEntry
from ci_coordinator.consumer_contract_lab.node_runtime import (
    ConsumerControlResult,
    execute_consumer_controls,
)
from ci_coordinator.consumer_contract_lab.source_epoch import (
    PreparedConsumerContract,
    assert_consumer_contract_unchanged,
    prepare_consumer_contract,
)
from ci_coordinator.kernel import canonical_json, hash_object


@dataclass(frozen=True, slots=True)
class ConsumerLabReceipt:
    receipt_id: str
    contract: PreparedConsumerContract
    results: tuple[ConsumerControlResult, ...]

    def __post_init__(self) -> None:
        if not self.receipt_id.startswith("consumer_lab_"):
            raise ValueError("consumer lab receipt identity is invalid")
        if type(self.contract) is not PreparedConsumerContract:
            raise TypeError("consumer lab receipt requires an exact contract")
        if (
            type(self.results) is not tuple
            or not self.results
            or any(type(result) is not ConsumerControlResult for result in self.results)
        ):
            raise TypeError("consumer lab receipt requires exact scenario results")
        if self.receipt_id != _receipt_id(self.contract, self.results):
            raise ValueError("consumer lab receipt identity does not match its content")

    def to_mapping(self) -> dict[str, object]:
        return {
            "schemaVersion": RECEIPT_SCHEMA,
            "receiptId": self.receipt_id,
            **_receipt_body(self.contract, self.results),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json(self.to_mapping()) + b"\n"


def run_consumer_contract_lab(
    *,
    coordinator_root: Path,
    coordinator_commit: str,
    coordinator_package_root: Path,
    target_root: Path,
    profile_path: Path,
) -> ConsumerLabReceipt:
    contract = prepare_consumer_contract(
        coordinator_root=coordinator_root,
        coordinator_commit=coordinator_commit,
        coordinator_package_root=coordinator_package_root,
        target_root=target_root,
        profile_path=profile_path,
    )
    results = tuple(
        execute_consumer_controls(
            contract,
            scenario,
            issue_scenario(contract, scenario, scenario_index=index),
        )
        for index, scenario in enumerate(contract.corpus.scenarios, start=1)
    )
    assert_consumer_contract_unchanged(contract)
    return ConsumerLabReceipt(_receipt_id(contract, results), contract, results)


def _receipt_id(
    contract: PreparedConsumerContract,
    results: tuple[ConsumerControlResult, ...],
) -> str:
    return "consumer_lab_" + hash_object(_receipt_body(contract, results))[:32]


def _receipt_body(
    contract: PreparedConsumerContract,
    results: tuple[ConsumerControlResult, ...],
) -> dict[str, object]:
    return {
        "profileId": contract.profile.profile_id,
        "sourceEpochId": contract.source_epoch_id,
        "coordinatorSource": contract.coordinator_source.to_mapping(),
        "targetSource": contract.target_source.to_mapping(),
        "contractManifestSha256": contract.manifest_sha256,
        "contractManifest": [_manifest_mapping(item) for item in contract.manifest_entries],
        "executionAuthority": contract.execution_authority.to_mapping(),
        "authority": "lab-fixture",
        "providerEnforcement": False,
        "targetJobsExecuted": False,
        "scenarioResults": [result.to_mapping() for result in results],
    }


def _manifest_mapping(entry: ManifestEntry) -> dict[str, object]:
    return entry.to_mapping()

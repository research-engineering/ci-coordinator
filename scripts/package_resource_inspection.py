from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from importlib.metadata import Distribution, PackagePath, distribution
from pathlib import Path, PurePosixPath

_CONFIG_RESOURCES = frozenset(
    {
        "audit-json-resource-profile.v1.json",
        "audit-persistence-byte-profile.v1.json",
        "compiled-repository-policy.schema.v1.json",
        "config-document-profile.v1.json",
        "config-producer-feasibility-profile.v1.json",
        "policy-admission-result-profile.v1.json",
        "repository-policy-semantics.v1.json",
        "repository-policy.schema.v1.json",
    }
)
_CONSUMER_CONTRACT_LAB_RESOURCES = frozenset(
    {
        "runtime-harness.cjs",
        "node-runtime-guard.cjs",
    }
)
_CAPACITY_QUALIFICATION_RESOURCES = frozenset({"capacity-qualification-profile.v1.json"})
_CI_ECONOMICS_RESOURCES = frozenset({"ci-economics-profile.v1.json"})
_GITHUB_INGESTION_RESOURCES = frozenset({"webhook-ingestion-profile.v2.json"})
_RUNTIME_SETTINGS_RESOURCES = frozenset(
    {
        "build-identity.v1.json",
        "python-runtime-profile.v1.json",
        "runtime-caller-inventory.v1.json",
        "runtime-entrypoint-disposition.v1.json",
    }
)
_TARGET_ARTIFACT_SCHEMA_RESOURCES = frozenset(
    {
        "dependency-graph.schema.v1.json",
        "plan-trust-root.schema.v1.json",
        "target-artifacts-source.schema.v1.json",
        "target-execution-registry.schema.v1.json",
        "test-manifest.schema.v1.json",
    }
)
_TARGET_ARTIFACT_EXECUTABLE_RESOURCES = frozenset(
    {
        "ci-coordinator.cjs",
        "trusted-plan-request.yml",
    }
)
_PRODUCTION_ADMISSION_RESOURCES = frozenset({"production-admission-envelope.schema.v2.json"})
_NESTED_PACKAGE_DATA_WITNESS = PurePosixPath(
    "ci_coordinator/api/http/static/assets/package-witness/nested.js"
)
_NESTED_PACKAGE_DATA_CONTENT = b"export const packageDataWitness = true;\n"


def inspect_installed_package(
    config_root: Path,
    runtime_root: Path,
    target_resource_root: Path,
    consumer_lab_resource_root: Path,
    capacity_qualification_resource_root: Path,
    ci_economics_resource_root: Path,
    inspection_venv: Path,
    locked_site_packages: Path,
) -> dict[str, object]:
    sys.path.append(str(locked_site_packages))
    installed = distribution("ci-coordinator-backend")
    package_root = Path(str(installed.locate_file("ci_coordinator"))).resolve()
    if not package_root.is_relative_to(inspection_venv):
        raise RuntimeError("wheel files did not originate from the inspection environment")
    distribution_files = installed.files
    if distribution_files is None:
        raise RuntimeError("wheel distribution RECORD is unavailable")

    groups = {
        "capacityQualificationResourceCount": (
            "capacity_qualification/resources",
            _same_root(_CAPACITY_QUALIFICATION_RESOURCES, capacity_qualification_resource_root),
            "capacity-qualification",
            frozenset({".json"}),
        ),
        "ciEconomicsResourceCount": (
            "ci_economics/resources",
            _same_root(_CI_ECONOMICS_RESOURCES, ci_economics_resource_root),
            "CI-economics",
            frozenset({".json"}),
        ),
        "consumerContractLabResourceCount": (
            "consumer_contract_lab/resources",
            _same_root(_CONSUMER_CONTRACT_LAB_RESOURCES, consumer_lab_resource_root),
            "consumer-contract-lab",
            frozenset({".cjs"}),
        ),
        "resourceCount": (
            "config_control/resources",
            _same_root(_CONFIG_RESOURCES, config_root),
            "policy-admission",
            frozenset({".json"}),
        ),
        "githubIngestionResourceCount": (
            "github_ingestion/resources",
            _same_root(_GITHUB_INGESTION_RESOURCES, runtime_root),
            "GitHub-ingestion",
            frozenset({".json"}),
        ),
        "runtimeSettingsResourceCount": (
            "runtime_settings/resources",
            _same_root(_RUNTIME_SETTINGS_RESOURCES, runtime_root),
            "runtime-settings",
            frozenset({".json"}),
        ),
        "targetArtifactsResourceCount": (
            "target_artifacts/resources",
            {
                **_same_root(_TARGET_ARTIFACT_SCHEMA_RESOURCES, runtime_root),
                **_same_root(_TARGET_ARTIFACT_EXECUTABLE_RESOURCES, target_resource_root),
            },
            "target-artifacts",
            frozenset({".cjs", ".json", ".yml"}),
        ),
        "productionAdmissionResourceCount": (
            "production_admission/resources",
            _same_root(_PRODUCTION_ADMISSION_RESOURCES, runtime_root),
            "production-admission",
            frozenset({".json"}),
        ),
        "persistenceResourceCount": (
            "persistence/resources",
            {
                "database-compatibility-profile.v1.json": config_root,
                "runtime-state-profile.v1.json": runtime_root,
                "shadow-reconciliation-state-profile.v1.json": runtime_root,
            },
            "persistence",
            frozenset({".json"}),
        ),
    }
    counts = {
        metric: _admit_resource_group(
            installed,
            distribution_files,
            package_directory=package_directory,
            expected_roots=expected_roots,
            inspection_venv=inspection_venv,
            label=label,
            suffixes=suffixes,
        )
        for metric, (
            package_directory,
            expected_roots,
            label,
            suffixes,
        ) in groups.items()
    }
    counts["operatorUiNestedAssetCount"] = _admit_nested_operator_ui_asset(
        installed, distribution_files, inspection_venv=inspection_venv
    )
    _admit_installed_semantics()
    return {**counts, "sitePackages": str(package_root.parent)}


def _admit_nested_operator_ui_asset(
    installed: Distribution,
    distribution_files: Sequence[PackagePath],
    *,
    inspection_venv: Path,
) -> int:
    matches = tuple(
        entry
        for entry in distribution_files
        if PurePosixPath(str(entry)) == _NESTED_PACKAGE_DATA_WITNESS
    )
    if len(matches) != 1:
        raise RuntimeError("wheel omitted the nested operator UI package-data witness")
    installed_path = Path(str(installed.locate_file(matches[0]))).resolve()
    if not installed_path.is_relative_to(inspection_venv):
        raise RuntimeError("wheel nested operator UI asset escaped the inspection environment")
    if installed_path.read_bytes() != _NESTED_PACKAGE_DATA_CONTENT:
        raise RuntimeError("wheel nested operator UI asset is stale")
    return 1


def _admit_resource_group(
    installed: Distribution,
    distribution_files: Sequence[PackagePath],
    *,
    package_directory: str,
    expected_roots: Mapping[str, Path],
    inspection_venv: Path,
    label: str,
    suffixes: frozenset[str] = frozenset({".json"}),
) -> int:
    prefix = PurePosixPath("ci_coordinator") / package_directory
    resources = tuple(
        sorted(
            PurePosixPath(str(entry))
            for entry in distribution_files
            if PurePosixPath(str(entry)).parent == prefix
            and PurePosixPath(str(entry)).suffix in suffixes
        )
    )
    observed_names = {resource.name for resource in resources}
    expected_names = set(expected_roots)
    if observed_names != expected_names:
        raise RuntimeError(
            f"wheel {label} resource inventory mismatch: "
            f"missing={sorted(expected_names - observed_names)!r} "
            f"excess={sorted(observed_names - expected_names)!r}"
        )
    for resource in resources:
        canonical_path = expected_roots[resource.name] / resource.name
        if not canonical_path.is_file():
            raise RuntimeError(f"canonical {label} resource is missing: {resource.name}")
        packaged_path = Path(str(installed.locate_file(resource))).resolve()
        if not packaged_path.is_relative_to(inspection_venv):
            raise RuntimeError(
                f"wheel {label} resource escaped the inspection environment: {resource.name}"
            )
        if packaged_path.read_bytes() != canonical_path.read_bytes():
            raise RuntimeError(f"wheel {label} resource is stale: {resource.name}")
    return len(resources)


def _same_root(names: frozenset[str], root: Path) -> dict[str, Path]:
    return {name: root for name in names}


def _admit_installed_semantics() -> None:
    from ci_coordinator.capacity_qualification import CAPACITY_QUALIFICATION_PROFILE
    from ci_coordinator.ci_economics import load_bundled_ci_economics_profile
    from ci_coordinator.github_ingestion import load_bundled_profile
    from ci_coordinator.persistence.compatibility_profile import (
        load_bundled_profile as load_database_profile,
    )
    from ci_coordinator.persistence.runtime_state_profile import (
        load_bundled_runtime_state_profile,
    )
    from ci_coordinator.persistence.shadow_reconciliation_state_profile import (
        load_bundled_shadow_reconciliation_state_profile,
    )
    from ci_coordinator.runtime_settings import load_bundled_caller_inventory

    if (
        CAPACITY_QUALIFICATION_PROFILE.profile_id != "ci-coordinator-capacity-qualification/v1"
        or CAPACITY_QUALIFICATION_PROFILE.algorithm != "Ed25519"
    ):
        raise RuntimeError("installed capacity-qualification profile did not admit its contract")

    ci_economics_profile = load_bundled_ci_economics_profile()
    if (
        ci_economics_profile.profile_id != "ci-coordinator.ci-economics/v1"
        or ci_economics_profile.maximum_jobs_per_attempt != 2_000
    ):
        raise RuntimeError("installed CI-economics profile did not admit its contract")

    github_profile = load_bundled_profile()
    if (
        github_profile.profile_id != "ci-coordinator.github-webhook-ingestion/v2"
        or github_profile.limits.maximum_body_bytes != 25 * 1024 * 1024
    ):
        raise RuntimeError("installed GitHub-ingestion profile did not admit its contract")

    runtime_inventory = load_bundled_caller_inventory()
    if {record.caller_id for record in runtime_inventory.records} != {
        "container.healthcheck",
        "container.command",
        "python.replay.console",
        "python.database-access.console",
        "python.runtime.module",
        "python.target-artifacts.console",
    }:
        raise RuntimeError("installed runtime-settings inventory did not admit its contract")
    if load_database_profile().database_major_version != 18:
        raise RuntimeError("installed database profile did not admit PostgreSQL 18")
    runtime_state_profile = load_bundled_runtime_state_profile()
    if (
        runtime_state_profile.delivery_id_utf8_bytes != 256
        or runtime_state_profile.signed_envelope_canonical_bytes != 1_048_576
    ):
        raise RuntimeError("installed runtime-state profile did not admit its contract")
    shadow_profile = load_bundled_shadow_reconciliation_state_profile()
    if shadow_profile.reconciliation_observation_canonical_bytes != 65_536:
        raise RuntimeError("installed shadow reconciliation profile did not admit its contract")


def main() -> int:
    if len(sys.argv) != 9:
        print(
            "usage: package_resource_inspection.py "
            "CONFIG_ROOT RUNTIME_ROOT TARGET_RESOURCE_ROOT CONSUMER_LAB_RESOURCE_ROOT "
            "CAPACITY_QUALIFICATION_RESOURCE_ROOT CI_ECONOMICS_RESOURCE_ROOT "
            "INSPECTION_VENV LOCKED_SITE_PACKAGES",
            file=sys.stderr,
        )
        return 2
    try:
        result = inspect_installed_package(*(Path(value).resolve() for value in sys.argv[1:]))
    except (OSError, RuntimeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

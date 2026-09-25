"""Declarative mutation-suite entrypoint specifications and inventory policies."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Final, Literal, cast

from scripts.mutation.mutation_manifest import (
    MutationManifest,
    assert_manifest_fits_execution_envelope,
    collect_manifest_applicability_failures,
    decode_mutation_manifest,
)
from scripts.mutation.mutation_suite_runner import (
    REPO_ROOT,
    MutationReport,
    MutationSuiteConfig,
    mutation_report_exit_code,
    run_mutation_suite,
    write_mutation_report,
)
from scripts.quality_plan import (
    QUALITY_PLAN_RELATIVE_PATH,
    WITNESS_PLAN_RELATIVE_PATH,
    QualityPlan,
    load_quality_plan,
)

type JsonObject = dict[str, object]
type SuiteName = Literal[
    "audit-json-resource-limits",
    "audit-persistence-byte-limits",
    "frontend-safety-kernels",
    "python-capacity-evidence",
    "python-config-policy-admission",
    "python-database-compatibility",
    "python-http-admission",
    "python-persistence",
]

SPECS_RELATIVE_PATH: Final = "scripts/mutation/mutation_suite_specs.py"
EXECUTION_ENVELOPE_AUTHORITY_RELATIVE_PATHS: Final = (
    "scripts/quality_plan.py",
    "scripts/proofkit_common.py",
    QUALITY_PLAN_RELATIVE_PATH,
    WITNESS_PLAN_RELATIVE_PATH,
)
OPERATOR_WORKBENCH_TIMEOUT_MINUTES: Final = 40
OPERATOR_WORKBENCH_RESERVE_MS: Final = 30 * 60_000


class InventoryPolicy(StrEnum):
    NONE = "none"
    AUDIT_PERSISTENCE_BYTES = "audit-persistence-bytes"
    CAPACITY_EVIDENCE = "capacity-evidence"
    CONFIG_POLICY = "config-policy"
    DATABASE_COMPATIBILITY = "database-compatibility"
    FRONTEND_SAFETY = "frontend-risk"
    HTTP_ADMISSION = "http-admission"


class ExecutionGroup(StrEnum):
    PERSISTENCE_MUTATION = "persistence-mutation"
    OPERATOR_WORKBENCH = "operator-workbench"


@dataclass(frozen=True, slots=True)
class MutationSuiteSpec:
    config: MutationSuiteConfig
    command_id: str
    execution_group: ExecutionGroup = ExecutionGroup.PERSISTENCE_MUTATION
    inventory_policy: InventoryPolicy = InventoryPolicy.NONE


@dataclass(frozen=True, slots=True)
class InventoryEvidence:
    canonical_ids: tuple[str, ...]
    invalid_mutants: tuple[JsonObject, ...]


def _config(
    *,
    dependencies: tuple[str, ...],
    manifest_relative_path: str,
    report_id: str,
    temp_prefix: str,
) -> MutationSuiteConfig:
    return MutationSuiteConfig(
        dependencies=dependencies,
        manifest_relative_path=manifest_relative_path,
        report_id=report_id,
        temp_prefix=temp_prefix,
        authority_relative_paths=(
            SPECS_RELATIVE_PATH,
            *EXECUTION_ENVELOPE_AUTHORITY_RELATIVE_PATHS,
            manifest_relative_path,
        ),
    )


SUITES: Final[dict[SuiteName, MutationSuiteSpec]] = {
    "audit-json-resource-limits": MutationSuiteSpec(
        config=_config(
            dependencies=("backend/.venv",),
            manifest_relative_path=("fixtures/conformance/v1/audit-json-resource-mutants.v1.json"),
            report_id="ci-coordinator.audit-json-resource-mutation",
            temp_prefix="ci-audit-json-mutation-",
        ),
        command_id="mutation.audit-json-resources",
    ),
    "audit-persistence-byte-limits": MutationSuiteSpec(
        config=_config(
            dependencies=("backend/.venv",),
            manifest_relative_path=(
                "fixtures/conformance/v1/audit-persistence-byte-mutants.v1.json"
            ),
            report_id="ci-coordinator.audit-persistence-byte-mutation",
            temp_prefix="ci-audit-persistence-byte-mutation-",
        ),
        command_id="mutation.audit-persistence-bytes",
        inventory_policy=InventoryPolicy.AUDIT_PERSISTENCE_BYTES,
    ),
    "frontend-safety-kernels": MutationSuiteSpec(
        config=_config(
            dependencies=("frontend/node_modules", "node_modules"),
            manifest_relative_path=(
                "fixtures/conformance/v1/frontend-safety-kernel-mutants.v1.json"
            ),
            report_id="ci-coordinator.frontend-safety-kernel-mutation",
            temp_prefix="ci-frontend-safety-mutation-",
        ),
        command_id="mutation.frontend-safety-kernels",
        execution_group=ExecutionGroup.OPERATOR_WORKBENCH,
        inventory_policy=InventoryPolicy.FRONTEND_SAFETY,
    ),
    "python-capacity-evidence": MutationSuiteSpec(
        config=_config(
            dependencies=("backend/.venv",),
            manifest_relative_path=(
                "fixtures/conformance/v1/python-capacity-evidence-mutants.v1.json"
            ),
            report_id="ci-coordinator.python-capacity-evidence-mutation",
            temp_prefix="ci-python-capacity-evidence-mutation-",
        ),
        command_id="mutation.python-capacity-evidence",
        inventory_policy=InventoryPolicy.CAPACITY_EVIDENCE,
    ),
    "python-config-policy-admission": MutationSuiteSpec(
        config=_config(
            dependencies=("backend/.venv",),
            manifest_relative_path=(
                "fixtures/conformance/v1/python-config-policy-admission-mutants.v1.json"
            ),
            report_id="ci-coordinator.python-config-policy-admission-mutation",
            temp_prefix="ci-python-config-policy-admission-mutation-",
        ),
        command_id="mutation.python-config-policy-admission",
        inventory_policy=InventoryPolicy.CONFIG_POLICY,
    ),
    "python-database-compatibility": MutationSuiteSpec(
        config=_config(
            dependencies=("backend/.venv",),
            manifest_relative_path=(
                "fixtures/conformance/v1/python-database-compatibility-mutants.v1.json"
            ),
            report_id="ci-coordinator.python-database-compatibility-mutation",
            temp_prefix="ci-python-database-compatibility-mutation-",
        ),
        command_id="mutation.python-database-compatibility",
        inventory_policy=InventoryPolicy.DATABASE_COMPATIBILITY,
    ),
    "python-http-admission": MutationSuiteSpec(
        config=_config(
            dependencies=("backend/.venv",),
            manifest_relative_path=(
                "fixtures/conformance/v1/python-http-admission-mutants.v1.json"
            ),
            report_id="ci-coordinator.python-http-admission-mutation",
            temp_prefix="ci-python-http-admission-mutation-",
        ),
        command_id="mutation.python-http-admission",
        inventory_policy=InventoryPolicy.HTTP_ADMISSION,
    ),
    "python-persistence": MutationSuiteSpec(
        config=_config(
            dependencies=("backend/.venv",),
            manifest_relative_path="fixtures/conformance/v1/python-persistence-mutants.v1.json",
            report_id="ci-coordinator.python-persistence-mutation",
            temp_prefix="ci-python-persistence-mutation-",
        ),
        command_id="mutation.python-persistence",
    ),
}


def run_named_suite(name: SuiteName, *, repo_root: Path = REPO_ROOT) -> int:
    spec = SUITES[name]
    manifest = _load_manifest(spec, repo_root)
    _assert_suite_execution_envelope(spec, manifest, load_quality_plan(repo_root))
    inventory = _validate_source_inventory(spec, cast(JsonObject, manifest))
    report = run_mutation_suite(spec.config, repo_root=repo_root)
    if spec.inventory_policy is InventoryPolicy.AUDIT_PERSISTENCE_BYTES:
        if inventory is None:
            raise AssertionError("audit persistence inventory evidence is missing")
        _decorate_audit_persistence_report(report, inventory)
    write_mutation_report(report)
    return mutation_report_exit_code(report)


def assert_all_suite_manifests_applicable(*, repo_root: Path = REPO_ROOT) -> int:
    failures: list[JsonObject] = []
    mutant_count = 0
    suite_outer_timeouts_ms: list[int] = []
    operator_workbench_timeouts_ms: list[int] = []
    quality_plan = load_quality_plan(repo_root)
    for name, spec in SUITES.items():
        manifest = _load_manifest(spec, repo_root)
        _assert_suite_execution_envelope(spec, manifest, quality_plan)
        _validate_source_inventory(spec, cast(JsonObject, manifest))
        mutant_count += len(manifest["mutants"])
        if spec.execution_group is ExecutionGroup.OPERATOR_WORKBENCH:
            operator_workbench_timeouts_ms.append(manifest["outerTimeoutMs"])
        else:
            suite_outer_timeouts_ms.append(manifest["outerTimeoutMs"])
        failures.extend(
            {"suite": name, **failure}
            for failure in collect_manifest_applicability_failures(
                manifest,
                source_root=repo_root,
            )
        )
    assert_direct_mutation_job_execution_envelope(
        suite_outer_timeouts_ms,
        quality_plan=quality_plan,
    )
    _assert_operator_workbench_execution_envelope(operator_workbench_timeouts_ms)
    if failures:
        encoded = json.dumps(
            failures,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        raise RuntimeError(f"mutation suite applicability preflight failed: {encoded}")
    return mutant_count


def assert_direct_mutation_job_execution_envelope(
    suite_outer_timeouts_ms: Sequence[int],
    *,
    quality_plan: QualityPlan,
) -> None:
    if not suite_outer_timeouts_ms or any(
        type(timeout_ms) is not int or timeout_ms <= 0 for timeout_ms in suite_outer_timeouts_ms
    ):
        raise RuntimeError("direct mutation job suite timeouts are invalid")
    required_timeout_ms = max(suite_outer_timeouts_ms) + quality_plan.direct_mutation_job_reserve_ms
    job_timeout_ms = quality_plan.direct_mutation_job_timeout_minutes * 60_000
    if required_timeout_ms > job_timeout_ms:
        raise RuntimeError("direct mutation job timeout does not preserve its execution reserve")


def _assert_operator_workbench_execution_envelope(
    suite_outer_timeouts_ms: Sequence[int],
) -> None:
    if not suite_outer_timeouts_ms or any(
        type(timeout_ms) is not int or timeout_ms <= 0 for timeout_ms in suite_outer_timeouts_ms
    ):
        raise RuntimeError("operator-workbench mutation suite timeouts are invalid")
    required_timeout_ms = sum(suite_outer_timeouts_ms) + OPERATOR_WORKBENCH_RESERVE_MS
    if required_timeout_ms > OPERATOR_WORKBENCH_TIMEOUT_MINUTES * 60_000:
        raise RuntimeError("operator-workbench timeout does not preserve its execution reserve")


def _load_manifest(spec: MutationSuiteSpec, repo_root: Path) -> MutationManifest:
    return decode_mutation_manifest((repo_root / spec.config.manifest_relative_path).read_bytes())


def _assert_suite_execution_envelope(
    spec: MutationSuiteSpec,
    manifest: MutationManifest,
    quality_plan: QualityPlan,
) -> None:
    command = quality_plan.commands.get(spec.command_id)
    if command is None:
        raise RuntimeError(f"mutation suite command is not declared: {spec.command_id}")
    assert_manifest_fits_execution_envelope(
        manifest,
        timeout_ms=command.timeout_ms,
    )


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["preflight"]:
        assert_all_suite_manifests_applicable()
        return 0
    if len(arguments) != 1 or arguments[0] not in SUITES:
        choices = ", ".join((*SUITES, "preflight"))
        sys.stderr.write(f"usage: python -m scripts.mutation.mutation_suite_specs <{choices}>\n")
        return 2
    return run_named_suite(arguments[0])


def validate_inventory(policy: InventoryPolicy, value: JsonObject) -> InventoryEvidence:
    if policy is InventoryPolicy.AUDIT_PERSISTENCE_BYTES:
        return _validate_audit_persistence_inventory(value)
    if policy is InventoryPolicy.CONFIG_POLICY:
        return _validate_numbered_inventory(
            value,
            prefix="CP",
            count=75,
            schema_version="ci-python-config-policy-admission-mutation-suite/v1",
            suite_id="ci-python-config-policy-admission/v1",
            error_message="config-policy mutation inventory is not canonical and complete",
        )
    if policy is InventoryPolicy.CAPACITY_EVIDENCE:
        return _validate_numbered_inventory(
            value,
            prefix="CE",
            count=27,
            schema_version="ci-python-capacity-evidence-mutation-suite/v1",
            suite_id="ci-python-capacity-evidence/v1",
            error_message="capacity-evidence mutation inventory is not canonical and complete",
        )
    if policy is InventoryPolicy.DATABASE_COMPATIBILITY:
        return _validate_numbered_inventory(
            value,
            prefix="DB",
            count=36,
            schema_version="ci-python-database-compatibility-mutation-suite/v1",
            suite_id="ci-python-database-compatibility/v1",
            error_message="database compatibility mutation inventory is not canonical and complete",
        )
    if policy is InventoryPolicy.FRONTEND_SAFETY:
        return _validate_numbered_inventory(
            value,
            prefix="FR",
            count=12,
            schema_version="ci-frontend-safety-kernel-mutation-suite/v1",
            suite_id="ci-frontend-safety-kernels/v1",
            error_message="frontend safety mutation inventory is not canonical and complete",
        )
    if policy is InventoryPolicy.HTTP_ADMISSION:
        return _validate_http_admission_inventory(value)
    raise ValueError("the none inventory policy has no inventory to validate")


def _validate_source_inventory(
    spec: MutationSuiteSpec,
    value: JsonObject,
) -> InventoryEvidence | None:
    if spec.inventory_policy is InventoryPolicy.NONE:
        return None
    return validate_inventory(spec.inventory_policy, value)


def _validate_audit_persistence_inventory(value: JsonObject) -> InventoryEvidence:
    parts = _inventory_parts(value)
    canonical_ids = _string_list(value.get("canonicalInventoryIds"))
    if parts is None or canonical_ids is None:
        raise RuntimeError(
            "audit persistence byte mutation inventory is not canonical and exhaustive"
        )
    active_ids, invalid_ids, invalid_mutants = parts
    combined_ids = [*active_ids, *invalid_ids]
    if (
        value.get("schemaVersion") != "ci-audit-persistence-byte-mutation-suite/v1"
        or value.get("suiteId") != "ci-audit-event-persistence-bytes/v1"
        or not canonical_ids
        or combined_ids != canonical_ids
        or len(set(combined_ids)) != len(canonical_ids)
        or _string_list(value.get("expectedMutantIds")) != active_ids
        or _string_list(value.get("expectedInvalidMutantIds")) != invalid_ids
        or value.get("expectedKilled") != len(active_ids)
    ):
        raise RuntimeError(
            "audit persistence byte mutation inventory is not canonical and exhaustive"
        )
    if any(
        mutant.get("status") != "invalid"
        or not isinstance(mutant.get("reason"), str)
        or not mutant["reason"]
        for mutant in invalid_mutants
    ):
        raise RuntimeError("invalid audit persistence byte mutants require an explicit reason")
    return InventoryEvidence(tuple(canonical_ids), tuple(invalid_mutants))


def _validate_numbered_inventory(
    value: JsonObject,
    *,
    prefix: str,
    count: int,
    schema_version: str,
    suite_id: str,
    error_message: str,
) -> InventoryEvidence:
    canonical_ids = [f"{prefix}{index:02d}" for index in range(1, count + 1)]
    parts = _inventory_parts(value)
    classified_ids = _classified_ids(value)
    if parts is None or classified_ids is None:
        raise RuntimeError(error_message)
    active_ids, invalid_ids, invalid_mutants = parts
    if (
        value.get("schemaVersion") != schema_version
        or value.get("suiteId") != suite_id
        or _string_list(value.get("canonicalInventoryIds")) != canonical_ids
        or _string_list(value.get("expectedMutantIds")) != active_ids
        or _string_list(value.get("expectedInvalidMutantIds")) != invalid_ids
        or active_ids != canonical_ids
        or len(set(classified_ids)) != len(classified_ids)
        or sorted(classified_ids) != canonical_ids
        or invalid_ids
        or value.get("expectedKilled") != len(active_ids)
    ):
        raise RuntimeError(error_message)
    return InventoryEvidence(tuple(canonical_ids), tuple(invalid_mutants))


def _validate_http_admission_inventory(value: JsonObject) -> InventoryEvidence:
    canonical_ids = [f"HA{index:02d}" for index in range(1, 23)]
    parts = _inventory_parts(value)
    classified_ids = _classified_ids(value)
    if parts is None or classified_ids is None:
        raise RuntimeError("HTTP-admission mutation inventory is not canonical and complete")
    active_ids, invalid_ids, invalid_mutants = parts
    if (
        value.get("schemaVersion") != "ci-python-http-admission-mutation-suite/v1"
        or value.get("suiteId") != "ci-python-http-admission/v1"
        or _string_list(value.get("canonicalInventoryIds")) != canonical_ids
        or _string_list(value.get("expectedMutantIds")) != active_ids
        or _string_list(value.get("expectedInvalidMutantIds")) != invalid_ids
        or active_ids != canonical_ids
        or classified_ids != canonical_ids
        or invalid_ids
        or value.get("expectedKilled") != len(active_ids)
    ):
        raise RuntimeError("HTTP-admission mutation inventory is not canonical and complete")
    return InventoryEvidence(tuple(canonical_ids), tuple(invalid_mutants))


def _inventory_parts(
    value: JsonObject,
) -> tuple[list[str], list[str], list[JsonObject]] | None:
    mutants = _object_list(value.get("mutants"))
    invalid_mutants = _object_list(value.get("invalidMutants"))
    if mutants is None or invalid_mutants is None:
        return None
    active_ids = _object_ids(mutants)
    invalid_ids = _object_ids(invalid_mutants)
    if active_ids is None or invalid_ids is None:
        return None
    return active_ids, invalid_ids, invalid_mutants


def _classified_ids(value: JsonObject) -> list[str] | None:
    coverage = value.get("ownerClassCoverage", {})
    if not isinstance(coverage, dict):
        return None
    classified_ids: list[str] = []
    for raw_ids in coverage.values():
        ids = _string_list(raw_ids)
        if ids is None:
            return None
        classified_ids.extend(ids)
    return classified_ids


def _object_list(value: object) -> list[JsonObject] | None:
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        return None
    return cast(list[JsonObject], value)


def _object_ids(values: list[JsonObject]) -> list[str] | None:
    ids = [value.get("id") for value in values]
    if not all(isinstance(value, str) for value in ids):
        return None
    return cast(list[str], ids)


def _string_list(value: object) -> list[str] | None:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return None
    return cast(list[str], value)


def _decorate_audit_persistence_report(
    report: MutationReport,
    inventory: InventoryEvidence,
) -> None:
    summary = report.get("summary")
    non_claims = report.get("nonClaims")
    if not isinstance(summary, dict) or not isinstance(non_claims, list):
        raise TypeError("mutation report is missing summary or non-claims")
    summary["canonicalInventoryCount"] = len(inventory.canonical_ids)
    summary["declaredInvalidCount"] = len(inventory.invalid_mutants)
    summary["supportedCount"] = len(inventory.canonical_ids) - len(inventory.invalid_mutants)
    report["inventory"] = {
        "canonicalIds": list(inventory.canonical_ids),
        "invalid": list(inventory.invalid_mutants),
    }
    if inventory.invalid_mutants:
        report["state"] = "failed"
        report["inventoryState"] = "incomplete"
        non_claims.append(
            "Canonical inventory entries declared invalid are missing proof and never count "
            "as killed."
        )


if __name__ == "__main__":
    raise SystemExit(main())

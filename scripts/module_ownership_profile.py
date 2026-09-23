from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from scripts.proofkit_common import as_array, as_object
from scripts.proofkit_common import exact_fields as _exact_keys
from scripts.proofkit_common import nonempty_string as _required_string
from scripts.proofkit_common import object_rows as _object_rows
from scripts.repository_paths import read_repository_regular_file

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROFILE_PATH = Path("docs/specs/ci-coordinator-core/module-ownership-profile.v1.json")
MAX_PROFILE_BYTES = 128 * 1024
_SIGNAL_GRAMMAR_ID = "ci-coordinator.module-ownership-signals.v2"
_SIGNAL_RUNTIME_IDS = ("cpython-3.13.15",)
_FILE_KINDS = (
    "composition-root",
    "declarative",
    "documentation",
    "generated",
    "migration",
    "other",
    "production-authority",
    "production-like-script",
    "test",
    "vendor-build-cache",
)
_REVIEW_METRIC_IDS = (
    "physical-lines",
    "recognized-public-declarations",
    "first-party-import-contexts",
)
_TOP_LEVEL_FIELDS = {
    "candidateQueue",
    "decision",
    "exclusiveOwnershipRoots",
    "fileKindClassification",
    "fileKinds",
    "forbiddenCoownership",
    "nonClaims",
    "ownerId",
    "profileId",
    "requiredColocation",
    "responsibilities",
    "reviewSignals",
    "schemaVersion",
    "traversalLimits",
    "waiver",
}
_TRAVERSAL_LIMIT_CEILINGS = {
    "gitPathDiscoveryTimeoutSeconds": 120,
    "maximumCandidateFileBytes": 16 * 1024 * 1024,
    "maximumDepth": 16,
    "maximumEntries": 4096,
    "maximumOwnedFiles": 2048,
    "maximumRelativePathBytes": 2048,
    "maximumTotalCandidateBytes": 512 * 1024 * 1024,
    "maximumTotalPathBytes": 4 * 1024 * 1024,
}


@dataclass(frozen=True, slots=True)
class TraversalLimits:
    git_path_discovery_timeout_seconds: int
    maximum_candidate_file_bytes: int
    maximum_depth: int
    maximum_entries: int
    maximum_owned_files: int
    maximum_relative_path_bytes: int
    maximum_total_candidate_bytes: int
    maximum_total_path_bytes: int


@dataclass(frozen=True, slots=True)
class Responsibility:
    responsibility_id: str
    path_patterns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ExclusiveOwnershipRoot:
    responsibility_ids: tuple[str, ...]
    root: Path
    suffixes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewMetric:
    applicable_file_kinds: tuple[str, ...]
    applicable_suffixes: tuple[str, ...]
    metric_id: str
    operator: str
    threshold: int

    def matches(self, value: int) -> bool:
        if self.operator == "gt":
            return value > self.threshold
        if self.operator == "gte":
            return value >= self.threshold
        raise AssertionError(f"unadmitted review metric operator: {self.operator}")


@dataclass(frozen=True, slots=True)
class FileKindRule:
    kind: str
    path_patterns: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ModuleOwnershipProfile:
    default_file_kind: str
    exclusive_roots: tuple[ExclusiveOwnershipRoot, ...]
    file_kind_rules: tuple[FileKindRule, ...]
    forbidden_coownership_rule_ids: tuple[str, ...]
    profile_digest: str
    required_colocation_rule_ids: tuple[str, ...]
    responsibilities: tuple[Responsibility, ...]
    review_metrics: tuple[ReviewMetric, ...]
    signal_grammar_id: str
    signal_runtime_ids: tuple[str, ...]
    traversal_limits: TraversalLimits


def load_profile(
    repo_root: Path = REPO_ROOT,
    profile_path: Path = DEFAULT_PROFILE_PATH,
) -> ModuleOwnershipProfile:
    source = _read_bounded_file(repo_root, profile_path)
    raw = _parse_profile(source)
    _exact_keys(raw, _TOP_LEVEL_FIELDS, "module ownership profile")
    if raw.get("schemaVersion") != 1:
        raise ValueError("module ownership profile schemaVersion must equal 1")
    if raw.get("profileId") != "ci-coordinator.module-ownership.v1":
        raise ValueError("module ownership profileId is invalid")
    if raw.get("ownerId") != "ci-coordinator.core":
        raise ValueError("module ownership ownerId is invalid")
    if _ordered_strings(raw.get("fileKinds"), "file kinds") != _FILE_KINDS:
        raise ValueError("module ownership file kinds differ from the admitted vocabulary")
    _ordered_strings(raw.get("nonClaims"), "module ownership non-claims")
    _validate_candidate_queue(raw.get("candidateQueue"))
    review_metrics, signal_runtime_ids = _validate_review_signals(raw.get("reviewSignals"))
    traversal_limits = _traversal_limits(raw.get("traversalLimits"))
    default_file_kind, file_kind_rules = _validate_file_kind_classification(
        raw.get("fileKindClassification")
    )
    responsibilities = _validate_responsibilities(raw.get("responsibilities"))
    responsibility_ids = {item.responsibility_id for item in responsibilities}
    exclusive_roots = _validate_exclusive_roots(
        raw.get("exclusiveOwnershipRoots"), responsibility_ids
    )
    forbidden_rule_ids = _validate_rules(
        raw.get("forbiddenCoownership"), responsibility_ids, "forbidden coownership"
    )
    required_colocation_rule_ids = _validate_rules(
        raw.get("requiredColocation"), responsibility_ids, "required colocation"
    )
    _validate_decision(raw.get("decision"))
    _validate_waiver(raw.get("waiver"))
    return ModuleOwnershipProfile(
        default_file_kind=default_file_kind,
        exclusive_roots=exclusive_roots,
        file_kind_rules=file_kind_rules,
        forbidden_coownership_rule_ids=forbidden_rule_ids,
        profile_digest=hashlib.sha256(source.encode("utf-8")).hexdigest(),
        required_colocation_rule_ids=required_colocation_rule_ids,
        responsibilities=responsibilities,
        review_metrics=review_metrics,
        signal_grammar_id=_SIGNAL_GRAMMAR_ID,
        signal_runtime_ids=signal_runtime_ids,
        traversal_limits=traversal_limits,
    )


def _validate_candidate_queue(value: object) -> None:
    queue = as_object(value, "module ownership candidate queue")
    expected = {
        "cappedDeferredVerdict": "abstain",
        "defaultMaximumCandidates": None,
        "requiresCompletenessAccounting": True,
        "separateTestCohort": True,
    }
    _exact_keys(queue, set(expected), "module ownership candidate queue")
    if queue != expected:
        raise ValueError("module ownership candidate queue semantics differ from the contract")


def _validate_review_signals(
    value: object,
) -> tuple[tuple[ReviewMetric, ...], tuple[str, ...]]:
    signals = as_object(value, "module ownership review signals")
    _exact_keys(
        signals,
        {
            "grammarId",
            "metrics",
            "runtimeIds",
            "signalOnly",
            "unknownDisposition",
            "verdictCeiling",
        },
        "module ownership review signals",
    )
    if signals.get("signalOnly") is not True:
        raise ValueError("module ownership metrics must remain signal-only")
    if signals.get("grammarId") != _SIGNAL_GRAMMAR_ID:
        raise ValueError("module ownership signal grammar id is invalid")
    if signals.get("verdictCeiling") != "review-required":
        raise ValueError("metric-only verdict ceiling must equal review-required")
    if signals.get("unknownDisposition") != "review-required":
        raise ValueError("unknown review signals must remain review-required")
    runtime_ids = _ordered_strings(
        signals.get("runtimeIds"),
        "module ownership signal runtime ids",
    )
    if runtime_ids != _SIGNAL_RUNTIME_IDS:
        raise ValueError("module ownership signal runtime ids differ from the contract")

    rows = _object_rows(signals.get("metrics"), "module ownership review metrics")
    metrics: list[ReviewMetric] = []
    for index, row in enumerate(rows):
        label = f"module ownership review metric {index}"
        _exact_keys(
            row,
            {
                "applicableFileKinds",
                "applicableSuffixes",
                "metricId",
                "operator",
                "threshold",
            },
            label,
        )
        metric_id = _required_string(row.get("metricId"), f"{label} id")
        operator = _required_string(row.get("operator"), f"{label} operator")
        if operator not in {"gt", "gte"}:
            raise ValueError(f"{label} operator is invalid")
        threshold = row.get("threshold")
        if type(threshold) is not int or threshold <= 0:
            raise ValueError(f"{label} threshold must be a positive integer")
        applicable_file_kinds = _ordered_strings(
            row.get("applicableFileKinds"),
            f"{label} applicable file kinds",
        )
        if any(kind not in _FILE_KINDS for kind in applicable_file_kinds):
            raise ValueError(f"{label} contains an unknown applicable file kind")
        applicable_suffixes = _ordered_strings(
            row.get("applicableSuffixes"),
            f"{label} applicable suffixes",
        )
        if any(
            suffix != "*" and (not suffix.startswith(".") or "/" in suffix)
            for suffix in applicable_suffixes
        ):
            raise ValueError(f"{label} contains an invalid applicable suffix")
        if "*" in applicable_suffixes and len(applicable_suffixes) != 1:
            raise ValueError(f"{label} wildcard suffix must be exclusive")
        metrics.append(
            ReviewMetric(
                applicable_file_kinds=applicable_file_kinds,
                applicable_suffixes=applicable_suffixes,
                metric_id=metric_id,
                operator=operator,
                threshold=threshold,
            )
        )

    if tuple(metric.metric_id for metric in metrics) != _REVIEW_METRIC_IDS:
        raise ValueError("module ownership review metric ids differ from the contract")
    expected_boundaries = {
        "physical-lines": ("gt", 400),
        "recognized-public-declarations": ("gt", 12),
        "first-party-import-contexts": ("gte", 3),
    }
    if any(
        (metric.operator, metric.threshold) != expected_boundaries[metric.metric_id]
        for metric in metrics
    ):
        raise ValueError("module ownership review metric boundaries differ from the contract")
    return tuple(metrics), runtime_ids


def _validate_file_kind_classification(
    value: object,
) -> tuple[str, tuple[FileKindRule, ...]]:
    classification = as_object(value, "module ownership file-kind classification")
    _exact_keys(
        classification,
        {"defaultKind", "resolution", "rules"},
        "module ownership file-kind classification",
    )
    if classification.get("resolution") != "first-match":
        raise ValueError("module ownership file-kind resolution must equal first-match")
    default_kind = _required_string(
        classification.get("defaultKind"),
        "module ownership default file kind",
    )
    if default_kind not in _FILE_KINDS:
        raise ValueError("module ownership default file kind is unknown")

    rows = _object_rows(classification.get("rules"), "module ownership file-kind rules")
    rules: list[FileKindRule] = []
    observed_kinds: set[str] = set()
    for index, row in enumerate(rows):
        label = f"module ownership file-kind rule {index}"
        _exact_keys(row, {"kind", "pathPatterns"}, label)
        kind = _required_string(row.get("kind"), f"{label} kind")
        if kind not in _FILE_KINDS:
            raise ValueError(f"{label} kind is unknown")
        if kind in observed_kinds:
            raise ValueError(f"{label} kind is duplicated")
        patterns = _ordered_strings(row.get("pathPatterns"), f"{label} path patterns")
        for pattern in patterns:
            _safe_pattern(pattern, f"{label} path pattern")
        observed_kinds.add(kind)
        rules.append(FileKindRule(kind=kind, path_patterns=patterns))
    if not rules:
        raise ValueError("module ownership file-kind rules must not be empty")
    return default_kind, tuple(rules)


def _traversal_limits(value: object) -> TraversalLimits:
    limits = as_object(value, "module ownership traversal limits")
    _exact_keys(limits, set(_TRAVERSAL_LIMIT_CEILINGS), "module ownership traversal limits")
    admitted: dict[str, int] = {}
    for field, ceiling in _TRAVERSAL_LIMIT_CEILINGS.items():
        candidate = limits.get(field)
        if type(candidate) is not int or not 1 <= candidate <= ceiling:
            raise ValueError(f"{field} must be a positive integer no greater than {ceiling}")
        admitted[field] = candidate
    if admitted["maximumOwnedFiles"] > admitted["maximumEntries"]:
        raise ValueError("maximumOwnedFiles cannot exceed maximumEntries")
    if admitted["maximumCandidateFileBytes"] > admitted["maximumTotalCandidateBytes"]:
        raise ValueError("maximumCandidateFileBytes cannot exceed maximumTotalCandidateBytes")
    return TraversalLimits(
        git_path_discovery_timeout_seconds=admitted["gitPathDiscoveryTimeoutSeconds"],
        maximum_candidate_file_bytes=admitted["maximumCandidateFileBytes"],
        maximum_depth=admitted["maximumDepth"],
        maximum_entries=admitted["maximumEntries"],
        maximum_owned_files=admitted["maximumOwnedFiles"],
        maximum_relative_path_bytes=admitted["maximumRelativePathBytes"],
        maximum_total_candidate_bytes=admitted["maximumTotalCandidateBytes"],
        maximum_total_path_bytes=admitted["maximumTotalPathBytes"],
    )


def _validate_responsibilities(value: object) -> tuple[Responsibility, ...]:
    rows = _object_rows(value, "module ownership responsibilities")
    ids: list[str] = []
    responsibilities: list[Responsibility] = []
    for row in rows:
        _exact_keys(
            row,
            {"description", "pathPatterns", "responsibilityId"},
            "module ownership responsibility",
        )
        responsibility_id = _required_string(row.get("responsibilityId"), "responsibility id")
        ids.append(responsibility_id)
        _required_string(row.get("description"), "responsibility description")
        patterns = _ordered_strings(row.get("pathPatterns"), "responsibility path patterns")
        for pattern in patterns:
            _safe_pattern(pattern, "responsibility path pattern")
        responsibilities.append(
            Responsibility(responsibility_id=responsibility_id, path_patterns=patterns)
        )
    _canonical_ids(ids, "responsibility ids")
    return tuple(responsibilities)


def _validate_exclusive_roots(
    value: object, responsibilities: set[str]
) -> tuple[ExclusiveOwnershipRoot, ...]:
    rows = _object_rows(value, "exclusive ownership roots")
    roots: list[str] = []
    admitted: list[ExclusiveOwnershipRoot] = []
    for row in rows:
        _exact_keys(
            row,
            {"responsibilityIds", "root", "suffixes"},
            "exclusive ownership root",
        )
        root = _safe_path(row.get("root"), "exclusive ownership root")
        roots.append(root)
        owner_ids = _ordered_strings(
            row.get("responsibilityIds"), "exclusive ownership responsibility ids"
        )
        unknown = sorted(set(owner_ids) - responsibilities)
        if unknown:
            raise ValueError(f"exclusive ownership root references unknown owners: {unknown}")
        suffixes = _ordered_strings(row.get("suffixes"), "owned suffixes")
        if any(not suffix.startswith(".") or "/" in suffix for suffix in suffixes):
            raise ValueError("owned suffixes must be simple dot-prefixed extensions")
        admitted.append(
            ExclusiveOwnershipRoot(
                responsibility_ids=owner_ids,
                root=Path(root),
                suffixes=suffixes,
            )
        )
    _canonical_ids(roots, "exclusive ownership roots")
    return tuple(admitted)


def _validate_rules(value: object, responsibilities: set[str], label: str) -> tuple[str, ...]:
    rows = _object_rows(value, label)
    ids: list[str] = []
    for row in rows:
        _exact_keys(
            row,
            {"applicabilityPatterns", "responsibilitySets", "ruleId"},
            label,
        )
        ids.append(_required_string(row.get("ruleId"), f"{label} rule id"))
        for pattern in _ordered_strings(row.get("applicabilityPatterns"), f"{label} patterns"):
            _safe_pattern(pattern, f"{label} pattern")
        responsibility_sets = as_array(
            row.get("responsibilitySets"), f"{label} responsibility sets"
        )
        if not responsibility_sets:
            raise ValueError(f"{label} rule requires at least one responsibility set")
        admitted_sets = [
            _ordered_strings(item, f"{label} responsibility set") for item in responsibility_sets
        ]
        if any(len(item) != 2 for item in admitted_sets):
            raise ValueError(
                f"{label} responsibility sets must contain exactly two responsibilities"
            )
        if admitted_sets != sorted(set(admitted_sets)):
            raise ValueError(f"{label} responsibility sets must be unique and canonically ordered")
        unknown = sorted(
            {
                responsibility_id
                for responsibility_set in admitted_sets
                for responsibility_id in responsibility_set
            }
            - responsibilities
        )
        if unknown:
            raise ValueError(f"{label} rule references unknown responsibilities: {unknown}")
    _canonical_ids(ids, f"{label} rule ids")
    return tuple(ids)


def _validate_decision(value: object) -> None:
    decision = as_object(value, "module ownership decision")
    expected = {
        "heuristicOnly": "review-required",
        "newOrWorsenedClosedViolation": "fail",
        "preexistingClosedViolation": "warn",
        "requiresApplicableRule": True,
        "requiresExactProfileEpoch": True,
        "requiresHardConstraintPreservation": True,
        "requiresNoApplicableRequiredColocation": True,
        "requiresObservablePreservation": True,
        "requiresSafeDecomposition": True,
        "requiresStableResponsibilitySet": True,
        "requiresTypedCurrentEvidence": True,
        "unknownOrConflict": "abstain",
    }
    _exact_keys(decision, set(expected), "module ownership decision")
    if decision != expected:
        raise ValueError("module ownership decision semantics differ from the governing contract")


def _validate_waiver(value: object) -> None:
    waiver = as_object(value, "module ownership waiver")
    expected = {
        "requiresExactRuleAndPath": True,
        "requiresExpiry": True,
        "requiresOwner": True,
        "requiresReason": True,
    }
    _exact_keys(waiver, set(expected), "module ownership waiver")
    if waiver != expected:
        raise ValueError("module ownership waiver must remain exact, owned, reasoned, and expiring")


def _read_bounded_file(repo_root: Path, relative_path: Path) -> str:
    payload = read_repository_regular_file(
        repo_root,
        relative_path,
        "module ownership profile",
        maximum_bytes=MAX_PROFILE_BYTES,
    )
    return payload.decode("utf-8", errors="strict")


def _parse_profile(source: str) -> dict[str, object]:
    try:
        value: object = json.loads(
            source,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as error:
        raise ValueError(f"module ownership profile is not JSON: {error.msg}") from error
    return as_object(value, "module ownership profile")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"module ownership profile has duplicate key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> object:
    raise ValueError(f"module ownership profile has non-finite constant: {value}")


def _ordered_strings(value: object, label: str) -> tuple[str, ...]:
    values = tuple(_required_string(item, label) for item in as_array(value, label))
    if not values:
        raise ValueError(f"{label} must be non-empty")
    if values != tuple(sorted(set(values))):
        raise ValueError(f"{label} must be unique and canonically ordered")
    return values


def _canonical_ids(values: Sequence[str], label: str) -> None:
    if list(values) != sorted(set(values)):
        raise ValueError(f"{label} must be unique and canonically ordered")


def _safe_path(value: object, label: str) -> str:
    path = _required_string(value, label)
    if (
        path.startswith("/")
        or "\\" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise ValueError(f"{label} is not a safe repository path: {path}")
    return path


def _safe_pattern(value: str, label: str) -> str:
    if (
        value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise ValueError(f"{label} is not a safe repository pattern: {value}")
    return value

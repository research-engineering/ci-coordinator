from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from scripts.proofkit_common import JsonObject, as_object, js_json_dumps, safe_repo_path


@dataclass(frozen=True, slots=True)
class _BaselineVariant:
    binding_state: str
    binding_set_sha256: str
    affected_requirement_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _Transition:
    transition_id: str
    baseline_variants: tuple[_BaselineVariant, ...]
    paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _ParsedManifest:
    replacement_pairs: tuple[JsonObject, ...]
    replacements: Mapping[str, str]
    transitions: tuple[_Transition, ...]


def proof_like_path_patterns_for_range(
    base_profile: Mapping[str, object], head_profile: Mapping[str, object]
) -> list[str]:
    return sorted(
        set(
            _proof_like_path_patterns(base_profile, "base repo profile")
            + _proof_like_path_patterns(head_profile, "head repo profile")
        )
    )


def admitted_proof_owner_retirements(
    *,
    base_bindings: Mapping[str, object],
    deleted_proof_like_paths: Sequence[object],
    head_bindings: Mapping[str, object],
    head_path_exists: Callable[[str], bool],
    manifest: Mapping[str, object],
) -> set[str]:
    deleted = set(_normalized_string_set(deleted_proof_like_paths, "deleted proof-like paths"))
    parsed_manifest = _parse_retirement_manifest(manifest)
    admitted: set[str] = set()
    declared_paths: set[str] = set()

    for transition in parsed_manifest.transitions:
        for path in transition.paths:
            if path in declared_paths:
                raise ValueError(f"proof owner retirement path is declared more than once: {path}")
            declared_paths.add(path)
        if not any(path in deleted for path in transition.paths):
            continue
        undeleted = [path for path in transition.paths if path not in deleted]
        if undeleted:
            raise ValueError(
                "proof owner retirement transition is only partially present in the range: "
                + ", ".join(undeleted)
            )
        if any(head_path_exists(path) for path in transition.paths):
            raise ValueError(
                f"proof owner retirement retains a head path: {transition.transition_id}"
            )
        if any(
            safe_repo_path(binding.get("witnessPath")) in transition.paths
            for binding in _document_rows(head_bindings, "bindings")
        ):
            raise ValueError(
                f"proof owner retirement retains a head binding: {transition.transition_id}"
            )

        base_rows = normalized_owner_rows(base_bindings, transition.paths)
        bound_paths = {str(row["witnessPath"]) for row in base_rows}
        unbound_paths = [path for path in transition.paths if path not in bound_paths]
        actual_binding_state = "unbound" if not base_rows else "bound"
        if actual_binding_state == "bound" and unbound_paths:
            raise ValueError(
                "proof owner retirement has a mixed baseline binding state: "
                + ", ".join(unbound_paths)
            )
        affected_requirements = sorted({str(row["requirementId"]) for row in base_rows})
        actual_digest = owner_binding_digest(base_rows)
        baseline_variant = next(
            (
                variant
                for variant in transition.baseline_variants
                if variant.binding_state == actual_binding_state
                and variant.binding_set_sha256 == actual_digest
                and list(variant.affected_requirement_ids) == affected_requirements
            ),
            None,
        )
        if baseline_variant is None:
            raise ValueError(
                f"proof owner retirement baseline variant changed: {transition.transition_id}"
            )
        unwitnessed = [
            requirement_id
            for requirement_id in affected_requirements
            if not _requirement_has_live_head_witness(
                base_bindings,
                requirement_id,
                head_bindings,
                parsed_manifest.replacements.get(requirement_id, requirement_id),
                head_path_exists,
            )
        ]
        if unwitnessed:
            raise ValueError(
                "proof owner retirement leaves requirements without a live head witness: "
                + ", ".join(unwitnessed)
            )
        admitted.update(transition.paths)
    return admitted


def requirement_replacement_pairs(manifest: Mapping[str, object]) -> list[JsonObject]:
    return [dict(pair) for pair in _parse_retirement_manifest(manifest).replacement_pairs]


def retired_proof_owner_paths(manifest: Mapping[str, object]) -> list[str]:
    parsed = _parse_retirement_manifest(manifest)
    return sorted({path for transition in parsed.transitions for path in transition.paths})


def owner_binding_digest(rows: Sequence[Mapping[str, object]]) -> str:
    source = js_json_dumps(list(rows)).encode()
    return hashlib.sha256(source).hexdigest()


def normalized_owner_rows(
    bindings: Mapping[str, object], paths: Sequence[object]
) -> list[JsonObject]:
    admitted_paths = {safe_repo_path(path) for path in paths}
    rows: list[JsonObject] = []
    for binding in _document_rows(bindings, "bindings"):
        witness_path = safe_repo_path(binding.get("witnessPath"))
        if witness_path not in admitted_paths:
            continue
        rows.append(
            {
                "requirementId": _nonempty_string(
                    binding.get("requirementId"), "binding requirement id"
                ),
                "scenarioId": _nonempty_string(binding.get("scenarioId"), "binding scenario id"),
                "witnessId": _nonempty_string(binding.get("witnessId"), "binding witness id"),
                "witnessKind": _nonempty_string(binding.get("witnessKind"), "binding witness kind"),
                "witnessPath": witness_path,
                "commandIds": _normalized_unique_strings(
                    binding.get("commandIds"), "binding command ids"
                ),
                "environmentClasses": _normalized_unique_strings(
                    binding.get("environmentClasses"), "binding environment classes"
                ),
            }
        )
    return sorted(rows, key=js_json_dumps)


def _parse_retirement_manifest(manifest: Mapping[str, object]) -> _ParsedManifest:
    _exact_keys(
        manifest,
        ("schemaVersion", "requirementReplacements", "transitions"),
        "proof owner retirement manifest",
    )
    if type(manifest.get("schemaVersion")) is not int or manifest["schemaVersion"] != 2:
        raise ValueError("proof owner retirement manifest schema version is unsupported")
    raw_transitions = manifest.get("transitions")
    if not isinstance(raw_transitions, list):
        raise TypeError("proof owner retirement transitions must be an array")
    raw_replacements = manifest.get("requirementReplacements")
    if not isinstance(raw_replacements, list):
        raise TypeError("proof owner retirement requirement replacements must be an array")

    replacement_pairs: list[JsonObject] = []
    for raw_pair in raw_replacements:
        pair = as_object(raw_pair, "proof owner retirement requirement replacement")
        _exact_keys(
            pair,
            ("nextRequirementId", "previousRequirementId"),
            "proof owner retirement requirement replacement",
        )
        previous_id = _nonempty_string(pair.get("previousRequirementId"), "previous requirement id")
        next_id = _nonempty_string(pair.get("nextRequirementId"), "next requirement id")
        if previous_id == next_id:
            raise ValueError("proof owner retirement requirement replacement must change identity")
        replacement_pairs.append(
            {"nextRequirementId": next_id, "previousRequirementId": previous_id}
        )
    canonical_pairs = sorted(replacement_pairs, key=js_json_dumps)
    if replacement_pairs != canonical_pairs:
        raise ValueError(
            "proof owner retirement requirement replacements must be canonically ordered"
        )
    previous_ids = [str(pair["previousRequirementId"]) for pair in replacement_pairs]
    next_ids = [str(pair["nextRequirementId"]) for pair in replacement_pairs]
    if len(set(previous_ids)) != len(previous_ids):
        raise ValueError("proof owner retirement previous requirement ids must be unique")
    if len(set(next_ids)) != len(next_ids):
        raise ValueError("proof owner retirement next requirement ids must be unique")
    replacements = dict(zip(previous_ids, next_ids, strict=True))

    transitions: list[_Transition] = []
    for raw_transition in raw_transitions:
        value = as_object(raw_transition, "proof owner retirement transition")
        _exact_keys(
            value,
            ("transitionId", "baselineVariants", "paths", "disposition"),
            "proof owner retirement transition",
        )
        disposition = as_object(value.get("disposition"), "proof owner retirement disposition")
        _exact_keys(
            disposition,
            ("kind", "authorityRefs", "rationale"),
            "proof owner retirement disposition",
        )
        if disposition.get("kind") != "obsolete":
            raise ValueError("proof owner retirement disposition must be explicit obsolete")
        raw_variants = value.get("baselineVariants")
        if not isinstance(raw_variants, list) or not raw_variants:
            raise ValueError("proof owner retirement baseline variants must be a non-empty array")
        _sorted_unique_strings(disposition.get("authorityRefs"), "authority refs")
        _nonempty_string(disposition.get("rationale"), "retirement rationale")
        paths = tuple(
            safe_repo_path(path)
            for path in _sorted_unique_strings(value.get("paths"), "retired proof owner paths")
        )
        transitions.append(
            _Transition(
                transition_id=_nonempty_string(
                    value.get("transitionId"), "proof owner retirement transition id"
                ),
                baseline_variants=tuple(
                    _retirement_baseline_variant(
                        as_object(raw, "proof owner retirement baseline variant")
                    )
                    for raw in raw_variants
                ),
                paths=paths,
            )
        )
    return _ParsedManifest(
        replacement_pairs=tuple(replacement_pairs),
        replacements=replacements,
        transitions=tuple(transitions),
    )


def _retirement_baseline_variant(value: Mapping[str, object]) -> _BaselineVariant:
    _exact_keys(
        value,
        ("bindingState", "bindingSetSha256", "affectedRequirementIds"),
        "proof owner retirement baseline variant",
    )
    binding_state = value.get("bindingState")
    if binding_state not in {"bound", "unbound"}:
        raise ValueError("proof owner retirement baseline binding state is unsupported")
    digest = _nonempty_string(value.get("bindingSetSha256"), "baseline binding digest")
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise ValueError("proof owner retirement baseline digest must be lowercase SHA-256")
    affected = _sorted_unique_strings(
        value.get("affectedRequirementIds"),
        "affected requirement ids",
        allow_empty=True,
    )
    if binding_state == "unbound" and affected:
        raise ValueError("unbound proof owner retirement cannot affect requirements")
    return _BaselineVariant(
        binding_state=str(binding_state),
        binding_set_sha256=digest,
        affected_requirement_ids=tuple(affected),
    )


def _proof_like_path_patterns(profile: Mapping[str, object], context: str) -> list[str]:
    proofs_value = profile.get("proofs")
    proofs = as_object(proofs_value, f"{context} proofs") if proofs_value is not None else {}
    return _sorted_unique_strings(proofs.get("proofLikePaths"), f"{context} proof-like paths")


def _requirement_has_live_head_witness(
    base_bindings: Mapping[str, object],
    base_requirement_id: str,
    head_bindings: Mapping[str, object],
    head_requirement_id: str,
    head_path_exists: Callable[[str], bool],
) -> bool:
    requirement_exists = any(
        requirement.get("requirementId") == head_requirement_id
        for requirement in _document_rows(head_bindings, "requirements")
    )
    base_requires_non_contract = any(
        binding.get("requirementId") == base_requirement_id
        and binding.get("witnessKind") != "contract"
        for binding in _document_rows(base_bindings, "bindings")
    )
    return requirement_exists and any(
        binding.get("requirementId") == head_requirement_id
        and (not base_requires_non_contract or binding.get("witnessKind") != "contract")
        and head_path_exists(safe_repo_path(binding.get("witnessPath")))
        for binding in _document_rows(head_bindings, "bindings")
    )


def _document_rows(document: Mapping[str, object], field: str) -> list[JsonObject]:
    raw = document.get(field, [])
    if not isinstance(raw, list):
        raise TypeError(f"{field} must be an array")
    return [as_object(row, field) for row in raw]


def _exact_keys(value: Mapping[str, object], expected: Sequence[str], context: str) -> None:
    actual = sorted(value)
    canonical = sorted(expected)
    if actual != canonical:
        raise ValueError(f"{context} fields must equal {js_json_dumps(canonical)}")


def _sorted_unique_strings(value: object, context: str, allow_empty: bool = False) -> list[str]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or any(not isinstance(item, str) or not item for item in value)
    ):
        article = "a" if allow_empty else "a non-empty"
        raise ValueError(f"{context} must be {article} string array")
    strings = list(value)
    canonical = sorted(set(strings))
    if strings != canonical:
        raise ValueError(f"{context} canonical order must equal {js_json_dumps(canonical)}")
    return strings


def _normalized_unique_strings(value: object, context: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item for item in value)
    ):
        raise ValueError(f"{context} must be a non-empty string array")
    return sorted(set(value))


def _normalized_string_set(value: Sequence[object], context: str) -> list[str]:
    if any(not isinstance(item, str) or not item for item in value):
        raise ValueError(f"{context} must be a string array")
    strings = [item for item in value if isinstance(item, str)]
    return [safe_repo_path(item) for item in sorted(set(strings))]


def _nonempty_string(value: object, context: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{context} must be a non-empty string")
    return value

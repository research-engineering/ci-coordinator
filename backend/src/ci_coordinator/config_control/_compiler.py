from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from hashlib import sha256
from typing import Literal, cast

from ci_coordinator.config_control._dynamic_ci_compatibility import compile_dynamic_ci
from ci_coordinator.config_control._resources import ContractResource, contract_document
from ci_coordinator.config_control._schema_validation import validate_structure
from ci_coordinator.config_control.contracts import PolicyDiagnostic, RepositoryScope
from ci_coordinator.kernel.canonical_json import (
    CanonicalJsonError,
    bounded_canonical_json,
    canonical_json,
)

type JsonObject = dict[str, object]
type OutputProjection = Literal["normalized-document", "compiled-policy"]

_SOURCE_DOMAIN = b"ci-policy-source/v1\0"
_DOCUMENT_DOMAIN = b"ci-policy-document/v1\0"
_COMPILED_DOMAIN = b"ci-compiled-repository-policy/v1\0"
_EPOCH_ID_DOMAIN = b"ci-config-epoch/v1\0"


@dataclass(frozen=True, slots=True)
class _Compilation:
    scope: RepositoryScope
    normalized_document_bytes: bytes
    compiled_policy_bytes: bytes
    source_hash: str
    document_hash: str
    epoch_hash: str
    epoch_id: str


@dataclass(frozen=True, slots=True)
class _CompilerContract:
    schema_version: str
    max_normalized_bytes: int
    max_compiled_bytes: int
    code_order: dict[str, int]


def compile_policy(
    document: JsonObject,
    *,
    raw_source: bytes,
    source_format: str,
) -> _Compilation | PolicyDiagnostic:
    compiled = _compile_document(document)
    structural_failure = _compiled_structure_diagnostic(compiled)
    normalized_result = _admit_output_bytes(document, "normalized-document")
    compiled_result = _admit_output_bytes(compiled, "compiled-policy")
    failures = ([structural_failure] if structural_failure is not None else []) + [
        result
        for result in (normalized_result, compiled_result)
        if isinstance(result, PolicyDiagnostic)
    ]
    if failures:
        return _minimum_compile_diagnostic(failures)
    if not isinstance(normalized_result, bytes) or not isinstance(compiled_result, bytes):
        raise RuntimeError("output admission result algebra is incomplete")

    repository = _object(document["repository"])
    scope = RepositoryScope(
        installation_id=_scope_integer(repository["installationId"]),
        repository_id=_scope_integer(repository["repositoryId"]),
    )
    source_hash = _sha256(_SOURCE_DOMAIN + source_format.encode("utf-8") + b"\0" + raw_source)
    document_hash = _sha256(_DOCUMENT_DOMAIN + normalized_result)
    epoch_hash = compiled_policy_hash(compiled_result)
    epoch_id = config_epoch_id(
        scope=scope,
        source_hash=source_hash,
        document_hash=document_hash,
        epoch_hash=epoch_hash,
    )
    return _Compilation(
        scope=scope,
        normalized_document_bytes=normalized_result,
        compiled_policy_bytes=compiled_result,
        source_hash=source_hash,
        document_hash=document_hash,
        epoch_hash=epoch_hash,
        epoch_id=epoch_id,
    )


def compiled_policy_hash(compiled_policy_bytes: bytes) -> str:
    return _sha256(_COMPILED_DOMAIN + compiled_policy_bytes)


def config_epoch_id(
    *,
    scope: RepositoryScope,
    source_hash: str,
    document_hash: str,
    epoch_hash: str,
) -> str:
    scope_bytes = canonical_json(
        {
            "installationId": scope.installation_id,
            "repositoryId": scope.repository_id,
        }
    )
    return _sha256(
        _EPOCH_ID_DOMAIN
        + scope_bytes
        + b"\0"
        + source_hash.encode("ascii")
        + b"\0"
        + document_hash.encode("ascii")
        + b"\0"
        + epoch_hash.encode("ascii")
    )


def _compile_document(document: JsonObject) -> JsonObject:
    repository = _object(document["repository"])
    dynamic_value = repository["dynamicCi"]
    dynamic = None
    if dynamic_value is not None:
        dynamic_input = _object(dynamic_value)
        if _boolean(dynamic_input["planningEnabled"]):
            dynamic = compile_dynamic_ci(dynamic_input)
    return {
        "schemaVersion": _contract().schema_version,
        "scope": {
            "installationId": repository["installationId"],
            "repositoryId": repository["repositoryId"],
        },
        "owner": repository["owner"],
        "name": repository["name"],
        "defaultBranch": repository["defaultBranch"],
        "rules": [_compile_rule(_object(value)) for value in _array(repository["rules"])],
        "dynamicCi": dynamic,
    }


def _compile_rule(rule: JsonObject) -> JsonObject:
    event = _object(rule["on"])
    timing = _object(rule["timing"])
    return {
        "name": rule["name"],
        "event": event["event"],
        "branches": list(_array(event["branches"])),
        "mode": rule["mode"],
        "timing": {
            "expectedSignalTimeoutSeconds": timing["expectedSignalTimeoutSeconds"],
            "absenceVerificationWindowSeconds": timing["absenceVerificationWindowSeconds"],
            "absencePollLookbackSeconds": timing["absencePollLookbackSeconds"],
            "lateFindingWindowSeconds": timing["lateFindingWindowSeconds"],
            "mutableDecisionWindowSeconds": timing["mutableDecisionWindowSeconds"],
        },
        "expectedSignals": [
            _compile_expected_signal(_object(value)) for value in _array(rule["expectedSignals"])
        ],
        "omittedSignals": [
            _compile_omitted_signal(_object(value)) for value in _array(rule["omittedSignals"])
        ],
    }


def _compile_expected_signal(signal: JsonObject) -> JsonObject:
    return {
        "kind": signal["kind"],
        "name": signal["name"],
        "workflowFile": signal["workflowFile"],
        "source": signal["source"],
        "requiredConclusion": signal["requiredConclusion"],
        "required": signal["required"],
    }


def _compile_omitted_signal(signal: JsonObject) -> JsonObject:
    return {
        "kind": signal["kind"],
        "name": signal["name"],
        "workflowFile": signal["workflowFile"],
        "verifyAbsence": signal["verifyAbsence"],
        "reason": signal["reason"],
    }


def _compiled_structure_diagnostic(value: object) -> PolicyDiagnostic | None:
    failure = validate_structure(value, "compiled")
    if failure is None:
        return None
    return PolicyDiagnostic(
        code="compile.output_invalid",
        phase="compile",
        rule_id=f"schema:{failure.keyword}",
        instance_pointer=failure.instance_pointer,
        parameters={
            "schemaKeyword": failure.keyword,
            "schemaPointer": failure.schema_pointer,
        },
    )


def _admit_output_bytes(
    value: object,
    projection: OutputProjection,
) -> bytes | PolicyDiagnostic:
    contract = _contract()
    limit = (
        contract.max_normalized_bytes
        if projection == "normalized-document"
        else contract.max_compiled_bytes
    )
    try:
        return bounded_canonical_json(value, max_bytes=limit)
    except CanonicalJsonError as error:
        if error.code != "canonical_json_max_bytes_exceeded":
            raise RuntimeError("compiled output violated the canonical JSON domain") from error
        return PolicyDiagnostic(
            code="compile.output_too_large",
            phase="compile",
            rule_id="compile.output-bytes",
            instance_pointer="",
            parameters={
                "projection": projection,
                "limit": limit,
                "observed": limit + 1,
            },
        )


def _minimum_compile_diagnostic(
    candidates: list[PolicyDiagnostic],
) -> PolicyDiagnostic:
    if not candidates:
        raise RuntimeError("compile diagnostic selection requires a candidate")
    non_root = [candidate for candidate in candidates if candidate.instance_pointer]
    if len(non_root) > 1:
        raise RuntimeError("compile candidate set has multiple non-root diagnostics")
    code_order = _contract().code_order
    return min(
        candidates,
        key=lambda candidate: (
            0 if candidate.instance_pointer == "" else 1,
            code_order[candidate.code],
            candidate.rule_id,
            canonical_json(dict(candidate.parameters)),
        ),
    )


@cache
def _contract() -> _CompilerContract:
    profile = contract_document(ContractResource.DOCUMENT_PROFILE)
    compilation = _object(profile["compilation"])
    outputs = _object(profile["outputs"])
    byte_admission = _object(outputs["byteAdmission"])
    hashes = _object(profile["hashes"])
    diagnostics = _object(profile["diagnostics"])
    expected_hash_projections = {
        "sourceHashProjection": (
            "utf8(ci-policy-source/v1) + nul + utf8(explicit-format) + nul + "
            "exact-admitted-source-bytes"
        ),
        "documentHashProjection": (
            "utf8(ci-policy-document/v1) + nul + canonical-normalized-policy-document-v1"
        ),
        "epochHashProjection": (
            "utf8(ci-compiled-repository-policy/v1) + nul + canonical-compiled-repository-policy-v1"
        ),
    }
    for field, expected in expected_hash_projections.items():
        if hashes[field] != expected:
            raise RuntimeError(f"unsupported config identity projection: {field}")
    expected_output_contract = {
        "measure": "canonical-json-utf-8-octets/v1",
        "evaluation": (
            "measure both projections and create one compile.output_too_large candidate for "
            "each overflow"
        ),
        "overflowObserved": "the applicable limit plus one",
        "encoderTermination": (
            "stop a projection after proving its limit plus one overflow sentinel"
        ),
        "failureSelection": (
            "minimum candidate under diagnostics.failureSelection.diagnosticOrdering"
        ),
        "hashPrecondition": (
            "sourceHash, documentHash, epochHash, and epochId are not computed until both "
            "projections are admitted"
        ),
    }
    for field, expected in expected_output_contract.items():
        if byte_admission[field] != expected:
            raise RuntimeError(f"unsupported config output admission contract: {field}")
    expected_projections = ["normalized-document", "compiled-policy"]
    if _array(byte_admission["candidateProjections"]) != expected_projections:
        raise RuntimeError("unsupported config output candidate inventory")
    if outputs["scopeProjection"] != ("kernel.canonical_json({installationId,repositoryId})"):
        raise RuntimeError("unsupported config scope identity projection")
    if outputs["epochIdProjection"] != (
        "sha256(utf8(ci-config-epoch/v1) + nul + scopeProjection + nul + "
        "asciiLowerHex(sourceHash) + nul + asciiLowerHex(documentHash) + nul + "
        "asciiLowerHex(epochHash))"
    ):
        raise RuntimeError("unsupported config epoch id projection")
    codes = _array(diagnostics["codes"])
    if not all(type(code) is str for code in codes):
        raise RuntimeError("config diagnostic code inventory must contain strings")
    code_order = {str(code): index for index, code in enumerate(codes)}
    if set(code_order).intersection({"compile.output_invalid", "compile.output_too_large"}) != {
        "compile.output_invalid",
        "compile.output_too_large",
    }:
        raise RuntimeError("config diagnostic inventory is missing compile codes")
    return _CompilerContract(
        schema_version=_string(compilation["schemaVersion"]),
        max_normalized_bytes=_integer(outputs["maxNormalizedDocumentCanonicalBytes"]),
        max_compiled_bytes=_integer(outputs["maxCompiledPolicyCanonicalBytes"]),
        code_order=code_order,
    )


def _sha256(value: bytes) -> str:
    return sha256(value).hexdigest()


def _object(value: object) -> JsonObject:
    if not isinstance(value, dict) or any(type(key) is not str for key in value):
        raise RuntimeError("normalized policy object invariant failed")
    return cast(JsonObject, value)


def _array(value: object) -> list[object]:
    if not isinstance(value, list):
        raise RuntimeError("normalized policy array invariant failed")
    return cast(list[object], value)


def _string(value: object) -> str:
    if type(value) is not str:
        raise RuntimeError("normalized policy string invariant failed")
    return value


def _integer(value: object) -> int:
    if type(value) is not int:
        raise RuntimeError("compiler contract integer invariant failed")
    return value


def _scope_integer(value: object) -> int:
    if type(value) is int:
        return value
    if type(value) is float and value.is_integer():
        return int(value)
    raise RuntimeError("normalized policy integer invariant failed")


def _boolean(value: object) -> bool:
    if type(value) is not bool:
        raise RuntimeError("normalized policy boolean invariant failed")
    return value

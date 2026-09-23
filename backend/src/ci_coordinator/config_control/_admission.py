from __future__ import annotations

from ci_coordinator.config_control._compiler import _Compilation, compile_policy
from ci_coordinator.config_control._document import parse_policy_document
from ci_coordinator.config_control._feasibility import validate_producer_feasibility
from ci_coordinator.config_control._parser_support import ParsedDocument
from ci_coordinator.config_control._resources import ContractResource, contract_identity
from ci_coordinator.config_control._rules import normalize_policy_document
from ci_coordinator.config_control._semantics import validate_policy_semantics
from ci_coordinator.config_control.contracts import (
    PolicyAdmissionResult,
    PolicyDiagnostic,
    ValidatedEpochDraft,
)


def admit_policy_document(raw_source: bytes, format: str) -> PolicyAdmissionResult:
    parsed = parse_policy_document(raw_source, format)
    if isinstance(parsed, PolicyDiagnostic):
        return (parsed,)
    if not isinstance(parsed, ParsedDocument):
        raise RuntimeError("policy parser result algebra is incomplete")

    normalized = normalize_policy_document(parsed.value)
    if isinstance(normalized, PolicyDiagnostic):
        return (normalized,)

    semantic_failure = validate_policy_semantics(normalized)
    if semantic_failure is not None:
        return (semantic_failure,)

    feasibility_failure = validate_producer_feasibility(normalized)
    if feasibility_failure is not None:
        return (feasibility_failure,)

    compilation = compile_policy(
        normalized,
        raw_source=raw_source,
        source_format=parsed.source_format,
    )
    if isinstance(compilation, PolicyDiagnostic):
        return (compilation,)
    if not isinstance(compilation, _Compilation):
        raise RuntimeError("policy compiler result algebra is incomplete")

    return _validated_epoch(parsed, raw_source, compilation)


def _validated_epoch(
    parsed: ParsedDocument,
    raw_source: bytes,
    compilation: _Compilation,
) -> ValidatedEpochDraft:
    return ValidatedEpochDraft(
        source_format=parsed.source_format,
        source_bytes=raw_source,
        scope=compilation.scope,
        normalized_document_bytes=compilation.normalized_document_bytes,
        compiled_policy_bytes=compilation.compiled_policy_bytes,
        document_schema_id=contract_identity(ContractResource.DOCUMENT_SCHEMA),
        document_profile_id=contract_identity(ContractResource.DOCUMENT_PROFILE),
        semantic_profile_id=contract_identity(ContractResource.SEMANTIC_PROFILE),
        compiled_schema_id=contract_identity(ContractResource.COMPILED_SCHEMA),
        producer_resource_profile_id=contract_identity(ContractResource.AUDIT_RESOURCE_PROFILE),
        producer_byte_profile_id=contract_identity(ContractResource.AUDIT_BYTE_PROFILE),
        producer_feasibility_profile_id=contract_identity(ContractResource.FEASIBILITY_PROFILE),
        source_hash=compilation.source_hash,
        document_hash=compilation.document_hash,
        epoch_hash=compilation.epoch_hash,
        epoch_id=compilation.epoch_id,
    )

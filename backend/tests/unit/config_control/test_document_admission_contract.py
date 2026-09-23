from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import ci_coordinator.config_control as config_control
import ci_coordinator.config_control._admission as admission_module
from ci_coordinator.config_control import (
    PolicyDiagnostic,
    RepositoryScope,
    ValidatedEpochDraft,
    admit_policy_document,
)
from ci_coordinator.config_control._document import parse_policy_document
from ci_coordinator.config_control._parser_support import (
    ParsedDocument,
)
from ci_coordinator.config_control._rules import normalize_policy_document
from ci_coordinator.config_control.limits import MAX_CONFIG_CONTRACT_ID_UTF8_BYTES
from ci_coordinator.config_control.planning_projection import project_dynamic_ci_planning
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

from ._document_admission_support import (
    assert_failure,
    normalization_failure,
    valid_epoch_draft,
    valid_epoch_draft_arguments,
    valid_policy_document,
    valid_policy_source,
)

EXPECTED_NORMALIZED_POLICY_BYTES = (
    b'{"repository":{"defaultBranch":"main","dynamicCi":null,"installationId":1,'
    b'"name":"ci-coordinator","owner":"example-org","repositoryId":2,'
    b'"rules":[{"expectedSignals":[{"kind":"workflow","name":"CI","required":true,'
    b'"requiredConclusion":"success","source":"native","workflowFile":"ci.yml"}],'
    b'"mode":"observe","name":"main","omittedSignals":[],"on":{"branches":["main"],'
    b'"event":"push"},"timing":{"absencePollLookbackSeconds":3600,'
    b'"absenceVerificationWindowSeconds":300,"expectedSignalTimeoutSeconds":3600,'
    b'"lateFindingWindowSeconds":86400,"mutableDecisionWindowSeconds":300}}]},'
    b'"schemaVersion":"ci-repository-policy/v1"}'
)


def test_public_example_is_an_admitted_repository_policy() -> None:
    source = (Path(__file__).parents[4] / "config.example.yaml").read_bytes()

    result = admit_policy_document(source, "yaml-1.2")

    assert isinstance(result, ValidatedEpochDraft)
    projection = project_dynamic_ci_planning(result)
    assert projection is not None
    assert projection.validation_catalog.execution_profiles[0].capacity_class_id == (
        "self-hosted-default"
    )


def test_observe_only_policy_has_no_dynamic_planning_projection() -> None:
    result = admit_policy_document(valid_policy_source(), "json")

    assert isinstance(result, ValidatedEpochDraft)
    assert project_dynamic_ci_planning(result) is None


@pytest.mark.parametrize("event", ["merge_group", "pull_request", "push"])
def test_policy_event_domain_matches_runtime_planning(event: str) -> None:
    document = valid_policy_document()
    repository = document["repository"]
    assert isinstance(repository, dict)
    rules = repository["rules"]
    assert isinstance(rules, list)
    rule = rules[0]
    assert isinstance(rule, dict)
    trigger = rule["on"]
    assert isinstance(trigger, dict)
    trigger["event"] = event

    result = admit_policy_document(
        json.dumps(document, separators=(",", ":")).encode(),
        "json",
    )

    assert isinstance(result, ValidatedEpochDraft)
    compiled = json.loads(result.compiled_policy_bytes)
    assert compiled["rules"][0]["event"] == event


def test_policy_rejects_event_outside_runtime_planning_domain() -> None:
    document = valid_policy_document()
    repository = document["repository"]
    assert isinstance(repository, dict)
    rules = repository["rules"]
    assert isinstance(rules, list)
    rule = rules[0]
    assert isinstance(rule, dict)
    trigger = rule["on"]
    assert isinstance(trigger, dict)
    trigger["event"] = "workflow_dispatch"

    result = admit_policy_document(
        json.dumps(document, separators=(",", ":")).encode(),
        "json",
    )

    assert type(result) is tuple and len(result) == 1
    diagnostic = result[0]
    assert diagnostic.code == "structure.invalid"
    assert diagnostic.instance_pointer == "/repository/rules/0/on/event"


@pytest.mark.parametrize(
    "obsolete_field",
    [
        "allowedCredentialScopes",
        "allowedFixtureProfiles",
        "checks",
        "stableRequiredName",
        "validationCatalogVersion",
        "workflowCatalogVersion",
        "workflowFile",
    ],
)
def test_dynamic_policy_rejects_compatibility_aliases(obsolete_field: str) -> None:
    parsed = parse_policy_document(
        (Path(__file__).parents[4] / "config.example.yaml").read_bytes(),
        "yaml-1.2",
    )
    assert isinstance(parsed, ParsedDocument)
    assert isinstance(parsed.value, dict)
    repository = parsed.value["repository"]
    assert isinstance(repository, dict)
    dynamic = repository["dynamicCi"]
    assert isinstance(dynamic, dict)
    dynamic[obsolete_field] = None

    assert_failure(
        normalization_failure(parsed.value),
        "structure.invalid",
        pointer="/repository/dynamicCi",
        parameters={
            "schemaKeyword": "additionalProperties",
            "schemaPointer": "/$defs/dynamicCi/additionalProperties",
        },
    )


def test_dynamic_obligation_requires_at_least_one_witness() -> None:
    parsed = parse_policy_document(
        (Path(__file__).parents[4] / "config.example.yaml").read_bytes(),
        "yaml-1.2",
    )
    assert isinstance(parsed, ParsedDocument)
    assert isinstance(parsed.value, dict)
    repository = parsed.value["repository"]
    assert isinstance(repository, dict)
    dynamic = repository["dynamicCi"]
    assert isinstance(dynamic, dict)
    obligations = dynamic["obligations"]
    assert isinstance(obligations, list)
    obligation = obligations[0]
    assert isinstance(obligation, dict)
    obligation["requiredWitnessIds"] = []

    assert_failure(
        normalization_failure(parsed.value),
        "structure.invalid",
        pointer="/repository/dynamicCi/obligations/0/requiredWitnessIds",
        parameters={
            "schemaKeyword": "minItems",
            "schemaPointer": ("/$defs/validationObligation/properties/requiredWitnessIds/minItems"),
        },
    )


EXPECTED_COMPILED_POLICY_BYTES = (
    b'{"defaultBranch":"main","dynamicCi":null,"name":"ci-coordinator",'
    b'"owner":"example-org","rules":[{"branches":["main"],"event":"push",'
    b'"expectedSignals":[{"kind":"workflow","name":"CI","required":true,'
    b'"requiredConclusion":"success","source":"native","workflowFile":"ci.yml"}],'
    b'"mode":"observe","name":"main","omittedSignals":[],"timing":{'
    b'"absencePollLookbackSeconds":3600,"absenceVerificationWindowSeconds":300,'
    b'"expectedSignalTimeoutSeconds":3600,"lateFindingWindowSeconds":86400,'
    b'"mutableDecisionWindowSeconds":300}}],"schemaVersion":'
    b'"ci-compiled-repository-policy/v1","scope":{"installationId":1,"repositoryId":2}}'
)


class BytesSubclass(bytes):
    pass


class StringSubclass(str):
    pass


def test_policy_diagnostic_recursively_removes_mutable_aliases() -> None:
    nested = {"nested": "value"}
    source = {"z": [1, nested], "a": True}
    diagnostic = PolicyDiagnostic(
        code="semantics.invalid",
        phase="semantics",
        rule_id="rule.unique-name",
        instance_pointer="/repository/rules/1/name",
        parameters=source,
    )
    source["z"] = []
    nested["nested"] = "mutated"

    assert tuple(diagnostic.parameters) == ("a", "z")
    frozen_array = diagnostic.parameters["z"]
    assert isinstance(frozen_array, tuple)
    assert frozen_array[0] == 1
    assert isinstance(frozen_array[1], Mapping)
    assert dict(frozen_array[1]) == {"nested": "value"}
    with pytest.raises(TypeError):
        diagnostic.parameters["a"] = False  # type: ignore[index]
    with pytest.raises(TypeError):
        frozen_array[1]["nested"] = "changed"  # type: ignore[index]


def test_policy_diagnostic_is_immutable() -> None:
    diagnostic = PolicyDiagnostic(
        code="semantics.invalid",
        phase="semantics",
        rule_id="rule.unique-name",
        instance_pointer="",
        parameters={},
    )

    with pytest.raises(FrozenInstanceError):
        # noinspection PyDataclass
        diagnostic.code = "mutated"  # type: ignore[misc]


@pytest.mark.parametrize("pointer", ["repository", "/bad~", "/bad~2escape", "\ud800"])
def test_policy_diagnostic_rejects_invalid_json_pointers(pointer: str) -> None:
    with pytest.raises(ValueError):
        PolicyDiagnostic(
            code="structure.invalid",
            phase="structure",
            rule_id="schema:type",
            instance_pointer=pointer,
            parameters={},
        )


@pytest.mark.parametrize(
    ("installation_id", "repository_id"),
    [(0, 1), (1, 0), (-1, 1), (True, 1), (1, MAX_SAFE_JSON_INTEGER + 1)],
)
def test_repository_scope_rejects_values_outside_its_identity_domain(
    installation_id: int,
    repository_id: int,
) -> None:
    with pytest.raises(ValueError):
        RepositoryScope(installation_id=installation_id, repository_id=repository_id)


def test_repository_scope_is_immutable() -> None:
    scope = RepositoryScope(installation_id=1, repository_id=2)

    with pytest.raises(FrozenInstanceError):
        # noinspection PyDataclass
        scope.repository_id = 3  # type: ignore[misc]


def test_epoch_draft_validates_local_contract_invariants() -> None:
    draft = valid_epoch_draft()

    assert draft.scope == RepositoryScope(installation_id=1, repository_id=2)
    with pytest.raises(FrozenInstanceError):
        # noinspection PyDataclass
        draft.epoch_id = "b" * 64  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field_name", "invalid_value"),
    [
        ("source_format", "xml"),
        ("source_bytes", bytearray(b"{}")),
        ("scope", object()),
        ("normalized_document_bytes", b""),
        ("compiled_policy_bytes", b""),
        ("document_profile_id", ""),
        ("producer_feasibility_profile_id", "\ud800"),
        ("source_hash", "A" * 64),
        ("epoch_id", "a" * 63),
    ],
)
def test_epoch_draft_rejects_invalid_local_contract_fields(
    field_name: str,
    invalid_value: object,
) -> None:
    arguments = valid_epoch_draft_arguments()
    arguments[field_name] = invalid_value

    with pytest.raises(ValueError):
        ValidatedEpochDraft(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field_name", "size"),
    [
        ("source_bytes", 2_097_153),
        ("normalized_document_bytes", 4_194_305),
        ("compiled_policy_bytes", 4_194_305),
    ],
)
def test_epoch_draft_rejects_byte_values_above_limits(
    field_name: str,
    size: int,
) -> None:
    arguments = valid_epoch_draft_arguments()
    arguments[field_name] = b"x" * size

    with pytest.raises(ValueError):
        ValidatedEpochDraft(**arguments)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field_name",
    (
        "document_schema_id",
        "document_profile_id",
        "semantic_profile_id",
        "compiled_schema_id",
        "producer_resource_profile_id",
        "producer_byte_profile_id",
        "producer_feasibility_profile_id",
    ),
)
def test_epoch_draft_rejects_oversized_contract_identity(field_name: str) -> None:
    arguments = valid_epoch_draft_arguments()
    arguments[field_name] = "\u00e9" * (MAX_CONFIG_CONTRACT_ID_UTF8_BYTES // 2 + 1)

    with pytest.raises(ValueError, match="UTF-8 byte limit"):
        ValidatedEpochDraft(**arguments)  # type: ignore[arg-type]


def test_package_facade_exposes_only_materialized_machine_owned_contracts() -> None:
    assert config_control.__all__ == [
        "PolicySourceFormat",
        "PolicyPhase",
        "PolicyDiagnostic",
        "RepositoryScope",
        "ValidatedEpochDraft",
        "PolicyAdmissionResult",
        "admit_policy_document",
    ]
    assert not hasattr(config_control, "freeze_json")
    assert not hasattr(config_control, "FrozenJsonObject")


def test_public_admission_constructs_the_exact_validated_epoch_contract() -> None:
    source = valid_policy_source()

    expected = ValidatedEpochDraft(
        source_format="json",
        source_bytes=source,
        scope=RepositoryScope(installation_id=1, repository_id=2),
        normalized_document_bytes=EXPECTED_NORMALIZED_POLICY_BYTES,
        compiled_policy_bytes=EXPECTED_COMPILED_POLICY_BYTES,
        document_schema_id=(
            "https://github.com/research-engineering/ci-coordinator/"
            "schemas/repository-policy.schema.v1.json"
        ),
        document_profile_id="ci-config-document/v1",
        semantic_profile_id="ci-repository-policy-semantics/v1",
        compiled_schema_id=(
            "https://github.com/research-engineering/ci-coordinator/"
            "schemas/compiled-repository-policy.schema.v1.json"
        ),
        producer_resource_profile_id="ci-audit-event-json-resources/v1",
        producer_byte_profile_id="ci-audit-event-persistence-bytes/v1",
        producer_feasibility_profile_id="ci-config-dynamic-ci-audit-feasibility/v1",
        source_hash="d2ad8c8add75dce3d9c2b7e3ab8b0714dc1b7ad58234b0a2da29d544b75723ef",
        document_hash="7bbdfe611c584c47940519e07ad6a4399ed23a02118416282d0cb0611df0f993",
        epoch_hash="d70e91966b027feb9b7038d40d7dd943af5273ee7909ca3b601e267256b40a89",
        epoch_id="ff0399715b0371885da8f4f3eb2ab3701c414c78a03df196b2ab09dfefd3593a",
    )

    assert admit_policy_document(source, "json") == expected
    assert admit_policy_document(source, "json") == expected


@pytest.mark.parametrize(
    "number_token",
    ["1.0", "1e0", "0.99999999999999999", "1.0000000000000001"],
)
@pytest.mark.parametrize("source_format", ["json", "yaml-1.2"])
def test_integral_json_numbers_are_normalized_before_compilation(
    number_token: str,
    source_format: str,
) -> None:
    integer_source = valid_policy_source()
    integral_float_source = integer_source.replace(
        b'"installationId":1',
        f'"installationId":{number_token}'.encode(),
    )

    expected = admit_policy_document(integer_source, source_format)
    result = admit_policy_document(integral_float_source, source_format)

    assert isinstance(expected, ValidatedEpochDraft)
    assert isinstance(result, ValidatedEpochDraft)
    assert result.scope == expected.scope
    assert result.normalized_document_bytes == expected.normalized_document_bytes
    assert result.compiled_policy_bytes == expected.compiled_policy_bytes
    assert result.document_hash == expected.document_hash
    assert result.epoch_hash == expected.epoch_hash


@pytest.mark.parametrize("source_format", ["json", "yaml-1.2"])
def test_binary64_projection_applies_before_the_safe_number_boundary(
    source_format: str,
) -> None:
    document = valid_policy_document()
    repository = document["repository"]
    assert isinstance(repository, dict)
    repository["installationId"] = MAX_SAFE_JSON_INTEGER
    integer_source = json.dumps(document, separators=(",", ":")).encode()
    rounded_source = integer_source.replace(
        str(MAX_SAFE_JSON_INTEGER).encode(),
        b"9007199254740991.1",
        1,
    )

    result = admit_policy_document(rounded_source, source_format)

    assert isinstance(result, ValidatedEpochDraft)
    assert result.scope.installation_id == MAX_SAFE_JSON_INTEGER


def test_public_admission_preserves_equivalent_json_yaml_content_identity() -> None:
    source = valid_policy_source()

    json_result = admit_policy_document(source, "json")
    yaml_result = admit_policy_document(source, "yaml-1.2")

    assert isinstance(json_result, ValidatedEpochDraft)
    assert isinstance(yaml_result, ValidatedEpochDraft)
    assert yaml_result.source_format == "yaml-1.2"
    assert yaml_result.source_hash == (
        "299319864453b789475f68d49ff9e646d8ebc232a25ea9996148eb9300d6f620"
    )
    assert yaml_result.document_hash == json_result.document_hash
    assert yaml_result.epoch_hash == json_result.epoch_hash
    assert yaml_result.epoch_id == (
        "fd0a859b0aa4d7bda440fbc4a73e2d0dec91af3a1abfbd9b1ca44db51c519ff1"
    )


def test_public_admission_preserves_source_failure_precedence() -> None:
    assert admit_policy_document(b"{}", "toml") == (
        PolicyDiagnostic(
            code="source.unsupported_format",
            phase="source",
            rule_id="source.accepted-format",
            instance_pointer="",
            parameters={"accepted": ["json", "yaml-1.2"]},
        ),
    )
    assert admit_policy_document(b"x" * 2_097_153, "toml") == (
        PolicyDiagnostic(
            code="source.too_large",
            phase="source",
            rule_id="source.max-utf8-bytes",
            instance_pointer="",
            parameters={"limit": 2_097_152, "observed": 2_097_153},
        ),
    )


@pytest.mark.parametrize(
    ("failing_stage", "failure"),
    [
        (
            "parse_policy_document",
            PolicyDiagnostic(
                code="parse.invalid_syntax",
                phase="parse",
                rule_id="parse.syntax",
                instance_pointer="",
                parameters={"format": "json", "line": 1, "column": 1},
            ),
        ),
        (
            "normalize_policy_document",
            PolicyDiagnostic(
                code="structure.invalid",
                phase="structure",
                rule_id="schema:required",
                instance_pointer="",
                parameters={"schemaKeyword": "required", "schemaPointer": "/required"},
            ),
        ),
        (
            "validate_policy_semantics",
            PolicyDiagnostic(
                code="semantics.invalid",
                phase="semantics",
                rule_id="rule.default-branch-covered",
                instance_pointer="/repository/rules/0/on/branches",
                parameters={},
            ),
        ),
        (
            "validate_producer_feasibility",
            PolicyDiagnostic(
                code="feasibility.audit_projection_exceeded",
                phase="feasibility",
                rule_id="feasibility.audit-projection",
                instance_pointer="/repository/dynamicCi",
                parameters={
                    "profileId": "ci-config-dynamic-ci-audit-feasibility/v1",
                    "limit": 1_048_576,
                    "observed": 1_048_577,
                    "projectionId": "dynamic-ci-config-audit-floor/v1",
                },
            ),
        ),
        (
            "compile_policy",
            PolicyDiagnostic(
                code="compile.output_too_large",
                phase="compile",
                rule_id="compile.output-bytes",
                instance_pointer="",
                parameters={
                    "projection": "compiled-policy",
                    "limit": 4_194_304,
                    "observed": 4_194_305,
                },
            ),
        ),
    ],
)
def test_public_admission_short_circuits_after_the_first_failing_phase(
    monkeypatch: pytest.MonkeyPatch,
    failing_stage: str,
    failure: PolicyDiagnostic,
) -> None:
    normalized = normalize_policy_document(valid_policy_document())
    assert isinstance(normalized, dict)
    stage_results: dict[str, object] = {
        "parse_policy_document": ParsedDocument(
            value=valid_policy_document(),
            source_format="json",
        ),
        "normalize_policy_document": normalized,
        "validate_policy_semantics": None,
        "validate_producer_feasibility": None,
    }
    stage_order = (*stage_results, "compile_policy")
    failing_index = stage_order.index(failing_stage)
    calls: list[str] = []

    for index, stage in enumerate(stage_order):
        result = failure if stage == failing_stage else stage_results.get(stage)

        def stage_call(
            *args: object,
            _index: int = index,
            _result: object = result,
            _stage: str = stage,
            **kwargs: object,
        ) -> object:
            del args, kwargs
            if _index > failing_index:
                raise AssertionError(f"later phase {_stage} executed after failure")
            calls.append(_stage)
            return _result

        monkeypatch.setattr(admission_module, stage, stage_call)

    assert admission_module.admit_policy_document(b"source", "json") == (failure,)
    assert calls == list(stage_order[: failing_index + 1])


def test_compile_failure_cannot_construct_a_validated_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = PolicyDiagnostic(
        code="compile.output_invalid",
        phase="compile",
        rule_id="schema:required",
        instance_pointer="",
        parameters={"schemaKeyword": "required", "schemaPointer": "/required"},
    )
    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(admission_module, "compile_policy", lambda *_args, **_kwargs: failure)
    monkeypatch.setattr(
        admission_module,
        "_validated_epoch",
        lambda *_: pytest.fail("epoch constructed after compile failure"),
    )

    assert admission_module.admit_policy_document(valid_policy_source(), "json") == (failure,)


@pytest.mark.parametrize(
    "invalid_source",
    [bytearray(b"{}"), memoryview(b"{}"), "{}", BytesSubclass(b"{}")],
)
def test_public_admission_preserves_exact_source_host_contract(invalid_source: object) -> None:
    with pytest.raises(TypeError, match="exact bytes"):
        admit_policy_document(invalid_source, "json")  # type: ignore[arg-type]


@pytest.mark.parametrize("invalid_format", [1, StringSubclass("json")])
def test_public_admission_preserves_exact_format_host_contract(invalid_format: object) -> None:
    with pytest.raises(TypeError, match="exact string"):
        admit_policy_document(b"{}", invalid_format)  # type: ignore[arg-type]


def test_public_admission_does_not_reclassify_internal_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_internally(raw_source: bytes, source_format: str) -> object:
        del raw_source, source_format
        raise RuntimeError("internal sentinel")

    # noinspection PyUnresolvedReferences
    monkeypatch.setattr(admission_module, "parse_policy_document", fail_internally)

    with pytest.raises(RuntimeError, match="internal sentinel"):
        admission_module.admit_policy_document(b"{}", "json")

from __future__ import annotations

import pytest

import ci_coordinator.config_control._json_document as json_document_module
import ci_coordinator.config_control._yaml_document as yaml_document_module
from ci_coordinator.config_control import (
    PolicyDiagnostic,
)
from ci_coordinator.config_control._document import parse_policy_document
from ci_coordinator.config_control._parser_support import (
    ParsedDocument,
    diagnostic,
    select_parse_failure,
)
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER

from ._document_admission_support import (
    assert_failure,
    normalization_failure,
    parsed_value,
    policy_failure,
    valid_policy_document,
)


@pytest.mark.parametrize("invalid_source", [bytearray(b"{}"), memoryview(b"{}"), "{}"])
def test_source_parser_rejects_non_exact_bytes_as_programming_errors(
    invalid_source: object,
) -> None:
    with pytest.raises(TypeError, match="exact bytes"):
        parse_policy_document(invalid_source, "json")  # type: ignore[arg-type]


def test_source_parser_rejects_non_string_format_as_programming_error() -> None:
    with pytest.raises(TypeError, match="exact string"):
        parse_policy_document(b"{}", 1)  # type: ignore[arg-type]


def test_source_phase_precedes_format_admission_and_accepts_exact_limit() -> None:
    admitted = parse_policy_document(b'"' + b"a" * (2_097_152 - 2) + b'"', "json")
    assert parsed_value(admitted) == "a" * (2_097_152 - 2)

    failure = policy_failure(b"x" * 2_097_153, "toml")
    assert_failure(
        failure,
        "source.too_large",
        parameters={"limit": 2_097_152, "observed": 2_097_153},
    )


def test_unknown_string_format_is_a_typed_policy_failure() -> None:
    assert_failure(
        policy_failure(b"{}", "toml"),
        "source.unsupported_format",
        parameters={"accepted": ("json", "yaml-1.2")},
    )


def test_decode_precedence_and_diagnostics_do_not_disclose_source() -> None:
    assert_failure(
        policy_failure(b"\xff", "json"),
        "decode.invalid_utf8",
        parameters={"offset": 0},
    )
    assert_failure(
        policy_failure(b"\xef\xbb\xbf\xff", "json"),
        "decode.invalid_utf8",
        parameters={"offset": 3},
    )
    assert_failure(
        policy_failure(b"\xef\xbb\xbf{}", "json"),
        "decode.byte_order_mark_forbidden",
    )


@pytest.mark.parametrize(
    ("source", "code", "pointer"),
    [
        (b'{"repository":1,"repository":2}', "parse.duplicate_key", ""),
        (b"NaN", "parse.non_finite_number", ""),
        (b"Infinity", "parse.non_finite_number", ""),
        (b"9007199254740992", "parse.unsafe_integer", ""),
        (b"9.007199254740992e15", "parse.unsafe_integer", ""),
        (b'"\\ud800"', "parse.unpaired_surrogate", ""),
        (b'"\\udc00"', "parse.unpaired_surrogate", ""),
        (b'{"private-value": ["\\ud800"]}', "parse.unpaired_surrogate", "/0"),
        (
            b'{"repository":{"schemaVersion":["\\ud800"]}}',
            "parse.unpaired_surrogate",
            "/repository/0",
        ),
        (
            b'{"repository":{"dynamicCi":{"obligations":[{"requiredWitnessIds":["\\ud800"]}]}}}',
            "parse.unpaired_surrogate",
            "/repository/dynamicCi/obligations/0/requiredWitnessIds/0",
        ),
    ],
)
def test_json_parser_projects_stable_failures_without_unknown_keys(
    source: bytes,
    code: str,
    pointer: str,
) -> None:
    failure = policy_failure(source, "json")
    assert_failure(failure, code, pointer=pointer)
    assert source.decode("ascii", errors="ignore") not in repr(failure.parameters)


def test_json_syntax_location_and_surrogate_pair_projection_are_exact() -> None:
    assert_failure(
        policy_failure(b'{\n  "repository": ]}', "json"),
        "parse.invalid_syntax",
        parameters={"format": "json", "line": 2, "column": 17},
    )
    assert parsed_value(parse_policy_document(b'"\\ud83d\\ude00"', "json")) == "\U0001f600"


@pytest.mark.parametrize(
    ("source", "line", "column"),
    [
        ('["\U0001f600" x]'.encode(), 1, 6),
        (b"[\r x]", 2, 2),
        (b"[\r\nx]", 2, 1),
    ],
)
def test_json_syntax_locations_use_profile_owned_scalar_units(
    source: bytes,
    line: int,
    column: int,
) -> None:
    assert_failure(
        policy_failure(source, "json"),
        "parse.invalid_syntax",
        parameters={"format": "json", "line": line, "column": column},
    )


@pytest.mark.parametrize(
    ("source", "code"),
    [
        (b"---\n{}\n---\n{}\n", "parse.multiple_documents_forbidden"),
        (b"x: 1\nx: 2\n", "parse.duplicate_key"),
        (b"x: &a 1\n", "parse.graph_feature_forbidden"),
        (b"x: *a\n", "parse.graph_feature_forbidden"),
        (b"x: {<<: {a: 1}}\n", "parse.graph_feature_forbidden"),
        (b"2020-01-01\n", "parse.custom_tag_forbidden"),
        (b"!!binary SGVsbG8=\n", "parse.custom_tag_forbidden"),
        (b"1: value\n", "parse.non_string_key"),
        (b".inf\n", "parse.non_finite_number"),
        (b"9007199254740992\n", "parse.unsafe_integer"),
        (b"9.007199254740992e15\n", "parse.unsafe_integer"),
    ],
)
def test_yaml_event_preflight_rejects_forbidden_language_features(
    source: bytes,
    code: str,
) -> None:
    assert_failure(policy_failure(source, "yaml-1.2"), code)


def test_yaml_version_empty_stream_and_surrogate_projection_are_exact() -> None:
    assert_failure(
        policy_failure(b"%YAML 1.1\n---\nyes\n", "yaml-1.2"),
        "parse.invalid_syntax",
        parameters={"format": "yaml-1.2", "line": 1, "column": 1},
    )
    assert parsed_value(parse_policy_document(b"", "yaml-1.2")) is None
    assert parsed_value(parse_policy_document(b'"\\uD83D\\uDE00"\n', "yaml-1.2")) == "\U0001f600"
    assert_failure(
        policy_failure(b'"\\uD800"\n', "yaml-1.2"),
        "parse.unpaired_surrogate",
    )
    assert_failure(
        policy_failure(b'"\\uDC00"\n', "yaml-1.2"),
        "parse.unpaired_surrogate",
    )
    assert_failure(
        policy_failure(b"\x0c", "yaml-1.2"),
        "parse.invalid_syntax",
        parameters={"format": "yaml-1.2", "line": 1, "column": 1},
    )


@pytest.mark.parametrize(
    ("source", "line", "column"),
    [
        ('["\U0001f600" x]'.encode(), 1, 6),
        (b"a:\r  b: ]", 2, 6),
        (b"a:\r\n  b: ]", 2, 6),
    ],
)
def test_yaml_syntax_locations_use_profile_owned_scalar_units(
    source: bytes,
    line: int,
    column: int,
) -> None:
    assert_failure(
        policy_failure(source, "yaml-1.2"),
        "parse.invalid_syntax",
        parameters={"format": "yaml-1.2", "line": line, "column": column},
    )


def test_yaml_duplicate_detection_uses_folded_unicode_scalars() -> None:
    source = f'"\\uD83D\\uDE00": 1\n"{chr(0x1F600)}": 2\n'.encode()
    assert_failure(policy_failure(source, "yaml-1.2"), "parse.duplicate_key")


@pytest.mark.parametrize(
    "source",
    [
        b"!!bool nope\n",
        b"!!null nope\n",
        b"!!float nope\n",
        b"!!int +\n",
        b"!!int _\n",
        b"!!int 0b\n",
        b"!!int 0o\n",
        b"!!int 0x\n",
    ],
)
def test_yaml_malformed_explicit_core_scalars_are_typed_syntax_failures(
    source: bytes,
) -> None:
    assert_failure(
        policy_failure(source, "yaml-1.2"),
        "parse.invalid_syntax",
        parameters={"format": "yaml-1.2", "line": 1, "column": 1},
    )


@pytest.mark.parametrize(
    "source",
    [
        b"!!map value\n",
        b"!!seq value\n",
        b"!!bool {}\n",
        b"!!float {}\n",
        b"!!int {}\n",
        b"!!null {}\n",
        b"!!seq {}\n",
        b"!!str {}\n",
        b"!!bool []\n",
        b"!!float []\n",
        b"!!int []\n",
        b"!!map []\n",
        b"!!null []\n",
        b"!!str []\n",
    ],
)
def test_yaml_core_tags_must_match_the_event_node_kind(source: bytes) -> None:
    assert_failure(
        policy_failure(source, "yaml-1.2"),
        "parse.invalid_syntax",
        parameters={"format": "yaml-1.2", "line": 1, "column": 1},
    )


@pytest.mark.parametrize("source", ["!!map value", "!!int {}", "!!int []"])
def test_yaml_tag_kind_failure_precedes_depth_overflow_at_same_event(source: str) -> None:
    nested = ("[" * 65 + source + "]" * 65).encode()
    assert policy_failure(nested, "yaml-1.2").code == "parse.invalid_syntax"


@pytest.mark.parametrize("source", ["!!map value", "!!int {}", "!!int []"])
def test_yaml_tag_kind_failure_precedes_node_overflow_at_same_event(source: str) -> None:
    sequence = ("- 0\n" * 32_767 + f"- {source}\n").encode()
    assert policy_failure(sequence, "yaml-1.2").code == "parse.invalid_syntax"


@pytest.mark.parametrize("source", [b'"<<": 1\n', b"!!str <<: 1\n"])
def test_yaml_string_merge_spelling_remains_a_normal_key(source: bytes) -> None:
    assert parsed_value(parse_policy_document(source, "yaml-1.2")) == {"<<": 1}


def test_yaml_integer_admission_precedes_host_integer_construction() -> None:
    assert_failure(
        policy_failure(("9" * 5_000).encode(), "yaml-1.2"),
        "parse.unsafe_integer",
    )
    assert parsed_value(parse_policy_document(("0" * 5_000).encode(), "yaml-1.2")) == 0
    assert_failure(
        policy_failure(b"!!int not-an-integer\n", "yaml-1.2"),
        "parse.invalid_syntax",
        parameters={"format": "yaml-1.2", "line": 1, "column": 1},
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (b"0x1fffffffffffff", MAX_SAFE_JSON_INTEGER),
        (b"0b11111111111111111111111111111111111111111111111111111", MAX_SAFE_JSON_INTEGER),
        (b"0o377777777777777777", MAX_SAFE_JSON_INTEGER),
    ],
)
def test_yaml_safe_integer_radices_share_the_kernel_boundary(
    source: bytes,
    expected: int,
) -> None:
    assert parsed_value(parse_policy_document(source, "yaml-1.2")) == expected


@pytest.mark.parametrize(
    "source",
    [
        b"0x20000000000000",
        b"0b100000000000000000000000000000000000000000000000000000",
        b"0o400000000000000000",
    ],
)
def test_yaml_integer_radix_overflow_uses_the_shared_diagnostic(source: bytes) -> None:
    assert_failure(policy_failure(source, "yaml-1.2"), "parse.unsafe_integer")


@pytest.mark.parametrize(
    "source_format",
    ["json", "yaml-1.2"],
)
def test_scalar_failure_precedes_resource_failure_at_the_same_offset(
    source_format: str,
) -> None:
    unsafe = ("[" * 65 + "9007199254740992" + "]" * 65).encode()
    assert_failure(policy_failure(unsafe, source_format), "parse.unsafe_integer", pointer=None)

    if source_format == "json":
        invalid = ("[" * 65 + "x" + "]" * 65).encode()
    else:
        invalid = ("[" * 65 + "!!bool nope" + "]" * 65).encode()
    failure = policy_failure(invalid, source_format)
    assert failure.code == "parse.invalid_syntax"


@pytest.mark.parametrize("token", ['"\\q"', "1e"])
def test_earlier_resource_offset_precedes_later_json_token_syntax(token: str) -> None:
    source = ("[" * 65 + token + "]" * 65).encode()
    assert_failure(
        policy_failure(source, "json"),
        "resource.max_depth_exceeded",
        pointer=None,
        parameters={"limit": 64, "observed": 65},
    )


@pytest.mark.parametrize("source_format", ["json", "yaml-1.2"])
def test_scalar_failure_precedes_node_overflow_at_the_same_offset(
    source_format: str,
) -> None:
    if source_format == "json":
        source = ("[" + "0," * 32_767 + "9007199254740992]").encode()
    else:
        source = ("- 0\n" * 32_767 + "- 9007199254740992\n").encode()
    assert_failure(policy_failure(source, source_format), "parse.unsafe_integer", pointer=None)


@pytest.mark.parametrize("source_format", ["json", "yaml-1.2"])
def test_depth_boundary_is_shared_by_both_formats(source_format: str) -> None:
    admitted_source = ("[" * 64 + "0" + "]" * 64).encode()
    rejected_source = ("[" * 65 + "0" + "]" * 65).encode()

    assert isinstance(parse_policy_document(admitted_source, source_format), ParsedDocument)
    assert_failure(
        policy_failure(rejected_source, source_format),
        "resource.max_depth_exceeded",
        pointer=None,
        parameters={"limit": 64, "observed": 65},
    )


@pytest.mark.parametrize("source_format", ["json", "yaml-1.2"])
def test_node_boundary_is_shared_by_both_formats(source_format: str) -> None:
    if source_format == "json":
        admitted_source = ("[" + ",".join(["0"] * 32_767) + "]").encode()
        rejected_source = ("[" + ",".join(["0"] * 32_768) + "]").encode()
    else:
        admitted_source = ("- 0\n" * 32_767).encode()
        rejected_source = ("- 0\n" * 32_768).encode()

    assert isinstance(parse_policy_document(admitted_source, source_format), ParsedDocument)
    assert_failure(
        policy_failure(rejected_source, source_format),
        "resource.max_nodes_exceeded",
        pointer=None,
        parameters={"limit": 32_768, "observed": 32_769},
    )


@pytest.mark.parametrize("source_format", ["json", "yaml-1.2"])
@pytest.mark.parametrize("boundary", ["depth", "nodes"])
def test_resource_preflight_rejects_before_tree_construction(
    monkeypatch: pytest.MonkeyPatch,
    source_format: str,
    boundary: str,
) -> None:
    def forbidden_construction(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise AssertionError("resource overflow reached tree construction")

    if source_format == "json":
        monkeypatch.setattr(
            json_document_module,
            "_construct_json_document",
            forbidden_construction,
        )
    else:
        monkeypatch.setattr(
            yaml_document_module,
            "_construct_yaml_document",
            forbidden_construction,
        )

    if boundary == "depth":
        rejected_source = ("[" * 65 + "0" + "]" * 65).encode()
        expected_code = "resource.max_depth_exceeded"
        expected_parameters = {"limit": 64, "observed": 65}
    elif source_format == "json":
        rejected_source = ("[" + ",".join(["0"] * 32_768) + "]").encode()
        expected_code = "resource.max_nodes_exceeded"
        expected_parameters = {"limit": 32_768, "observed": 32_769}
    else:
        rejected_source = ("- 0\n" * 32_768).encode()
        expected_code = "resource.max_nodes_exceeded"
        expected_parameters = {"limit": 32_768, "observed": 32_769}
    assert_failure(
        policy_failure(rejected_source, source_format),
        expected_code,
        pointer=None,
        parameters=expected_parameters,
    )


def test_parse_failure_selection_uses_profile_code_order_not_argument_order() -> None:
    selected = select_parse_failure(
        diagnostic("parse.custom_tag_forbidden"),
        diagnostic("parse.graph_feature_forbidden"),
    )

    assert isinstance(selected, PolicyDiagnostic)
    assert selected.code == "parse.graph_feature_forbidden"


def test_yaml_failure_precedence_is_preserved_through_public_parser_wiring() -> None:
    assert_failure(
        policy_failure(b"x: &a !custom value\n", "yaml-1.2"),
        "parse.graph_feature_forbidden",
    )
    later_syntax = ("[" * 65 + "0" + "]" * 65 + " ]").encode()
    assert_failure(
        policy_failure(later_syntax, "yaml-1.2"),
        "resource.max_depth_exceeded",
        pointer=None,
        parameters={"limit": 64, "observed": 65},
    )


def test_json_and_yaml_construct_the_same_json_algebra() -> None:
    json_result = parse_policy_document(
        b'{"repository":{"rules":[true,null,1,1.5,"value"]}}',
        "json",
    )
    yaml_result = parse_policy_document(
        b"repository:\n  rules: [true, null, 1, 1.5, value]\n",
        "yaml-1.2",
    )
    assert parsed_value(json_result) == parsed_value(yaml_result)


def test_structural_failure_algorithm_uses_profile_keyword_order() -> None:
    type_failure = normalization_failure([])
    assert_failure(
        type_failure,
        "structure.invalid",
        parameters={"schemaKeyword": "type", "schemaPointer": "/type"},
    )

    required_failure = normalization_failure({})
    assert_failure(
        required_failure,
        "structure.invalid",
        parameters={"schemaKeyword": "required", "schemaPointer": "/required"},
    )

    policy = valid_policy_document()
    policy["unknownSecret"] = "must-not-leak"
    additional_failure = normalization_failure(policy)
    assert_failure(
        additional_failure,
        "structure.invalid",
        parameters={
            "schemaKeyword": "additionalProperties",
            "schemaPointer": "/additionalProperties",
        },
    )
    assert "unknownSecret" not in repr(additional_failure)
    assert "must-not-leak" not in repr(additional_failure)

    simultaneous_failure = normalization_failure({"unknown": None})
    assert_failure(
        simultaneous_failure,
        "structure.invalid",
        parameters={"schemaKeyword": "required", "schemaPointer": "/required"},
    )

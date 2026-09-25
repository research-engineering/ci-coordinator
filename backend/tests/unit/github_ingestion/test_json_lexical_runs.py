from __future__ import annotations

import cProfile
import json
from dataclasses import replace
from decimal import Decimal
from itertools import product
from types import CodeType

import pytest

from ci_coordinator.github_ingestion import strict_json
from ci_coordinator.github_ingestion.strict_json import JsonPayloadError, parse_json_object

from .test_strict_json import limits

_STRING_ATOMS = (
    "a",
    "\u00e9",
    "\U0001f600",
    r"\\",
    r"\"",
    r"\/",
    r"\n",
    r"\u0061",
    r"\ud83d\ude00",
)


@pytest.mark.parametrize("atoms", tuple(product(_STRING_ATOMS, repeat=2)))
def test_literal_and_escape_runs_preserve_decoded_values_and_exact_limit(
    atoms: tuple[str, str],
) -> None:
    token = '"' + "".join(atoms) + '"'
    expected = json.loads(token)
    assert len(expected) == 2
    body = ('{"a":' + token + "}").encode()
    admitted = parse_json_object(body, limits(maximum_string_code_points=2))
    assert admitted["a"] == expected
    with pytest.raises(JsonPayloadError, match=r"^webhook_payload_string_limit_exceeded$"):
        parse_json_object(body, limits(maximum_string_code_points=1))


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("0", 0),
        ("-1", -1),
        ("1.25", Decimal("1.25")),
        ("-0.0e+2", Decimal("-0.0e+2")),
        ("true", True),
        ("false", False),
        ("null", None),
    ],
)
@pytest.mark.parametrize("space", ("", " ", "\t\r\n", " \t\r\n" * 256))
def test_scalar_and_whitespace_runs_preserve_values(
    token: str, expected: object, space: str
) -> None:
    body = (space + '{"a":' + space + token + space + "}" + space).encode()
    admitted = parse_json_object(body, limits(maximum_body_bytes=len(body) + 32))
    assert admitted["a"] == expected
    assert type(admitted["a"]) is type(expected)


@pytest.mark.parametrize(
    "token",
    (
        "01",
        "+1",
        "1.",
        "1e",
        "1e+",
        ".1",
        "True",
        "NaN",
        "Infinity",
        "1\u00a0",
        "1\u001c",
        "1:2",
        r'"\q"',
        r'"\u00xz"',
        r'"\ud800"',
        r'"\udc00"',
        r'"\ud800\u0041"',
        '"raw\ncontrol"',
        '"unfinished',
    ),
)
def test_malformed_scalar_runs_still_fail_with_the_exact_code(token: str) -> None:
    with pytest.raises(JsonPayloadError, match=r"^invalid_json$"):
        parse_json_object(('{"a":' + token + "}").encode(), limits())


@pytest.mark.parametrize(
    ("suffix", "code"),
    (
        ('b"', "webhook_payload_string_limit_exceeded"),
        ('b\\q"', "webhook_payload_string_limit_exceeded"),
        (r'\n"', "webhook_payload_string_limit_exceeded"),
        (r'\ud83d\ude00"', "webhook_payload_string_limit_exceeded"),
        (r'\ud800"', "invalid_json"),
        (r'\q"', "invalid_json"),
        ('\n"', "invalid_json"),
    ),
)
def test_limit_precedence_is_preserved_before_host_construction(
    monkeypatch: pytest.MonkeyPatch, suffix: str, code: str
) -> None:
    def unexpected_decode(*_: object, **__: object) -> object:
        raise AssertionError("preflight rejection must precede host construction")

    monkeypatch.setattr(json, "loads", unexpected_decode)
    with pytest.raises(JsonPayloadError, match=f"^{code}$"):
        parse_json_object(('{"a":"a' + suffix + "}").encode(), limits(maximum_string_code_points=1))


@pytest.mark.parametrize("body", (b'{"ab":0}', b'{"a":"ab"}'))
def test_keys_and_values_share_the_same_preconstruction_string_bound(
    monkeypatch: pytest.MonkeyPatch, body: bytes
) -> None:
    def unexpected_decode(*_: object, **__: object) -> object:
        raise AssertionError("over-limit key/value must not construct a host object")

    monkeypatch.setattr(json, "loads", unexpected_decode)
    with pytest.raises(JsonPayloadError, match=r"^webhook_payload_string_limit_exceeded$"):
        parse_json_object(body, limits(maximum_string_code_points=1))


@pytest.mark.parametrize("token", ('"{}[],:"', r'"\"[]{}\\\n"'))
def test_string_punctuation_never_consumes_structure_or_node_budget(token: str) -> None:
    configured = limits(
        maximum_json_nodes=2,
        maximum_object_members=1,
        maximum_array_items=1,
    )
    result = parse_json_object(('{"a":' + token + "}").encode(), configured)
    assert result["a"] == json.loads(token)


@pytest.mark.parametrize("value", ("a", "\u00e9", "\U0001f600", "\n", "\\", '"'))
def test_large_plain_and_escaped_strings_preserve_the_same_exact_value(value: str) -> None:
    expected = value * 16_384
    body = json.dumps({"a": expected}, ensure_ascii=False).encode()
    configured = limits(
        maximum_body_bytes=len(body),
        maximum_string_code_points=len(expected),
    )
    assert parse_json_object(body, configured)["a"] == expected
    with pytest.raises(JsonPayloadError, match=r"^webhook_payload_string_limit_exceeded$"):
        parse_json_object(body, replace(configured, maximum_string_code_points=len(expected) - 1))


def test_large_declared_limits_do_not_overflow_native_match_indices() -> None:
    configured = limits(maximum_body_bytes=10**100, maximum_string_code_points=10**100)
    assert parse_json_object(b'{"a":"value"}', configured)["a"] == "value"


@pytest.mark.parametrize("suffix", (b'"', b'\\q"', b"\n"))
def test_long_plain_rejection_does_not_materialize_the_host_tree(
    monkeypatch: pytest.MonkeyPatch, suffix: bytes
) -> None:
    def unexpected_decode(*_: object, **__: object) -> object:
        raise AssertionError("oversized plain span must be rejected before decoding")

    monkeypatch.setattr(json, "loads", unexpected_decode)
    body = b'{"a":"' + b"x" * 1_048_576 + suffix + b"}"
    configured = limits(maximum_body_bytes=len(body), maximum_string_code_points=32)
    with pytest.raises(JsonPayloadError, match=r"^webhook_payload_string_limit_exceeded$"):
        parse_json_object(body, configured)


@pytest.mark.parametrize("value", ("\ud800", "\udfff", "prefix\ud800suffix"))
def test_freeze_defense_rejects_surrogates_even_if_a_decoder_is_substituted(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    def substituted_decode(*_: object, **__: object) -> object:
        return strict_json._ObjectPairs((("a", value),))

    monkeypatch.setattr(json, "loads", substituted_decode)
    with pytest.raises(JsonPayloadError, match=r"^invalid_json$"):
        parse_json_object(b'{"a":""}', limits())


@pytest.mark.parametrize(
    ("body", "overrides", "code"),
    (
        (b'{"a":[]}', {"maximum_nesting_depth": 1}, "webhook_payload_nesting_limit_exceeded"),
        (
            b'{"a":0}',
            {"maximum_json_nodes": 1, "maximum_object_members": 1, "maximum_array_items": 1},
            "webhook_payload_node_limit_exceeded",
        ),
        (
            b'{"a":0,"b":1}',
            {"maximum_object_members": 1},
            "webhook_payload_object_limit_exceeded",
        ),
        (b'{"a":[0,1]}', {"maximum_array_items": 1}, "webhook_payload_array_limit_exceeded"),
    ),
)
def test_every_structural_bound_precedes_host_construction(
    monkeypatch: pytest.MonkeyPatch, body: bytes, overrides: dict[str, int], code: str
) -> None:
    def unexpected_decode(*_: object, **__: object) -> object:
        raise AssertionError("structural overflow must be rejected before host construction")

    monkeypatch.setattr(json, "loads", unexpected_decode)
    with pytest.raises(JsonPayloadError, match=f"^{code}$"):
        parse_json_object(body, limits(**overrides))


@pytest.mark.parametrize("second_key", ("a", r"\u0061"))
def test_escape_spelling_cannot_hide_duplicate_object_keys(second_key: str) -> None:
    with pytest.raises(JsonPayloadError, match=r"^invalid_json$"):
        parse_json_object(('{"a":0,"' + second_key + '":1}').encode(), limits())


def test_plain_payloads_do_not_grow_python_call_count_with_character_count() -> None:
    calls: list[int] = []
    for size in (256, 262_144):
        body = b'{"a":"' + b"x" * size + b'"}'
        configured = limits(maximum_body_bytes=len(body), maximum_string_code_points=size)
        with cProfile.Profile() as profile:
            parsed = parse_json_object(body, configured)
        assert parsed["a"] == "x" * size
        calls.append(
            sum(
                entry.callcount
                for entry in profile.getstats()
                if isinstance(entry.code, CodeType)
                and entry.code.co_filename == strict_json.__file__
            )
        )
    assert 0 < calls[0] == calls[1]

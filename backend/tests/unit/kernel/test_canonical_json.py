from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from enum import IntEnum
from itertools import permutations
from pathlib import Path

import pytest

from ci_coordinator.kernel import (
    CanonicalJsonError,
    StrictJsonError,
    bounded_canonical_json,
    canonical_json,
    hash_object,
    is_safe_json_integer,
    load_strict_json,
    try_canonical_json,
    try_hash_object,
)

REPO_ROOT = Path(__file__).resolve().parents[4]
CONTRACT_VECTORS = (
    REPO_ROOT / "fixtures" / "conformance" / "v1" / "product-contract-vectors.v1.json"
)
MAX_SAFE_JSON_INTEGER = 9_007_199_254_740_991


class CustomInt(int):
    pass


class CustomFloat(float):
    pass


class CustomStr(str):
    pass


class CustomList(list[object]):
    pass


class CustomDict(dict[str, object]):
    pass


class Status(IntEnum):
    OK = 1


def test_matches_requirement_owned_canonical_identity_vector() -> None:
    case = json.loads(CONTRACT_VECTORS.read_text(encoding="utf8"))["cases"]["canonicalIdentity"]

    assert canonical_json(case["value"]).decode("utf8") == case["canonical"]
    assert hash_object(case["value"]) == case["hash"]


def test_object_key_order_is_semantic_not_insertion_order() -> None:
    left = {"z": [3, {"b": False, "a": True}], "a": None}
    right = {"a": None, "z": [3, {"a": True, "b": False}]}

    assert canonical_json(left) == canonical_json(right)
    assert hash_object(left) == hash_object(right)


def test_recursive_object_order_is_invariant_across_deterministic_permutations() -> None:
    expected = b'{"a":{"left":1,"right":[{"deep":true,"leaf":null}]},"m":"middle","z":0}'
    nested_pairs: list[tuple[str, object]] = [
        ("right", [{"leaf": None, "deep": True}]),
        ("left", 1),
    ]

    for top_order in permutations(["z", "a", "m"]):
        for nested_order in permutations(nested_pairs):
            value: dict[str, object] = {}
            for key in top_order:
                if key == "z":
                    value[key] = 0
                elif key == "m":
                    value[key] = "middle"
                else:
                    value[key] = dict(nested_order)

            assert canonical_json(value) == expected


def test_orders_keys_by_unicode_codepoint() -> None:
    value = {"\u00e4": 3, "\U0001f600": 4, "z": 2, "a": 1}

    assert canonical_json(value) == '{"a":1,"z":2,"\u00e4":3,"\U0001f600":4}'.encode("utf8")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ('line\n"quote"\\slash', b'"line\\n\\"quote\\"\\\\slash"'),
        ({"a": 1, "aa": 2, "b": 3}, b'{"a":1,"aa":2,"b":3}'),
        (
            {"a\u0301": "combining", "\u00e1": "composed"},
            '{"a\u0301":"combining","\u00e1":"composed"}'.encode("utf8"),
        ),
    ],
)
def test_string_escaping_and_multi_codepoint_keys_are_stable(
    value: object,
    expected: bytes,
) -> None:
    assert canonical_json(value) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "plain-ascii",
        '"\\\b\f\n\r\t\0',
        "\x7f",
        "".join(chr(code_point) for code_point in range(128)),
        "ascii\u0080\u07ff",
        "ascii\u0800\uffff",
        "ascii\U00010000\U0010ffff",
    ],
)
@pytest.mark.parametrize("as_key", [False, True])
def test_string_admission_preserves_exact_bytes_and_escaped_byte_limit(
    text: str, as_key: bool
) -> None:
    value: object = {text: [text]} if as_key else text
    expected = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf8")

    assert canonical_json(value) == expected
    assert bounded_canonical_json(value, max_bytes=len(expected)) == expected
    assert load_strict_json(expected, max_bytes=len(expected)) == value

    with pytest.raises(CanonicalJsonError) as failure:
        bounded_canonical_json(value, max_bytes=len(expected) - 1)

    assert failure.value.code == "canonical_json_max_bytes_exceeded"
    assert failure.value.path == "$"
    assert failure.value.instance_pointer == ""
    assert failure.value.limit == len(expected) - 1
    assert failure.value.observed == len(expected)


@pytest.mark.parametrize("text", ["a" * 64, "a" * 64 + "\u0080\u0800\U00010000"])
@pytest.mark.parametrize("bound_offset", [-1, 0])
@pytest.mark.parametrize("as_key", [False, True])
def test_raw_utf8_precheck_preserves_saturated_overflow(
    text: str, bound_offset: int, as_key: bool
) -> None:
    limit = len(text.encode("utf8")) + bound_offset
    value: object = {text: None} if as_key else [text]
    expected_path, expected_pointer = ("$", "")
    if bound_offset < 0:
        expected_path, expected_pointer = ("$", f"/{text}") if as_key else ("$[0]", "/0")

    with pytest.raises(CanonicalJsonError) as failure:
        bounded_canonical_json(value, max_bytes=limit)

    assert failure.value.code == "canonical_json_max_bytes_exceeded"
    assert failure.value.path == expected_path
    assert failure.value.instance_pointer == expected_pointer
    assert failure.value.limit == limit
    assert failure.value.observed == limit + 1


@pytest.mark.parametrize("surrogate", ["\ud800", "\udfff"])
@pytest.mark.parametrize("at_start", [False, True])
@pytest.mark.parametrize("as_key", [False, True])
@pytest.mark.parametrize("overflow_first", [False, True])
def test_ascii_prefix_or_suffix_cannot_mask_surrogate_rejection(
    surrogate: str, at_start: bool, as_key: bool, overflow_first: bool
) -> None:
    prefix = "a" * 128
    text = surrogate + prefix if at_start else prefix + surrogate
    value: object = {text: None} if as_key else text
    if overflow_first:
        value = ["a" * 64, value]

    with pytest.raises(CanonicalJsonError) as unbounded:
        canonical_json(value)
    with pytest.raises(CanonicalJsonError) as bounded:
        bounded_canonical_json(value, max_bytes=64 if overflow_first else 0)
    with pytest.raises(StrictJsonError):
        load_strict_json(json.dumps(value, ensure_ascii=True).encode("ascii"))

    for failure in (unbounded.value, bounded.value):
        assert failure.code == "invalid_unicode_scalar"
        assert failure.path == ("$[1]" if overflow_first else "$")
        assert failure.instance_pointer is None
        assert failure.limit is None
        assert failure.observed is None


def test_nested_semantic_change_changes_hash() -> None:
    baseline = {"checks": [{"id": "backend-tests", "depth": "targeted"}]}
    changed = {"checks": [{"id": "backend-tests", "depth": "full"}]}

    assert hash_object(baseline) != hash_object(changed)


def test_hash_object_hashes_canonical_bytes_without_digest_shortcuts() -> None:
    case = json.loads(CONTRACT_VECTORS.read_text(encoding="utf8"))["cases"]["canonicalIdentity"]

    assert hash_object(case["value"]) == case["hash"]
    assert hash_object(case["hash"]) != case["hash"]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1.0, b"1"),
        (-0.0, b"0"),
        (0.000001, b"0.000001"),
        (0.0000001, b"1e-7"),
        (-1.5, b"-1.5"),
        (-0.0000001, b"-1e-7"),
        (-5e-324, b"-5e-324"),
        (333333333.33333329, b"333333333.3333333"),
        (5e-324, b"5e-324"),
        (float(MAX_SAFE_JSON_INTEGER), b"9007199254740991"),
    ],
)
def test_number_format_uses_admitted_ecmascript_boundary_table(
    value: float,
    expected: bytes,
) -> None:
    assert canonical_json(value) == expected


@pytest.mark.parametrize("value", [MAX_SAFE_JSON_INTEGER, -MAX_SAFE_JSON_INTEGER])
def test_accepts_json_safe_integer_boundary(value: int) -> None:
    assert canonical_json(value) == str(value).encode("utf8")


@pytest.mark.parametrize(
    "value",
    [
        MAX_SAFE_JSON_INTEGER + 1,
        -(MAX_SAFE_JSON_INTEGER + 1),
        float(MAX_SAFE_JSON_INTEGER + 1),
        1e20,
        -1e20,
        1e21,
        -1e21,
        float.fromhex("0x1.fffffffffffffp+1023"),
    ],
)
def test_rejects_numbers_outside_cross_runtime_safe_magnitude(value: int | float) -> None:
    with pytest.raises(CanonicalJsonError) as error:
        canonical_json({"n": value})

    assert error.value.code == "unsafe_integer"
    assert error.value.path == "$.n"


@pytest.mark.parametrize(
    "value",
    [
        -0.0,
        0.0,
        1.0,
        1.5,
        -1.5,
        0.000001,
        0.0000001,
        -0.0000001,
        -5e-324,
        float(MAX_SAFE_JSON_INTEGER),
    ],
)
def test_admitted_numbers_are_closed_under_json_snapshot(value: float) -> None:
    canonical = canonical_json(value)

    assert canonical_json(json.loads(canonical)) == canonical


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, True),
        (1.0, True),
        (MAX_SAFE_JSON_INTEGER, True),
        (float(MAX_SAFE_JSON_INTEGER), True),
        (True, False),
        (1.5, False),
        (CustomInt(1), False),
        (CustomFloat(1.0), False),
        (Status.OK, False),
        (MAX_SAFE_JSON_INTEGER + 1, False),
        (float(MAX_SAFE_JSON_INTEGER + 1), False),
    ],
)
def test_safe_json_integer_predicate_is_host_type_independent(
    value: object,
    expected: bool,
) -> None:
    assert is_safe_json_integer(value) is expected


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan])
def test_rejects_non_finite_numbers(value: float) -> None:
    with pytest.raises(CanonicalJsonError) as error:
        canonical_json(value)

    assert error.value.code == "non_finite_number"
    assert error.value.path == "$"


@pytest.mark.parametrize("value", ["\ud800", {"\ud800": "bad"}])
def test_rejects_surrogate_code_points_with_stable_error(value: object) -> None:
    with pytest.raises(CanonicalJsonError) as error:
        canonical_json(value)

    assert error.value.code == "invalid_unicode_scalar"


@pytest.mark.parametrize(
    "value",
    [
        b"raw",
        bytearray(b"raw"),
        datetime(2026, 7, 9, tzinfo=UTC),
        {"bad", "set"},
        ("tuple",),
        object(),
    ],
)
def test_rejects_non_json_domain_values(value: object) -> None:
    with pytest.raises(CanonicalJsonError) as error:
        canonical_json(value)

    assert error.value.code == "non_canonical_input"


@pytest.mark.parametrize(
    "value",
    [
        CustomInt(7),
        CustomFloat(1.5),
        CustomStr("value"),
        CustomList([1]),
        CustomDict({"a": 1}),
        Status.OK,
    ],
)
def test_rejects_primitive_and_container_subclasses(value: object) -> None:
    with pytest.raises(CanonicalJsonError) as error:
        canonical_json(value)

    assert error.value.code == "non_canonical_input"


def test_rejects_non_string_object_keys() -> None:
    with pytest.raises(CanonicalJsonError) as error:
        canonical_json({1: "one"})

    assert error.value.code == "non_string_key"
    assert error.value.path == "$"


def test_rejects_cycles() -> None:
    value: list[object] = []
    value.append(value)

    with pytest.raises(CanonicalJsonError) as error:
        canonical_json(value)

    assert error.value.code == "cycle"
    assert error.value.path == "$[0]"


def test_try_canonical_json_returns_result_error_for_invalid_input() -> None:
    result = try_canonical_json({"n": MAX_SAFE_JSON_INTEGER + 1})

    assert result.is_err is True
    assert result.code == "unsafe_integer"


def test_try_hash_object_returns_result_without_digest_shortcuts() -> None:
    valid = try_hash_object({"b": 2, "a": 1})
    invalid = try_hash_object(math.nan)

    assert valid.is_ok is True
    assert valid.value == "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777"
    assert invalid.is_err is True
    assert invalid.code == "non_finite_number"

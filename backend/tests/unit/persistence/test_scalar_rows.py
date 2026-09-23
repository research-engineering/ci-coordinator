from collections.abc import Callable, Mapping

import pytest

from ci_coordinator.persistence.scalar_rows import (
    optional_int,
    optional_string,
    required_bytes,
    required_int,
    required_string,
)

type Reader = Callable[[Mapping[str, object], str], object]


class TextSubclass(str):
    pass


@pytest.mark.parametrize(
    "reader", [required_string, optional_string, required_int, optional_int, required_bytes]
)
def test_missing_scalar_is_not_defaulted(reader: Reader) -> None:
    with pytest.raises(KeyError, match="value"):
        reader({}, "value")


@pytest.mark.parametrize(
    ("reader", "value", "expected"),
    [
        (required_string, "", ""),
        (optional_string, None, None),
        (optional_string, "", ""),
        (optional_string, "text", "text"),
        (required_int, 0, 0),
        (optional_int, None, None),
        (optional_int, 0, 0),
        (optional_int, 42, 42),
        (required_bytes, b"raw", b"raw"),
        (required_bytes, memoryview(b"raw"), b"raw"),
    ],
)
def test_scalar_admission_preserves_values(reader: Reader, value: object, expected: object) -> None:
    actual = reader({"value": value}, "value")
    assert type(actual) is type(expected)
    assert actual == expected


@pytest.mark.parametrize(
    ("reader", "value", "message"),
    [
        (required_string, TextSubclass("text"), "value is not text"),
        (optional_string, TextSubclass("text"), "value is not text or null"),
        (required_int, True, "value is not an integer"),
        (optional_int, True, "value is not an integer or null"),
        (required_bytes, bytearray(b"raw"), "value is not bytes"),
    ],
)
def test_scalar_rejection_preserves_exact_type_and_diagnostic(
    reader: Reader, value: object, message: str
) -> None:
    with pytest.raises(TypeError) as caught:
        reader({"value": value}, "value")
    assert str(caught.value) == message

import pytest

from ci_coordinator.integrations.keycloak._text import bounded_text


@pytest.mark.parametrize(
    ("value", "maximum", "expected"),
    [
        ("", 10, False),
        (" ", 1, True),
        ("a", 0, False),
        ("a", 1, True),
        ("a\0b", 10, False),
        ("\ud800", 10, False),
        ("\udfff", 10, False),
        ("\u00e9", 1, False),
        ("\u00e9", 2, True),
        ("\U0001f642", 3, False),
        ("\U0001f642", 4, True),
    ],
)
def test_text_primitive_preserves_unicode_and_byte_boundaries(
    value: str, maximum: int, expected: bool
) -> None:
    assert bounded_text(value, maximum) is expected

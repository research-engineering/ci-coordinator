from __future__ import annotations


def exact_json_equal(actual: object, expected: object) -> bool:
    if type(actual) is not type(expected):
        return False
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return actual.keys() == expected.keys() and all(
            exact_json_equal(actual[key], value) for key, value in expected.items()
        )
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return False
        return len(actual) == len(expected) and all(
            exact_json_equal(actual_item, expected_item)
            for actual_item, expected_item in zip(actual, expected, strict=True)
        )
    return actual == expected


def require_exact_json_value(actual: object, expected: object, label: str) -> None:
    if not exact_json_equal(actual, expected):
        raise ValueError(f"{label} differs from the admitted contract")

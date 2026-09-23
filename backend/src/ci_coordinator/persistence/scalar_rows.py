from collections.abc import Mapping


def required_string(row: Mapping[str, object], key: str) -> str:
    value = row[key]
    if type(value) is not str:
        raise TypeError(f"{key} is not text")
    return value


def optional_string(row: Mapping[str, object], key: str) -> str | None:
    value = row[key]
    if value is not None and type(value) is not str:
        raise TypeError(f"{key} is not text or null")
    return value


def required_int(row: Mapping[str, object], key: str) -> int:
    value = row[key]
    if type(value) is not int:
        raise TypeError(f"{key} is not an integer")
    return value


def optional_int(row: Mapping[str, object], key: str) -> int | None:
    value = row[key]
    if value is not None and type(value) is not int:
        raise TypeError(f"{key} is not an integer or null")
    return value


def required_bytes(row: Mapping[str, object], key: str) -> bytes:
    value = row[key]
    if isinstance(value, memoryview):
        return value.tobytes()
    if type(value) is not bytes:
        raise TypeError(f"{key} is not bytes")
    return value

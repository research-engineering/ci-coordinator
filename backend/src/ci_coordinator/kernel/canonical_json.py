from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Final, Protocol, cast

from ci_coordinator.kernel.result import Err, Ok, ResultValue


class CanonicalJsonError(ValueError):
    def __init__(
        self,
        code: str,
        path: str,
        message: str,
        *,
        instance_pointer: str | None = None,
        limit: int | None = None,
        observed: int | None = None,
    ) -> None:
        super().__init__(f"{code} at {path}: {message}")
        self.code = code
        self.path = path
        self.message = message
        self.instance_pointer = instance_pointer
        self.limit = limit
        self.observed = observed


JSON_SEPARATORS: Final = (",", ":")
MAX_SAFE_JSON_INTEGER: Final = 9_007_199_254_740_991


@dataclass(frozen=True)
class JsonResourceLimits:
    max_depth: int
    max_nodes: int

    def __post_init__(self) -> None:
        if type(self.max_depth) is not int or self.max_depth < 0:
            raise ValueError("max_depth must be a non-negative integer")
        if type(self.max_nodes) is not int or self.max_nodes < 1:
            raise ValueError("max_nodes must be a positive integer")


@dataclass
class _ResourceState:
    limits: JsonResourceLimits | None
    nodes: int = 0

    def visit(self, depth: int, path: str, instance_pointer: str) -> None:
        if self.limits is None:
            return
        if depth > self.limits.max_depth:
            raise CanonicalJsonError(
                "json_max_depth_exceeded",
                path,
                f"maximum JSON depth is {self.limits.max_depth}",
                instance_pointer=instance_pointer,
                limit=self.limits.max_depth,
                observed=depth,
            )
        self.nodes += 1
        if self.nodes > self.limits.max_nodes:
            raise CanonicalJsonError(
                "json_max_nodes_exceeded",
                path,
                f"maximum JSON node count is {self.limits.max_nodes}",
                instance_pointer=instance_pointer,
                limit=self.limits.max_nodes,
                observed=self.nodes,
            )

    def admit_direct_children(
        self,
        child_count: int,
        path: str,
        instance_pointer: str,
    ) -> None:
        if self.limits is None:
            return
        if child_count > self.limits.max_nodes - self.nodes:
            raise CanonicalJsonError(
                "json_max_nodes_exceeded",
                path,
                f"maximum JSON node count is {self.limits.max_nodes}",
                instance_pointer=instance_pointer,
                limit=self.limits.max_nodes,
                observed=self.limits.max_nodes + 1,
            )


@dataclass(frozen=True, slots=True)
class _ValueFrame:
    value: object
    path: str
    instance_pointer: str
    depth: int


@dataclass(frozen=True, slots=True)
class _TextFrame:
    text: str


@dataclass(frozen=True, slots=True)
class _StringFrame:
    value: str
    path: str
    instance_pointer: str


@dataclass(frozen=True, slots=True)
class _ExitFrame:
    object_id: int


type _Frame = _ValueFrame | _TextFrame | _StringFrame | _ExitFrame


class _JsonOutput(Protocol):
    def append(self, text: str) -> None: ...

    def record_byte_overflow(self, path: str, instance_pointer: str) -> None: ...


@dataclass
class _TextOutput:
    chunks: list[str]

    def append(self, text: str) -> None:
        self.chunks.append(text)

    def record_byte_overflow(self, path: str, instance_pointer: str) -> None:
        del path, instance_pointer
        raise AssertionError("unbounded text output cannot overflow")


@dataclass
class _BoundedBytesOutput:
    max_bytes: int
    chunks: bytearray
    overflow: CanonicalJsonError | None = None

    def append(self, text: str) -> None:
        if self.overflow is not None:
            return
        encoded = text.encode("utf-8")
        remaining = self.max_bytes - len(self.chunks)
        if len(encoded) > remaining:
            self.record_byte_overflow("$", "")
            return
        self.chunks.extend(encoded)

    def record_byte_overflow(self, path: str, instance_pointer: str) -> None:
        if self.overflow is None:
            self.overflow = CanonicalJsonError(
                "canonical_json_max_bytes_exceeded",
                path,
                f"maximum canonical JSON byte count is {self.max_bytes}",
                instance_pointer=instance_pointer,
                limit=self.max_bytes,
                observed=self.max_bytes + 1,
            )

    def finish(self) -> bytes:
        if self.overflow is not None:
            raise self.overflow
        return bytes(self.chunks)


def canonical_json(
    value: object,
    *,
    resource_limits: JsonResourceLimits | None = None,
) -> bytes:
    return canonical_json_text(value, resource_limits=resource_limits).encode("utf-8")


def bounded_canonical_json(
    value: object,
    *,
    max_bytes: int,
    resource_limits: JsonResourceLimits | None = None,
) -> bytes:
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("max_bytes must be a non-negative integer")
    output = _BoundedBytesOutput(max_bytes=max_bytes, chunks=bytearray())
    _write_canonical_json(value, resource_limits=resource_limits, output=output)
    return output.finish()


def try_canonical_json(
    value: object,
    *,
    resource_limits: JsonResourceLimits | None = None,
) -> ResultValue[bytes]:
    try:
        return Ok(canonical_json(value, resource_limits=resource_limits))
    except CanonicalJsonError as error:
        return Err(code=error.code, message=error.message)


def is_safe_json_integer(value: object) -> bool:
    if type(value) is int:
        return abs(value) <= MAX_SAFE_JSON_INTEGER
    if type(value) is float:
        return math.isfinite(value) and value.is_integer() and abs(value) <= MAX_SAFE_JSON_INTEGER
    return False


def canonical_json_text(
    value: object,
    *,
    resource_limits: JsonResourceLimits | None = None,
) -> str:
    output = _TextOutput(chunks=[])
    _write_canonical_json(value, resource_limits=resource_limits, output=output)
    return "".join(output.chunks)


def _write_canonical_json(
    value: object,
    *,
    resource_limits: JsonResourceLimits | None,
    output: _JsonOutput,
) -> None:
    resources = _ResourceState(resource_limits)
    active: set[int] = set()
    frames: list[_Frame] = [_ValueFrame(value, "$", "", 0)]

    while frames:
        frame = frames.pop()
        if isinstance(frame, _TextFrame):
            output.append(frame.text)
            continue
        if isinstance(frame, _StringFrame):
            output.append(
                _bounded_json_string(
                    frame.value,
                    path=frame.path,
                    instance_pointer=frame.instance_pointer,
                    output=output,
                )
            )
            continue
        if isinstance(frame, _ExitFrame):
            active.remove(frame.object_id)
            continue

        candidate = frame.value
        resources.visit(frame.depth, frame.path, frame.instance_pointer)
        if candidate is None:
            output.append("null")
        elif type(candidate) is bool:
            output.append("true" if candidate else "false")
        elif type(candidate) is str:
            _reject_surrogates(candidate, frame.path, "string contains surrogate code point")
            output.append(
                _bounded_json_string(
                    candidate,
                    path=frame.path,
                    instance_pointer=frame.instance_pointer,
                    output=output,
                )
            )
        elif type(candidate) is int:
            if abs(candidate) > MAX_SAFE_JSON_INTEGER:
                raise CanonicalJsonError(
                    "unsafe_integer",
                    frame.path,
                    "integer exceeds JSON safe integer range",
                )
            output.append(str(candidate))
        elif type(candidate) is float:
            output.append(_canonical_float(candidate, frame.path))
        elif type(candidate) is list:
            _schedule_list(cast(list[object], candidate), frame, resources, active, output, frames)
        elif type(candidate) is dict:
            _schedule_object(
                cast(dict[object, object], candidate),
                frame,
                resources,
                active,
                output,
                frames,
            )
        elif isinstance(candidate, bytes | bytearray | memoryview):
            raise CanonicalJsonError(
                "non_canonical_input",
                frame.path,
                "bytes are not JSON values",
            )
        elif isinstance(candidate, datetime | date | time):
            raise CanonicalJsonError(
                "non_canonical_input",
                frame.path,
                "datetime values are not JSON values",
            )
        elif isinstance(candidate, set | frozenset | tuple):
            raise CanonicalJsonError(
                "non_canonical_input",
                frame.path,
                "collection type is not a JSON array",
            )
        else:
            raise CanonicalJsonError(
                "non_canonical_input",
                frame.path,
                f"{type(candidate).__name__} is not a JSON-domain value",
            )


def _schedule_list(
    values: list[object],
    frame: _ValueFrame,
    resources: _ResourceState,
    active: set[int],
    output: _JsonOutput,
    frames: list[_Frame],
) -> None:
    object_id = id(values)
    if object_id in active:
        raise CanonicalJsonError("cycle", frame.path, "cyclic arrays are not JSON values")
    resources.admit_direct_children(len(values), frame.path, frame.instance_pointer)

    active.add(object_id)
    output.append("[")
    frames.append(_ExitFrame(object_id))
    frames.append(_TextFrame("]"))
    for index in range(len(values) - 1, -1, -1):
        frames.append(
            _ValueFrame(
                values[index],
                f"{frame.path}[{index}]",
                _json_pointer_child(frame.instance_pointer, str(index)),
                frame.depth + 1,
            )
        )
        if index > 0:
            frames.append(_TextFrame(","))


def _schedule_object(
    value: dict[object, object],
    frame: _ValueFrame,
    resources: _ResourceState,
    active: set[int],
    output: _JsonOutput,
    frames: list[_Frame],
) -> None:
    object_id = id(value)
    if object_id in active:
        raise CanonicalJsonError("cycle", frame.path, "cyclic objects are not JSON values")
    resources.admit_direct_children(len(value), frame.path, frame.instance_pointer)

    entries: list[tuple[str, object]] = []
    for key, item in value.items():
        if type(key) is not str:
            raise CanonicalJsonError(
                "non_string_key",
                frame.path,
                f"object key {key!r} is not a string",
            )
        _reject_surrogates(key, frame.path, "object key contains surrogate code point")
        entries.append((key, item))
    entries.sort()

    active.add(object_id)
    output.append("{")
    frames.append(_ExitFrame(object_id))
    frames.append(_TextFrame("}"))
    for index in range(len(entries) - 1, -1, -1):
        key, item = entries[index]
        frames.append(
            _ValueFrame(
                item,
                f"{frame.path}.{key}",
                _json_pointer_child(frame.instance_pointer, key),
                frame.depth + 1,
            )
        )
        frames.append(_TextFrame(":"))
        frames.append(
            _StringFrame(
                key,
                frame.path,
                _json_pointer_child(frame.instance_pointer, key),
            )
        )
        if index > 0:
            frames.append(_TextFrame(","))


def _canonical_float(value: float, path: str) -> str:
    if not math.isfinite(value):
        raise CanonicalJsonError("non_finite_number", path, "number must be finite")
    if abs(value) > MAX_SAFE_JSON_INTEGER:
        raise CanonicalJsonError(
            "unsafe_integer",
            path,
            "number exceeds JSON safe integer magnitude",
        )
    if value == 0:
        return "0"
    return _ecmascript_number_text(value)


def _ecmascript_number_text(value: float) -> str:
    sign = "-" if value < 0 else ""
    magnitude = abs(value)
    text = repr(magnitude).lower()
    if "e" not in text:
        return sign + _strip_integer_float(text)

    mantissa, exponent_text = text.split("e", 1)
    exponent = int(exponent_text)
    digits, decimal_index = _decimal_parts(mantissa, exponent)
    if -6 <= exponent < 21:
        return sign + _fixed_decimal(digits, decimal_index)

    first_digit = digits[0]
    rest = digits[1:].rstrip("0")
    exponent_suffix = f"+{exponent}" if exponent >= 0 else str(exponent)
    if rest:
        return f"{sign}{first_digit}.{rest}e{exponent_suffix}"
    return f"{sign}{first_digit}e{exponent_suffix}"


def _decimal_parts(mantissa: str, exponent: int) -> tuple[str, int]:
    if "." not in mantissa:
        return mantissa, len(mantissa) + exponent
    whole, fraction = mantissa.split(".", 1)
    digits = whole + fraction
    return digits, len(whole) + exponent


def _fixed_decimal(digits: str, decimal_index: int) -> str:
    if decimal_index <= 0:
        text = "0." + ("0" * abs(decimal_index)) + digits
    elif decimal_index >= len(digits):
        text = digits + ("0" * (decimal_index - len(digits)))
    else:
        text = digits[:decimal_index] + "." + digits[decimal_index:]
    if "." not in text:
        return text
    return text.rstrip("0").rstrip(".")


def _strip_integer_float(text: str) -> str:
    if text.endswith(".0"):
        return text[:-2]
    return text


def _json_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=JSON_SEPARATORS)


def _bounded_json_string(
    value: str,
    *,
    path: str,
    instance_pointer: str,
    output: _JsonOutput,
) -> str:
    if not isinstance(output, _BoundedBytesOutput):
        return _json_string(value)
    if output.overflow is not None:
        return ""
    if _utf8_length_exceeds(value, output.max_bytes):
        output.record_byte_overflow(path, instance_pointer)
        return ""
    return _json_string(value)


def _utf8_length_exceeds(value: str, limit: int) -> bool:
    if value.isascii():
        return len(value) > limit
    observed = 0
    for character in value:
        code_point = ord(character)
        if code_point <= 0x7F:
            observed += 1
        elif code_point <= 0x7FF:
            observed += 2
        elif code_point <= 0xFFFF:
            observed += 3
        else:
            observed += 4
        if observed > limit:
            return True
    return False


def _json_pointer_child(instance_pointer: str, token: str) -> str:
    escaped_token = token.replace("~", "~0").replace("/", "~1")
    return f"{instance_pointer}/{escaped_token}"


def _reject_surrogates(value: str, path: str, message: str) -> None:
    if value.isascii():
        return
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise CanonicalJsonError("invalid_unicode_scalar", path, message)

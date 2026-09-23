"""Bounded strict admission for target-repository test manifests."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Final, cast

from ci_coordinator.kernel import StrictJsonError, load_strict_json, utf16_sort_key
from ci_coordinator.runner_capacity.model import ManifestTest

TEST_MANIFEST_SCHEMA_VERSION: Final = "dynamic-ci-test-manifest/v1"
MAX_TEST_MANIFEST_BYTES: Final = 524_288
MAX_TEST_MANIFEST_TESTS: Final = 4_096
MIN_EXPECTED_SECONDS: Final = 0.000_001
MAX_EXPECTED_SECONDS: Final = 604_800.0

_IDENTIFIER: Final = re.compile(r"[a-z][a-z0-9._-]{0,63}")
_VERSION: Final = re.compile(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}")
_MAX_TEST_ID_BYTES: Final = 256


@dataclass(frozen=True, slots=True)
class ParsedTestManifest:
    generator_id: str
    generator_version: str
    tests: tuple[ManifestTest, ...]
    duration_history: tuple[tuple[str, float], ...]

    def __post_init__(self) -> None:
        if _IDENTIFIER.fullmatch(self.generator_id) is None:
            raise ValueError("manifest generator id is invalid")
        if _VERSION.fullmatch(self.generator_version) is None:
            raise ValueError("manifest generator version is invalid")
        if type(self.tests) is not tuple or any(
            type(item) is not ManifestTest for item in self.tests
        ):
            raise TypeError("parsed manifest tests must be exact")
        test_ids = tuple(item.test_id for item in self.tests)
        if tuple(sorted(set(test_ids), key=utf16_sort_key)) != test_ids:
            raise ValueError("parsed manifest test identities must be canonical")
        expected_history = tuple(item[0] for item in self.duration_history)
        if expected_history != test_ids:
            raise ValueError("parsed manifest duration history must cover tests exactly")


def parse_test_manifest(content: bytes) -> ParsedTestManifest | None:
    try:
        root = _exact_object(
            load_strict_json(content, max_bytes=MAX_TEST_MANIFEST_BYTES),
            {"generator", "schemaVersion", "tests"},
        )
        if root["schemaVersion"] != TEST_MANIFEST_SCHEMA_VERSION:
            return None
        generator = _exact_object(root["generator"], {"id", "version"})
        generator_id = _matching_text(generator["id"], _IDENTIFIER)
        generator_version = _matching_text(generator["version"], _VERSION)
        tests = _tests(root["tests"])
        if generator_id is None or generator_version is None or tests is None:
            return None
        return ParsedTestManifest(
            generator_id=generator_id,
            generator_version=generator_version,
            tests=tuple(item[0] for item in tests),
            duration_history=tuple((item[0].test_id, item[1]) for item in tests),
        )
    except (KeyError, StrictJsonError, TypeError, ValueError):
        return None


def _tests(value: object) -> tuple[tuple[ManifestTest, float], ...] | None:
    if type(value) is not list or len(value) > MAX_TEST_MANIFEST_TESTS:
        return None
    parsed: list[tuple[ManifestTest, float]] = []
    seen: set[str] = set()
    for raw in value:
        item = _exact_object(raw, {"expectedSeconds", "testId", "witnessId"})
        test_id = _printable_ascii(item["testId"], _MAX_TEST_ID_BYTES)
        witness_id = _matching_text(item["witnessId"], _IDENTIFIER)
        duration = _bounded_number(
            item["expectedSeconds"],
            MIN_EXPECTED_SECONDS,
            MAX_EXPECTED_SECONDS,
        )
        if test_id is None or witness_id is None or duration is None or test_id in seen:
            return None
        seen.add(test_id)
        parsed.append((ManifestTest(test_id, witness_id), duration))
    return tuple(sorted(parsed, key=lambda item: utf16_sort_key(item[0].test_id)))


def _exact_object(value: object, keys: set[str]) -> dict[str, object]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError("test manifest object shape is invalid")
    return cast(dict[str, object], value)


def _matching_text(value: object, pattern: re.Pattern[str]) -> str | None:
    return value if type(value) is str and pattern.fullmatch(value) is not None else None


def _bounded_number(value: object, minimum: float, maximum: float) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    return numeric if math.isfinite(numeric) and minimum <= numeric <= maximum else None


def _printable_ascii(value: object, maximum_bytes: int) -> str | None:
    if (
        type(value) is not str
        or not value
        or not value.isascii()
        or len(value.encode("ascii")) > maximum_bytes
        or any(ord(character) < 0x20 or ord(character) > 0x7E for character in value)
    ):
        return None
    return value

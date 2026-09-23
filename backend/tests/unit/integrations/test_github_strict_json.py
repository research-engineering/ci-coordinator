from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import pytest

from ci_coordinator.integrations.github.app_credentials import (
    _InstallationToken,
    _parse_installation_token,
)
from ci_coordinator.integrations.github.reconciliation_observer_decoding import decode_repository
from ci_coordinator.integrations.github.repository_context_decoding import (
    parse_pull_request_snapshot,
)
from ci_coordinator.kernel import StrictJsonError, load_strict_json
from ci_coordinator.runner_capacity import parse_test_manifest

type Decoder = Callable[[bytes], object | None]

_BASE_SHA = b"a" * 40
_HEAD_SHA = b"b" * 40
_NOW = datetime(2026, 7, 16, tzinfo=UTC)


def _admitted_installation_token(body: bytes) -> object | None:
    result = _parse_installation_token(body, _NOW)
    return result if isinstance(result, _InstallationToken) else None


@pytest.mark.parametrize(
    "body",
    [
        b'{"key":0,"key":1}',
        b'{"\\ud800":0}',
        b'{"value":"\\ud800"}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
        b'{"value":-Infinity}',
        b'{"value":1e400}',
        b"\xff",
        '{"value":1}'.encode("utf-16"),
    ],
    ids=[
        "duplicate-key",
        "unsafe-scalar-key",
        "unsafe-scalar-value",
        "nan",
        "positive-infinity",
        "negative-infinity",
        "float-overflow",
        "invalid-utf8",
        "utf16-json",
    ],
)
def test_strict_json_rejects_unsafe_bytes(body: bytes) -> None:
    with pytest.raises(StrictJsonError):
        load_strict_json(body)


@pytest.mark.parametrize(
    ("body", "max_bytes"),
    [
        (bytearray(b"{}"), None),
        (b"{}", True),
        (b"{}", -1),
        (b"{}", 1),
    ],
    ids=["non-exact-bytes", "boolean-bound", "negative-bound", "oversize"],
)
def test_strict_json_rejects_invalid_input_or_bound(body: object, max_bytes: object) -> None:
    with pytest.raises(StrictJsonError):
        load_strict_json(
            cast(bytes, body),
            max_bytes=cast(int | None, max_bytes),
        )


def test_strict_json_accepts_exact_utf8_bytes_at_the_bound() -> None:
    body = b'{"\\ud83d\\ude00":1}'

    assert load_strict_json(body, max_bytes=len(body)) == {"\U0001f600": 1}


@pytest.mark.parametrize(
    ("decoder", "body"),
    [
        pytest.param(
            lambda value: parse_pull_request_snapshot(value, max_json_bytes=len(value)),
            b'{"number":7,"base":{"sha":"'
            + _BASE_SHA
            + b'"},"head":{"sha":"'
            + _HEAD_SHA
            + b'"},"metadata":NaN}',
            id="repository-context",
        ),
        pytest.param(
            decode_repository,
            b'{"id":1,"name":"repo","full_name":"acme/repo",'
            b'"owner":{"login":"acme"},"metadata":NaN}',
            id="reconciliation-repository",
        ),
        pytest.param(
            parse_test_manifest,
            b'{"schemaVersion":"dynamic-ci-test-manifest/v1","policy":{'
            b'"maxShards":1,"maxParallel":1,"setupSecondsPerShard":0,'
            b'"cpuWeight":0,"wallWeight":NaN,"operatorWeight":0},'
            b'"tests":[{"testId":"test","checkId":"check","expectedSeconds":1}]}',
            id="capacity-manifest",
        ),
        pytest.param(
            _admitted_installation_token,
            b'{"token":"secret","expires_at":"2026-07-16T01:00:00+00:00","metadata":NaN}',
            id="installation-token",
        ),
    ],
)
def test_github_json_decoders_reject_non_finite_constants(
    decoder: Decoder,
    body: bytes,
) -> None:
    assert decoder(body) is None


@pytest.mark.parametrize(
    "decoder",
    [
        pytest.param(parse_test_manifest, id="capacity-manifest"),
        pytest.param(_admitted_installation_token, id="installation-token"),
        pytest.param(decode_repository, id="reconciliation-repository"),
        pytest.param(
            lambda value: parse_pull_request_snapshot(value, max_json_bytes=len(value)),
            id="repository-context",
        ),
    ],
)
def test_github_json_decoders_convert_parser_recursion_to_failure(
    decoder: Decoder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raise_recursion(*_args: object, **_kwargs: object) -> object:
        raise RecursionError

    monkeypatch.setattr(
        "ci_coordinator.kernel.strict_json.json.loads",
        raise_recursion,
    )

    assert decoder(b"{}") is None

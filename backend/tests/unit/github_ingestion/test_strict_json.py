from __future__ import annotations

import pytest

from ci_coordinator.github_ingestion.payload_limits import WebhookPayloadLimits
from ci_coordinator.github_ingestion.strict_json import (
    FrozenJsonObject,
    JsonPayloadError,
    parse_json_object,
)


def limits(**overrides: int) -> WebhookPayloadLimits:
    values = {
        "maximum_body_bytes": 128,
        "maximum_nesting_depth": 3,
        "maximum_json_nodes": 8,
        "maximum_object_members": 4,
        "maximum_array_items": 4,
        "maximum_string_code_points": 32,
    }
    values.update(overrides)
    return WebhookPayloadLimits(**values)


@pytest.mark.parametrize(
    ("raw_body", "configured_limits", "code"),
    [
        (
            b"{}",
            limits(maximum_body_bytes=1, maximum_string_code_points=1),
            "webhook_payload_too_large",
        ),
        (b'{"a":{"b":{"c":1}}}', limits(), "webhook_payload_nesting_limit_exceeded"),
        (b'{"a":[1,2,3,4,5]}', limits(), "webhook_payload_array_limit_exceeded"),
        (b'{"a":1,"b":2,"c":3,"d":4,"e":5}', limits(), "webhook_payload_object_limit_exceeded"),
        (
            b'{"a":"123456789"}',
            limits(maximum_string_code_points=8),
            "webhook_payload_string_limit_exceeded",
        ),
        (
            b'{"a":1}',
            limits(
                maximum_json_nodes=1,
                maximum_object_members=1,
                maximum_array_items=1,
            ),
            "webhook_payload_node_limit_exceeded",
        ),
    ],
)
def test_resource_limits_fail_before_an_unbounded_payload_can_escape(
    raw_body: bytes,
    configured_limits: WebhookPayloadLimits,
    code: str,
) -> None:
    with pytest.raises(JsonPayloadError, match=f"^{code}$"):
        parse_json_object(raw_body, configured_limits)


@pytest.mark.parametrize(
    "raw_body",
    [
        b'{"a":1,"a":2}',
        b'{"a":"\\ud800"}',
        b'{"a":NaN}',
        b"[]",
    ],
)
def test_untrusted_json_never_becomes_a_partial_object(raw_body: bytes) -> None:
    with pytest.raises(JsonPayloadError):
        parse_json_object(raw_body, limits())


def test_string_limit_rejects_before_host_json_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*_: object, **__: object) -> object:
        raise AssertionError("json.loads must not run after preflight rejects the string")

    monkeypatch.setattr(
        "ci_coordinator.github_ingestion.strict_json.json.loads",
        fail_if_called,
    )

    with pytest.raises(JsonPayloadError, match=r"^webhook_payload_string_limit_exceeded$"):
        parse_json_object(b'{"a":"abc"}', limits(maximum_string_code_points=2))


def test_surrogate_pair_counts_as_one_preflight_code_point() -> None:
    parsed = parse_json_object(
        b'{"a":"\\ud83d\\ude00"}',
        limits(maximum_string_code_points=1),
    )

    assert parsed["a"] == "\U0001f600"


def test_valid_json_is_immutable_and_lookup_preserves_the_admitted_value() -> None:
    parsed = parse_json_object(b'{"repository":{"id":1},"values":[true,null]}', limits())

    repository = parsed["repository"]
    assert parsed["values"] == (True, None)
    assert isinstance(repository, FrozenJsonObject)
    assert repository["id"] == 1

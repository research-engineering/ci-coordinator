from __future__ import annotations

import json
from dataclasses import dataclass
from importlib.resources import files
from typing import cast

PROFILE_SCHEMA_VERSION = "ci-webhook-ingestion-profile/v2"
PROFILE_RESOURCE_NAME = "webhook-ingestion-profile.v2.json"
_PROFILE_ROOT_KEYS = frozenset(
    {"schemaVersion", "profileId", "provider", "limits", "eventActions", "stableCodes"}
)
_PROVIDER_KEYS = frozenset({"name", "maximumDeliveredPayloadBytes", "payloadCapSourceUrl"})
_LIMIT_KEYS = frozenset(
    {
        "maximumBodyBytes",
        "maximumNestingDepth",
        "maximumJsonNodes",
        "maximumObjectMembers",
        "maximumArrayItems",
        "maximumStringCodePoints",
    }
)
_SUPPORTED_EVENT_NAMES = (
    "merge_group",
    "ping",
    "pull_request",
    "push",
    "workflow_job",
    "workflow_run",
)


@dataclass(frozen=True, slots=True)
class WebhookPayloadLimits:
    maximum_body_bytes: int
    maximum_nesting_depth: int
    maximum_json_nodes: int
    maximum_object_members: int
    maximum_array_items: int
    maximum_string_code_points: int

    def __post_init__(self) -> None:
        values = (
            ("maximum_body_bytes", self.maximum_body_bytes),
            ("maximum_nesting_depth", self.maximum_nesting_depth),
            ("maximum_json_nodes", self.maximum_json_nodes),
            ("maximum_object_members", self.maximum_object_members),
            ("maximum_array_items", self.maximum_array_items),
            ("maximum_string_code_points", self.maximum_string_code_points),
        )
        for field_name, value in values:
            if type(value) is not int or value < 1:
                raise ValueError(f"{field_name} must be a positive integer")
        if self.maximum_object_members > self.maximum_json_nodes:
            raise ValueError("maximum_object_members must not exceed maximum_json_nodes")
        if self.maximum_array_items > self.maximum_json_nodes:
            raise ValueError("maximum_array_items must not exceed maximum_json_nodes")
        if self.maximum_string_code_points > self.maximum_body_bytes:
            raise ValueError("maximum_string_code_points must not exceed maximum_body_bytes")


@dataclass(frozen=True, slots=True)
class EventActionPolicy:
    event_name: str
    allowed_actions: tuple[str | None, ...]

    def __post_init__(self) -> None:
        if not self.event_name:
            raise ValueError("event_name must not be empty")
        if not self.allowed_actions:
            raise ValueError("allowed_actions must not be empty")
        invalid_action = any(
            action is not None and (type(action) is not str or not action)
            for action in self.allowed_actions
        )
        if invalid_action:
            raise ValueError("allowed_actions must contain non-empty strings or null")
        if len(set(self.allowed_actions)) != len(self.allowed_actions):
            raise ValueError("allowed_actions must be unique")


@dataclass(frozen=True, slots=True)
class WebhookIngestionProfile:
    profile_id: str
    provider_maximum_payload_bytes: int
    provider_payload_cap_source_url: str
    limits: WebhookPayloadLimits
    event_actions: tuple[EventActionPolicy, ...]
    stable_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.profile_id:
            raise ValueError("profile_id must not be empty")
        if (
            type(self.provider_maximum_payload_bytes) is not int
            or self.provider_maximum_payload_bytes < 1
        ):
            raise ValueError("provider_maximum_payload_bytes must be a positive integer")
        if type(
            self.provider_payload_cap_source_url
        ) is not str or not self.provider_payload_cap_source_url.startswith("https://"):
            raise ValueError("provider_payload_cap_source_url must be a non-empty HTTPS URL")
        if self.limits.maximum_body_bytes > self.provider_maximum_payload_bytes:
            raise ValueError("maximum_body_bytes must not exceed the provider payload cap")
        event_names = tuple(policy.event_name for policy in self.event_actions)
        if event_names != _SUPPORTED_EVENT_NAMES:
            raise ValueError("event_actions must match the supported v2 event families")
        if len(set(self.stable_codes)) != len(self.stable_codes):
            raise ValueError("stable_codes must be unique")
        if not self.stable_codes:
            raise ValueError("stable_codes must not be empty")

    def action_decision(self, event_name: str, action: str | None) -> str:
        for policy in self.event_actions:
            if policy.event_name == event_name:
                return "admitted" if action in policy.allowed_actions else "not_admitted"
        return "unsupported"


def load_bundled_profile() -> WebhookIngestionProfile:
    raw_profile = (
        files("ci_coordinator.github_ingestion.resources")
        .joinpath(PROFILE_RESOURCE_NAME)
        .read_bytes()
    )
    return parse_profile(raw_profile)


def parse_profile(raw_profile: bytes) -> WebhookIngestionProfile:
    if type(raw_profile) is not bytes:
        raise ValueError("profile bytes must be exact bytes")
    try:
        decoded = raw_profile.decode("utf-8")
        payload = json.loads(decoded, object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise ValueError("profile must be valid duplicate-free UTF-8 JSON") from error
    root = _exact_record(payload, "profile", _PROFILE_ROOT_KEYS)
    if _string(root.get("schemaVersion"), "schemaVersion") != PROFILE_SCHEMA_VERSION:
        raise ValueError("profile schemaVersion is unsupported")
    provider = _exact_record(root.get("provider"), "provider", _PROVIDER_KEYS)
    if _string(provider.get("name"), "provider.name") != "github":
        raise ValueError("provider.name must be github")
    limits = _exact_record(root.get("limits"), "limits", _LIMIT_KEYS)
    event_actions = _exact_record(root.get("eventActions"), "eventActions", _SUPPORTED_EVENT_NAMES)
    stable_codes = _string_array(root.get("stableCodes"), "stableCodes")
    return WebhookIngestionProfile(
        profile_id=_string(root.get("profileId"), "profileId"),
        provider_maximum_payload_bytes=_positive_integer(
            provider.get("maximumDeliveredPayloadBytes"),
            "provider.maximumDeliveredPayloadBytes",
        ),
        provider_payload_cap_source_url=_https_url(
            provider.get("payloadCapSourceUrl"),
            "provider.payloadCapSourceUrl",
        ),
        limits=WebhookPayloadLimits(
            maximum_body_bytes=_positive_integer(
                limits.get("maximumBodyBytes"),
                "limits.maximumBodyBytes",
            ),
            maximum_nesting_depth=_positive_integer(
                limits.get("maximumNestingDepth"), "limits.maximumNestingDepth"
            ),
            maximum_json_nodes=_positive_integer(
                limits.get("maximumJsonNodes"),
                "limits.maximumJsonNodes",
            ),
            maximum_object_members=_positive_integer(
                limits.get("maximumObjectMembers"), "limits.maximumObjectMembers"
            ),
            maximum_array_items=_positive_integer(
                limits.get("maximumArrayItems"), "limits.maximumArrayItems"
            ),
            maximum_string_code_points=_positive_integer(
                limits.get("maximumStringCodePoints"), "limits.maximumStringCodePoints"
            ),
        ),
        event_actions=tuple(
            EventActionPolicy(
                event_name=event_name,
                allowed_actions=_action_array(actions, f"eventActions.{event_name}"),
            )
            for event_name, actions in sorted(event_actions.items())
        ),
        stable_codes=tuple(sorted(stable_codes)),
    )


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("profile contains a duplicate key")
        result[key] = value
    return result


def _record(value: object, field_name: str) -> dict[str, object]:
    if type(value) is not dict:
        raise ValueError(f"{field_name} must be an object")
    return cast(dict[str, object], value)


def _exact_record(
    value: object,
    field_name: str,
    expected_keys: frozenset[str] | tuple[str, ...],
) -> dict[str, object]:
    record = _record(value, field_name)
    if set(record) != set(expected_keys):
        raise ValueError(f"{field_name} keys must match its v1 schema")
    return record


def _string(value: object, field_name: str) -> str:
    if type(value) is not str or not value:
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _https_url(value: object, field_name: str) -> str:
    candidate = _string(value, field_name)
    if not candidate.startswith("https://"):
        raise ValueError(f"{field_name} must be an HTTPS URL")
    return candidate


def _positive_integer(value: object, field_name: str) -> int:
    if type(value) is not int or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _string_array(value: object, field_name: str) -> tuple[str, ...]:
    if type(value) is not list:
        raise ValueError(f"{field_name} must be an array")
    return tuple(_string(item, field_name) for item in value)


def _action_array(value: object, field_name: str) -> tuple[str | None, ...]:
    if type(value) is not list or not value:
        raise ValueError(f"{field_name} must be a non-empty array")
    actions: list[str | None] = []
    for action in value:
        if action is None:
            actions.append(None)
        else:
            actions.append(_string(action, field_name))
    return tuple(actions)

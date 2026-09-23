from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime

from ci_coordinator.github_ingestion.provenance import (
    GitHubRepository,
    ProvenanceFailure,
    WebhookProvenance,
)
from ci_coordinator.github_ingestion.strict_json import FrozenJsonObject
from ci_coordinator.kernel import is_safe_json_integer

_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_RFC3339_INSTANT = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$"
)


@dataclass(frozen=True, slots=True)
class EventCommon:
    provenance: WebhookProvenance
    repository: GitHubRepository
    action: str | None


type CommonExtraction = EventCommon | ProvenanceFailure


def extract_event_common(
    provenance: WebhookProvenance,
    payload: FrozenJsonObject,
) -> CommonExtraction:
    installation = object_field(payload, "installation")
    installation_id = positive_integer(installation.get("id")) if installation else None
    if installation_id is None:
        return ProvenanceFailure("missing_installation")
    repository = object_field(payload, "repository")
    repository_id = positive_integer(repository.get("id")) if repository else None
    owner = object_field(repository, "owner") if repository else None
    owner_login = optional_string(owner, "login") if owner else None
    name = optional_string(repository, "name") if repository else None
    if repository_id is None or owner_login is None or name is None:
        return ProvenanceFailure("missing_repository")
    return EventCommon(
        provenance=provenance,
        repository=GitHubRepository(
            installation_id=installation_id,
            repository_id=repository_id,
            owner=owner_login,
            name=name,
        ),
        action=optional_string(payload, "action"),
    )


def object_field(record: FrozenJsonObject | None, key: str) -> FrozenJsonObject | None:
    if record is None:
        return None
    value = record.get(key)
    return value if isinstance(value, FrozenJsonObject) else None


def optional_string(record: FrozenJsonObject | None, key: str) -> str | None:
    if record is None:
        return None
    value = record.get(key)
    if type(value) is not str or not value:
        return None
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        return None
    return value


def optional_boolean(record: FrozenJsonObject | None, key: str) -> bool | None:
    if record is None:
        return None
    value = record.get(key)
    return value if type(value) is bool else None


def positive_integer(value: object) -> int | None:
    if type(value) is not int or value < 1 or not is_safe_json_integer(value):
        return None
    return value


def git_sha(record: FrozenJsonObject | None, key: str) -> str | None:
    value = optional_string(record, key)
    return value if value is not None and _GIT_SHA.fullmatch(value) is not None else None


def timestamp(value: object) -> datetime | None:
    if type(value) is not str or _RFC3339_INSTANT.fullmatch(value) is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed.astimezone(UTC) if parsed.utcoffset() is not None else None

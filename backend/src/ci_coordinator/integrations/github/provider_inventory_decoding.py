"""Strict endpoint-specific decoding for untrusted provider inventory bytes."""

from __future__ import annotations

import re
from datetime import datetime
from typing import cast

from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.integrations.github._response_decoding import (
    json_object_or_none,
    non_negative_safe_integer,
    object_or_none,
    positive_safe_integer,
)
from ci_coordinator.kernel import StrictJsonError, load_strict_json
from ci_coordinator.provider_inventory import (
    InstallationSummary,
    ProviderInstallationPage,
    ProviderRepositoryPage,
    RepositorySummary,
)
from ci_coordinator.provider_inventory.model import (
    AccountType,
    InstallationState,
    RepositorySelection,
    RepositoryVisibility,
)


def decode_installation_page(
    body: bytes,
    *,
    page: int,
    per_page: int,
    has_next_page: bool,
) -> ProviderInstallationPage | None:
    try:
        values = load_strict_json(body)
    except StrictJsonError:
        return None
    if type(values) is not list or len(values) > per_page:
        return None
    installations: list[InstallationSummary] = []
    for value in values:
        installation = decode_installation_value(value)
        if installation is None:
            return None
        installations.append(installation)
    try:
        return ProviderInstallationPage(
            page,
            per_page,
            has_next_page,
            tuple(sorted(installations, key=lambda item: item.installation_id)),
        )
    except ValueError:
        return None


def decode_installation(body: bytes) -> InstallationSummary | None:
    value = json_object_or_none(body)
    return decode_installation_value(value)


def decode_installation_value(value: object) -> InstallationSummary | None:
    value = object_or_none(value)
    if value is None or "suspended_at" not in value:
        return None
    installation_id = positive_safe_integer(value.get("id"))
    repository_selection = _enum_text(value.get("repository_selection"), {"all", "selected"})
    account = object_or_none(value.get("account"))
    if account is None:
        return None
    account_id = positive_safe_integer(account.get("id"))
    account_login = _bounded_text(account.get("login"), maximum_bytes=512)
    account_type = _enum_text(account.get("type"), {"Organization", "User"})
    suspended = _suspension_state(value.get("suspended_at"))
    if (
        installation_id is None
        or repository_selection is None
        or account_id is None
        or account_login is None
        or account_type is None
        or suspended is None
    ):
        return None
    state = (
        "suspended"
        if suspended
        else ("active" if account_type == "Organization" else "unsupported")
    )
    try:
        return InstallationSummary(
            installation_id=installation_id,
            account_id=account_id,
            account_login=account_login,
            account_type=cast(AccountType, account_type),
            repository_selection=cast(RepositorySelection, repository_selection),
            state=cast(InstallationState, state),
        )
    except (TypeError, ValueError):
        return None


def decode_repository_page(
    body: bytes,
    *,
    installation_id: int,
    page: int,
    per_page: int,
    has_next_page: bool,
) -> ProviderRepositoryPage | None:
    value = json_object_or_none(body)
    if value is None:
        return None
    total_count = non_negative_safe_integer(value.get("total_count"))
    raw_repositories = value.get("repositories")
    if (
        total_count is None
        or type(raw_repositories) is not list
        or len(raw_repositories) > per_page
    ):
        return None
    repositories: list[RepositorySummary] = []
    for raw_repository in raw_repositories:
        repository = _decode_repository(raw_repository, installation_id=installation_id)
        if repository is None:
            return None
        repositories.append(repository)
    try:
        return ProviderRepositoryPage(
            page=page,
            per_page=per_page,
            total_count=total_count,
            has_next_page=has_next_page,
            repositories=tuple(repositories),
        )
    except (TypeError, ValueError):
        return None


def _decode_repository(value: object, *, installation_id: int) -> RepositorySummary | None:
    repository = object_or_none(value)
    if repository is None:
        return None
    repository_id = positive_safe_integer(repository.get("id"))
    node_id = _bounded_text(repository.get("node_id"), maximum_bytes=512)
    name = _bounded_text(repository.get("name"), maximum_bytes=512)
    full_name = _bounded_text(repository.get("full_name"), maximum_bytes=1_025)
    visibility = _enum_text(repository.get("visibility"), {"public", "private", "internal"})
    default_branch = _bounded_text(repository.get("default_branch"), maximum_bytes=1_024)
    owner = object_or_none(repository.get("owner"))
    if owner is None:
        return None
    owner_id = positive_safe_integer(owner.get("id"))
    owner_login = _bounded_text(owner.get("login"), maximum_bytes=512)
    flags = tuple(repository.get(name) for name in ("archived", "disabled", "fork"))
    raw_created_at = repository.get("created_at")
    created_at = _repository_created_at(raw_created_at)
    if (
        repository_id is None
        or node_id is None
        or name is None
        or full_name is None
        or visibility is None
        or default_branch is None
        or owner_id is None
        or owner_login is None
        or any(type(flag) is not bool for flag in flags)
        or (raw_created_at is not None and created_at is None)
    ):
        return None
    try:
        return RepositorySummary(
            scope=RepositoryScope(installation_id, repository_id),
            node_id=node_id,
            owner_id=owner_id,
            owner_login=owner_login,
            name=name,
            full_name=full_name,
            visibility=cast(RepositoryVisibility, visibility),
            default_branch=default_branch,
            archived=cast(bool, flags[0]),
            disabled=cast(bool, flags[1]),
            fork=cast(bool, flags[2]),
            created_at=created_at,
        )
    except (TypeError, ValueError):
        return None


def _repository_created_at(value: object) -> datetime | None:
    if value is None or type(value) is not str:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value) is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _suspension_state(value: object) -> bool | None:
    if value is None:
        return False
    text = _bounded_text(value, maximum_bytes=128)
    if text is None:
        return None
    try:
        instant = datetime.fromisoformat(text)
    except ValueError:
        return None
    return True if instant.tzinfo is not None else None


def _enum_text(value: object, admitted: set[str]) -> str | None:
    text = _bounded_text(value, maximum_bytes=64)
    return text if text in admitted else None


def _bounded_text(value: object, *, maximum_bytes: int) -> str | None:
    if type(value) is not str or not value or value != value.strip():
        return None
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        return None
    return value if len(value.encode("utf-8")) <= maximum_bytes else None

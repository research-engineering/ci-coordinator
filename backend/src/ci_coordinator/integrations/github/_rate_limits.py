"""Bounded, non-authorizing projection of GitHub rate-limit evidence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from ci_coordinator.integrations.github.contracts import GitHubFailure
from ci_coordinator.kernel import JsonResourceLimits, StrictJsonError, load_strict_json

_ERROR_LIMITS: Final = JsonResourceLimits(max_depth=2, max_nodes=16)
_SECONDARY_MESSAGE: Final = "You have exceeded a secondary rate limit"
_RETRY_HEADERS: Final = frozenset({"retry-after", "x-ratelimit-remaining", "x-ratelimit-reset"})
_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MAX_EPOCH_SECONDS: Final = 253_402_300_799


def has_secondary_rate_limit_message(body: bytes) -> bool:
    try:
        value = load_strict_json(body, max_bytes=4_096, resource_limits=_ERROR_LIMITS)
    except StrictJsonError:
        return False
    if type(value) is not dict or not set(value).issubset(
        {"message", "documentation_url", "status"}
    ):
        return False
    message = value.get("message")
    if not _bounded_error_text(message):
        return False
    if "documentation_url" in value and not _bounded_error_text(value["documentation_url"]):
        return False
    if "status" in value and value["status"] != "403":
        return False
    return isinstance(message, str) and (
        message in {_SECONDARY_MESSAGE, _SECONDARY_MESSAGE + "."}
        or message.startswith(_SECONDARY_MESSAGE + ". ")
    )


def retry_after_seconds(failure: GitHubFailure) -> int | None:
    evidence = failure.rate_limit
    if failure.kind != "rate_limited" or evidence is None:
        return None
    response = failure.response
    if response is not None:
        names = [header.name.casefold() for header in response.headers]
        if any(names.count(name) > 1 for name in _RETRY_HEADERS):
            return None

    delay = None
    if evidence.retry_after is not None:
        delay = _decimal(evidence.retry_after, max_digits=4)
        if delay is None or delay > 3_600:
            return None

    if evidence.remaining == 0 and evidence.reset_at is not None:
        reset = _decimal(evidence.reset_at, max_digits=12)
        if (
            reset is None
            or reset > _MAX_EPOCH_SECONDS
            or response is None
            or response.received_at is None
        ):
            return None
        # Integer reset seconds minus floored receipt seconds rounds the wait up.
        received_seconds = (response.received_at - _EPOCH) // timedelta(seconds=1)
        reset_delay = max(0, reset - received_seconds)
        if reset_delay > 3_600:
            return None
        delay = max(delay or 0, reset_delay)
    return delay


def _decimal(value: str, *, max_digits: int) -> int | None:
    if len(value) > max_digits or not value.isascii() or not value.isdecimal():
        return None
    return int(value)


def _bounded_error_text(value: object) -> bool:
    return (
        type(value) is str
        and 0 < len(value.encode("utf-8")) <= 1_024
        and not any(ord(character) < 32 or ord(character) == 127 for character in value)
    )

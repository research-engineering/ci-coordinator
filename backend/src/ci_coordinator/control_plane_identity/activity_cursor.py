from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from ci_coordinator.control_plane_identity.activity import ActivityPrincipal
from ci_coordinator.control_plane_identity.activity_query import ActivityQuery
from ci_coordinator.kernel import load_strict_json
from ci_coordinator.kernel.canonical_json import MAX_SAFE_JSON_INTEGER, JsonResourceLimits


@dataclass(frozen=True, slots=True)
class ActivityPosition:
    through: int
    before: int
    expires_at: datetime


class ActivityCursorCodec:
    def __init__(self, key: bytes) -> None:
        if type(key) is not bytes or len(key) < 32:
            raise ValueError("activity cursor key requires at least 32 bytes")
        self._key = key

    def encode(
        self, query: ActivityQuery, principal: ActivityPrincipal, position: ActivityPosition
    ) -> str:
        payload = json.dumps(
            [
                self._binding(query, principal),
                position.through,
                position.before,
                position.expires_at.isoformat(),
            ],
            separators=(",", ":"),
        ).encode("ascii")
        signature = hmac.digest(self._key, payload, "sha256")
        return base64.urlsafe_b64encode(signature + payload).decode("ascii")

    def decode(
        self, value: str, query: ActivityQuery, principal: ActivityPrincipal, *, now: datetime
    ) -> ActivityPosition:
        if type(value) is not str or not 1 <= len(value) <= 1024:
            raise ValueError("invalid activity cursor")
        try:
            raw = base64.b64decode(value, altchars=b"-_", validate=True)
            if base64.urlsafe_b64encode(raw).decode("ascii") != value:
                raise ValueError("noncanonical activity cursor")
            signature, payload = raw[:32], raw[32:]
            if not hmac.compare_digest(signature, hmac.digest(self._key, payload, "sha256")):
                raise ValueError("invalid activity cursor")
            parts = load_strict_json(
                payload,
                max_bytes=1024,
                resource_limits=JsonResourceLimits(max_depth=2, max_nodes=8),
            )
            if not isinstance(parts, list) or len(parts) != 4:
                raise ValueError("invalid activity cursor")
            binding, through, before, expiry = parts
            if binding != self._binding(query, principal):
                raise ValueError("activity cursor binding changed")
            if (
                type(through) is not int
                or type(before) is not int
                or not 0 < before <= through <= MAX_SAFE_JSON_INTEGER
                or type(expiry) is not str
            ):
                raise ValueError("invalid activity cursor bounds")
            expires = datetime.fromisoformat(expiry)
            if expires.tzinfo is None or not now < expires <= now + timedelta(minutes=5):
                raise ValueError("expired activity cursor")
            return ActivityPosition(through, before, expires)
        except (binascii.Error, UnicodeError, TypeError, ValueError) as error:
            raise ValueError("invalid activity cursor") from error

    @staticmethod
    def _binding(query: ActivityQuery, principal: ActivityPrincipal) -> str:
        return hashlib.sha256(
            json.dumps(
                [query.binding(), principal.actor_id, principal.authority_profile_digest],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ).encode("ascii")
        ).hexdigest()

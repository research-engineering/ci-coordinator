import base64
import binascii
import hashlib
import hmac

from ci_coordinator.ci_economics.history_read import HistoryReadCursor, HistoryReadQuery
from ci_coordinator.kernel import hash_object

MAX_HISTORY_CURSOR_CHARS = 4096


class HistoryCursorCodec:
    def __init__(self, key: bytes) -> None:
        if type(key) is not bytes or len(key) < 32:
            raise ValueError("archive cursor authentication needs at least 32 key bytes")
        self._key = key

    def encode(self, cursor: HistoryReadCursor) -> str:
        cursor = HistoryReadCursor.model_validate(cursor)
        raw = cursor.model_dump_json().encode("utf-8")
        payload = base64.urlsafe_b64encode(raw).decode("ascii")
        signature = hmac.new(self._key, b"archive-read/v1:" + raw, hashlib.sha256).hexdigest()
        return payload + "." + signature

    def decode(self, token: str, *, query: HistoryReadQuery, actor: str) -> HistoryReadCursor:
        if type(token) is not str or not 1 <= len(token) <= MAX_HISTORY_CURSOR_CHARS:
            raise ValueError("invalid archive cursor")
        try:
            payload, signature = token.split(".")
            raw = base64.b64decode(payload, altchars=b"-_", validate=True)
            expected = hmac.new(self._key, b"archive-read/v1:" + raw, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("invalid archive cursor")
            cursor = HistoryReadCursor.model_validate_json(raw)
            if (
                cursor.query_digest != history_query_digest(query, actor)
                or self.encode(cursor) != token
            ):
                raise ValueError("foreign archive cursor")
            return cursor
        except (ValueError, TypeError, binascii.Error, UnicodeError):
            raise ValueError("invalid archive cursor") from None


def history_query_digest(query: HistoryReadQuery, actor: str) -> str:
    query = HistoryReadQuery.model_validate(query)
    return hash_object({"query": query.model_dump(mode="json"), "actor": actor})

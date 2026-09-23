from __future__ import annotations

from typing import Final

from schemathesis.python import asgi

REQUALIFIED_SCHEMATHESIS_VERSION: Final = "4.27.5"


class ClosingLifespan(asgi._Lifespan):
    """Close streams left open by the pinned native lifecycle, including failed transitions."""

    def start(self) -> None:
        try:
            super().start()
        except BaseException:
            self._close_streams()
            raise

    def stop(self) -> None:
        try:
            super().stop()
        finally:
            self._close_streams()

    def _close_streams(self) -> None:
        self.portal.call(self.receive_stream.aclose)
        self.portal.call(self.send_stream.aclose)

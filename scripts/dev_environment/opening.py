from __future__ import annotations

import os
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

from scripts.dev_environment.compose import ComposeError
from scripts.dev_environment.diagnostics import Reason


def open_loopback_ui(url: str) -> bool:
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port is None
        or not 1 <= parsed.port <= 65535
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ComposeError("UI endpoint is not admitted", reason=Reason.INVALID_PROVIDER_RESPONSE)
    if sys.platform == "darwin":
        opener = shutil.which("open")
    elif sys.platform == "linux" and (
        os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
    ):
        opener = shutil.which("xdg-open")
    else:
        return False
    if opener is None:
        return False
    try:
        result = subprocess.run(  # noqa: S603 - fixed opener and admitted loopback URL
            [opener, url],
            check=False,
            timeout=5,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0

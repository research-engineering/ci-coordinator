from __future__ import annotations

from pathlib import Path
from typing import Literal

import pytest
from scripts.prometheus_rules import PROMETHEUS_IMAGE, RULES_PATH, TESTS_PATH, docker_arguments


@pytest.mark.parametrize(("operation", "input_path"), [("check", RULES_PATH), ("test", TESTS_PATH)])
def test_promtool_command_is_immutable_read_only_and_network_isolated(
    tmp_path: Path,
    operation: Literal["check", "test"],
    input_path: Path,
) -> None:
    arguments = docker_arguments(tmp_path, operation)

    assert PROMETHEUS_IMAGE.endswith(
        "@sha256:5ce7540c3c00ef4ab0c9d2c995c6a5b9c421f44b4a115d97a2c7af3b1c21cbb0"
    )
    assert arguments == (
        "run",
        "--rm",
        "--network=none",
        "--read-only",
        "--tmpfs=/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=65532:65532",
        "--entrypoint=/bin/promtool",
        "--volume",
        f"{(tmp_path / RULES_PATH.parent).resolve()}:/rules:ro",
        PROMETHEUS_IMAGE,
        operation,
        "rules",
        f"/rules/{input_path.name}",
    )

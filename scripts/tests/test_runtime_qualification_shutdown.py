from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest
from ruamel.yaml import YAML
from scripts.bounded_process import spawn

ROOT = Path(__file__).resolve().parents[2]
SUCCESS = '{"event":"server_log","level":"INFO","reason":"shutdown_complete"}\n'


@pytest.mark.parametrize(
    "source",
    [
        "",
        "Application shutdown complete.\n",
        '{"message":"Application shutdown complete."}\n',
        '{"event":"other","level":"INFO","reason":"shutdown_complete"}\n',
        '{"event":"server_log","level":"ERROR","reason":"shutdown_complete"}\n',
        '{"event":"server_log","level":"INFO","reason":"shutdown_waiting"}\n',
        '{"event":"server_log","level":"ERROR","reason":"shutdown_failed"}\n',
        SUCCESS + '{"event":"server_log","level":"ERROR","reason":"shutdown_failed"}\n',
        SUCCESS + "not-json\n",
        SUCCESS + "[]\n",
        SUCCESS + "null\n",
    ],
)
def test_exact_runtime_shutdown_query_rejects_missing_or_contradictory_success(
    source: str,
) -> None:
    workflow: dict[str, Any] = YAML(typ="safe").load(
        (ROOT / ".github/workflows/runtime-image-qualification.yml").read_text()
    )
    step = next(
        step
        for step in workflow["jobs"]["qualify"]["steps"]
        if step.get("name") == "Qualify read-only runtime and PostgreSQL TLS"
    )
    query = step["env"]["SHUTDOWN_LOG_QUERY"]
    assert type(query) is str
    command = 'jq -e -s "${SHUTDOWN_LOG_QUERY}" "${root}/shutdown.log" > /dev/null'
    assert command in step["run"]
    assert step["run"].index('test -s "${root}/shutdown.log"') < step["run"].index(command)
    assert step["run"].index('test "$(wc -c < "${root}/shutdown.log")" -le 1048576') < step[
        "run"
    ].index(command)
    executable = shutil.which("jq")
    assert executable is not None, "Native runtime qualification witness requires jq"

    positive = spawn(
        executable,
        ("-e", "-s", query),
        cwd=ROOT,
        env={},
        input_text=SUCCESS,
        timeout_seconds=5,
        max_buffer=4096,
    )
    assert positive.error is None and positive.status == 0, positive
    assert positive.stdout.strip() == "true"

    negative = spawn(
        executable,
        ("-e", "-s", query),
        cwd=ROOT,
        env={},
        input_text=source,
        timeout_seconds=5,
        max_buffer=4096,
    )
    assert negative.error is None, negative
    assert negative.status is not None and negative.status != 0, negative

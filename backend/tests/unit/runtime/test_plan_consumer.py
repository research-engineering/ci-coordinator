from __future__ import annotations

import base64
import json
import stat
from pathlib import Path

import pytest
from plan_consumer_support import plan_transport_environment, run_plan_consumer

_VALID_PLAN = b'{"schemaVersion":"dynamic-ci-plan/v1","planId":"plan-1"}\n'


@pytest.mark.parametrize(
    ("environment", "reason"),
    [
        ({}, "plan_request_unavailable"),
        ({"CI_COORDINATOR_PLAN_REASON": "coordinator_timeout"}, "coordinator_timeout"),
        ({"CI_COORDINATOR_PLAN_REASON": "INVALID-REASON"}, "plan_request_unavailable"),
    ],
    ids=("missing-output", "request-fallback", "invalid-reason"),
)
def test_plan_consumer_maps_absent_transport_to_private_fallback(
    tmp_path: Path,
    environment: dict[str, str],
    reason: str,
) -> None:
    completed, plan_path = run_plan_consumer(tmp_path, environment)

    assert completed.returncode == 0
    assert json.loads(plan_path.read_bytes()) == {
        "schemaVersion": "dynamic-ci-plan/v0",
        "fallback": True,
        "reason": reason,
    }
    assert stat.S_IMODE(plan_path.stat().st_mode) == 0o600


def test_plan_consumer_preserves_exact_signed_plan_bytes(tmp_path: Path) -> None:
    completed, plan_path = run_plan_consumer(
        tmp_path,
        plan_transport_environment(_VALID_PLAN),
    )

    assert completed.returncode == 0, completed.stderr
    assert plan_path.read_bytes() == _VALID_PLAN
    assert stat.S_IMODE(plan_path.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("environment", "reason"),
    [
        (
            {
                "CI_COORDINATOR_PLAN_REASON": "signed_plan_available",
                "CI_COORDINATOR_PLAN_CHUNK_0": "eA",
                "CI_COORDINATOR_PLAN_CHUNK_2": "eA",
            },
            "plan_transport_chunks_invalid",
        ),
        (
            {
                "CI_COORDINATOR_PLAN_REASON": "signed_plan_available",
                "CI_COORDINATOR_PLAN_CHUNK_0": "%",
            },
            "plan_transport_chunks_invalid",
        ),
        (
            {
                "CI_COORDINATOR_PLAN_REASON": "signed_plan_available",
                "CI_COORDINATOR_PLAN_CHUNK_0": "A",
            },
            "plan_transport_encoding_invalid",
        ),
        (
            {
                "CI_COORDINATOR_PLAN_REASON": "coordinator_timeout",
                "CI_COORDINATOR_PLAN_CHUNK_0": "eA",
            },
            "plan_transport_state_invalid",
        ),
        (
            {
                "CI_COORDINATOR_PLAN_REASON": "signed_plan_available",
                "CI_COORDINATOR_PLAN_CHUNK_0": base64.urlsafe_b64encode(b"not-gzip")
                .rstrip(b"=")
                .decode(),
            },
            "plan_transport_compression_invalid",
        ),
        (
            plan_transport_environment(b"[]\n"),
            "coordinator_plan_response_invalid",
        ),
        (
            plan_transport_environment(b"\xff"),
            "coordinator_plan_response_invalid",
        ),
        (
            plan_transport_environment(b" " * 1_048_577),
            "plan_transport_compression_invalid",
        ),
    ],
    ids=(
        "chunk-hole",
        "invalid-alphabet",
        "noncanonical-base64url",
        "reason-chunk-conflict",
        "invalid-gzip",
        "non-object-json",
        "invalid-utf8",
        "decompressed-bound",
    ),
)
def test_plan_consumer_rejects_malformed_or_excessive_transport(
    tmp_path: Path,
    environment: dict[str, str],
    reason: str,
) -> None:
    completed, plan_path = run_plan_consumer(tmp_path, environment)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(plan_path.read_bytes()) == {
        "schemaVersion": "dynamic-ci-plan/v0",
        "fallback": True,
        "reason": reason,
    }


def test_plan_consumer_rejects_an_oversized_environment_chunk(tmp_path: Path) -> None:
    completed, plan_path = run_plan_consumer(
        tmp_path,
        {
            "CI_COORDINATOR_PLAN_REASON": "signed_plan_available",
            "CI_COORDINATOR_PLAN_CHUNK_0": "A" * 65_537,
        },
    )

    assert completed.returncode == 0
    assert json.loads(plan_path.read_bytes())["reason"] == "plan_transport_chunks_invalid"


def test_plan_consumer_atomically_replaces_without_following_a_plan_path_symlink(
    tmp_path: Path,
) -> None:
    protected = tmp_path / "protected.json"
    protected.write_text("protected\n", encoding="utf-8")
    (tmp_path / "plan.json").symlink_to(protected)

    completed, plan_path = run_plan_consumer(
        tmp_path,
        plan_transport_environment(_VALID_PLAN),
    )

    assert completed.returncode == 0
    assert protected.read_text(encoding="utf-8") == "protected\n"
    assert not plan_path.is_symlink()
    assert plan_path.read_bytes() == _VALID_PLAN
    assert stat.S_IMODE(plan_path.stat().st_mode) == 0o600

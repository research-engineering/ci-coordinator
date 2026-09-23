from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

import psycopg

from scripts.ci_business_witness.fixture import ACTOR, ISSUER, SUBJECT

STAGES = (
    "authenticated",
    "committed",
    "reloaded",
    "replayed",
    "operation-conflict",
    "revision-conflict",
    "csrf-rejected",
    "unauthenticated",
    "canary-rejected",
)
CONFIGURATION = {
    "enabled": True,
    "selector": {
        "sampleKey": "connected-sample",
        "producerDigest": "a" * 64,
        "method": "waited_children/v1",
        "runnerClassDigest": None,
    },
    "counter": "elapsed",
    "maximumUs": 123456789,
}


class WitnessFailure(RuntimeError):
    pass


def require(value: bool, code: str) -> None:
    if not value:
        raise WitnessFailure(code)


def admit_logs(data: bytes, canaries: tuple[bytes, ...]) -> None:
    require(bool(data) and len(data) <= 4 * 1024 * 1024, "log-population")
    require(bool(canaries) and all(canaries), "canary-population")
    require(not any(value in data for value in canaries), "secret-disclosure")


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def verify_checkpoint(
    connection: psycopg.Connection[tuple[object, ...]],
    document: Mapping[str, object],
    *,
    stage: str,
    policy_key: str,
) -> str:
    require(
        document.get("stage") == stage and document.get("actorId") == ACTOR, "checkpoint-identity"
    )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolname, rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
            "FROM pg_roles WHERE rolname IN "
            "('ci_coordinator_runtime','ci_coordinator_migration') ORDER BY rolname"
        )
        require(
            cursor.fetchall()
            == [
                ("ci_coordinator_migration", False, False, False, False, False),
                ("ci_coordinator_runtime", False, False, False, False, False),
            ],
            "database-role-boundary",
        )
        cursor.execute(
            "SELECT issuer, subject, actor_id FROM ci_coordinator.control_plane_sessions"
        )
        expected_sessions = (
            [] if stage in {"unauthenticated", "canary-rejected"} else [(ISSUER, SUBJECT, ACTOR)]
        )
        require(cursor.fetchall() == expected_sessions, "durable-session")
        cursor.execute(
            "SELECT installation_id, repository_id, policy_key, revision, "
            "policy_digest, policy_canonical "
            "FROM ci_coordinator.ci_economics_budget_policies"
        )
        policies = cursor.fetchall()
        cursor.execute(
            "SELECT idempotency_key, subject_id, actor, payload_canonical_json "
            "FROM ci_coordinator.audit_events WHERE event_type=%s",
            (b"ci-economics-budget-configured/v1",),
        )
        events = cursor.fetchall()
    if stage == "authenticated":
        require(policies == [] and events == [], "initial-state")
        return "empty"
    command = document.get("command")
    require(type(command) is dict, "command-shape")
    if not isinstance(command, dict):
        raise WitnessFailure("command-shape")
    operation = command.get("operationId")
    require(
        type(operation) is str and re.fullmatch(r"[a-f0-9-]{36}", operation) is not None,
        "operation-identity",
    )
    expected_command = {
        "installationId": 1,
        "repositoryId": 1,
        "policyKey": policy_key,
        "expectedRevision": 0,
        "operationId": operation,
        "configuration": CONFIGURATION,
    }
    require(command == expected_command, "independent-command-values")
    policy = {
        "schemaVersion": "ci-economics-budget-policy/v1",
        "installationId": 1,
        "repositoryId": 1,
        "policyKey": policy_key,
        "revision": 1,
        "configuration": CONFIGURATION,
    }
    policy_bytes = canonical(policy)
    digest = hashlib.sha256(policy_bytes).hexdigest()
    require(
        policies == [(1, 1, policy_key, 1, digest, policy_bytes)], "policy-bytes-or-cardinality"
    )
    command_digest = hashlib.sha256(
        canonical(
            {
                **expected_command,
                "schemaVersion": "ci-economics-budget-command/v1",
                "actor": ACTOR,
            }
        )
    ).hexdigest()
    payload = canonical({"commandDigest": command_digest, "policy": policy})
    require(
        events
        == [
            (
                f"ci-economics-budget:1:1:{operation}".encode(),
                f"ci-budget:1:1:{policy_key}".encode(),
                ACTOR.encode(),
                payload,
            )
        ],
        "audit-bytes-or-cardinality",
    )
    return digest

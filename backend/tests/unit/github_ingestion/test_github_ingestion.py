from __future__ import annotations

import asyncio
import hmac
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import cast

import pytest

from ci_coordinator.github_ingestion import (
    DeliveryClaimed,
    DeliveryConflict,
    DeliveryDuplicate,
    DeliveryIdempotencyKey,
    DeliveryIdempotencyResult,
    DeliveryStoreUnavailable,
    DeliveryUnavailable,
    DuplicateDelivery,
    FullCiInvalidatingRange,
    IngestionRejection,
    IngestionResult,
    MergeGroupSeed,
    NoopDelivery,
    PingDelivery,
    PreparedWebhookIngestion,
    PullRequestSeed,
    SeedIngestion,
    UnsupportedWebhook,
    WebhookIngestionProfile,
    WorkflowJobObservation,
    WorkflowRunObservation,
    complete_webhook_ingestion,
    load_bundled_profile,
    parse_profile,
    prepare_trusted_webhook_ingestion,
)
from ci_coordinator.github_ingestion.events import (
    DurableWorkflowObservation,
    NormalizedWorkflowJobEvent,
    NormalizedWorkflowRunEvent,
)
from ci_coordinator.github_ingestion.ports import PreparedDeliveryClaim
from ci_coordinator.identity_admission import (
    RejectedIdentity,
    TrustedWebhook,
    verify_webhook_at,
)
from ci_coordinator.kernel import sha256_hex

BASE_SHA = "a" * 40
HEAD_SHA = "b" * 40
NOW = datetime(2026, 7, 13, 12, 0, tzinfo=UTC)
SECRET = "webhook-test-secret"
ADMITTED_COMPLETED_CONCLUSIONS = (
    "success",
    "failure",
    "cancelled",
    "timed_out",
    "skipped",
    "neutral",
    "action_required",
    "startup_failure",
    "stale",
)


@dataclass
class FakeDeliveryIdempotency:
    result: DeliveryIdempotencyResult | BaseException
    claimed_keys: list[PreparedDeliveryClaim] = field(default_factory=list)
    observations: list[DurableWorkflowObservation | None] = field(default_factory=list)

    async def commit(
        self,
        key: PreparedDeliveryClaim,
        observation: DurableWorkflowObservation | None,
    ) -> DeliveryIdempotencyResult:
        self.claimed_keys.append(key)
        self.observations.append(observation)
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


def test_bundled_profile_is_byte_identical_to_its_spec_owner() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    source_profile = (
        repository_root / "docs/specs/ci-coordinator-runtime/webhook-ingestion-profile.v2.json"
    )
    bundled_profile = (
        repository_root
        / "backend/src/ci_coordinator/github_ingestion/resources/webhook-ingestion-profile.v2.json"
    )

    assert bundled_profile.read_bytes() == source_profile.read_bytes()
    profile = load_bundled_profile()
    assert profile.limits.maximum_body_bytes == 25 * 1024 * 1024
    assert profile.provider_payload_cap_source_url.startswith("https://")
    assert profile.action_decision("pull_request", "closed") == "not_admitted"
    assert profile.action_decision("ping", None) == "admitted"
    assert profile.action_decision("ping", "completed") == "not_admitted"
    assert "incomplete_workflow_job" in profile.stable_codes


@pytest.mark.parametrize(
    "mutation",
    ["unknown_root_key", "unimplemented_event", "old_version"],
)
def test_profile_rejects_unmodeled_or_unsupported_v2_contracts(
    mutation: str,
) -> None:
    repository_root = Path(__file__).resolve().parents[4]
    profile_path = (
        repository_root / "docs/specs/ci-coordinator-runtime/webhook-ingestion-profile.v2.json"
    )
    profile = cast(dict[str, object], json.loads(profile_path.read_text(encoding="utf-8")))
    if mutation == "unknown_root_key":
        profile["unmodeledV1Field"] = True
    elif mutation == "old_version":
        profile["schemaVersion"] = "ci-webhook-ingestion-profile/v1"
    else:
        event_actions = cast(dict[str, object], profile["eventActions"])
        event_actions["future_event"] = [None]

    with pytest.raises(ValueError):
        parse_profile(json.dumps(profile, separators=(",", ":")).encode())


@pytest.mark.parametrize("repository_scoped", (False, True))
@pytest.mark.parametrize("hook_id", (1, 7, 2**53 - 1))
def test_ping_is_durably_acknowledged_without_repository_or_planning_authority(
    repository_scoped: bool,
    hook_id: int,
) -> None:
    payload = ping_payload()
    payload.update(hook_id=hook_id, hook={"id": hook_id})
    if repository_scoped:
        payload.update(
            installation={"id": 1},
            repository={"id": 2, "owner": {"login": "example"}, "name": "ci"},
        )
    body = json.dumps(payload).encode()
    ledger = ledger_for(body)
    result = asyncio.run(
        ingest_trusted_webhook(
            trusted_webhook(body, event_name="ping"), body, load_bundled_profile(), ledger
        )
    )
    assert isinstance(result, PingDelivery)
    assert result.provenance.body_sha256 == sha256_hex(body)
    assert result.provenance.event_name == "ping"
    assert ledger.claimed_keys[0].key == DeliveryIdempotencyKey("delivery-1", sha256_hex(body))
    assert len(ledger.claimed_keys) == 1
    assert ledger.observations == [None]


@pytest.mark.parametrize(
    "field,value",
    (
        ("zen", None),
        ("zen", 1),
        ("hook_id", True),
        ("hook_id", 0),
        ("hook_id", "7"),
        ("hook_id", 2**53),
        ("hook", None),
        ("hook", []),
        ("hook", {}),
        ("hook", {"id": True}),
        ("hook", {"id": 8}),
        ("action", "completed"),
        ("action", False),
    ),
)
def test_invalid_ping_core_never_claims_a_delivery(field: str, value: object) -> None:
    payload = ping_payload()
    payload[field] = value
    body = json.dumps(payload).encode()
    ledger = ledger_for(body)
    result = asyncio.run(
        ingest_trusted_webhook(
            trusted_webhook(body, event_name="ping"), body, load_bundled_profile(), ledger
        )
    )
    assert isinstance(result, IngestionRejection)
    assert ledger.claimed_keys == []


@pytest.mark.parametrize("field", ("zen", "hook_id", "hook"))
def test_ping_requires_each_core_field(field: str) -> None:
    payload = ping_payload()
    del payload[field]
    body = json.dumps(payload).encode()
    result = prepare_trusted_webhook_ingestion(
        trusted_webhook(body, event_name="ping"), body, load_bundled_profile()
    )
    assert isinstance(result, IngestionRejection)


@pytest.mark.parametrize(
    "hook_id,nested_id",
    ((7, 7.0), (7.0, 7), (1, True), (True, 1), (0, 0), (-1, -1)),
)
def test_ping_numeric_equality_does_not_replace_exact_positive_ids(
    hook_id: object, nested_id: object
) -> None:
    payload = ping_payload()
    payload.update(hook_id=hook_id, hook={"id": nested_id})
    body = json.dumps(payload).encode()
    ledger = ledger_for(body)
    result = asyncio.run(
        ingest_trusted_webhook(
            trusted_webhook(body, event_name="ping"), body, load_bundled_profile(), ledger
        )
    )
    assert isinstance(result, IngestionRejection)
    assert result.code == "invalid_webhook_body"
    assert ledger.claimed_keys == []


def test_ping_profile_veto_rejects_before_delivery_claim() -> None:
    profile = load_bundled_profile()
    restricted = replace(
        profile,
        event_actions=tuple(
            replace(policy, allowed_actions=("completed",))
            if policy.event_name == "ping"
            else policy
            for policy in profile.event_actions
        ),
    )
    assert restricted.action_decision("ping", None) == "not_admitted"
    body = json.dumps(ping_payload()).encode()
    ledger = ledger_for(body)
    result = asyncio.run(
        ingest_trusted_webhook(trusted_webhook(body, event_name="ping"), body, restricted, ledger)
    )
    assert isinstance(result, IngestionRejection)
    assert result.code == "invalid_webhook_body"
    assert ledger.claimed_keys == []


@pytest.mark.parametrize("zen", ("", "bounded"))
def test_ping_admits_empty_zen_null_action_and_inert_optional_metadata(zen: str) -> None:
    payload = ping_payload()
    payload.update(zen=zen, action=None, sender={"login": "example"}, organization={"id": 3})
    body = json.dumps(payload).encode()
    result = prepare_trusted_webhook_ingestion(
        trusted_webhook(body, event_name="ping"), body, load_bundled_profile()
    )
    assert isinstance(result, PreparedWebhookIngestion)
    assert isinstance(result.outcome, PingDelivery)


@pytest.mark.parametrize(
    "family", ("push", "pull_request", "merge_group", "workflow_job", "workflow_run")
)
def test_ping_and_ci_payload_families_cannot_be_relabelled(family: str) -> None:
    payload = ping_payload()
    payload.update(
        installation={"id": 1}, repository={"id": 2, "owner": {"login": "example"}, "name": "ci"}
    )
    ping_body = json.dumps(payload).encode()
    assert isinstance(
        prepare_trusted_webhook_ingestion(
            trusted_webhook(ping_body, event_name=family), ping_body, load_bundled_profile()
        ),
        IngestionRejection,
    )
    if family == "push":
        payload.update(ref="refs/heads/main", before=BASE_SHA, after=HEAD_SHA, deleted=False)
    else:
        payload[family] = {}
    body = json.dumps(payload).encode()
    for event_name in ("ping", family):
        ledger = ledger_for(body)
        result = asyncio.run(
            ingest_trusted_webhook(
                trusted_webhook(body, event_name=event_name), body, load_bundled_profile(), ledger
            )
        )
        assert isinstance(result, IngestionRejection)
        assert result.code == "invalid_webhook_body"
        assert ledger.claimed_keys == []


@pytest.mark.parametrize("outcome", ("duplicate", "conflict", "unavailable", "cancelled"))
def test_ping_preserves_durable_delivery_outcomes(outcome: str) -> None:
    body = json.dumps(ping_payload()).encode()
    ledger = ledger_for(body)
    match outcome:
        case "duplicate":
            ledger.result = DeliveryDuplicate(
                DeliveryIdempotencyKey("delivery-1", sha256_hex(body))
            )
        case "conflict":
            ledger.result = DeliveryConflict("delivery-1", "f" * 64)
        case "unavailable":
            ledger.result = DeliveryStoreUnavailable()
        case "cancelled":
            ledger.result = asyncio.CancelledError()
    operation = ingest_trusted_webhook(
        trusted_webhook(body, event_name="ping"), body, load_bundled_profile(), ledger
    )
    if outcome == "cancelled":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(operation)
    else:
        result = asyncio.run(operation)
        if outcome == "duplicate":
            assert isinstance(result, DuplicateDelivery)
        elif outcome == "conflict":
            assert isinstance(result, IngestionRejection)
            assert result.code == "delivery_id_conflict"
        else:
            assert isinstance(result, DeliveryUnavailable)
    assert len(ledger.claimed_keys) == 1
    assert ledger.observations == [None]


def ping_payload() -> dict[str, object]:
    return {
        "zen": "Keep it logically bounded.",
        "hook_id": 7,
        "hook": {
            "type": "App",
            "id": 7,
            "config": {"url": "https://untrusted.invalid/never-fetch"},
        },
    }


def test_body_mismatch_and_invalid_json_never_claim_a_delivery() -> None:
    valid_body = push_payload(action=None)
    mismatch_ledger = ledger_for(valid_body)
    mismatch = verify_webhook_at(
        dict(signed_headers(valid_body)),
        valid_body + b" ",
        SECRET,
        NOW,
    )
    malformed_body = b'{"installation":{"id":1}'
    malformed_ledger = ledger_for(malformed_body)
    malformed = asyncio.run(
        ingest_trusted_webhook(
            trusted_webhook(malformed_body),
            malformed_body,
            load_bundled_profile(),
            malformed_ledger,
        )
    )

    assert isinstance(mismatch, RejectedIdentity)
    assert mismatch.reason_code == "invalid_webhook_signature"
    assert mismatch_ledger.claimed_keys == []
    assert isinstance(malformed, IngestionRejection)
    assert malformed.code == "invalid_json"
    assert malformed_ledger.claimed_keys == []


def test_trusted_identity_cannot_be_reused_with_different_body_bytes() -> None:
    trusted_body = push_payload(action=None)
    different_body = trusted_body + b" "

    prepared = prepare_trusted_webhook_ingestion(
        trusted_webhook(trusted_body),
        different_body,
        load_bundled_profile(),
    )

    assert isinstance(prepared, IngestionRejection)
    assert prepared.code == "webhook_body_hash_mismatch"


def test_admitted_delivery_reaches_the_port_as_prepared_provenance() -> None:
    body = push_payload(action=None)
    ledger = ledger_for(body)

    result = asyncio.run(
        ingest_trusted_webhook(
            trusted_webhook(body),
            body,
            load_bundled_profile(),
            ledger,
        )
    )

    assert isinstance(result, SeedIngestion)
    assert len(ledger.claimed_keys) == 1
    prepared = ledger.claimed_keys[0]
    assert type(prepared) is PreparedDeliveryClaim
    assert prepared.key == DeliveryIdempotencyKey("delivery-1", sha256_hex(body))
    assert prepared.verified_at == NOW


def test_naive_verification_time_cannot_create_a_delivery_claim() -> None:
    body = push_payload(action=None)
    ledger = ledger_for(body)
    unbound_time = TrustedWebhook(
        delivery_id="delivery-1",
        event_name="push",
        body_sha256=sha256_hex(body),
        verified_at=datetime(2026, 7, 15, 12, 0),
    )

    result = asyncio.run(
        ingest_trusted_webhook(
            unbound_time,
            body,
            load_bundled_profile(),
            ledger,
        )
    )

    assert isinstance(result, IngestionRejection)
    assert result.code == "invalid_webhook_body"
    assert ledger.claimed_keys == []


def test_duplicate_and_store_unavailable_cannot_emit_a_second_seed() -> None:
    body = push_payload(action=None)
    key = DeliveryIdempotencyKey("delivery-1", sha256_hex(body))
    duplicate = asyncio.run(
        ingest_trusted_webhook(
            trusted_webhook(body),
            body,
            load_bundled_profile(),
            FakeDeliveryIdempotency(DeliveryDuplicate(key)),
        )
    )
    unavailable = asyncio.run(
        ingest_trusted_webhook(
            trusted_webhook(body),
            body,
            load_bundled_profile(),
            FakeDeliveryIdempotency(DeliveryStoreUnavailable()),
        )
    )

    assert isinstance(duplicate, DuplicateDelivery)
    assert isinstance(unavailable, DeliveryUnavailable)


def test_conflicting_delivery_id_is_not_silently_deduplicated() -> None:
    body = push_payload(action=None)
    result = asyncio.run(
        ingest_trusted_webhook(
            trusted_webhook(body),
            body,
            load_bundled_profile(),
            FakeDeliveryIdempotency(DeliveryConflict("delivery-1", "c" * 64)),
        )
    )

    assert isinstance(result, IngestionRejection)
    assert result.code == "delivery_id_conflict"


def test_non_admitted_action_is_an_explicit_noop_not_a_pull_request_seed() -> None:
    body = pull_request_payload(action="closed")

    result = asyncio.run(
        ingest_trusted_webhook(
            trusted_webhook(body, event_name="pull_request"),
            body,
            load_bundled_profile(),
            ledger_for(body),
        )
    )

    assert isinstance(result, NoopDelivery)
    assert result.reason_code == "event_action_not_admitted"
    assert not isinstance(result, SeedIngestion)


def test_admitted_pull_request_action_builds_a_sha_bound_seed() -> None:
    body = pull_request_payload(action="synchronize")

    result = asyncio.run(
        ingest_trusted_webhook(
            trusted_webhook(body, event_name="pull_request"),
            body,
            load_bundled_profile(),
            ledger_for(body),
        )
    )

    assert isinstance(result, SeedIngestion)
    assert isinstance(result.seed, PullRequestSeed)
    assert result.seed.base_sha == BASE_SHA
    assert result.seed.head_sha == HEAD_SHA
    assert result.seed.ref == "refs/pull/42/merge"


def test_merge_group_without_ref_is_full_ci_invalidating_not_a_synthetic_ref() -> None:
    body = (
        b'{"installation":{"id":1},"repository":{"id":2,"name":"ci",'
        b'"owner":{"login":"example"}},"action":"checks_requested",'
        b'"merge_group":{"base_sha":"'
        + BASE_SHA.encode()
        + b'","head_sha":"'
        + HEAD_SHA.encode()
        + b'"}}'
    )

    result = asyncio.run(
        ingest_trusted_webhook(
            trusted_webhook(body, event_name="merge_group"),
            body,
            load_bundled_profile(),
            ledger_for(body),
        )
    )

    assert isinstance(result, SeedIngestion)
    assert isinstance(result.seed, MergeGroupSeed)
    assert result.seed.ref is None
    assert isinstance(result.seed.range_binding, FullCiInvalidatingRange)
    assert result.seed.range_binding.reason_codes == ("missing_merge_group_head_ref",)


@pytest.mark.parametrize("conclusion", ADMITTED_COMPLETED_CONCLUSIONS)
def test_workflow_run_is_preserved_as_an_observation_without_issuing_a_plan(
    conclusion: str,
) -> None:
    body = workflow_run_payload(overrides={"conclusion": conclusion})

    ledger = ledger_for(body)
    result = asyncio.run(
        ingest_trusted_webhook(
            trusted_webhook(body, event_name="workflow_run"),
            body,
            load_bundled_profile(),
            ledger,
        )
    )

    assert isinstance(result, WorkflowRunObservation)
    assert result.event.workflow_run_id == 7
    assert result.event.run_attempt == 2
    assert result.event.workflow_id == 9
    assert result.event.head_sha == HEAD_SHA
    assert result.event.status == "completed"
    assert result.event.conclusion == conclusion
    assert result.event.created_at == datetime(2026, 7, 13, 11, 55, tzinfo=UTC)
    assert result.event.run_started_at == datetime(2026, 7, 13, 11, 56, tzinfo=UTC)
    assert result.event.updated_at == NOW
    assert len(ledger.claimed_keys) == 1
    assert len(ledger.observations) == 1
    assert isinstance(ledger.observations[0], NormalizedWorkflowRunEvent)


@pytest.mark.parametrize(
    ("overrides", "removed_fields"),
    (
        ({}, ("run_attempt",)),
        ({"run_attempt": 0}, ()),
        ({"run_attempt": "2"}, ()),
        ({"conclusion": "future_provider_conclusion"}, ()),
    ),
)
def test_completed_workflow_run_rejects_inexact_attempt_or_conclusion(
    overrides: dict[str, object],
    removed_fields: tuple[str, ...],
) -> None:
    body = workflow_run_payload(overrides=overrides, removed_fields=removed_fields)

    prepared = prepare_trusted_webhook_ingestion(
        trusted_webhook(body, event_name="workflow_run"),
        body,
        load_bundled_profile(),
    )

    assert isinstance(prepared, PreparedWebhookIngestion)
    assert isinstance(prepared.outcome, UnsupportedWebhook)
    assert prepared.outcome.reason_code == "incomplete_workflow_run"


def test_completed_workflow_job_is_strictly_normalized_for_telemetry() -> None:
    body = workflow_job_payload()

    prepared = prepare_trusted_webhook_ingestion(
        trusted_webhook(body, event_name="workflow_job"),
        body,
        load_bundled_profile(),
    )

    assert isinstance(prepared, PreparedWebhookIngestion)
    event = prepared.outcome
    assert isinstance(event, NormalizedWorkflowJobEvent)
    assert event.repository.installation_id == 1
    assert event.repository.repository_id == 2
    assert event.workflow_run_id == 7
    assert event.run_attempt == 2
    assert event.workflow_job_id == 11
    assert event.head_sha == HEAD_SHA
    assert event.status == "completed"
    assert event.conclusion == "success"
    assert event.created_at == datetime(2026, 7, 13, 11, 55, tzinfo=UTC)
    assert event.started_at == datetime(2026, 7, 13, 11, 56, tzinfo=UTC)
    assert event.completed_at == NOW
    assert event.labels == ("linux", "self-hosted", "x64")
    assert event.runner is not None
    assert event.runner.runner_id == 101
    assert event.runner.runner_name == "example-dev-01"
    assert event.runner.runner_group_id == 12
    assert event.runner.runner_group_name == "example dev"


@pytest.mark.parametrize(
    "conclusion",
    ADMITTED_COMPLETED_CONCLUSIONS,
)
def test_completed_workflow_job_preserves_every_admitted_conclusion(conclusion: str) -> None:
    body = workflow_job_payload(overrides={"conclusion": conclusion})

    prepared = prepare_trusted_webhook_ingestion(
        trusted_webhook(body, event_name="workflow_job"),
        body,
        load_bundled_profile(),
    )

    assert isinstance(prepared, PreparedWebhookIngestion)
    assert isinstance(prepared.outcome, NormalizedWorkflowJobEvent)
    assert prepared.outcome.conclusion == conclusion


def test_completed_workflow_job_commits_its_delivery_and_observation_together() -> None:
    body = workflow_job_payload()
    ledger = ledger_for(body)

    result = asyncio.run(
        ingest_trusted_webhook(
            trusted_webhook(body, event_name="workflow_job"),
            body,
            load_bundled_profile(),
            ledger,
        )
    )

    assert isinstance(result, WorkflowJobObservation)
    assert len(ledger.claimed_keys) == 1
    assert len(ledger.observations) == 1
    assert isinstance(ledger.observations[0], NormalizedWorkflowJobEvent)


def test_non_completed_workflow_job_is_an_explicit_noop() -> None:
    body = workflow_job_payload(
        action="queued",
        overrides={"status": "queued", "conclusion": None},
    )

    prepared = prepare_trusted_webhook_ingestion(
        trusted_webhook(body, event_name="workflow_job"),
        body,
        load_bundled_profile(),
    )

    assert isinstance(prepared, PreparedWebhookIngestion)
    assert isinstance(prepared.outcome, NoopDelivery)
    assert prepared.outcome.reason_code == "event_action_not_admitted"


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "in_progress"},
        {"conclusion": None},
        {"run_id": 0},
        {"run_attempt": 0},
        {"head_sha": "not-a-sha"},
        {"name": ""},
        {"created_at": "2026-07-13T11:57:00Z"},
        {"started_at": "2026-07-13T12:01:00Z"},
        {"completed_at": "2026-07-13T12:00:00"},
        {"name": "n" * 513},
        {"labels": [f"label-{index}" for index in range(33)]},
        {"labels": ["x" * 129]},
        {"labels": ["linux", "linux"]},
        {"runner_name": None},
        {"runner_name": "r" * 257},
        {"runner_group_name": None},
        {"runner_group_name": "g" * 257},
    ],
)
def test_malformed_workflow_job_fails_closed(overrides: dict[str, object]) -> None:
    body = workflow_job_payload(overrides=overrides)

    prepared = prepare_trusted_webhook_ingestion(
        trusted_webhook(body, event_name="workflow_job"),
        body,
        load_bundled_profile(),
    )

    assert isinstance(prepared, PreparedWebhookIngestion)
    assert isinstance(prepared.outcome, UnsupportedWebhook)
    assert prepared.outcome.reason_code == "incomplete_workflow_job"


def test_completed_workflow_job_may_have_no_assigned_runner() -> None:
    body = workflow_job_payload(
        overrides={
            "runner_id": None,
            "runner_name": None,
            "runner_group_id": None,
            "runner_group_name": None,
        }
    )

    prepared = prepare_trusted_webhook_ingestion(
        trusted_webhook(body, event_name="workflow_job"),
        body,
        load_bundled_profile(),
    )

    assert isinstance(prepared, PreparedWebhookIngestion)
    assert isinstance(prepared.outcome, NormalizedWorkflowJobEvent)
    assert prepared.outcome.runner is None


def test_unsupported_event_and_invalid_sha_are_explicit_results() -> None:
    unsupported_body = (
        b'{"installation":{"id":1},"repository":{"id":2,"name":"ci",'
        b'"owner":{"login":"example"}},"issue":{"number":7}}'
    )
    unsupported = asyncio.run(
        ingest_trusted_webhook(
            trusted_webhook(unsupported_body, event_name="issues"),
            unsupported_body,
            load_bundled_profile(),
            ledger_for(unsupported_body),
        )
    )
    invalid_sha_body = push_payload(action=None, head_sha="not-a-git-sha")
    invalid_sha = asyncio.run(
        ingest_trusted_webhook(
            trusted_webhook(invalid_sha_body),
            invalid_sha_body,
            load_bundled_profile(),
            ledger_for(invalid_sha_body),
        )
    )

    assert isinstance(unsupported, UnsupportedWebhook)
    assert unsupported.reason_code == "unsupported_event"
    assert isinstance(invalid_sha, UnsupportedWebhook)
    assert invalid_sha.reason_code == "incomplete_push"


def test_event_header_cannot_reinterpret_a_different_payload_family() -> None:
    body = push_payload(action=None)
    ledger = ledger_for(body)

    result = asyncio.run(
        ingest_trusted_webhook(
            trusted_webhook(body, event_name="pull_request"),
            body,
            load_bundled_profile(),
            ledger,
        )
    )

    assert isinstance(result, IngestionRejection)
    assert result.code == "invalid_webhook_body"
    assert ledger.claimed_keys == []


def test_actual_task_cancellation_and_unexpected_port_failures_propagate() -> None:
    body = push_payload(action=None)
    profile = load_bundled_profile()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            ingest_trusted_webhook(
                trusted_webhook(body),
                body,
                profile,
                FakeDeliveryIdempotency(asyncio.CancelledError()),
            )
        )
    with pytest.raises(RuntimeError, match="store bug"):
        asyncio.run(
            ingest_trusted_webhook(
                trusted_webhook(body),
                body,
                profile,
                FakeDeliveryIdempotency(RuntimeError("store bug")),
            )
        )


def trusted_webhook(body: bytes, *, event_name: str = "push") -> TrustedWebhook:
    return TrustedWebhook(
        delivery_id="delivery-1",
        event_name=event_name,
        body_sha256=sha256_hex(body),
        verified_at=NOW,
    )


async def ingest_trusted_webhook(
    trusted: TrustedWebhook,
    body: bytes,
    profile: WebhookIngestionProfile,
    deliveries: FakeDeliveryIdempotency,
) -> IngestionResult | RejectedIdentity:
    admitted = verify_webhook_at(
        dict(
            signed_headers(
                body,
                delivery_id=trusted.delivery_id,
                event_name=trusted.event_name,
            )
        ),
        body,
        SECRET,
        trusted.verified_at,
    )
    if isinstance(admitted, RejectedIdentity):
        return admitted
    prepared = prepare_trusted_webhook_ingestion(
        admitted,
        body,
        profile,
    )
    if not isinstance(prepared, PreparedWebhookIngestion):
        return prepared
    return await complete_webhook_ingestion(prepared, deliveries)


def signed_headers(
    body: bytes,
    *,
    delivery_id: str | None = "delivery-1",
    event_name: str | None = "push",
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    digest = hmac.new(SECRET.encode(), body, sha256).hexdigest()
    values = {
        "x-hub-signature-256": (f"sha256={digest}",),
    }
    if delivery_id is not None:
        values["x-github-delivery"] = (delivery_id,)
    if event_name is not None:
        values["x-github-event"] = (event_name,)
    return tuple(sorted(values.items()))


def ledger_for(body: bytes) -> FakeDeliveryIdempotency:
    return FakeDeliveryIdempotency(
        DeliveryClaimed(DeliveryIdempotencyKey("delivery-1", sha256_hex(body)))
    )


def push_payload(*, action: str | None, head_sha: str = HEAD_SHA) -> bytes:
    action_field = b"" if action is None else b'"action":"' + action.encode() + b'",'
    return (
        b'{"installation":{"id":1},"repository":{"id":2,"name":"ci",'
        b'"owner":{"login":"example"}},'
        + action_field
        + b'"ref":"refs/heads/main","before":"'
        + BASE_SHA.encode()
        + b'","after":"'
        + head_sha.encode()
        + b'","deleted":false}'
    )


def pull_request_payload(*, action: str) -> bytes:
    return (
        b'{"installation":{"id":1},"repository":{"id":2,"name":"ci",'
        b'"owner":{"login":"example"}},"action":"'
        + action.encode()
        + b'","number":42,"pull_request":{"base":{"sha":"'
        + BASE_SHA.encode()
        + b'"},"head":{"sha":"'
        + HEAD_SHA.encode()
        + b'"}}}'
    )


def workflow_job_payload(
    *,
    action: str = "completed",
    overrides: dict[str, object] | None = None,
) -> bytes:
    workflow_job: dict[str, object] = {
        "id": 11,
        "run_id": 7,
        "run_attempt": 2,
        "head_sha": HEAD_SHA,
        "name": "backend / test",
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-07-13T11:55:00Z",
        "started_at": "2026-07-13T11:56:00Z",
        "completed_at": "2026-07-13T12:00:00Z",
        "labels": ["self-hosted", "linux", "x64"],
        "runner_id": 101,
        "runner_name": "example-dev-01",
        "runner_group_id": 12,
        "runner_group_name": "example dev",
    }
    workflow_job.update(overrides or {})
    return json.dumps(
        {
            "installation": {"id": 1},
            "repository": {"id": 2, "name": "ci", "owner": {"login": "example"}},
            "action": action,
            "workflow_job": workflow_job,
        },
        separators=(",", ":"),
    ).encode()


def workflow_run_payload(
    *,
    overrides: dict[str, object] | None = None,
    removed_fields: tuple[str, ...] = (),
) -> bytes:
    workflow_run: dict[str, object] = {
        "id": 7,
        "run_attempt": 2,
        "workflow_id": 9,
        "name": "test",
        "path": ".github/workflows/test.yml",
        "head_sha": HEAD_SHA,
        "event": "pull_request",
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-07-13T11:55:00Z",
        "run_started_at": "2026-07-13T11:56:00Z",
        "updated_at": "2026-07-13T12:00:00Z",
    }
    workflow_run.update(overrides or {})
    for field_name in removed_fields:
        workflow_run.pop(field_name)
    return json.dumps(
        {
            "installation": {"id": 1},
            "repository": {"id": 2, "name": "ci", "owner": {"login": "example"}},
            "action": "completed",
            "workflow_run": workflow_run,
        },
        separators=(",", ":"),
    ).encode()

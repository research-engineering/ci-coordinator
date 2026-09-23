from __future__ import annotations

import hmac
import json
from datetime import UTC, datetime

import pytest

from ci_coordinator.github_ingestion import (
    IngestionRejection,
    PreparedWebhookIngestion,
    UnsupportedWebhook,
    load_bundled_profile,
    prepare_trusted_webhook_ingestion,
)
from ci_coordinator.github_ingestion.events import NormalizedWorkflowJobEvent
from ci_coordinator.identity_admission import TrustedWebhook, verify_webhook_at
from ci_coordinator.integrations.github.reconciliation_observer_decoding import (
    JobPage,
    decode_job_page,
)

type Reference = tuple[int | None, str | None, int | None, str | None]

_FIELDS = ("runner_id", "runner_name", "runner_group_id", "runner_group_name")
_POSITIVE: Reference = (101, "runner", 12, "group")
_ABSENT: Reference = (None, None, None, None)
_NOW = datetime(2026, 9, 9, 12, tzinfo=UTC)


def _admit(
    references: dict[str, object],
) -> tuple[PreparedWebhookIngestion | IngestionRejection, JobPage | None]:
    job: dict[str, object] = {
        "id": 11,
        "run_id": 7,
        "run_attempt": 1,
        "head_sha": "b" * 40,
        "name": "tests",
        "status": "completed",
        "conclusion": "success",
        "created_at": "2026-09-09T11:55:00Z",
        "started_at": "2026-09-09T11:56:00Z",
        "completed_at": "2026-09-09T12:00:00Z",
        "labels": ["ubuntu-24.04"],
        **references,
    }
    body = json.dumps(
        {
            "installation": {"id": 1},
            "repository": {"id": 2, "name": "service", "owner": {"login": "acme"}},
            "action": "completed",
            "workflow_job": job,
        }
    ).encode()
    secret = "runner-reference-fixture"
    trusted = verify_webhook_at(
        {
            "x-hub-signature-256": "sha256=" + hmac.digest(secret.encode(), body, "sha256").hex(),
            "x-github-delivery": "runner-reference-delivery",
            "x-github-event": "workflow_job",
        },
        body,
        secret,
        _NOW,
    )
    assert isinstance(trusted, TrustedWebhook)
    return (
        prepare_trusted_webhook_ingestion(trusted, body, load_bundled_profile()),
        decode_job_page(
            json.dumps({"total_count": 1, "jobs": [job]}).encode(),
            expected_run_id=7,
            expected_head_sha="b" * 40,
        ),
    )


def _assert_reference(references: dict[str, object], expected: Reference) -> None:
    webhook, page = _admit(references)
    assert isinstance(webhook, PreparedWebhookIngestion)
    assert isinstance(webhook.outcome, NormalizedWorkflowJobEvent)
    assert page is not None and page.total_count == 1 and len(page.jobs) == 1
    for runner in (webhook.outcome.runner, page.jobs[0].runner):
        actual = (
            (runner.runner_id, runner.runner_name, runner.runner_group_id, runner.runner_group_name)
            if runner is not None
            else _ABSENT
        )
        assert actual == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param(_POSITIVE, _POSITIVE, id="self-hosted"),
        pytest.param(
            (101, "hosted", 0, "GitHub Actions"), (101, "hosted", None, None), id="hosted"
        ),
        pytest.param(
            (101, "runner", 0, "arbitrary label"), (101, "runner", None, None), id="no-label-policy"
        ),
        pytest.param((101, "runner", 0, ""), (101, "runner", None, None), id="empty-group"),
        pytest.param((101, "runner", 0, None), (101, "runner", None, None), id="null-group-name"),
        pytest.param((0, "", 0, ""), _ABSENT, id="unassigned"),
        pytest.param((0, "display label", 0, "group label"), _ABSENT, id="zero-is-not-identity"),
        pytest.param((0, None, None, None), _ABSENT, id="zero-null"),
        pytest.param((None, "", None, ""), _ABSENT, id="null-empty"),
        pytest.param(_ABSENT, _ABSENT, id="absent"),
        pytest.param(
            (101, "r" * 256, 12, "g" * 256), (101, "r" * 256, 12, "g" * 256), id="name-bound"
        ),
        pytest.param(
            (2**53 - 1, "r", 2**53 - 1, "g"), (2**53 - 1, "r", 2**53 - 1, "g"), id="id-bound"
        ),
    ],
)
def test_runner_references_agree_across_rest_and_webhook_and_normalize_idempotently(
    raw: Reference, expected: Reference
) -> None:
    _assert_reference(dict(zip(_FIELDS, raw, strict=True)), expected)
    _assert_reference(dict(zip(_FIELDS, expected, strict=True)), expected)


def test_missing_runner_references_are_absent_at_both_boundaries() -> None:
    _assert_reference({}, _ABSENT)


def _assert_unsupported(references: dict[str, object]) -> None:
    webhook, page = _admit(references)
    assert page is None
    assert isinstance(webhook, PreparedWebhookIngestion)
    assert isinstance(webhook.outcome, UnsupportedWebhook)
    assert webhook.outcome.reason_code == "incomplete_workflow_job"


@pytest.mark.parametrize("field", ["runner_id", "runner_group_id"])
@pytest.mark.parametrize("value", [False, True, 0.0, 1.5, -1, "0", [], {}])
def test_runner_reference_ids_reject_non_integer_or_out_of_range_values(
    field: str, value: object
) -> None:
    _assert_unsupported({**dict(zip(_FIELDS, _POSITIVE, strict=True)), field: value})


@pytest.mark.parametrize(
    ("field", "control", "expected"),
    [
        ("runner_name", (0, "", None, None), _ABSENT),
        ("runner_group_name", (101, "runner", 0, ""), (101, "runner", None, None)),
    ],
)
@pytest.mark.parametrize("value", ["x" * 257, False, 0, [], {}])
def test_zero_reference_does_not_hide_invalid_name(
    field: str, control: Reference, expected: Reference, value: object
) -> None:
    references: dict[str, object] = dict(zip(_FIELDS, control, strict=True))
    _assert_reference(references, expected)
    _assert_unsupported({**references, field: value})


@pytest.mark.parametrize(
    "raw",
    [
        (2**53, "runner", 12, "group"),
        (101, "runner", 2**53, "group"),
        (0, "\ud800", None, None),
        (101, "runner", 0, "\ud800"),
    ],
)
def test_invalid_json_is_rejected_before_webhook_preparation(raw: Reference) -> None:
    webhook, page = _admit(dict(zip(_FIELDS, raw, strict=True)))
    assert isinstance(webhook, IngestionRejection)
    assert webhook.code == "invalid_json"
    assert page is None


@pytest.mark.parametrize("prefix", ["runner", "runner_group"])
@pytest.mark.parametrize(("identifier", "name"), [(None, "name"), (101, None), (101, "")])
def test_nonzero_reference_pairs_must_remain_coherent(
    prefix: str, identifier: int | None, name: str | None
) -> None:
    _assert_unsupported(
        {
            **dict(zip(_FIELDS, _POSITIVE, strict=True)),
            f"{prefix}_id": identifier,
            f"{prefix}_name": name,
        }
    )


@pytest.mark.parametrize("identifier", [0, None])
def test_group_identity_without_runner_is_rejected(identifier: int | None) -> None:
    _assert_unsupported(dict(zip(_FIELDS, (identifier, None, 12, "group"), strict=True)))

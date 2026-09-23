import hashlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import cast

import psycopg
import pytest
from scripts.bounded_process import CommandResult
from scripts.ci_business_witness.diagnostics import (
    browser_diagnostic,
    container_diagnostic,
    provider_diagnostic,
    readiness_diagnostic,
    startup_diagnostic,
)
from scripts.ci_business_witness.fixture import ACTOR, ISSUER, SUBJECT, Fixture
from scripts.ci_business_witness.lifecycle import (
    LOCAL_DOCKER_HOST,
    WitnessBudget,
    admit_docker_environment,
    database_host,
)
from scripts.ci_business_witness.oracle import (
    CONFIGURATION,
    WitnessFailure,
    admit_logs,
    canonical,
    verify_checkpoint,
)
from scripts.ci_business_witness.runner import Runner

from ci_coordinator.api.http.ci_economics_budget_contracts import ConfigureBudgetPolicyBody


def test_browser_diagnostic_keeps_only_known_progress_and_source_coordinates() -> None:
    sensitive_text = "minted-oidc-value"
    output = (
        f"Error: {sensitive_text}\n"
        " at /workspace/frontend/tests/connected/administratorBudget.spec.ts:134:69\n"
        " at /foreign/secret.ts:12:4\n"
    )
    progress = {
        "browserPhase": "open-editor",
        "policyReadStatus": 503,
        "policyWriteStatus": None,
        "policyWriteMedia": None,
        "policyWriteFinished": False,
        "policyWriteFailed": False,
        "policyWriteAborted": False,
    }
    assert browser_diagnostic(output, json.dumps(progress)) == {
        **progress,
        "errorMarkers": [],
        "progressAdmitted": True,
        "sourceLocations": [{"line": 134, "column": 69}],
    }
    assert sensitive_text not in json.dumps(browser_diagnostic(output, json.dumps(progress)))


def test_browser_coordinates_require_bounded_stack_lines_and_a_finite_population() -> None:
    source = "/workspace/frontend/tests/connected/administratorBudget.spec.ts"
    output = "\n".join(
        [
            "at " * 5000,
            f"arbitrary at {source}:900:1",
            "at " + "x" * 5000 + f"{source}:901:1",
            *(f"    at {source}:{line}:4" for line in range(1, 20)),
            "Error: response.json: Target page, context or browser has been closed",
        ]
    )
    assert browser_diagnostic(output, "") == {
        "errorMarkers": ["target-closed"],
        "progressAdmitted": False,
        "sourceLocations": [{"line": line, "column": 4} for line in range(1, 9)],
    }


@pytest.mark.parametrize(
    "patch",
    [
        {"browserPhase": "minted-oidc-value"},
        {"extra": "minted-oidc-value"},
        {"policyReadStatus": True},
        {"policyWriteStatus": 600},
        {"policyReadStatus": "200"},
        {"policyWriteMedia": "application/json; minted-oidc-value"},
        {"policyWriteMedia": "xml"},
        {"policyWriteMedia": True},
        {"policyWriteMedia": ["json"]},
    ],
)
def test_browser_diagnostic_rejects_arbitrary_progress(patch: dict[str, object]) -> None:
    progress = {
        "browserPhase": "open-editor",
        "policyReadStatus": 200,
        "policyWriteStatus": None,
        "policyWriteMedia": None,
        "policyWriteFinished": False,
        "policyWriteFailed": False,
        "policyWriteAborted": False,
    }
    assert browser_diagnostic("", json.dumps({**progress, **patch})) == {
        "errorMarkers": [],
        "progressAdmitted": False,
        "sourceLocations": [],
    }


@pytest.mark.parametrize("media", [None, "json", "html", "other"])
def test_browser_progress_media_is_a_required_finite_category(media: str | None) -> None:
    progress = {
        "browserPhase": "commit-response",
        "policyReadStatus": 200,
        "policyWriteStatus": 200,
        "policyWriteMedia": media,
        "policyWriteFinished": False,
        "policyWriteFailed": False,
        "policyWriteAborted": False,
    }
    diagnostic = browser_diagnostic("", json.dumps(progress))
    assert diagnostic["progressAdmitted"] is True
    assert diagnostic["policyWriteMedia"] == media
    del progress["policyWriteMedia"]
    assert browser_diagnostic("", json.dumps(progress))["progressAdmitted"] is False


@pytest.mark.parametrize(
    "field", ["policyWriteFinished", "policyWriteFailed", "policyWriteAborted"]
)
@pytest.mark.parametrize("value", [None, 0, 1, "false", [], {}])
def test_browser_request_observations_require_boolean_values(field: str, value: object) -> None:
    progress = {
        "browserPhase": "commit-response",
        "policyReadStatus": 200,
        "policyWriteStatus": 200,
        "policyWriteMedia": "json",
        "policyWriteFinished": False,
        "policyWriteFailed": False,
        "policyWriteAborted": False,
        field: value,
    }
    assert browser_diagnostic("", json.dumps(progress)) == {
        "errorMarkers": [],
        "progressAdmitted": False,
        "sourceLocations": [],
    }


@pytest.mark.parametrize(
    "field", ["policyWriteFinished", "policyWriteFailed", "policyWriteAborted"]
)
def test_browser_request_observations_cannot_be_omitted(field: str) -> None:
    progress = {
        "browserPhase": "commit-response",
        "policyReadStatus": 200,
        "policyWriteStatus": 200,
        "policyWriteMedia": "json",
        "policyWriteFinished": False,
        "policyWriteFailed": False,
        "policyWriteAborted": False,
    }
    del progress[field]
    assert browser_diagnostic("", json.dumps(progress)) == {
        "errorMarkers": [],
        "progressAdmitted": False,
        "sourceLocations": [],
    }


@pytest.mark.parametrize(
    ("finished", "failed", "aborted", "admitted"),
    [
        (False, False, False, True),
        (True, False, False, True),
        (False, True, False, True),
        (False, True, True, True),
        (True, True, False, True),
        (True, True, True, True),
        (False, False, True, False),
        (True, False, True, False),
    ],
)
def test_browser_request_facts_allow_both_events_but_abort_requires_failure(
    finished: bool, failed: bool, aborted: bool, admitted: bool
) -> None:
    progress = {
        "browserPhase": "commit-response",
        "policyReadStatus": 200,
        "policyWriteStatus": 200,
        "policyWriteMedia": "json",
        "policyWriteFinished": finished,
        "policyWriteFailed": failed,
        "policyWriteAborted": aborted,
    }
    diagnostic = browser_diagnostic("", json.dumps(progress))
    assert diagnostic == {
        **(progress if admitted else {}),
        "errorMarkers": [],
        "progressAdmitted": admitted,
        "sourceLocations": [],
    }


@pytest.mark.parametrize(
    ("message", "markers"),
    [
        (
            "Error: response.json: Protocol error (Network.getResponseBody): "
            "No resource with given identifier found",
            ["response-body-no-resource", "response-body-protocol"],
        ),
        (
            "Error: response.json: Protocol error (Network.getResponseBody): "
            "No data found for resource with given identifier",
            ["response-body-no-resource", "response-body-protocol"],
        ),
        (
            "Error: response.body: Protocol error (Network.getResponseBody)",
            ["response-body-protocol"],
        ),
        (
            "Error: response.json: Target page, context or browser has been closed",
            ["target-closed"],
        ),
        ("SyntaxError: Unexpected end of JSON input", ["json-truncated"]),
        ("SyntaxError: Unterminated string in JSON at position 8", ["json-truncated"]),
        ("SyntaxError: Unexpected token '<', not valid JSON", ["json-unexpected-token"]),
        ("Error: page.goto: net::ERR_CONNECTION_RESET", ["request-failed"]),
        ("Error: response.json: Request failed", ["request-failed"]),
        ("TimeoutError: page.waitForResponse: Timeout 30000ms exceeded", ["timeout"]),
        ("Error: Test timeout of 45000ms exceeded.", ["timeout"]),
    ],
)
def test_browser_error_markers_publish_only_fixed_categories(
    message: str, markers: list[str]
) -> None:
    sensitive_text = "minted-oidc-value"
    output = f"  {message} {sensitive_text}\n  {message}\n"
    diagnostic = browser_diagnostic(output, "")
    assert diagnostic == {
        "errorMarkers": markers,
        "progressAdmitted": False,
        "sourceLocations": [],
    }
    assert sensitive_text not in json.dumps(diagnostic)


@pytest.mark.parametrize(
    "output",
    [
        "Error: minted-oidc-value",
        "body: Unexpected token minted-oidc-value",
        'body: {"errorMarkers":["timeout"],"token":"minted-oidc-value"}',
        "Error: minted-oidc-value " + "x" * 4096 + " Target closed",
        "x" * (4 * 1024 * 1024) + "\nError: Target closed",
    ],
    ids=["unknown-error", "non-error-text", "body-spoof", "oversize-line", "output-bound"],
)
def test_browser_marker_scan_rejects_unclassified_text_and_respects_output_bounds(
    output: str,
) -> None:
    assert browser_diagnostic(output, "") == {
        "errorMarkers": [],
        "progressAdmitted": False,
        "sourceLocations": [],
    }


@pytest.mark.parametrize(
    "defect",
    [
        "none",
        "external",
        "foreign-name",
        "foreign-label",
        "missing-container",
        "public-address",
        "loopback",
        "link-local",
        "ipv6",
        "wrong-driver",
        "duplicate",
        "empty",
        "wrong-boolean",
        "malformed",
    ],
)
def test_sql_endpoint_is_bound_to_the_owned_internal_bridge(defect: str) -> None:
    container = "a" * 64
    endpoint = {"IPv4Address": "172.28.0.2/16"}
    members = {container: endpoint}
    labels = {"com.docker.compose.project": "fixture"}
    network: dict[str, object] = {
        "Name": "fixture_isolated",
        "Driver": "bridge",
        "Internal": True,
        "Labels": labels,
        "Containers": members,
    }
    if defect == "external":
        network["Internal"] = False
    elif defect == "foreign-name":
        network["Name"] = "foreign_isolated"
    elif defect == "foreign-label":
        labels["com.docker.compose.project"] = "foreign"
    elif defect == "missing-container":
        members.clear()
    elif defect in {"public-address", "loopback", "link-local", "ipv6"}:
        endpoint["IPv4Address"] = {
            "public-address": "8.8.8.8/24",
            "loopback": "127.0.0.1/8",
            "link-local": "169.254.1.1/16",
            "ipv6": "::1/128",
        }[defect]
    elif defect == "wrong-driver":
        network["Driver"] = "host"
    elif defect == "wrong-boolean":
        network["Internal"] = "true"
    networks = [] if defect == "empty" else [network] * (2 if defect == "duplicate" else 1)
    output = "not-json" if defect == "malformed" else json.dumps(networks)
    if defect == "none":
        assert database_host(output, project="fixture", container=container) == "172.28.0.2"
    else:
        with pytest.raises((ValueError, WitnessFailure)):
            database_host(output, project="fixture", container=container)


@pytest.mark.parametrize("data", [b"", b"safe canary-value output", b"x" * (4 * 1024 * 1024 + 1)])
def test_log_oracle_rejects_absence_disclosure_and_overflow(data: bytes) -> None:
    with pytest.raises(WitnessFailure):
        admit_logs(data, (b"canary-value",))


def test_log_oracle_accepts_present_non_secret_output() -> None:
    admit_logs(b"service started", (b"canary-value",))


class _Database:
    def __init__(self, rows: list[list[tuple[object, ...]]]) -> None:
        self.rows = iter(rows)

    def cursor(self) -> "_Database":
        return self

    def __enter__(self) -> "_Database":
        return self

    def __exit__(self, *_arguments: object) -> None:
        return None

    def execute(self, *_arguments: object) -> None:
        pass

    def fetchall(self) -> list[tuple[object, ...]]:
        return next(self.rows)


@pytest.mark.parametrize(
    "defect",
    ["none", "duplicate-policy", "duplicate-audit", "wrong-actor", "wrong-value", "wrong-role"],
)
def test_sql_oracle_requires_exact_persisted_effect_and_actor(defect: str) -> None:
    operation = "f3ef1607-4d8b-4777-b3d8-a7c1a5b93a3e"
    command = {
        "installationId": 1,
        "repositoryId": 1,
        "policyKey": "witness",
        "expectedRevision": 0,
        "operationId": operation,
        "configuration": CONFIGURATION,
    }
    policy = {
        "schemaVersion": "ci-economics-budget-policy/v1",
        "installationId": 1,
        "repositoryId": 1,
        "policyKey": "witness",
        "revision": 1,
        "configuration": CONFIGURATION,
    }
    data = canonical(policy)
    digest = hashlib.sha256(data).hexdigest()
    command_digest = hashlib.sha256(
        canonical({**command, "schemaVersion": "ci-economics-budget-command/v1", "actor": ACTOR})
    ).hexdigest()
    policy_row = (1, 1, "witness", 1, digest, data if defect != "wrong-value" else b"{}")
    event = (
        f"ci-economics-budget:1:1:{operation}".encode(),
        b"ci-budget:1:1:witness",
        ACTOR.encode() if defect != "wrong-actor" else b"foreign-actor",
        canonical({"commandDigest": command_digest, "policy": policy}),
    )
    rows: list[list[tuple[object, ...]]] = [
        [
            ("ci_coordinator_migration", False, False, False, False, False),
            ("ci_coordinator_runtime", defect == "wrong-role", False, False, False, False),
        ],
        [(ISSUER, SUBJECT, ACTOR)],
        [policy_row] * (2 if defect == "duplicate-policy" else 1),
        [event] * (2 if defect == "duplicate-audit" else 1),
    ]
    database = cast(psycopg.Connection[tuple[object, ...]], _Database(rows))
    document = {"stage": "replayed", "actorId": ACTOR, "command": command}
    if defect == "none":
        assert (
            verify_checkpoint(database, document, stage="replayed", policy_key="witness") == digest
        )
    else:
        with pytest.raises(WitnessFailure):
            verify_checkpoint(database, document, stage="replayed", policy_key="witness")


@pytest.mark.parametrize(
    ("stage", "kind", "denial_status"),
    [
        ("csrf-rejected", "csrf", 403),
        ("unauthenticated", "anonymous", 401),
        ("canary-rejected", "canary", 401),
    ],
)
@pytest.mark.parametrize("execute_before_denial", [False, True])
def test_expected_denial_cannot_hide_an_otherwise_valid_persisted_mutation(
    stage: str, kind: str, denial_status: int, execute_before_denial: bool
) -> None:
    command = {
        "installationId": 1,
        "repositoryId": 1,
        "policyKey": "witness",
        "expectedRevision": 0,
        "operationId": "f3ef1607-4d8b-4777-b3d8-a7c1a5b93a3e",
        "configuration": CONFIGURATION,
    }
    original = ConfigureBudgetPolicyBody.model_validate(command).to_command(ACTOR)
    forbidden = ConfigureBudgetPolicyBody.model_validate(
        {
            **command,
            "policyKey": f"witness-{kind}",
            "operationId": f"{kind}-{original.operation_id}",
            "expectedRevision": 0,
        }
    ).to_command(ACTOR)
    assert forbidden.scope == original.scope
    assert forbidden.policy_key != original.policy_key
    assert forbidden.audit_key != original.audit_key
    assert forbidden.expected_revision == 0
    assert forbidden.next_policy.revision == 1
    assert forbidden.configuration == original.configuration

    accepted_commands = [original]
    if execute_before_denial:
        accepted_commands.append(forbidden)
    policies: list[tuple[object, ...]] = []
    events: list[tuple[object, ...]] = []
    for accepted in accepted_commands:
        policy = accepted.next_policy.canonical_mapping()
        policy_bytes = canonical(policy)
        policies.append(
            (1, 1, accepted.policy_key, 1, hashlib.sha256(policy_bytes).hexdigest(), policy_bytes)
        )
        events.append(
            (
                accepted.audit_key.encode(),
                f"ci-budget:1:1:{accepted.policy_key}".encode(),
                ACTOR.encode(),
                canonical({"commandDigest": accepted.command_digest, "policy": policy}),
            )
        )
    rows: list[list[tuple[object, ...]]] = [
        [
            ("ci_coordinator_migration", False, False, False, False, False),
            ("ci_coordinator_runtime", False, False, False, False, False),
        ],
        [(ISSUER, SUBJECT, ACTOR)] if stage == "csrf-rejected" else [],
        policies,
        events,
    ]
    database = cast(psycopg.Connection[tuple[object, ...]], _Database(rows))
    # Both handler variants return the expected denial; only durable evidence differs.
    document = {"stage": stage, "actorId": ACTOR, "command": command, "httpStatus": denial_status}
    if execute_before_denial:
        with pytest.raises(WitnessFailure, match="policy-bytes-or-cardinality"):
            verify_checkpoint(database, document, stage=stage, policy_key="witness")
    else:
        assert verify_checkpoint(database, document, stage=stage, policy_key="witness") == (
            hashlib.sha256(canonical(original.next_policy.canonical_mapping())).hexdigest()
        )


def test_execution_deadline_limits_steps_and_cannot_be_renewed() -> None:
    now = [0.0]
    budget = WitnessBudget(clock=lambda: now[0])
    assert budget.timeout(900) == 900
    now[0] = 1790
    assert budget.timeout(900) == 10
    now[0] = 1800
    with pytest.raises(WitnessFailure, match="whole-witness-deadline"):
        budget.timeout(1)


def test_cleanup_reserve_is_separate_but_not_renewable() -> None:
    now = [0.0]
    budget = WitnessBudget(clock=lambda: now[0])
    now[0] = 1800
    assert budget.begin_cleanup() == 120
    now[0] = 1870
    assert budget.begin_cleanup() == 50
    assert budget.timeout(900) == 50
    now[0] = 1920
    with pytest.raises(WitnessFailure, match="whole-witness-deadline"):
        budget.begin_cleanup()


@pytest.mark.parametrize(
    "key", ["DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH"]
)
def test_foreign_docker_selectors_are_rejected(key: str) -> None:
    with pytest.raises(WitnessFailure, match="ambient-docker-selector"):
        admit_docker_environment({key: "foreign"})


def test_provider_call_uses_local_socket_and_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [0.0]
    budget = WitnessBudget(clock=lambda: now[0])
    now[0] = 1790
    observed: list[tuple[str, Sequence[str], object]] = []

    def provider(command: str, args: Sequence[str], **kwargs: object) -> CommandResult:
        observed.append((command, args, kwargs["timeout_seconds"]))
        return CommandResult(status=0, stdout="safe", stderr="")

    monkeypatch.setattr("scripts.ci_business_witness.runner.spawn", provider)
    fixture = Fixture(Path("/unused"), (b"canary",), "unused", "policy")
    runner = Runner(fixture, "fixture-project", budget)
    assert runner.command(("ps",), timeout=900) == "safe"
    assert observed == [("docker", ("--host", LOCAL_DOCKER_HOST, "ps"), 10)]


def test_provider_failure_diagnostic_omits_arbitrary_errors_arguments_and_output() -> None:
    sensitive_text = "minted-oidc-value"
    result = CommandResult(
        1, sensitive_text, sensitive_text, error=sensitive_text, failure_kind="timeout"
    )
    diagnostic = provider_diagnostic(
        "application-readiness",
        ("compose", "--project-name", sensitive_text, "--file", sensitive_text, "run"),
        result,
    )
    assert diagnostic["operation"] == "compose:run"
    assert diagnostic["stage"] == "application-readiness"
    assert diagnostic["exitCode"] == 1
    assert diagnostic["failureKind"] == "timeout"
    assert diagnostic["processError"] is True
    assert sensitive_text not in json.dumps(diagnostic)


@pytest.mark.parametrize(
    "output,expected",
    [
        ("curl: (22) private request", ["download-http"]),
        (
            "computed checksum did NOT match; failed to solve",
            ["buildkit-failed", "checksum-mismatch"],
        ),
        ("ERR_PNPM_FETCH_404 private location", ["frontend-dependency"]),
        ("unclassified private failure", []),
    ],
)
def test_build_diagnostics_publish_only_fixed_non_authoritative_markers(
    output: str, expected: list[str]
) -> None:
    result = CommandResult(1, "", output)
    observed = provider_diagnostic("build-application", ("build", "private argument"), result)
    assert observed["buildErrorMarkers"] == expected
    assert "private" not in json.dumps(observed)
    assert provider_diagnostic("database-migrate", ("start",), result)["buildErrorMarkers"] == []


def test_container_diagnostic_retains_status_but_drops_health_output_and_error_text() -> None:
    sensitive_text = "minted-oidc-value"
    diagnostic = container_diagnostic(
        json.dumps(
            {
                "Status": "exited",
                "ExitCode": 2,
                "Running": False,
                "OOMKilled": False,
                "Error": sensitive_text,
                "Health": {"Status": "unhealthy", "Log": [{"Output": sensitive_text}]},
            }
        )
    )
    assert diagnostic == {
        "availability": "observed",
        "status": "exited",
        "exitCode": 2,
        "running": False,
        "oomKilled": False,
        "health": "unhealthy",
        "providerError": True,
    }
    assert sensitive_text not in json.dumps(diagnostic)


def test_startup_diagnostic_emits_only_known_markers_rejections_and_setting_names() -> None:
    sensitive_text = "minted-oidc-value"
    field = "CI_COORDINATOR_KEYCLOAK_ISSUER"
    output = "\n".join(
        (
            "backend | Application startup failed. KeycloakUnavailable: " + sensitive_text,
            "backend | "
            + json.dumps(
                {"code": "invalid_setting_value", "field": field, "value": sensitive_text}
            ),
            "backend | " + json.dumps({"code": sensitive_text, "field": sensitive_text}),
            "backend | " + json.dumps({"code": "invalid_setting_value", "field": sensitive_text}),
        )
    )
    diagnostic = startup_diagnostic(output, allowed_fields=frozenset({field}))
    assert diagnostic == {
        "markers": ["keycloak-unavailable", "startup-failed"],
        "rejections": [
            {"code": "invalid_setting_value", "field": field},
            {"code": "invalid_setting_value", "field": None},
        ],
    }
    assert sensitive_text not in json.dumps(diagnostic)


@pytest.mark.parametrize(
    "patch",
    [
        {"failure": "minted-oidc-value"},
        {"extra": "minted-oidc-value"},
        {"ready": True, "httpStatus": 503, "failure": None},
        {"attempts": 0},
    ],
)
def test_readiness_diagnostic_rejects_unknown_or_contradictory_evidence(
    patch: dict[str, object],
) -> None:
    value = {
        "schemaVersion": "connected-readiness/v1",
        "ready": False,
        "attempts": 3,
        "httpStatus": 502,
        "failure": "http-status",
        **patch,
    }
    assert readiness_diagnostic(json.dumps(value)) == {"admitted": False}


def test_readiness_failure_is_preserved_before_provider_failure_is_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sensitive_text = "minted-oidc-value"
    readiness = {
        "schemaVersion": "connected-readiness/v1",
        "ready": False,
        "attempts": 8,
        "httpStatus": 502,
        "failure": "http-status",
    }

    def provider(_command: str, _args: Sequence[str], **_kwargs: object) -> CommandResult:
        return CommandResult(1, json.dumps(readiness), sensitive_text)

    monkeypatch.setattr("scripts.ci_business_witness.runner.spawn", provider)
    runner = Runner(
        Fixture(Path("/unused"), (b"canary",), "unused", "policy"),
        "fixture-project",
        WitnessBudget(clock=lambda: 0.0),
    )
    runner.stage = "application-readiness"
    with pytest.raises(WitnessFailure, match="provider-command"):
        runner.command((*runner.compose, "run", "browser"), readiness=True)
    assert runner.provider_failures[0]["exitCode"] == 1
    assert runner.readiness_checks == [
        {
            "stage": "application-readiness",
            "admitted": True,
            "ready": False,
            "attempts": 8,
            "httpStatus": 502,
            "failure": "http-status",
        }
    ]
    assert sensitive_text not in json.dumps([runner.provider_failures, runner.readiness_checks])


def test_readiness_success_requires_the_complete_safe_response() -> None:
    value = {
        "schemaVersion": "connected-readiness/v1",
        "ready": True,
        "attempts": 1,
        "httpStatus": 200,
        "failure": None,
    }
    assert readiness_diagnostic(json.dumps(value)) == {
        "admitted": True,
        "ready": True,
        "attempts": 1,
        "httpStatus": 200,
        "failure": None,
    }


@pytest.mark.parametrize("fault", ["capture-ended", "exited", "oom", "provider-error", "none"])
def test_service_log_admission_preserves_liveness_until_intentional_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    from scripts.ci_business_witness.attached_logs import CapturedOutput

    runner = Runner(
        Fixture(tmp_path, (b"private-canary",), "unused", "policy"), "project", WitnessBudget()
    )
    runner.attached.close()
    runner._stream_services = {"backend", "keycloak", "proxy", "postgres"}
    runner.containers = {
        name: str(index) * 64 for index, name in enumerate(sorted(runner._stream_services), 1)
    }
    (tmp_path / "backend.env").write_text("APP_MODE=fixture\n")
    ended = False
    stopped = False
    finished = False

    class Capture:
        def assert_running(self) -> None:
            if ended:
                raise WitnessFailure("attached-log-ended-before-stop")

        def finish(self, exit_codes: dict[str, int]) -> dict[str, CapturedOutput]:
            nonlocal finished
            assert stopped and set(exit_codes) == runner._stream_services
            assert set(exit_codes.values()) == {143}
            finished = True
            return {name: CapturedOutput(b"service output\n", b"") for name in exit_codes}

    def state(_service: str) -> dict[str, object]:
        nonlocal ended
        assert not runner._logs_terminal
        ended = fault == "capture-ended"
        return {
            "availability": "observed",
            "status": "exited" if fault == "exited" else "running",
            "running": fault != "exited",
            "oomKilled": fault == "oom",
            "providerError": fault == "provider-error",
            "exitCode": 1 if fault == "exited" else 0,
        }

    def stop(*arguments: str, **_kwargs: object) -> str:
        nonlocal stopped
        assert arguments[0] == "stop" and runner._logs_terminal
        stopped = True
        return ""

    def inspect(arguments: tuple[str, ...], **_kwargs: object) -> str:
        assert stopped and arguments[0] == "inspect"
        return json.dumps(
            [
                {
                    "Id": arguments[1],
                    "Config": {"Labels": {"com.docker.compose.project": "project"}},
                    "State": {
                        "Running": False,
                        "Status": "exited",
                        "OOMKilled": False,
                        "Error": "",
                        "ExitCode": 143,
                    },
                }
            ]
        )

    monkeypatch.setattr(runner, "attached", Capture())
    monkeypatch.setattr(runner, "service_state", state)
    monkeypatch.setattr(runner, "compose_command", stop)
    monkeypatch.setattr(runner, "command", inspect)
    if fault == "none":
        runner.service_logs()
        assert stopped and finished and set(runner.log_receipts) == runner._stream_services
    else:
        with pytest.raises(WitnessFailure, match="ended-before-stop"):
            runner.service_logs()
        assert not stopped and not finished


def test_cleanup_rejects_a_stopped_owned_container_before_unmount_can_be_admitted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner = Runner(
        Fixture(tmp_path, (b"private-canary",), "unused", "policy"), "project", WitnessBudget()
    )
    runner.owned = True
    calls: list[tuple[str, ...]] = []

    def inspect(arguments: tuple[str, ...], **_kwargs: object) -> str:
        calls.append(arguments)
        if arguments[:2] == ("container", "ls"):
            return "a" * 64 if "--all" in arguments else ""
        return ""

    monkeypatch.setattr(runner, "compose_command", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(runner, "command", inspect)
    with pytest.raises(WitnessFailure, match="resource-cleanup"):
        runner.cleanup()
    assert calls == [
        (
            "container",
            "ls",
            "--all",
            "--quiet",
            "--filter",
            "label=com.docker.compose.project=project",
        )
    ]

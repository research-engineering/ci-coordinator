from __future__ import annotations

import io
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from scripts.dev_environment import cli
from scripts.dev_environment.compose import ComposeError, ComposeProject, ServiceStatus
from scripts.dev_environment.diagnostics import Reason
from scripts.dev_environment.identity import InstanceIdentity, derive_instance_identity
from scripts.dev_environment.lifecycle import instance_operation_lock
from scripts.dev_environment.observation import observe
from scripts.dev_environment.secrets import ensure_instance_state


class ObservationProject(ComposeProject):
    def __init__(
        self,
        identity: InstanceIdentity,
        *,
        changing: bool = False,
        unavailable: bool = False,
        stopped: bool = False,
        unhealthy: bool = False,
        endpoint_failure: Reason | None = None,
    ) -> None:
        self.identity = identity
        self.changing = changing
        self.unavailable = unavailable
        self.stopped = stopped
        self.unhealthy = unhealthy
        self.endpoint_failure = endpoint_failure
        self.calls = 0
        self.budgets = 0

    @contextmanager
    def observation_budget(self, seconds: float = 30) -> Iterator[None]:
        self.budgets += 1
        yield

    def diagnostic_status(self) -> tuple[ServiceStatus, ...]:
        self.calls += 1
        if self.unavailable:
            raise ComposeError("sensitive-provider-output", reason=Reason.PROVIDER_UNAVAILABLE)
        return tuple(
            ServiceStatus(
                name,
                "exited" if self.stopped and name == "backend" else "running",
                "unhealthy" if self.unhealthy else "healthy",
                1 if self.stopped else 0,
                "a" * 64,
            )
            for name in ("backend", "frontend", "postgres")
        )

    def observation_signature(self, statuses: object) -> tuple[tuple[object, ...], ...]:
        return ((self.calls if self.changing else 1,),)

    def endpoint(self, service: str) -> str:
        if service == "backend" and self.endpoint_failure is not None:
            raise ComposeError("sensitive-provider-output", reason=self.endpoint_failure)
        return {
            "backend": "http://127.0.0.1:41001",
            "frontend": "http://127.0.0.1:41002",
            "postgres": "postgresql://127.0.0.1:41003",
        }[service]


@pytest.fixture
def identity(tmp_path: Path) -> InstanceIdentity:
    return derive_instance_identity(tmp_path, state_home=tmp_path.parent / f"{tmp_path.name}-state")


def test_complete_unhealthy_observation_does_not_claim_readiness(
    identity: InstanceIdentity,
) -> None:
    result = observe(identity, ObservationProject(identity, unhealthy=True))
    payload = json.loads(json.dumps(result.payload))
    assert result.reason is None
    assert payload["state"] == "observed"
    assert payload["observation"]["completeness"] == "complete"
    assert payload["services"][0]["health"] == "unhealthy"


def test_stopped_service_retains_other_endpoints_and_exit_code(
    identity: InstanceIdentity,
) -> None:
    project = ObservationProject(identity, stopped=True)
    result = observe(identity, project)
    payload = json.loads(json.dumps(result.payload))
    assert result.reason == Reason.PARTIAL_OBSERVATION
    assert "api" not in payload["endpoints"]
    assert payload["endpoints"]["ui"] == "http://127.0.0.1:41002"
    assert payload["services"][0]["exitCode"] == 1
    assert project.calls == 2


def test_changing_observation_retries_once_under_one_budget(
    identity: InstanceIdentity,
) -> None:
    project = ObservationProject(identity, changing=True)
    result = observe(identity, project)
    payload = json.loads(json.dumps(result.payload))
    assert result.reason == Reason.STALE_OBSERVATION
    assert payload["observation"]["consistency"] == "changing"
    assert project.calls == 4
    assert project.budgets == 1


def test_partial_port_failure_retains_status_without_provider_output(
    identity: InstanceIdentity,
) -> None:
    result = observe(
        identity, ObservationProject(identity, endpoint_failure=Reason.PROVIDER_TIMEOUT)
    )
    payload = json.loads(json.dumps(result.payload))
    assert result.reason == Reason.PARTIAL_OBSERVATION
    assert payload["services"]
    assert "sensitive-provider-output" not in json.dumps(result.payload)
    assert payload["observation"]["reasons"] == [
        {"service": "backend", "reason": "provider_timeout"}
    ]


def test_foreign_ownership_does_not_become_a_partial_success(
    identity: InstanceIdentity,
) -> None:
    with pytest.raises(ComposeError) as caught:
        observe(
            identity,
            ObservationProject(identity, endpoint_failure=Reason.FOREIGN_STATE),
        )
    assert caught.value.reason == Reason.FOREIGN_STATE


def test_status_is_available_while_an_owned_mutation_lock_is_held(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch
) -> None:
    ensure_instance_state(identity)
    project = ObservationProject(identity)
    monkeypatch.setattr(cli, "_project", lambda *_args, **_kwargs: project)
    out, error = io.StringIO(), io.StringIO()
    with instance_operation_lock(identity):
        result = cli.run(
            ["status"],
            repo_root=identity.repo_root,
            state_home=identity.state_home,
            stdout=out,
            stderr=error,
        )
    assert result == 0
    assert json.loads(out.getvalue())["observation"]["consistency"] == "stable"
    assert error.getvalue() == ""


@pytest.mark.parametrize(
    "unavailable,expected",
    [(False, "partial_observation"), (True, "provider_unavailable")],
)
def test_status_partial_projection_and_diagnostic_are_on_separate_streams(
    identity: InstanceIdentity,
    monkeypatch: pytest.MonkeyPatch,
    unavailable: bool,
    expected: str,
) -> None:
    project = ObservationProject(identity, unavailable=unavailable, stopped=True)
    monkeypatch.setattr(cli, "_project", lambda *_args, **_kwargs: project)
    out, error = io.StringIO(), io.StringIO()
    result = cli.run(
        ["status"],
        repo_root=identity.repo_root,
        state_home=identity.state_home,
        stdout=out,
        stderr=error,
    )
    assert result == 2
    assert json.loads(out.getvalue())["rootDigest"] == identity.root_digest
    assert json.loads(error.getvalue())["diagnostic"]["reason"] == expected
    assert "sensitive-provider-output" not in out.getvalue() + error.getvalue()


def test_missing_status_has_identity_without_creating_state(
    identity: InstanceIdentity,
) -> None:
    out, error = io.StringIO(), io.StringIO()
    result = cli.run(
        ["status", "--format", "json"],
        repo_root=identity.repo_root,
        state_home=identity.state_home,
        stdout=out,
        stderr=error,
    )
    assert result == 2
    assert json.loads(out.getvalue())["state"] == "missing"
    assert json.loads(error.getvalue())["diagnostic"]["reason"] == "missing_state"
    assert not identity.state_directory.exists()


@pytest.mark.parametrize(
    "arguments",
    [
        ["logs", "--tail", "10001"],
        ["logs", "--tail", "-1"],
        ["logs", "--service", "secret-value"],
        ["down", "--follow"],
        ["status", "--stop-watch"],
    ],
)
def test_invalid_selection_fails_before_provider_or_instance_admission(
    identity: InstanceIdentity, monkeypatch: pytest.MonkeyPatch, arguments: list[str]
) -> None:
    def reject(*_args: object, **_kwargs: object) -> None:
        pytest.fail("invalid arguments reached provider admission")

    monkeypatch.setattr(cli, "_project", reject)
    out, error = io.StringIO(), io.StringIO()
    result = cli.run(
        arguments,
        repo_root=identity.repo_root,
        state_home=identity.state_home,
        stdout=out,
        stderr=error,
    )
    assert result == 2
    assert json.loads(error.getvalue())["diagnostic"]["reason"] == "invalid_argument"
    assert "secret-value" not in error.getvalue()
    assert not identity.state_directory.exists()

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import pytest
from control_plane_http_support import (
    ACTOR,
    StaticControlPlaneAuthenticator,
    StaticMutationAdmission,
    StaticRoleAdmission,
    human_principal,
)
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ci_coordinator.api.http.app import create_app
from ci_coordinator.api.http.dependencies import (
    GovernanceBaselineRouteDependencies,
    HttpRouteDependencies,
    InvalidCredential,
)
from ci_coordinator.api.http.governance_baseline_contracts import (
    GovernanceBaselineApprovalResponse,
    governance_baseline_approval_response,
    governance_baseline_read_response,
)
from ci_coordinator.api.http.routers.governance_baselines import (
    GOVERNANCE_BASELINES_PATH,
)
from ci_coordinator.app.governance_baseline import (
    GovernanceBaselineApprovalOutcome,
    GovernanceBaselineApprovalState,
    GovernanceBaselineReadOutcome,
    GovernanceBaselineUseCase,
)
from ci_coordinator.config_control import RepositoryScope
from ci_coordinator.governance_baseline import (
    GovernanceBaselineCommand,
    GovernanceBaselineDraft,
    GovernanceBaselinePointer,
    GovernanceBaselineRecord,
    prepare_governance_baseline,
)
from ci_coordinator.governance_observation import (
    EffectiveGovernanceRule,
    GovernanceRepository,
    GovernanceState,
    encode_governance_state,
)
from ci_coordinator.kernel import canonical_json, sha256_hex

SCOPE = RepositoryScope(1, 2)
NOW = datetime(2026, 7, 26, 10, tzinfo=UTC)
PATH = GOVERNANCE_BASELINES_PATH.format(installation_id=1, repository_id=2)
PUBLIC_ORIGIN = "https://ci.example.test"
STATE = None


def _body(
    *,
    expected_active: GovernanceBaselinePointer | None = None,
) -> dict[str, object]:
    return {
        "operationId": "approve-1",
        "expectedStateDigest": _state_digest(),
        "expectedActive": (
            None
            if expected_active is None
            else {
                "baselineId": expected_active.baseline_id,
                "version": expected_active.version,
                "stateDigest": expected_active.state_digest,
            }
        ),
        "reason": "Adopt repository governance",
    }


@dataclass
class _UseCase:
    approval_state: GovernanceBaselineApprovalState = "accepted"
    read_state: str = "active"
    retained_operation_id: str | None = None
    calls: list[tuple[str, object, object]] = field(default_factory=list)

    async def read_active(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
    ) -> GovernanceBaselineReadOutcome:
        self.calls.append(("read", actor, scope))
        if self.read_state == "active":
            return GovernanceBaselineReadOutcome(
                "active",
                _record(_command(actor)),
            )
        return GovernanceBaselineReadOutcome(self.read_state)  # type: ignore[arg-type]

    async def accept(
        self,
        command: GovernanceBaselineCommand,
    ) -> GovernanceBaselineApprovalOutcome:
        self.calls.append(("accept", command, command.scope))
        if self.approval_state not in {"accepted", "duplicate", "unchanged"}:
            return GovernanceBaselineApprovalOutcome(self.approval_state)
        retained = command
        if self.approval_state == "unchanged":
            retained = _command(command.actor)
        if self.retained_operation_id is not None:
            retained = replace(retained, operation_id=self.retained_operation_id)
        return GovernanceBaselineApprovalOutcome(
            self.approval_state,
            _record(retained),
        )


def test_read_distinguishes_absence_and_projects_complete_reconstructible_state() -> None:
    active_client = _client(_UseCase())
    active = active_client.get(PATH)
    absent_client = _client(_UseCase(read_state="absent"))
    absent = absent_client.get(PATH)

    assert active.status_code == 200
    body = active.json()
    assert body["state"] == "active"
    assert body["scope"] == {"installationId": 1, "repositoryId": 2}
    assert body["baseline"]["authority"] == "approved_expected_state"
    assert body["baseline"]["state"]["stateDigest"] == _state_digest()
    assert body["baseline"]["state"]["repository"]["scope"] == body["scope"]
    assert body["baseline"]["state"]["rules"][0]["canonicalJson"] == (
        _state().rules[0].canonical_text
    )
    assert absent.status_code == 200
    assert absent.json() == {
        "ok": True,
        "state": "absent",
        "scope": {"installationId": 1, "repositoryId": 2},
        "baseline": None,
    }
    assert active.headers["cache-control"] == absent.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    ("state", "status_code"),
    [
        ("accepted", 201),
        ("duplicate", 200),
        ("unchanged", 200),
        ("forbidden", 403),
        ("stale", 409),
        ("baseline_conflict", 409),
        ("operation_conflict", 409),
        ("unavailable", 503),
    ],
)
def test_approval_preserves_complete_outcome_algebra(
    state: GovernanceBaselineApprovalState,
    status_code: int,
) -> None:
    use_case = _UseCase(approval_state=state)
    client = _client(use_case)

    response = client.post(
        PATH,
        json=(
            _body(
                expected_active=_record(_command(ACTOR)).pointer,
            )
            if state == "unchanged"
            else _body()
        ),
    )

    assert response.status_code == status_code
    if state in {"accepted", "duplicate", "unchanged"}:
        body = response.json()
        assert body["state"] == state
        assert body["requestOperationId"] == "approve-1"
        assert body["baseline"]["operationId"] == "approve-1"
    else:
        assert response.json() == {"ok": False, "error": state}


def test_approval_binds_session_actor_scope_and_retained_operation_separately() -> None:
    retained = _record(
        replace(
            _command(ACTOR),
            operation_id="retained-operation",
        )
    )
    use_case = _UseCase(
        approval_state="unchanged",
        retained_operation_id="retained-operation",
    )
    client = _client(use_case)

    response = client.post(
        PATH,
        json=_body(expected_active=retained.pointer),
    )

    command = use_case.calls[0][1]
    assert isinstance(command, GovernanceBaselineCommand)
    assert command.actor == ACTOR
    assert command.scope == SCOPE
    assert command.reason == "Adopt repository governance"
    assert response.json()["requestOperationId"] == "approve-1"
    assert response.json()["baseline"]["operationId"] == "retained-operation"


@pytest.mark.parametrize("mutation", ["scope", "operation"])
def test_approval_response_rejects_impossible_backend_identity(mutation: str) -> None:
    command = _command(ACTOR)
    response = governance_baseline_approval_response(
        GovernanceBaselineApprovalOutcome("accepted", _record(command)),
        command=command,
    )
    body = json.loads(bytes(response.body))
    invalid = deepcopy(body)
    if mutation == "scope":
        invalid["scope"]["repositoryId"] = 3
    else:
        invalid["state"] = "duplicate"
        invalid["baseline"]["operationId"] = "retained-operation"

    with pytest.raises(ValidationError):
        GovernanceBaselineApprovalResponse.model_validate(
            invalid,
            by_alias=True,
            by_name=False,
        )


@pytest.mark.parametrize("state", ["accepted", "duplicate", "unchanged"])
def test_approval_projection_rejects_a_result_that_contradicts_its_command(
    state: GovernanceBaselineApprovalState,
) -> None:
    command = _command(ACTOR)
    record = _record(
        replace(command, reason="Different retained command") if state != "unchanged" else command
    )

    with pytest.raises(RuntimeError, match="contradicts its command"):
        governance_baseline_approval_response(
            GovernanceBaselineApprovalOutcome(state, record),
            command=command,
        )


def test_read_projection_rejects_a_record_from_another_scope() -> None:
    record = _record(_command(ACTOR))

    with pytest.raises(ValidationError):
        governance_baseline_read_response(
            GovernanceBaselineReadOutcome("active", record),
            RepositoryScope(1, 3),
        )


def test_mutation_rejects_auth_integrity_and_body_failures_before_dispatch() -> None:
    use_case = _UseCase()
    unauthenticated = _client(use_case, authenticated=False).post(PATH, json=_body())
    forbidden = _client(use_case, mutation_admitted=False).post(PATH, json=_body())
    noncanonical = _client(use_case).post(
        PATH,
        json={**_body(), "reason": " padded "},
    )
    oversized = _client(use_case).post(
        PATH,
        content=b"{" + b" " * 4_096 + b"}",
        headers={"Content-Type": "application/json"},
    )

    assert unauthenticated.status_code == 401
    assert forbidden.status_code == 403
    assert noncanonical.status_code == 422
    assert oversized.status_code == 413
    assert use_case.calls == []


def test_unexpected_read_or_approval_failure_is_a_noncacheable_internal_error() -> None:
    client = _client(_ExplodingUseCase(), raise_server_exceptions=False)
    read = client.get(PATH)
    approval = client.post(PATH, json=_body())

    for response in (read, approval):
        assert response.status_code == 500
        assert response.json() == {"code": "internal_error"}
        assert response.headers["cache-control"] == "no-store"


class _ExplodingUseCase:
    async def read_active(
        self,
        *,
        actor: str,
        scope: RepositoryScope,
    ) -> GovernanceBaselineReadOutcome:
        del actor, scope
        raise RuntimeError("sentinel")

    async def accept(
        self,
        command: GovernanceBaselineCommand,
    ) -> GovernanceBaselineApprovalOutcome:
        del command
        raise RuntimeError("sentinel")


def _client(
    use_case: GovernanceBaselineUseCase,
    *,
    authenticated: bool = True,
    mutation_admitted: bool = True,
    raise_server_exceptions: bool = True,
) -> TestClient:
    return TestClient(
        create_app(
            HttpRouteDependencies(
                governance_baseline=GovernanceBaselineRouteDependencies(
                    authenticator=StaticControlPlaneAuthenticator(
                        human_principal() if authenticated else InvalidCredential()
                    ),
                    role_admission=StaticRoleAdmission(),
                    mutation_admission=StaticMutationAdmission(mutation_admitted),
                    use_case=use_case,
                ),
            )
        ),
        base_url=PUBLIC_ORIGIN,
        raise_server_exceptions=raise_server_exceptions,
    )


def _command(actor: str) -> GovernanceBaselineCommand:
    return GovernanceBaselineCommand(
        scope=SCOPE,
        operation_id="approve-1",
        expected_state_digest=_state_digest(),
        expected_active=None,
        actor=actor,
        reason="Adopt repository governance",
    )


def _record(command: GovernanceBaselineCommand) -> GovernanceBaselineRecord:
    draft = GovernanceBaselineDraft(command, _state(), NOW)
    prepared = prepare_governance_baseline(
        draft,
        approved_at=NOW + timedelta(seconds=1),
    )
    return GovernanceBaselineRecord.from_draft(
        draft,
        approved_at=prepared.approved_at,
        audit_event_id="audit_" + "a" * 32,
        audit_input_hash=prepared.audit_input_hash,
    )


def _state_digest() -> str:
    return sha256_hex(encode_governance_state(_state()))


def _state() -> GovernanceState:
    value = {
        "parameters": {"required_status_checks": ["CI"]},
        "ruleset_id": 41,
        "ruleset_source": "example/repository",
        "ruleset_source_type": "Repository",
        "type": "required_status_checks",
    }
    rule = EffectiveGovernanceRule(
        rule_type="required_status_checks",
        ruleset_source_type="Repository",
        ruleset_source="example/repository",
        ruleset_id=41,
        canonical_json=canonical_json(value),
    )
    return GovernanceState(
        GovernanceRepository(
            SCOPE,
            owner_id=101,
            owner="example",
            name="repository",
            full_name="example/repository",
            default_branch="master",
        ),
        "2026-03-10",
        (rule,),
    )

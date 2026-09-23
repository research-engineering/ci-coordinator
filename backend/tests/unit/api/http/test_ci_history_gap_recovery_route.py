import json
from unittest.mock import AsyncMock

import pytest
from ci_economics.gap_recovery_factories import gap_command
from ci_history_http_support import history_app as _app
from control_plane_http_support import human_principal
from fastapi.testclient import TestClient

from ci_coordinator.api.http.routers.ci_history import (
    HISTORY_GAP_REPAIR_PATH,
    MAX_HISTORY_BODY_BYTES,
)
from ci_coordinator.app.ci_history_administration import CiHistoryAdministrationUseCase
from ci_coordinator.ci_economics.history_gap_recovery import (
    HistoryGapRepairInterval,
    HistoryGapRepairReceipt,
    HistoryGapRepairResult,
    RepairHistoryGaps,
)

BODY = gap_command().request.model_dump(mode="json")


@pytest.mark.parametrize("outcome", ["committed", "replayed"])
def test_retry_route_enriches_actor_and_preserves_exact_receipt(outcome: str) -> None:
    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    command = RepairHistoryGaps.from_request(
        gap_command().request, actor=human_principal().actor_id
    )
    result = HistoryGapRepairResult.model_validate(
        {
            "outcome": outcome,
            "operationId": command.operation_id,
            "receipt": HistoryGapRepairReceipt(
                request=command.request,
                intervals=(
                    HistoryGapRepairInterval(workflowRunId=303, fromAttempt=1, throughAttempt=1),
                ),
            ),
        }
    )
    use_case.repair.return_value = result
    with TestClient(_app(use_case)) as client:
        response = client.post(HISTORY_GAP_REPAIR_PATH, json=BODY)
    assert response.status_code == 200 and response.json() == result.model_dump(mode="json")
    assert response.headers["cache-control"] == "no-store"
    use_case.repair.assert_awaited_once_with(command)


@pytest.mark.parametrize(
    "authority,integrity,roles,expected",
    [
        ("invalid", True, frozenset({"configure"}), 401),
        ("unavailable", True, frozenset({"configure"}), 503),
        ("valid", False, frozenset({"configure"}), 403),
        ("valid", True, frozenset({"audit"}), 403),
    ],
)
def test_authority_failure_has_no_repair_effect(
    authority: str, integrity: bool, roles: frozenset[str], expected: int
) -> None:
    from typing import cast

    from ci_coordinator.control_plane_identity import ControlPlaneRole

    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    with TestClient(
        _app(
            use_case,
            authority=authority,
            integrity=integrity,
            roles=cast(frozenset[ControlPlaneRole], roles),
        )
    ) as client:
        response = client.post(HISTORY_GAP_REPAIR_PATH, json=BODY)
    assert response.status_code == expected and not use_case.mock_calls


@pytest.mark.parametrize(
    "variant", ["actor", "duplicates", "fractional", "overflow", "media", "encoding", "query"]
)
def test_invalid_wire_input_never_reaches_application(variant: str) -> None:
    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    raw = json.dumps({**BODY, **({"actor": "injected"} if variant == "actor" else {})})
    if variant == "duplicates":
        raw = raw[:-1] + ',"generation":1}'
    if variant == "fractional":
        raw = raw.replace('"installationId": 101', '"installationId": 101.0')
    if variant == "overflow":
        raw += " " * (MAX_HISTORY_BODY_BYTES + 1)
    headers = {"content-type": "text/plain" if variant == "media" else "application/json"}
    if variant == "encoding":
        headers["content-encoding"] = "gzip"
    with TestClient(_app(use_case)) as client:
        response = client.post(
            HISTORY_GAP_REPAIR_PATH + ("?actor=admin" if variant == "query" else ""),
            content=raw,
            headers=headers,
        )
    assert response.status_code in {400, 413, 422} and not use_case.mock_calls


@pytest.mark.parametrize(
    "outcome",
    [
        "capacity_reached",
        "unsupported_gap",
        "revision_conflict",
        "generation_conflict",
        "operation_conflict",
    ],
)
def test_typed_refusal_never_becomes_success(outcome: str) -> None:
    use_case = AsyncMock(spec=CiHistoryAdministrationUseCase)
    use_case.repair.return_value = HistoryGapRepairResult.model_validate(
        {"outcome": outcome, "operationId": BODY["operationId"], "receipt": None}
    )
    with TestClient(_app(use_case)) as client:
        response = client.post(HISTORY_GAP_REPAIR_PATH, json=BODY)
    assert response.status_code == 409 and response.json()["receipt"] is None

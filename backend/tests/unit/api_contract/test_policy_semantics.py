from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from config_epoch_support import CONFIG_SOURCE
from hypothesis import HealthCheck, given, seed, settings
from hypothesis import strategies as st

from ci_coordinator.config_control import RepositoryScope

from .harness import Harness
from .oracles import validate_response
from .profile import HEADERS, REQUEST_TIMEOUT_SECONDS, VALIDATION_PATH, campaign_from_environment

CAMPAIGN = campaign_from_environment()


@seed(CAMPAIGN.seed)
@settings(
    max_examples=CAMPAIGN.max_examples,
    database=None,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    installation_id=st.integers(min_value=1, max_value=9_007_199_254_740_991),
    repository_id=st.integers(min_value=1, max_value=9_007_199_254_740_991),
)
def test_generated_policy_has_independent_positive_and_negative_expectations(
    harness: Harness, installation_id: int, repository_id: int
) -> None:
    document: dict[str, Any] = json.loads(CONFIG_SOURCE)
    document["repository"].update(installationId=installation_id, repositoryId=repository_id)
    for valid in (True, False):
        document["repository"]["rules"][0]["expectedSignals"][0]["requiredConclusion"] = (
            "success" if valid else "failure"
        )
        source = json.dumps(document, separators=(",", ":"))
        with harness.example() as client:
            case = harness.schema[VALIDATION_PATH]["POST"].Case(
                body={
                    "schemaVersion": "ci-config-epoch-validation/v1",
                    "sourceFormat": "json",
                    "source": source,
                },
                media_type="application/json",
            )
            response = case.call(
                session=client, headers=dict(HEADERS), timeout=REQUEST_TIMEOUT_SECONDS
            )
            assert response.status_code == (200 if valid else 422)
            body = response.json()
            if valid:
                assert body["ok"] is True
                assert (body["installationId"], body["repositoryId"]) == (
                    installation_id,
                    repository_id,
                )
                assert (
                    body["sourceHash"]
                    == sha256(b"ci-policy-source/v1\0json\0" + source.encode()).hexdigest()
                )
                assert harness.ports.scope_calls == [
                    (
                        harness.ports.principal.actor_id,
                        RepositoryScope(installation_id, repository_id),
                    )
                ]
            else:
                assert body["ok"] is False
                assert body["error"] == "invalid_config"
                assert body["diagnostics"] == [
                    {
                        "code": "semantics.invalid",
                        "phase": "semantics",
                        "ruleId": "signal.success-conclusion",
                        "instancePointer": (
                            "/repository/rules/0/expectedSignals/0/requiredConclusion"
                        ),
                    }
                ]
                assert harness.ports.scope_calls == []
            validate_response(case, response, harness.ports)

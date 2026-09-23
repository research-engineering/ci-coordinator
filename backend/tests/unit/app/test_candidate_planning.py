from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Never

import pytest
from planning_command_support import dynamic_plan_command as _command
from prometheus_support import prometheus_samples

from ci_coordinator.app import DeterministicCandidatePlanner
from ci_coordinator.config_control import ValidatedEpochDraft, admit_policy_document
from ci_coordinator.config_epochs import ActiveConfigEpoch, ActiveConfigEpochSnapshot
from ci_coordinator.integrations.github.repository_context import (
    GitHubRepositoryContextProvider,
)
from ci_coordinator.observability import RuntimeMetrics

NOW = datetime(2026, 7, 15, tzinfo=UTC)


class _UnavailableTransportFactory:
    def for_installation(self, installation_id: int) -> Never:
        del installation_id
        raise RuntimeError("provider unavailable")


class _FailingContexts:
    def __init__(self, failure: BaseException) -> None:
        self._failure = failure
        self.calls = 0

    async def load(self, request: object, projection: object) -> Never:
        del request, projection
        self.calls += 1
        raise self._failure


def test_provider_unavailability_produces_a_verified_full_ci_candidate() -> None:
    planner = DeterministicCandidatePlanner(
        GitHubRepositoryContextProvider(_UnavailableTransportFactory())
    )

    result = asyncio.run(planner.build_candidate(_command(NOW), _active_epoch()))

    assert result is not None
    assert result.fallback.triggered is True
    assert result.fallback.reason is not None
    assert tuple(
        (obligation.obligation_id, obligation.depth, obligation.required_witness_ids)
        for obligation in result.selected_obligations
    ) == (("backend-tests", "full", ("python-tests",)),)
    assert tuple(
        (witness.witness_id, witness.depth, witness.required_by_obligation_ids)
        for witness in result.selected_witnesses
    ) == (("python-tests", "full", ("backend-tests",)),)
    assert result.catalog.catalog_hash == result.source_plan.catalog_hash


def test_ordinary_context_failure_withholds_the_candidate() -> None:
    metrics = RuntimeMetrics()
    planner = DeterministicCandidatePlanner(
        _FailingContexts(RuntimeError("unavailable")),
        metrics,
    )

    result = asyncio.run(planner.build_candidate(_command(NOW), _active_epoch()))

    assert result is None
    assert (
        prometheus_samples(metrics)[
            (
                "ci_coordinator_planning_unavailable_total",
                (("reason", "candidate_exception"), ("stage", "candidate")),
            )
        ]
        == 1
    )


def test_observe_only_epoch_withholds_candidate_without_loading_context() -> None:
    contexts = _FailingContexts(AssertionError("context must not be loaded"))
    planner = DeterministicCandidatePlanner(contexts)

    result = asyncio.run(planner.build_candidate(_command(NOW), _active_epoch(dynamic=False)))

    assert result is None
    assert contexts.calls == 0


def test_cancellation_propagates_without_becoming_a_fallback() -> None:
    planner = DeterministicCandidatePlanner(_FailingContexts(asyncio.CancelledError()))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(planner.build_candidate(_command(NOW), _active_epoch()))


def test_local_projection_failure_is_not_misclassified_as_provider_unavailability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_projection(_: object) -> Never:
        raise AssertionError("local projection defect")

    monkeypatch.setattr(
        "ci_coordinator.app.candidate_planning.project_dynamic_ci_planning",
        fail_projection,
    )
    planner = DeterministicCandidatePlanner(_FailingContexts(RuntimeError("must not run")))

    with pytest.raises(AssertionError, match="local projection defect"):
        asyncio.run(planner.build_candidate(_command(NOW), _active_epoch()))


def _active_epoch(*, dynamic: bool = True) -> ActiveConfigEpochSnapshot:
    draft = _admitted_draft(dynamic=dynamic)
    return ActiveConfigEpochSnapshot(
        ActiveConfigEpoch(draft.scope, draft.epoch_id, 1),
        draft,
    )


def _admitted_draft(*, dynamic: bool = True) -> ValidatedEpochDraft:
    source = {
        "schemaVersion": "ci-repository-policy/v1",
        "repository": {
            "installationId": 100,
            "repositoryId": 200,
            "owner": "example-org",
            "name": "ci-coordinator",
            "defaultBranch": "main",
            "rules": [
                {
                    "name": "main",
                    "on": {"event": "push", "branches": ["main"]},
                    "mode": "observe",
                    "expectedSignals": [
                        {
                            "kind": "workflow",
                            "name": "CI",
                            "workflowFile": "ci.yml",
                            "source": "native",
                            "requiredConclusion": "success",
                            "required": True,
                        }
                    ],
                }
            ],
            "dynamicCi": {
                "planningEnabled": True,
                "policyVersion": "p1",
                "riskClasses": ["backend"],
                "agentAdvice": {
                    "enabled": False,
                    "modelIdAllowlist": [],
                    "promptHashAllowlist": [],
                    "minConfidence": 0.7,
                    "promptInjectionEvalRequired": False,
                },
                "dependencyGraph": {
                    "source": "configured",
                    "globalRiskPaths": [".github/workflows/**"],
                },
                "fallbackTimeoutSeconds": 60,
                "obligations": [
                    {
                        "obligationId": "backend-tests",
                        "responsibility": {
                            "paths": ["backend/**"],
                            "riskClasses": ["backend"],
                        },
                        "requiredWitnessIds": ["python-tests"],
                        "defaultDepth": "targeted",
                        "fullDepth": "full",
                        "omitAllowed": True,
                    }
                ],
                "witnesses": [
                    {
                        "witnessId": "python-tests",
                        "executionProfileId": "python-linux",
                        "supportedDepths": ["targeted", "full"],
                    }
                ],
                "executionProfiles": [
                    {
                        "profileId": "python-linux",
                        "runnerProfileId": "ubuntu-24.04",
                        "permissionProfileId": "contents-read",
                        "credentialProfileId": "none",
                        "fixtureProfileId": "unit",
                        "serviceProfileIds": [],
                        "capacityClassId": "self-hosted-default",
                        "shardingPolicy": {
                            "maxShards": 8,
                            "maxParallel": 8,
                            "maxItemsPerShard": 1000,
                            "setupSecondsPerShard": 10.0,
                            "cpuWeight": 1.0,
                            "wallWeight": 1.0,
                            "operatorWeight": 1.0,
                        },
                    }
                ],
            },
        },
    }
    if not dynamic:
        repository = source["repository"]
        assert isinstance(repository, dict)
        repository["dynamicCi"] = None
    result = admit_policy_document(
        json.dumps(source, separators=(",", ":")).encode("utf-8"),
        "json",
    )
    assert isinstance(result, ValidatedEpochDraft)
    return result

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from app.test_candidate_planning import _admitted_draft
from app.test_dynamic_plan_service import (
    NOW,
    _ActiveEpochs,
    _authority,
    _Capacity,
    _identity,
    _issuer,
    _planning,
    _Reconciliation,
    _request,
)
from integrations.test_github_repository_context import (
    _contents_response,
    _Factory,
    _graph,
    _pull_request_metadata,
    _repository_identity,
    _response,
    _Transport,
    _valid_handler,
)
from package_b_support import make_execution_projection

from ci_coordinator.app import DeterministicCandidatePlanner, DynamicPlanCommand, DynamicPlanService
from ci_coordinator.config_control import ValidatedEpochDraft, admit_policy_document
from ci_coordinator.config_control.planning_projection import project_dynamic_ci_planning
from ci_coordinator.config_epochs import ActiveConfigEpoch, ActiveConfigEpochSnapshot
from ci_coordinator.integrations.github.contracts import (
    GitHubRequest,
    GitHubTransportFailure,
    GitHubTransportResult,
)
from ci_coordinator.integrations.github.repository_context import (
    DEPENDENCY_GRAPH_PATH,
    GitHubRepositoryContextProvider,
)
from ci_coordinator.plan_issuance import FullCiExecution, Issued, SelectedExecution


@pytest.mark.parametrize(
    ("event", "drift"),
    [
        ("push", "repository"),
        ("push", "provider"),
        ("push", "policy"),
        ("pull_request", "repository"),
        ("pull_request", "pr_base"),
        ("pull_request", "pr_head"),
    ],
)
def test_prepared_facts_cannot_override_fresh_selected_issuance_authority(
    event: str, drift: str
) -> None:
    async def scenario() -> None:
        request, identity = _request(), _identity()
        if event == "pull_request":
            request = replace(
                request, event_name="pull_request", ref="refs/pull/7/merge", pull_request_number=7
            )
            identity = replace(identity, event_name=event, ref=request.ref)
        command = DynamicPlanCommand(request, identity)
        source = json.loads(_admitted_draft().source_bytes)
        dynamic = source["repository"]["dynamicCi"]
        dynamic["obligations"].append(
            {
                **dynamic["obligations"][0],
                "obligationId": "documentation-checks",
                "responsibility": {"paths": ["docs/**"], "riskClasses": []},
                "requiredWitnessIds": ["documentation-tests"],
            }
        )
        dynamic["witnesses"].append({**dynamic["witnesses"][0], "witnessId": "documentation-tests"})
        draft = admit_policy_document(json.dumps(source).encode(), "json")
        assert isinstance(draft, ValidatedEpochDraft)
        active = ActiveConfigEpochSnapshot(ActiveConfigEpoch(draft.scope, draft.epoch_id, 1), draft)
        epochs = _ActiveEpochs(active)
        projection = project_dynamic_ci_planning(active.draft)
        assert projection is not None
        graph = json.loads(_graph())
        graph["provenance"]["source"] = "configured"
        graph["globalRiskPaths"] = [".github/workflows/**"]
        changed = False

        def handler(call: GitHubRequest) -> GitHubTransportResult:
            if changed and drift == "provider":
                return GitHubTransportFailure(kind="unavailable", message="access lost")
            if call.operation == "repositories.get_by_id":
                return _response(
                    _repository_identity(
                        repository_id=request.repository_id,
                        owner=request.owner,
                        name="renamed" if changed and drift == "repository" else request.repository,
                    )
                )
            if call.operation == "diff.get_pull_request":
                return _response(
                    _pull_request_metadata(
                        number=7,
                        base_sha="d" * 40 if changed and drift == "pr_base" else request.base_sha,
                        head_sha="d" * 40 if changed and drift == "pr_head" else request.head_sha,
                    )
                )
            if call.operation == "workflow_catalog.get_content":
                return _response(
                    _contents_response(DEPENDENCY_GRAPH_PATH, json.dumps(graph).encode())
                )
            return _valid_handler()(call)

        transport = _Transport(handler)
        contexts = GitHubRepositoryContextProvider(_Factory(transport))
        candidates = DeterministicCandidatePlanner(contexts)
        verified = await candidates.build_candidate(command, active)
        assert verified is not None and not verified.fallback.triggered
        assert verified.selected_witnesses
        assert tuple(item.obligation_id for item in verified.omitted_obligations) == (
            "documentation-checks",
        )
        execution = make_execution_projection(verified, free_slots=2, now=NOW)
        authority = _authority(verified, execution, identity=identity)
        reconciliation = _Reconciliation(True)

        async def issue() -> Issued:
            # A fresh real store isolates current admission from earlier request idempotency.
            service = DynamicPlanService(
                active_epochs=epochs,
                candidates=candidates,
                issuer=_issuer(),
                capacity=_Capacity(_planning(execution)),
                reconciliation=reconciliation,
                enforcement_authority=authority,
            )
            result = await service.request_dynamic_plan(command)
            assert isinstance(result, Issued)
            return result

        cold = await issue()
        assert isinstance(cold.record.envelope.payload.execution, SelectedExecution)
        context = await contexts.load(request, projection)
        assert (
            await contexts.prepare(context.repo_epoch, context.planning_input.policy) == "prepared"
        )
        transport.requests.clear()
        warm = await issue()
        assert warm.record.envelope.payload.execution == cold.record.envelope.payload.execution
        assert warm.record.envelope.payload.verified_plan_id == verified.execution_plan_id
        assert [call.operation for call in transport.requests] == (
            ["repositories.get_by_id", "diff.get_pull_request"]
            if event == "pull_request"
            else ["repositories.get_by_id"]
        )
        changed = True
        if drift == "policy":
            epochs.snapshot = None
        transport.requests.clear()
        reconciliation.calls.clear()
        rejected = await issue()
        assert isinstance(rejected.record.envelope.payload.execution, FullCiExecution)
        assert rejected.record.envelope.payload.verified_plan_id is None
        assert not any(not full_ci for full_ci, _ in reconciliation.calls)
        if drift == "policy":
            assert transport.requests == []
        contexts.close_prepared_contexts()

    asyncio.run(scenario())

from __future__ import annotations

from dataclasses import fields, replace

import pytest
from planning_core._support import make_input_value, make_policy_value

from ci_coordinator.kernel import hash_object
from ci_coordinator.planning_core.model import (
    DeterministicPlan,
    OmissionPredicates,
    OmissionProof,
    OmittedObligation,
    PlanEvidence,
    PlanFallback,
    SelectedObligation,
    SelectedWitness,
)
from ci_coordinator.planning_core.policy import PlanningPolicy
from ci_coordinator.repo_context import (
    DependencyGraphContext,
    DependencyGraphNode,
    DiffFileChangeInput,
    PlanningInput,
)
from ci_coordinator.repo_context.dependency_graph import _graph_context_hash
from ci_coordinator.verification_core import (
    VerifiedPlan,
    admit_deterministic_plan,
    validate_omission_proof,
    verify,
)


def test_independent_fixture_admits_a_valid_omission_and_a_conservative_superset() -> None:
    input = _input()
    policy = make_policy_value()
    selective = _candidate(input, policy)
    superset = _candidate(input, policy, include_backend=True)

    assert validate_omission_proof(input, policy, selective.omitted_obligations[0]) is True
    for candidate in (selective, superset):
        assert admit_deterministic_plan(input, policy, candidate) is None
        verified = verify(input, policy, candidate)
        assert verified.fallback.triggered is False
        assert verified.source_plan == candidate
        assert verified.selected_obligations == candidate.selected_obligations
        assert verified.selected_witnesses == candidate.selected_witnesses
        assert verified.omitted_obligations == candidate.omitted_obligations


@pytest.mark.parametrize(
    "fault",
    (
        "input-reason",
        "diff-reason",
        "truncated",
        "incomplete",
        "file-count",
        "file-limit",
        "stale-graph",
        "base-sha",
        "head-sha",
        "admitted_repo_epoch_hash",
        "admitted_diff_hash",
        "admitted_config_epoch_id",
        "admitted_policy_hash",
        "admitted_compiled_policy_hash",
        "diff-hash",
        "input-hash",
    ),
)
def test_verifier_rejects_invalid_context_with_rebound_candidate_coordinates(fault: str) -> None:
    policy = make_policy_value()
    input = _invalid_context(_input(), fault)
    candidate = _candidate(input, policy)

    assert admit_deterministic_plan(input, policy, candidate) == "deterministic_plan_mismatch"
    assert validate_omission_proof(input, policy, candidate.omitted_obligations[0]) is False
    _assert_full_ci(verify(input, policy, candidate), "deterministic_plan_mismatch")


@pytest.mark.parametrize("field_name", ("config_epoch_id", "compiled_policy_hash", "policy_hash"))
def test_public_omission_validator_rejects_foreign_policy_even_with_rebound_proof(
    field_name: str,
) -> None:
    input = _input()
    current = make_policy_value()
    policy = replace(
        current,
        config_epoch_id="f" * 64 if field_name == "config_epoch_id" else current.config_epoch_id,
        compiled_policy_hash="f" * 64
        if field_name == "compiled_policy_hash"
        else current.compiled_policy_hash,
        policy_hash="f" * 64 if field_name == "policy_hash" else current.policy_hash,
    )
    candidate = _candidate(input, policy)
    omitted = candidate.omitted_obligations[0]

    assert omitted.proof.policy_hash == policy.policy_hash
    assert omitted.proof.catalog_hash == policy.catalog_hash
    assert validate_omission_proof(input, policy, omitted) is False
    _assert_full_ci(verify(input, policy, candidate), "deterministic_plan_mismatch")


@pytest.mark.parametrize(
    ("field_name", "identity_key"),
    (
        ("config_epoch_id", "configEpochId"),
        ("repo_epoch_hash", "repoEpochHash"),
        ("input_hash", "inputHash"),
        ("diff_hash", "diffHash"),
        ("compiled_policy_hash", "compiledPolicyHash"),
        ("policy_hash", "policyHash"),
        ("dependency_graph_hash", "dependencyGraphHash"),
        ("catalog_hash", "catalogHash"),
    ),
)
def test_verifier_checks_each_candidate_coordinate_after_identity_resealing(
    field_name: str,
    identity_key: str,
) -> None:
    input = _input()
    policy = make_policy_value()
    candidate = _candidate(input, policy)
    identity = candidate.identity_mapping()
    identity[identity_key] = "f" * 64
    candidate = replace(
        candidate,
        plan_id="dynamic_ci_plan_" + hash_object(identity)[:32],
        config_epoch_id="f" * 64 if field_name == "config_epoch_id" else candidate.config_epoch_id,
        repo_epoch_hash="f" * 64 if field_name == "repo_epoch_hash" else candidate.repo_epoch_hash,
        input_hash="f" * 64 if field_name == "input_hash" else candidate.input_hash,
        diff_hash="f" * 64 if field_name == "diff_hash" else candidate.diff_hash,
        compiled_policy_hash="f" * 64
        if field_name == "compiled_policy_hash"
        else candidate.compiled_policy_hash,
        policy_hash="f" * 64 if field_name == "policy_hash" else candidate.policy_hash,
        dependency_graph_hash="f" * 64
        if field_name == "dependency_graph_hash"
        else candidate.dependency_graph_hash,
        catalog_hash="f" * 64 if field_name == "catalog_hash" else candidate.catalog_hash,
    )

    assert validate_omission_proof(input, policy, candidate.omitted_obligations[0]) is True
    _assert_full_ci(verify(input, policy, candidate), "deterministic_plan_mismatch")


@pytest.mark.parametrize(
    "field_name",
    ("repo_epoch_hash", "diff_hash", "policy_hash", "graph_hash", "catalog_hash", "rule_id"),
)
def test_verifier_checks_each_omission_coordinate_after_identity_resealing(field_name: str) -> None:
    input = _input()
    policy = make_policy_value()
    omitted = _candidate(input, policy).omitted_obligations[0]
    proof = omitted.proof
    omitted = replace(
        omitted,
        proof=replace(
            proof,
            repo_epoch_hash="f" * 64 if field_name == "repo_epoch_hash" else proof.repo_epoch_hash,
            diff_hash="f" * 64 if field_name == "diff_hash" else proof.diff_hash,
            policy_hash="f" * 64 if field_name == "policy_hash" else proof.policy_hash,
            graph_hash="f" * 64 if field_name == "graph_hash" else proof.graph_hash,
            catalog_hash="f" * 64 if field_name == "catalog_hash" else proof.catalog_hash,
            rule_id="f" * 64 if field_name == "rule_id" else proof.rule_id,
        ),
    )
    candidate = _candidate(input, policy, omitted=omitted)

    assert validate_omission_proof(input, policy, omitted) is False
    _assert_full_ci(verify(input, policy, candidate), "omission_proof_mismatch")


@pytest.mark.parametrize(
    ("changed_path", "nodes", "global_risk_paths"),
    (
        (
            "docs/guide.md",
            (DependencyGraphNode("docs/guide.md", (), ("docs",)),),
            ("docs/**",),
        ),
        (
            "docs/guide.md",
            (DependencyGraphNode("docs/guide.md", (), ("backend",)),),
            (),
        ),
        (
            "docs/guide.md",
            (
                DependencyGraphNode("docs/guide.md", ("lib/adapter.py",), ("docs",)),
                DependencyGraphNode("lib/adapter.py", (), ("backend",)),
            ),
            (),
        ),
        (
            "docs/guide.md",
            (
                DependencyGraphNode("docs/guide.md", ("src/service.py",), ("docs",)),
                DependencyGraphNode("src/service.py", (), ()),
            ),
            (),
        ),
        ("docs/unknown.md", (DependencyGraphNode("docs/guide.md", (), ("docs",)),), ()),
    ),
    ids=("global-risk", "direct-risk-only", "transitive-risk-only", "transitive-path", "unknown"),
)
def test_verifier_rederives_impact_instead_of_trusting_current_omission_coordinates(
    changed_path: str,
    nodes: tuple[DependencyGraphNode, ...],
    global_risk_paths: tuple[str, ...],
) -> None:
    policy = make_policy_value()
    input = make_input_value(
        DiffFileChangeInput(path=changed_path, status="modified"),
        graph_nodes=nodes,
        global_risk_paths=global_risk_paths,
    )
    candidate = _candidate(input, policy)

    assert not input.fallback_reasons
    assert input.dependency_graph.fresh
    assert admit_deterministic_plan(input, policy, candidate) == "omission_proof_mismatch"
    assert validate_omission_proof(input, policy, candidate.omitted_obligations[0]) is False
    _assert_full_ci(verify(input, policy, candidate), "omission_proof_mismatch")


def _input() -> PlanningInput:
    return make_input_value(
        DiffFileChangeInput(path="docs/extra.md", status="modified"),
        DiffFileChangeInput(path="docs/guide.md", status="modified"),
        graph_nodes=(
            DependencyGraphNode("docs/extra.md", (), ("docs",)),
            DependencyGraphNode("docs/guide.md", (), ("docs",)),
        ),
    )


def _invalid_context(input: PlanningInput, fault: str) -> PlanningInput:
    if fault.startswith("admitted_"):
        return _seal_input(
            replace(input, dependency_graph=_graph(input.dependency_graph, **{fault: "f" * 64}))
        )
    if fault == "input-hash":
        return replace(input, input_hash="f" * 64)
    if fault == "stale-graph":
        return _seal_input(
            replace(
                input,
                dependency_graph=_graph(
                    input.dependency_graph,
                    fresh=False,
                    invalidating_reasons=("graph_head_mismatch",),
                ),
            )
        )
    if fault == "input-reason":
        return replace(input, fallback_reasons=("provider_unavailable",))
    if fault in {"base-sha", "head-sha"}:
        input = replace(
            input,
            repo_epoch=replace(
                input.repo_epoch,
                base_sha="f" * 40 if fault == "base-sha" else input.repo_epoch.base_sha,
                head_sha="f" * 40 if fault == "head-sha" else input.repo_epoch.head_sha,
            ),
        )
    elif fault == "diff-reason":
        input = replace(
            input, diff=replace(input.diff, invalidating_reasons=("provider_incomplete",))
        )
    elif fault == "truncated":
        input = replace(input, diff=replace(input.diff, truncated=True))
    elif fault in {"incomplete", "file-count", "file-limit"}:
        source = input.diff.source
        if fault == "incomplete":
            source = replace(source, complete=False)
        elif fault == "file-count":
            source = replace(source, file_count=3)
        else:
            source = replace(source, max_files=1)
        input = replace(input, diff=replace(input.diff, source=source))
    elif fault != "diff-hash":
        raise AssertionError("unknown context fault: " + fault)
    diff_hash = "f" * 64 if fault == "diff-hash" else hash_object(input.diff.to_identity_mapping())
    input = replace(
        input,
        diff=replace(input.diff, diff_hash=diff_hash),
        dependency_graph=_graph(
            input.dependency_graph,
            admitted_repo_epoch_hash=hash_object(input.repo_epoch.to_identity_mapping()),
            admitted_diff_hash=diff_hash,
        ),
    )
    return _seal_input(input)


def _graph(graph: DependencyGraphContext, **changes: object) -> DependencyGraphContext:
    values = {
        field.name: getattr(graph, field.name)
        for field in fields(graph)
        if field.name != "graph_hash"
    }
    values.update(changes)
    return DependencyGraphContext(graph_hash=_graph_context_hash(**values), **values)


def _seal_input(input: PlanningInput) -> PlanningInput:
    return replace(
        input,
        input_hash=hash_object(
            {
                "repoEpoch": input.repo_epoch.to_identity_mapping(),
                "configEpochId": input.policy.epoch_id,
                "compiledPolicyHash": input.policy.compiled_policy_hash,
                "diffHash": input.diff.diff_hash,
                "policyHash": input.policy.policy_hash,
                "dependencyGraphHash": input.dependency_graph.graph_hash,
            }
        ),
    )


def _candidate(
    input: PlanningInput,
    policy: PlanningPolicy,
    *,
    omitted: OmittedObligation | None = None,
    include_backend: bool = False,
) -> DeterministicPlan:
    selected: tuple[SelectedObligation, ...] = (
        SelectedObligation("docs-lint", "smoke", ("docs-witness", "shared-quality")),
        SelectedObligation("required-baseline", "standard", ("baseline-witness",)),
    )
    witnesses: tuple[SelectedWitness, ...] = (
        SelectedWitness("baseline-witness", "standard", ("required-baseline",)),
        SelectedWitness("docs-witness", "smoke", ("docs-lint",)),
        SelectedWitness("shared-quality", "smoke", ("docs-lint",)),
    )
    repo_epoch_hash = hash_object(input.repo_epoch.to_identity_mapping())
    omission = omitted or OmittedObligation(
        "backend-tests",
        ("backend-witness", "shared-quality"),
        OmissionProof(
            "backend-tests",
            "backend-tests:no-impact",
            repo_epoch_hash,
            input.diff.diff_hash,
            policy.policy_hash,
            input.dependency_graph.graph_hash,
            policy.catalog_hash,
            OmissionPredicates(),
            (
                "dependency graph includes all known dependents for changed paths",
                "validation catalog is closed over obligation and witness identities",
            ),
            (
                "changed path is unknown",
                "dependency graph is stale",
                "diff is truncated",
                "policy changes",
                "validation catalog changes",
            ),
        ),
    )
    if include_backend:
        selected = (
            SelectedObligation("backend-tests", "targeted", ("backend-witness", "shared-quality")),
            *selected,
        )
        witnesses = (
            SelectedWitness("backend-witness", "targeted", ("backend-tests",)),
            *witnesses[:2],
            SelectedWitness("shared-quality", "targeted", ("backend-tests", "docs-lint")),
        )
    omissions = () if include_backend else (omission,)
    evidence = (
        *(PlanEvidence("omitted", item.obligation_id, "independent fixture") for item in omissions),
        *(PlanEvidence("selected", item.obligation_id, "independent fixture") for item in selected),
    )
    fallback = PlanFallback(policy.fallback_timeout_seconds, False, None)
    identity = {
        "schemaVersion": "deterministic-plan/v1",
        "plannerVersion": "planning-core/v1",
        "configEpochId": input.policy.epoch_id,
        "repoEpochHash": repo_epoch_hash,
        "inputHash": input.input_hash,
        "diffHash": input.diff.diff_hash,
        "compiledPolicyHash": policy.compiled_policy_hash,
        "policyHash": policy.policy_hash,
        "dependencyGraphHash": input.dependency_graph.graph_hash,
        "catalogHash": policy.catalog_hash,
        "selectedObligations": [item.to_identity_mapping() for item in selected],
        "selectedWitnesses": [item.to_identity_mapping() for item in witnesses],
        "omittedObligations": [item.to_identity_mapping() for item in omissions],
        "fallback": fallback.to_identity_mapping(),
        "evidence": [item.to_identity_mapping() for item in evidence],
    }
    return DeterministicPlan(
        plan_id="dynamic_ci_plan_" + hash_object(identity)[:32],
        schema_version="deterministic-plan/v1",
        planner_version="planning-core/v1",
        config_epoch_id=input.policy.epoch_id,
        repo_epoch_hash=repo_epoch_hash,
        input_hash=input.input_hash,
        diff_hash=input.diff.diff_hash,
        compiled_policy_hash=policy.compiled_policy_hash,
        policy_hash=policy.policy_hash,
        dependency_graph_hash=input.dependency_graph.graph_hash,
        catalog_hash=policy.catalog_hash,
        selected_obligations=selected,
        selected_witnesses=witnesses,
        omitted_obligations=omissions,
        fallback=fallback,
        evidence=evidence,
    )


def _assert_full_ci(verified: VerifiedPlan, reason: str) -> None:
    assert verified.fallback.triggered is True
    assert verified.fallback.reason == reason
    assert verified.omitted_obligations == ()
    assert verified.selected_obligations == (
        SelectedObligation("backend-tests", "full", ("backend-witness", "shared-quality")),
        SelectedObligation("docs-lint", "standard", ("docs-witness", "shared-quality")),
        SelectedObligation("required-baseline", "full", ("baseline-witness",)),
    )
    assert verified.selected_witnesses == (
        SelectedWitness("backend-witness", "full", ("backend-tests",)),
        SelectedWitness("baseline-witness", "full", ("required-baseline",)),
        SelectedWitness("docs-witness", "standard", ("docs-lint",)),
        SelectedWitness("shared-quality", "full", ("backend-tests", "docs-lint")),
    )
    assert verified.deterministic_evidence == (PlanEvidence("fallback", None, reason),)

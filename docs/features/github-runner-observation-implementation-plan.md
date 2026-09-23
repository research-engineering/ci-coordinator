# GitHub Runner Observation Implementation Plan

Status: implementation and validation plan
Date: 2026-09-09
Design: [GitHub runner observation](github-runner-observation.md)

## Ordered Work

1. Freeze the current source and redacted provider counterexample. Preserve
   the pilot target workflow, the registered attempt, source time and retention.
2. Update only runner-reference decoding in `github_ingestion/workflow_job.py`
   and `integrations/github/reconciliation_observer_decoding.py`.
   Validate raw operands before erasing sentinels. Keep domain, persistence,
   dependency edges, hash algorithms, scopes and collection lifecycle unchanged.
3. Add a parameterized cross-boundary corpus with independent expected pairs.
   Exercise the public webhook preparation and REST page decoding boundaries,
   not just a private helper. Include hosted, self-hosted, unassigned and absent
   references; invalid types, bounds and incoherent pairs; normalization replay.
   Invalid-name cases must start from an accepted zero-reference control and
   change only the name. Unsafe integers and surrogate text must produce
   strict JSON rejection, not a prepared unsupported webhook.
4. Add a composed real observer/provider test with a fake wire transport and
   stable complete responses. Require a captured provider snapshot, two exact
   repository/run-attempt reads, preserved positive runner ID and absent group.
5. Route the new design and witnesses through the current requirement bindings,
   update the documentation index and record the pilot's actual roadmap state.
6. Run owner-admitted static lint/types/import/docs/Proofkit checks. Freeze the
   candidate for one independent reviewer under `AGENTS.md`. Native regression
   and whole-gate evidence must come from exact-head GitHub Full Check.
7. Squash only after admission and green required CI, inspect postmerge, publish
   an attested immutable release and deploy only the owned Coordinator service.
8. Confirm healthy rollout and real administrator login. Reread pilot target collection through
   the panel, retaining failure/duplicate outcomes. Record actual job durations
   separately from unknown CPU and queue/wall measurements.

## Acceptance Matrix

| Claim                                           | Sensitive witness                                                           |
|-------------------------------------------------|-----------------------------------------------------------------------------|
| Zero is a wire sentinel, not canonical identity | Hosted zero-group and fully unassigned zero-ID controls                     |
| Raw types remain strict                         | Bool, float, string, negative and unsafe-ID mutants                         |
| Name validation cannot be skipped               | Oversized, surrogate and non-string names with zero ID                      |
| References remain coherent                      | Missing/empty positive-ID name; null-ID nonempty name; group without runner |
| Both boundaries agree                           | Same corpus through signed webhook preparation and REST admission           |
| Canonicalization is stable                      | Project admitted pairs back to the wire; normalize again                    |
| Collection reaches the repaired path            | Native observer plus economics provider over complete wire responses        |
| Identity and outcome remain bound               | Existing foreign run/SHA, malformed status and pagination falsifiers        |
| Runtime repair works for the pilot              | Exact deployed artifact and original retained pilot target source reread           |

## Delivery Boundary

Local checks cannot close native or live rows. No schema migration, source
re-registration, retry reset, pilot target pull request or workflow dispatch belongs
to this batch. Persistent budgets, observation subscriptions and return-route
UX remain separate roadmap work; failure to complete them does not invalidate
this bounded correction and this correction does not complete them.

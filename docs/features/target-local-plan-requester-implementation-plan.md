# Target-Local Plan Requester Implementation Plan

The [design](target-local-plan-requester.md) owns the behavioral delta and
fallback premises. One PR covers requester distribution through exact local
admission. Existing design/plan payloads remain unchanged.

## Ordered Work

| Order | Owner and paths                                                                 | Change                                                                       | Native acceptance and falsifier                                                                                                       |
|-------|---------------------------------------------------------------------------------|------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------|
| 1     | `target_artifacts/requester.py`, package resources, `backend/pyproject.toml`    | Expose one bounded immutable requester resource and package it               | Source/package/target byte parity; invalid resource and missing package cannot be reported current                                    |
| 2     | `execution_orchestration/target_registry.py`, machine schemas                   | Admit the single local reference and require adapter-path closure            | Positive local/external controls; unknown path, omitted adapter and one-field identity changes rejected                               |
| 3     | `target_artifacts/renderer.py`, `cli.py`                                        | Check source requester digest; explicit render/check requester output        | Exact target bytes, drift exit status, no hidden parent-directory writes; modified digest rejected                                    |
| 4     | `integrations/github/adapter_snapshot.py`, import policy                        | Compare the exact loaded Git blob to the public packaged resource            | Self-consistent modified blob/registry rejected; no extra provider read or import into pure layers                                    |
| 5     | `app/dynamic_plan_service.py`, `consumer_contract_lab/composition.py`           | Bind caller and callee coordinates and construct matching lab identities     | Public planning rejects mismatched caller/callee without selected projection; local lab works without claiming live identity proof    |
| 6     | `target_artifacts/control_source/validation_execution.cjs` and generated bundle | Mirror local ref, adapter closure and exact caller/callee identity admission | Re-signed one-field requester mutations fail at the intended guard; valid local and external plans pass; generated bundle drift fails |
| 7     | Existing native requester, runtime and target proof routes                      | Register new source/tests/docs with existing requirements                    | Exact-head Full Check, installed package and actual reusable job pass; no local behavioral execution                                  |

No generic requester interface, new domain, database table, service, retry
queue, bot permission or deployment is needed. The new provider import is
restricted to the artifact capability; all existing forbidden domain/provider
edges remain forbidden.

## Review And Delivery

1. Before runtime edits, refresh owner-bound writer readiness against this
   design, each public admission operand and the exact source snapshot.
2. Run allowed static checks; freeze the complete candidate and obtain one
   independent Astra/max review. Investigate concrete counterexamples, not
   metric-only decomposition suggestions. Use a second pass only for a
   material remaining risk or reviewer-introduced mechanism.
3. Preserve existing source/schema consumers and external mode. Regenerate
   executable projections with their current owner tooling, not manual edits.
4. Publish one owned initial commit. Use additive repairs after PR creation;
   require exact-head native checks and a squash merge, then inspect the
   post-merge Full Check.
5. Record remaining live-pilot/admin prerequisites separately. A green lab or
   fallback test does not demonstrate deployed requests or measured CPU savings.

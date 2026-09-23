# History Operational Alerts Plan

Status: implementation sequence; native acceptance pending

Date: 2026-09-13

## Contract And Scope

The [design](history-operational-alerts.md) owns warning semantics, thresholds,
independent operands, alternatives and writer-readiness. This plan owns the
cohesive delivery sequence for the D4/E1
[roadmap](../../ROADMAP.md) work. The
[runbook](../how-to/operate-production-observability.md) owns investigation.

Start from `a7962f874602ad409ee2267ece19fd68b3682a6b` in the isolated
operational-alerts worktree. Own only the rule YAML, fixture YAML, runbook and
these two new documents. The existing runner and its tests need no new
mechanism. Do not change old designs/plans, ROADMAP, INDEX, registries, runtime,
workflows, planning, paging authority or other worktrees.

## Sequence

1. Bind clean baseline and read exact metric help, producer outcomes, selected
   sample queries, SLO owner, current fixtures and native runner.
2. Before semantic rule edits, create this plan and its design with the current,
   intended and protected observations and every independent operand.
3. Append the five design-owned warning rules. Preserve every predecessor rule.
4. Append named outcome fixtures for the seven missing identities and all new
   warnings; retain predecessor coverage and strengthen target matching/hold
   recovery where needed. Use the existing public promtool fixture grammar.
5. Add failed-target drilldown and sample-aware history investigation to the
   runbook. Link the new design/plan without modifying shared indexes.
6. Statically inspect YAML shape, ASCII, exact file manifest and predecessor
   preservation. This does not execute PromQL or establish a passing suite.
7. Commit the complete owned delta. Return the commit and pending obligations
   for root's fresh-target integration, one batch review under AGENTS.md and
   exact-head GitHub qualification. No writer-owned push or workflow dispatch.

## Native Acceptance Matrix

All runtime/behavior execution below is GitHub-only. The exact existing entry
is [python-persistence.yml](../../.github/workflows/python-persistence.yml),
which invokes `backend/.venv/bin/python -m scripts.prometheus_rules`.
Its [runner](../../scripts/prometheus_rules.py) pins the Prometheus image and
passes the actual rule and fixture files to promtool. Do not run its Docker
operations locally. Integration also rebinds the repository-owned documentation
and changed-path gates; source outside the writer's read support is not claimed
covered by this implementation.

| Acceptance item            | Required observation                                                                                                    | Writer result                                                  |
|----------------------------|-------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------|
| Baseline inventory         | Eleven alerts, four named fixture identities, exact seven missing names                                                 | Static baseline confirmed                                      |
| Predecessor preservation   | No prior rule or fixture changed or removed                                                                             | Static byte-prefix and parsed predecessor comparison confirmed |
| Safety oracles             | Separate counter signals, reset/absence/zero, aggregation, finite hold                                                  | Pending native execution                                       |
| Terminal/readiness oracles | Mixed targets, independent pending duration, transient negatives, recovery and holds                                    | Pending native execution                                       |
| Availability oracles       | Both failure outcomes, both windows independently, eligible-only denominator/minimum, threshold and missing/reset cases | Pending native execution                                       |
| Latency oracles            | Correct bucket and count, all selectors, both windows and minimum, missing/reset cases                                  | Pending native execution                                       |
| Telemetry/webhook oracles  | Retained outcomes plus target matching and restored telemetry                                                           | Pending native execution                                       |
| History counters           | Each selected error, excluded idle/normal results, lane and target aggregation, minimum/pending/window/reset/hold       | Pending native execution                                       |
| History samples            | Count and exact bucket independently, positive/zero/age boundary, minimum, missing/reset/window/hold                    | Pending native execution                                       |
| Oracle sensitivity         | Remove/invert each material guard or selector and require the corresponding owner-valid witness to fail for that cause  | Pending root review and authorized native mutation evidence    |
| Runbook and documentation  | Actionable drilldown; sample and production non-claims; reachable new documents                                         | Pending root integration                                       |
| Full batch                 | Fresh exact source/support digests, one independent reviewer, required native jobs complete                             | Pending root qualification                                     |

## Writer Batch Record

The five warning predicates, appended fixtures and runbook are implemented.
Static YAML inspection found sixteen rules, 127 uniquely named scenarios and
226 alert expectations; every rule has positive and negative expectations.
This is an inventory, not an executed outcome or a mutation-sensitivity receipt.
All original rule/fixture bytes remain unchanged prefixes of the final files.
The five owned files are ASCII and `git diff --check` reports no whitespace
errors. The runner and its tests were not changed.

The sample model was refined within the planned batch after a direct
counterexample: summing count and bucket independently could fabricate or hide
observations when only one target loses an operand. Native `ignoring(le)`
pairing before summation and four mixed-target fixtures address that case.
The design records this local/end-to-end cost decision; native execution still
must qualify it. Final integration must rebind the support sources listed in
the design as well as all five changed files.

## Oracle Isolation Repair

Repair baseline: `656c9bb7262dd1a1482e3b3c798d3b6963a1050b`.
Root admitted four independent oracle countermodels at this epoch. This batch
owns only appended fixture scenarios and updates to these two new-in-this-PR
documents. All sixteen rule definitions, the runbook and all 127 predecessor
scenarios remain byte-for-byte unchanged. Earlier batch counts above describe
the predecessor, not the repaired inventory.

Model disposition: reuse the finite metric-observation and pending/hold model.
Current oracles can mask a changed operand behind another false conjunct;
the intended delta is independently distinguishing input trajectories, not a
new rule, evaluator, SLO or runtime mechanism. Proof adequacy is material to
qualification; there is no additional operational behavior change.

| Repair   | Independent causal obligation                                                                         | Smallest distinguishing trajectory                                                                                                                                             | Readiness and root gate                                                                     |
|----------|-------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------|
| ORACLE-1 | Each latency denominator method/route/status selector in each window, and le in each numerator        | Hold the other window and volume true; separately supply foreign counts to the selected false window. A positive coherent multi-bucket family distinguishes le removal.        | Ready to append; native original and each isolated selector mutant remain pending           |
| ORACLE-2 | Each eligible outcome in both availability denominators and the volume selector; each window boundary | All-failure controls for both failure outcomes; issued-dependent volume; short/long near-boundary negatives with the other window true; explicit nominal-boundary trajectories | Ready to append; native floating-point boundary and targeted mutation checks remain pending |
| ORACLE-3 | Reset handling precedes target aggregation for three failure counters and two paired histograms       | One constant target resets; another retains nonzero stationary history; no new event; observe before/after pending, window and hold                                            | Ready to append; native reset-order mutants remain pending                                  |
| ORACLE-4 | Terminal max detects simultaneous replica failures without changing page identity                     | Two failed and one healthy replica; exactly one service alert after 1m                                                                                                         | Ready to append; native max-to-sum mutation remains pending                                 |

The fixture owner affects only native test input and expected outcomes. The
design records the operand inputs and falsifiers; this plan records execution.
The cheapest sufficient whole-chain gate is the existing native promtool route
over the unchanged shipped rules, followed by root's isolated sensitivity
checks and fresh source/documentation admission. The independent validators
remain root's admitted reviewer and native Prometheus, not the writer.

Appending explicit cases with existing anchors costs more fixture rows than
editing a shared helper, but preserves predecessor evidence and exposes each
causal input. A new generator/evaluator adds maintenance and proof cost without
closing another assigned obligation. Reopen a trajectory if another false
guard, prior hold, invalid histogram or actual Prometheus float result masks
its intended mutant. Nominal rational equality is not a native float receipt;
root must validate or recalibrate boundary inputs without weakening the oracle
or production rule. No global mutation-space completeness is claimed.

Implementation sequence: record these readiness rows, append the four groups,
inspect YAML/ASCII and predecessor/rule preservation, then commit all three
owned paths and return for fresh-target native qualification. No local test,
promtool, collection, container, provider or network execution is admitted.

Repair source record: appended 28 scenarios (7 / 15 / 5 / 1 across ORACLE-1
through ORACLE-4), adding 69 expectations. Static YAML inspection now counts
155 unique scenario names and 295 expectations. The 127 predecessor scenarios
remain equal as parsed values and unchanged as a byte prefix; rule bytes are
unchanged against the repair baseline. All three changed paths are ASCII and
`git diff --check` reports no whitespace errors. These are source observations,
not executed outcomes. Original and targeted-mutant native receipts, especially
the four nominal float-boundary cases, remain pending root qualification.

## Stop And Rebind

Root integration delivers this alert portfolio and the independently reviewed
[cleanup fixture optimization](cleanup-fixture-preparation.md) through one
native qualification. Their contracts remain separate; no alert determines a
cleanup expectation, and no fixture changes runtime behavior.

Refresh the mirrored `deployment.documents` entrypoint-disposition digest from
the unchanged exact two-file member set after changing alert definitions or
fixtures. Retain `non_runtime`, null caller and every runtime-authority row.
The independent native inventory oracle must compare actual source bytes;
copying its expected value without recomputing the source projection is not
admission. This packaged metadata delta is not executable behavior, but it
prevents a byte-equivalence claim with a predecessor image.

The integrated documentation population exceeds its former 256-document
profile bound. Raise only `maxDocumentCount` to 320, below the existing 4096
implementation ceiling. Keep the 16-MiB aggregate and inventory byte limits,
1-MiB per-document limit and 4096-link limit unchanged. This adds bounded node
capacity without expanding parser-byte or edge budgets. Removing reachable
owner documents would lose contract coverage; raising every ceiling or disabling
the count guard is unnecessary. The current graph and existing independent
count/byte/edge falsifiers must still pass. Revisit if actual graph cost, document
redundancy or a later inventory exceeds this finite allowance.

Unexpected source drift or a missing write owner stops this batch. Semantic
edits within this authorized batch invalidate prior evidence but do not block
the remaining planned edits. Root must start a new target epoch before using
this work as conformance evidence. A red native fixture is an unresolved
oracle/implementation question, not permission to weaken its protected owner.

No native receipts, semantic mutation results, production scrape, notification
delivery, backlog capacity, quota diagnosis or complete historical collection
are claimed by this plan. A quiet warning is not evidence that an enabled
dataset made progress; routine paused/idle work cannot become an outage.

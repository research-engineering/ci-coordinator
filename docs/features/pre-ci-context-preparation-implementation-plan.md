# Pre-CI Context Preparation Implementation Plan

The [design](pre-ci-context-preparation.md) owns the new semantics. This plan
owns implementation order and native acceptance, not a second policy.

## Ordered Work

| Step | Semantic owner and files                                                                         | Work and independent acceptance                                                                                                                                                                                                                                              |
|------|--------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1    | `repo_context/planning_preparation.py`; `app/planning_preparation.py`                            | Declare the run-independent preparation port; admit compact exact webhook epochs; resolve current dynamic policy without fabricating a run. Native wrong event/range/ref/type/size and missing-policy cases prevent provider work                                            |
| 2    | `integrations/github/repository_context.py`, `repository_context_diff.py`, `prepared_context.py` | Reuse ordinary epoch acquisition; add bounded immutable context cache with complete keys and producer-owned monotonic expiry. Keep fresh repository and PR-range checks; prove hit/miss semantic equality and one-field negatives                                            |
| 3    | `runtime/planning_preparation.py`                                                                | Own a nonblocking bounded queue, exact-key coalescing, one cooperatively timed worker and terminal cleanup. Native event-controlled tests cover saturation, cancellation, failure followed by progress, close-before-start and partial startup                               |
| 4    | `runtime/webhook_admission.py`, `api/http/webhook_ingress.py`, `routers/github_webhooks.py`      | Schedule only after successful durable seed ingestion; expose the truthful best-effort downstream result through existing response models. Served HTTP/OpenAPI and process-boundary witnesses preserve all negative ingress paths                                            |
| 5    | `runtime/composition.py`; `observability/runtime_metrics.py`                                     | Share one context provider between preparation and request planning, bind the queue to the existing resource lifecycle, and expose finite outcomes. Composition witnesses prove the actual connected path can warm then consume without bypassing current issuance authority |
| 6    | Runtime requirements, trace profile, Proofkit routes, documentation indexes, current roadmap     | Add one owning requirement with exact source/native routes; retain every existing requirement and negative oracle; synchronize the trace-count witness as part of this same closure                                                                                          |

No existing design or implementation-plan prose changes. Review all derived
OpenAPI, requirement, route, package and architecture projections through their
current generators. Do not add a general-purpose cache, worker framework,
duplicate capability protocol or an environment setting merely to wire this
single lifecycle.

## Falsifier Matrix

- Positive warm and cold paths yield the same required/omitted obligation
  meaning for the same admitted context and policy.
- Mutate installation, repository ID, owner, name, event, ref, base, head and
  every policy coordinate independently; no foreign entry is consumed.
- Advance monotonic time during acquisition and during live hit validation;
  equality with expiry rejects, and a read/failure cannot renew the lifetime.
- A valid cached input plus current repository rename, PR-range change,
  provider refusal or unavailable active configuration cannot issue selected
  execution through the public planning operation.
- An old policy preparation completing after activation cannot replace or
  satisfy the new key; different contexts remain isolated.
- Overflow entry count, aggregate graph/diff units and compact-epoch fields;
  observe exact bounds, eviction and the intact ordinary request path.
- Ingress storage failure, rejection, duplicate, ignored event and admission
  timeout never offer work. A real committed delivery plus queue saturation
  still reports the claim truthfully without promising preparation.
- Block one background provider attempt with controlled events, let its
  deadline expire, and prove the next item runs. Foreground acquisition does
  not await that blocked item or a shared preparation future.
- Stop while idle, queued, acquiring and partially started; no task survives
  resource shutdown, no late cache publication occurs, and shared GitHub
  resources close only after the preparation owner is quiescent.
- Native metrics tests distinguish every finite outcome without identity or
  payload labels. Readiness and signing retain their independent authorities.

Use real classes at the claimed operation boundary. Fakes must preserve the
cache mutation, provider-call and durable-claim effects relevant to each
assertion. Native unit/helper cases supplement, not replace, HTTP, connected
composition and PostgreSQL transaction evidence where applicable.

The connected PostgreSQL/HTTP witness runs in the existing non-enforcing
mode: a cache hit must still produce signed FullCI with a null verified-plan
identity. Its graph fixture must match the admitted policy's source and
global-risk paths. A separate public `DynamicPlanService` witness uses the real
deterministic planner, GitHub context cache and signed issuer: establish cold
and warm selected issuance, then independently remove current policy, provider
access, repository identity or PR-range agreement and require FullCI. Existing
capacity and effect-recording reconciliation fixtures isolate these guards;
they do not establish PostgreSQL reconciliation or production admission.

## Verification And Delivery

Rebind writer readiness to the current merged base and admitted design before
runtime changes. The design-only worktree may progress while earlier CI runs;
the independent review freeze waits until base settlement and static closure.

Local verification remains bounded static-only: Ruff, mypy, import boundaries,
schema/docs/requirement admission and exact committed Proofkit routing. All
behavioral tests, coverage, installed-package checks and container/database
proof run through repository-owned GitHub Actions. Do not run local pytest or
import the application as a shortcut.

Freeze source, tests, design, plan and relevant transitive prerequisites for an
independent review under `AGENTS.md`; add a control pass only for a material
unresolved finding or independent uncovered scope. Preserve every original
result and its exact target. Publish one owned initial commit; later repairs
are additive. Require green exact-head native CI before squash merge and
inspect the post-merge Full Check.

This slice is complete only when the connected path and its falsifiers pass.
Measured preparation usefulness, working administrator-owned webhook, shared
replica efficiency, independent input completeness, CPU savings and production
admission remain their separate D2/D4/E1/E2 obligations.

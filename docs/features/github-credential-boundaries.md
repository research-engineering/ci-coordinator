# GitHub Credential Boundaries

Status: selected design
Date: 2026-09-26

Normative owner: [GitHub integration](../architecture/modules/github-integration.md).
Delivery: [implementation plan](github-credential-boundaries-implementation-plan.md).
Execution authority remains [CI-054 in the roadmap](../../ROADMAP.md).

## Decision

Use the existing GitHub App transport and credential owner. Every installation
binding requires an independently known `repository_id` keyword; there is no
broad default or caller-selected permission object. Complete request-shape
admission precedes credential acquisition. The module's closed 30-operation
table selects one read permission and a private immutable grant key K:

| Purpose | Caller context | K and token request |
| --- | --- | --- |
| Repository read | `(I,R)` | `(I,R,P)`; singleton `repository_ids: [R]`, one read permission P |
| Installation inventory | `(I,None)` | `(I,None,metadata)`; metadata read, no repository selectors |
| Organization runner population | `(I,R)` | `(I,None,organization_self_hosted_runners)`; organization read only, no repository selectors |

Only the two existing organization-runner operations use the last row. Their
callers retain R for visibility, eligibility and result identity. The actual
credential is organization-scoped, not R-scoped. Provider-implicit metadata
breadth across installation repositories is an accepted tradeoff, not proof of
zero repository access. Inventory likewise retains its installation population.
Repository runner listing remains singleton R plus Administration read.

All cache, refresh, waiter, callback and eviction identities use the full K.
Equal organization grants can coalesce across R without sharing results. Keep
1,024 total cache entries, 64 global refreshes and 64 HTTP exchanges per factory,
the existing absolute send deadline, expiry skew, resampling, cancellation and
drain rules. The old installation-count profile name does not multiply that
cache budget. No failure selects a broader token, permission union or retry.

## Protected Observations And Falsifiers

| Invariant | Independent falsifier |
| --- | --- |
| Authority comes from admitted scope, not URL spelling | A numeric repository path differs from caller R yet reaches mint; a named URL supplies R or permissions |
| Closed shape precedes credentials | Unknown operation, method, body, query or noncanonical path obtains any credential |
| All 16 factory-call relations retain context | A caller drops R, substitutes another repository, or hides it behind a permissive fake default |
| Sharing follows actual authority | Different R/P/I repository grants share a token or cleanup; equal O grants cannot coalesce across R |
| Organization population stays complete | Narrowing the mint removes a group member before existing eligibility projection |
| Bounds and lifecycle remain global | Mixed grants bypass the cache/refresh ceiling, extend a deadline or cancel another live waiter |
| Existing identity and outcomes are stable | Audience/report bytes change, provider errors become success, or cancellation becomes unavailability |

These are independent oracle obligations, not claims that native tests or live
provider qualification have passed. App-JWT and reviewer credential planes,
public DTOs, persistent state, permission grants and endpoint origin do not change.

## Public Recipe Trust

The [measurement guide](../how-to/measure-and-compare-ci.md) is the executable
example owner. Its measured command, dependencies, workflow and runner must be
trusted. The requester URL and audience/recipient pair come from independently
approved deployment configuration, not PR text, artifacts or job output.
Align the current requester example without forcing every abstract audience or
browser origin to equal a URL. Keep only the existing job permissions needed
by that example and preserve the report-v1 audience derivation:

```text
urn:ci-coordinator:measurement-report:v1: + SHA256(planAudience UTF-8)
```

The measured producer and receiver keep their existing job/execution identity.
Environment filtering or moving upload to another job does not prove isolation
from hostile same-job code or authorize another producer identity. Untrusted CI
can retain ordinary checks without this optional reporter. No new GitHub grant
or mandatory reporting policy follows from this recipe.

## Alternatives And Revision Conditions

- Keeping a broad installation token or documenting caller intent alone does
  not narrow the issued credential; reject both.
- Permissions alone omit repository isolation; singleton R alone retains every
  granted permission. Both operands are needed for repository operations.
- Combined R/O grants assume unqualified organization population semantics.
  O-only declares the real purpose and permits sharing the same actual grant;
  it accepts incidental metadata breadth instead of claiming R confinement.
- A broker, persistent token store or generic grant framework adds no required
  authority boundary. Reuse the current factory and finite projection.

Reopen the selected O policy if a stricter incidental-metadata requirement is
admitted with population-preserving provider evidence. New operations, missing
independent R, provider-incompatible permissions or changed caller trust require
owner review, never a permissive fallback. Numeric repository metadata access
and effective issued grants remain live qualification gaps. Extra cold mints
and cache misses are possible; no latency neutrality, measured benefit,
production capacity or universal least-privilege optimum is claimed.

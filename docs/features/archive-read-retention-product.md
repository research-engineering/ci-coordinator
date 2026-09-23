# Archive Read And Retention Product

Status: bounded implementation design; native qualification and shared wiring pending

## Ownership And Scope

This additive design owns the archive read projection and explicit bounded
existing-detail operations. It refines, without rewriting, the retained-fact,
first-import and policy-reference laws in [retention](actions-history-retention.md),
[storage](actions-history-storage.md), [policy resolution](actions-history-policy-resolution.md)
and [administration](actions-history-administration.md). Execution belongs to the
[plan](archive-read-retention-product-plan.md). Baseline: `4404947b99db9d767fbee587e626b4dd5fd8c62e`.

## Minimum Sufficient Model

Disposition: reuseOwnerModel. These decisions are material: public disclosure,
persisted retention and irreversible child deletion change. The model is the
existing scope/generation/configuration/data/default revision tuple, immutable
first import, applied policy, dataset fence and transaction order. It is not a
new authority for provider availability or statistical erasure.

As-is: permanent attempts/jobs and gap records exist; status is public, records
are not. Optional detail has a storage envelope and expiry but no production
payload/import contract. Intended delta: bounded authenticated local reads and
previewed, audited application/removal of selected existing detail.

Protected observations: statistical bytes, identities, job operands, import
anchors, prospective policy behavior, quota accounting, non-destructive rescan,
90-day active-evidence eligibility and absence of planning authority.

## Read Contract

Read one current-generation page, at most 50 entries plus one SQL lookahead.
Attempt headers exclude jobs; jobs have a separate page. Filters bind source
time, workflow and optional exact job name for attempts; jobs bind an exact
attempt and optional job name. Attempt filtering uses a correlated EXISTS over
the same repository, generation, run and attempt. It does not load job bodies
or duplicate attempt headers when several jobs match.
Gaps use their indexed observation-identity order and expose observations, not resolved-gap
or current deletion claims. Current status remains the coverage owner.

Cursors authenticate the exact query, actor, generation, configuration/data
revision, observation instant and last key. A changed revision requires restart.
Each page has a fresh single SQL statement snapshot, no dataset write lock and
no provider fetch. Delayed physical cleanup cannot expose logically expired
detail. Upstream availability remains `not_checked`; provider-not-found gaps
are not proof of deletion. An unavailable source never deletes local statistics.

The cursor key is injected by composition, at least 32 bytes, never generated or
read from the environment here. Rotation invalidates cursors; replicas must use
the same admitted key. Tokens are not access grants and have a 20-minute life.

## Existing Detail Operations

Preview selects 1..100 unique explicit attempt keys with expected generation,
configuration/data/default revisions and a first-import cutoff. It reports each
old/new retention, payload bytes and exact deletions. No payload is decoded.
Preview uses the same bounded lock order as apply to obtain coherent operands;
it writes no rows and its transaction releases every lock on return. This small
contention cost avoids a multi-statement mixed-revision preview; revisit if
measured read contention warrants a separate single-statement preview query.
`apply_policy` uses the currently resolved policy; `erase_details` preserves the
applied policy and first import while making retained detail terminally expired.
Neither action deletes statistics. Repository future policy updates continue
through the existing configure endpoint, separately from existing-data apply.

Apply repeats current authorization, locks global admission then scope then
dataset/parents, checks revisions/cutoff, recomputes the reviewed digest at DB
time, and requires explicit operation ID. Time crossing a deletion boundary
invalidates a preview. Child deletion, retention update, actual-byte quota
release, data revision and token-free audit receipt commit in one transaction.
Exact replay returns the original receipt; different command/actor conflicts.
Savepoint failure cannot leave partial effects. Each batch is a complete finite
selection, not an implicit promise to process an unbounded remainder.

## Writer Readiness

| Changed owner                                          | Intended delta and derived surfaces                                 | Protected behavior                                                      | Whole-chain gate / independent validator                                  |
|--------------------------------------------------------|---------------------------------------------------------------------|-------------------------------------------------------------------------|---------------------------------------------------------------------------|
| `ci_economics` archive header/read/retention contracts | Bounded header, page and reviewed selection types; HTTP projections | Existing full-statistics admission and policy transition laws           | Header/population, cursor and strict-command cases in GitHub; root review |
| `persistence` history read/retention adapters          | Single-snapshot pages and scoped audited batches                    | Exact columns, generation, anchors, quotas, rollback and original bytes | Real PostgreSQL boundary/race cases in GitHub; root replay                |
| `app` history read service                             | Fresh authorization, deadline, cursor authenticity, output binding  | False/unknown access never reaches storage; cancellation propagates     | Application negative cases in GitHub; root review                         |
| `api/http` history product routes                      | Existing role/mutation admission, strict input, no-store responses  | No actor spoofing, cached sensitive output or unbounded request         | Mounted HTTP tests and root-owned production middleware/OpenAPI checks    |

Derived operands: page continuation depends independently on query/actor/revision,
ordered keys and lookahead; preview digest depends on selection, revisions,
cutoff, policy, old/new retention and actual bytes. Falsify each operand in
isolation. No analytics totals or forecast owner is introduced.

## Alternatives And Revision Conditions

SQL keysets plus an injected HMAC key cost one small codec and explicit runtime
wiring. Unsigned offsets are cheaper locally but do not meet forged-cursor
rejection; offset pagination also drifts. Revisit if an existing admitted token
capability preserves the same bindings with lower end-to-end maintenance cost.

Explicit finite selections avoid durable job state and migration/worker wiring.
Their cost is operator re-preview after competing writes. Revisit if measured
contention prevents useful progress or whole-dataset policy application is
separately admitted. A generic retention engine is not justified by this batch.

## Unresolved Decisions And Non-Claims

No raw detail response: `detail_canonical` has no admitted public field allowlist
or production importer. Return availability/retention metadata with content
unavailable, not arbitrary JSON. Root must admit a versioned payload and producer
before enabling rich detail reads.

Global-default mutation needs a service-wide authorization and audit subject,
including inheritor-set review; scoped configure authority does not establish
that right. Defaults remain readable through existing scoped status. Full
statistical erasure needs root admission of the distinct destructive permission,
generation transition and resumable fenced cleanup. Neither missing operation
is exposed as a successful stub. No schema/ACL, provider, runtime, deployment,
analytics, full-history completeness or old external-audit closure is claimed.

The shared audit pair registry also requires root integration for the new
retention event. Current unregistered lookup fails closed before mutation.
The authored transactional implementation is not independently executable as
a successful product operation until this registry admission is made.

## Qualification Refinements

Instance revalidation consumes the explicit Pydantic field iterator. A valid
field named `keys` must not accidentally select Python's mapping-constructor
protocol. Renaming this one wire field would leave the shared normalization
defect in place; preserving iterable field semantics is the smaller general fix.
Forged instances and undeclared fields remain rejected.

Strict JSON tuple normalization belongs to the existing economics payload
boundary, not individual consumers. A JSON array becomes an immutable tuple only
in JSON validation mode; Python-mode coercion remains rejected unless an
existing request-specific owner explicitly admits it. HTTP retention receipts
use the owned response projection and warning-strict serializer, not a direct
domain dump in the router. Native roundtrip, malformed-instance and exact wire
tests distinguish these obligations from a permissive parser workaround.

Raw GET query budgets are 8192 bytes for analytics and 16384 for archive pages.
The former accommodates a 512-scalar astral job name (6144 percent-encoded bytes)
and all finite analytics parameters. The latter additionally accommodates a
4096-byte URL-safe cursor. Scalar bounds remain unchanged; byte guards reject
oversized encoding before parsing, rather than silently truncating filters.

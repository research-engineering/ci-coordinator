# Analytics Purpose Configuration

Status: implementation design; independent review and native qualification pending.

## Scope And Owner Model

This design owns durable administrator purpose settings. The existing
[analytics design](archive-analytics-product.md) retains authority over numerical
analytics and unknown/mixed classification. This successor replaces only its
process-local mapping delivery assumption. The [plan](analytics-purpose-configuration-plan.md)
owns execution and qualification, not product semantics.

Baseline: `a4c6cea1ca1bc2ab653e5d6734b0d455038731c6`. Runtime currently supplies no
mapping, so named-purpose reads cannot work. Intended delta: authorized GET/PUT
and an ordinary row editor persist exact workflow-ID/job-name category mappings;
the next analytics read uses committed configuration without restart.

Model disposition: construct a bounded transition model, reusing PurposeMapping,
history generation fencing, repository authorization, audit replay and UoW.
Materiality follows from new persisted state, public mutation and authorization.
No global optimum or new execution/source authority is claimed.

Let S be exact installation/repository, G the current history generation, R the
purpose revision, E the ordered finite entry tuple, A the authenticated actor,
O the operation ID, and X the expected revision. Missing settings have R=0 and
mapping=null; configured empty settings have R>0 and entries=[]. No name inference
or Unicode normalization occurs. Multiple explicit purposes classify as mixed;
an absent key classifies as unknown.

PUT(S,G,X,E,O,A) first admits current configure role, CSRF and repository access.
The transaction acquires compatibility then history-scope locks, verifies an
active/paused dataset and exact G, and checks the durable audit operation. Same
operation and same actor-bound payload returns the original committed snapshot;
substitution conflicts. A fresh command requires X=R and commits R+1, E and its
attributed audit receipt atomically. No success is returned before commit. An old
generation cannot replay or write into a replacement generation. Revisions reset
only at generation boundaries; the current table has at most one row per scope.

GET uses audit authorization and returns exact scope/generation plus either a
current snapshot or missing settings. Analytics reads load the mapping inside
their existing database context and recheck its monotonic identity after all
aggregations, alongside the existing dataset revision check. A changed operand
returns snapshot_changed. The operation neither calls a provider nor scans audit
history; receipt lookup uses the existing indexed identity.

## Complete Operands And Protected Observables

Mutation operands are request bytes and closed schema, path/query identity,
current identity/roles/CSRF/scope admission, dataset state/generation, operation
identity and payload digest, prior mapping revision, transaction commit and
audit attribution. Removing any one can admit a foreign, stale, substituted or
unaudited change. Analytics operands additionally include query, retained facts,
dataset revisions and the same admitted mapping before/after aggregate reads.
Frontend response admission binds status, operation, scope, generation, revision
and exact entries, not merely a successful HTTP status.

The settings response decoder checks bounded original bytes before Zod: fatal
UTF-8, unique decoded object keys, strict JSON, at most 16 container levels and
4096 nodes, and safe integer tokens. This integer-only boundary matches the
settings contract; decimal/exponent tokens are not silently coerced to IDs or
revisions. Whitespace, key order and valid string escapes remain unrestricted.
`jsonc-parser` owns syntax and token offsets; its fault-tolerant default is not
used: every error throws. Native `Response.json()` loses duplicate-key and
numeric-token evidence. A handwritten parser would duplicate grammar ownership.
`lossless-json` was considered but its duplicate handler excludes equal-valued
duplicates, which does not satisfy this unique-key contract. The existing
economics transport takes this decoder only for settings; other endpoints retain
their current contracts. Revisit when the settings owner admits non-integer
numbers or changes the resource bounds.

Protected: archive collection and source epochs; exact case-sensitive job names;
unknown versus mixed; descriptive duration/occupancy and their existing limits;
authorization-before-replay; no-read/no-write on denied scope; bounded responses;
session/scope lifetime; no draft replacement by refresh; and no fresh operation
after an uncertain save. Existing source epochs gain no authority from labels.

Bounds: 64 entries; 1..5 unique existing categories per entry; safe positive IDs;
job names 1..512 Unicode scalar code points and at most 2048 UTF-8 bytes; request
body at most 262144 bytes; bounded JSON depth/nodes; query at most 256 bytes;
20-second application deadline; 5-second SQL statement/lock deadlines; two
concurrent requests per settings admission key. The runtime owns transport
deadlines. Current mapping and audit payload sizes remain explicitly bounded.

## Alternatives And Revision Conditions

Reuse the process callback alone: cheaper locally but not durable, multi-process,
API-first or usable without restart. Reusing configuration source epochs would
couple descriptive labels to source-authority lifecycle and require broader proof.
A standalone settings service/cache creates extra invalidation and coordination
cost without a requirement. A current-row table plus existing audit receipt has
one migration and bounded lookup, at the cost of capability/ACL/schema witnesses.
Reopen if audit retention cannot preserve required replay or if mapping read
revalidation cannot distinguish every concurrent mapping transition. Native
PostgreSQL falsifiers, not model consistency, decide those premises.

## Falsifiers And Writer Readiness

| Changed owner      | Delta and derived consumers                                             | Isolated falsifier                                                                                                                        | Post-batch gate / independent validator                |
|--------------------|-------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------|
| ci_economics       | closed settings command/snapshot; HTTP and UI schemas                   | duplicates, surrogate/NUL, byte/codepoint or cardinality boundary; missing equals empty                                                   | domain/contract tests; root review                     |
| app / HTTP         | GET audit, PUT configure+CSRF; route/dependency/admission wiring        | revoked or foreign actor reaches store/replay; unknown query/body key accepted                                                            | HTTP/application tests; root security review           |
| persistence        | current row, generation/CAS, durable receipt, same-context mapping read | concurrent same revision both commit; lost-response retry writes twice; audit failure leaves state; map changes without invalidating read | native PostgreSQL tests; root independent review       |
| schema / runtime   | forward 0013 after 0012; capability, exact catalog and ACL wiring       | predecessor/runtime missing capability accepted; PUBLIC or excess DML; schema drift admitted                                              | migration/attestation tests; root native qualification |
| economics frontend | typed PUT transport, compact row editor, retained same-scope view       | substituted response admitted; navigation loses uncertain command; refresh clobbers draft; foreign scope reuses command                   | frontend/browser tests; root independent review        |

These rows authorize the cohesive implementation batch, not a claim that its
future witnesses have passed. Shared final generated contracts, Proofkit,
ROADMAP and INDEX registration remain root-owned. A genuinely missing write
authority or unexpected target drift stops the batch. All native tests remain
GitHub-only; no local database, browser, provider, server or deployment is used.

The migration witness must distinguish missing declaration coverage from ACL or
missing-table errors. Catalog damage must reject the real purpose UoW under the
runtime principal, between positive controls before damage and after repair.
Removing either the UoW capability requirement or its catalog attestor call must
invalidate its own witness, independently of other admission predicates.

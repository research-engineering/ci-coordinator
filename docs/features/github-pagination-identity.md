# GitHub Repository Pagination Identity

> Public-export boundary: historical source, PR, run, provider and rollout
> observations retained below are design context only, not acceptance evidence
> for `research-engineering/ci-coordinator`. Former private receipts are revoked.
> Synthetic pilot archetypes are proposed examples, not renamed executions.
> Requalify applicable requirements and open tasks against the new exact source.

Status: proposed provider-adapter correction.
Execution: [implementation plan](github-pagination-identity-plan.md).

## Decision

Admit a repository pagination Link when its path is either the exact requested
named path or its exact numeric repository alias, derived from a previously
verified repository ID. Preserve every existing origin, query, sequential-page,
size, completeness and subject guard. The Link is never followed directly:
the next request remains constructed by the existing client from the admitted
page number and operation.

## Counterexample

A synthetic counterexample uses a named repository run-list request and a next
Link expressed through the same independently bound numeric repository ID.
Named-only path equality rejects that valid alias and can repeatedly defer
collection. The page must still satisfy the existing run-list byte/item bounds.
This scenario generalizes a historical failure mechanism; it is not a receipt
for a real or synthetic public-repository run.

## Identity Law

For current verified binding B=(repository ID, owner, name), requested path P
and suffix X, define:

```text
P = /repos/encode(B.owner)/encode(B.name)/X
Alias(B, P) = /repositories/decimal(B.id)/X
AdmittedPath(B, P, L) = L = P or L = Alias(B, P)

AdmittedNext = AdmittedPath and existingNextPageGuards
```

The numeric ID must be an exact positive safe integer, not a bool, float or
string. The requested named prefix and suffix remain byte-exact; no generic
decoding, case folding, path traversal, redirect or arbitrary alternate URL is
admitted. Missing numeric binding permits only exact named equality.

The ID comes from the caller's independently admitted repository lookup,
repository-bound epoch or authority record. Neither the Link nor the helper
establishes that relationship. Organization and installation list operations
do not opt in. A repository ID is identity evidence, not access authorization.

## Boundaries And Alternatives

One primitive in `_routes` owns path comparison. The shared next-page validator
and the two existing custom validators consume it. Their unrelated semantics
remain unchanged in this correction. Repository callers supply IDs only where
the existing owner already binds ID to the named request.

Retaining exact named equality cannot consume the observed provider response.
Following Link blindly loses the current URL and query boundary. Switching all
requests to undocumented numeric endpoint variants changes more transport
surface than needed. The two-path predicate is the smallest sufficient change
for this provider shape and preserves the current request builders.

No global absence of other pagination defects is claimed. Revisit for another
provider origin, repository transfer/reinstall, new resource aliases, changed
operation suffix or any caller without an independently verified ID/name pair.

## Verification

Native positive controls cover named and numeric links. Independent negatives
change ID, owner/name, suffix, host, port, userinfo, fragment, page, page size,
query multiplicity and created interval. At least one 100-run discovery page
must pass through the actual adapter with the numeric Link, not just the path
primitive. Sibling changed callers receive numeric-alias fixtures while their
existing malformed/truncated/unstable cases remain.

On deployment, the admitted pilot's original generation/cursor must resume without reset,
row edits or changed quota. Source tests do not prove full provider history,
production capacity, client CI savings or safe omission.

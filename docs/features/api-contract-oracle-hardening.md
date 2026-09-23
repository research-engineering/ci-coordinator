# API Contract Oracle Hardening

Owner: repository test portfolio. Baseline: `9547a85b1f9b2a8c79c59bf8c8633167731c9021`.
This is a bounded successor to [schema-driven API testing](schema-driven-api-testing.md)
and its [implementation plan](schema-driven-api-testing-plan.md).

## Intended Change

Keep the three-operation population, native test gate, campaign budgets and
production API unchanged. Close three independently reproducible proof gaps:

| Boundary         | Counterexample                                                                  | Required postcondition                                                                                |
|------------------|---------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------|
| Cancellation     | Terminating the campaign kills its supervisor but not pytest in another session | SIGINT/SIGTERM requests bounded child-group cleanup before campaign exit and dependency-lease release |
| Authentication   | A trusted POST receives a schema-valid, effect-free 401 and passes              | A trusted machine request cannot receive 401; explicit invalid-credential controls remain valid       |
| Request identity | Both port and response consistently use a different repository                  | Successful read binds serialized path/query, exact port call and response                             |

## Decisions And Falsifiers

Reuse `bounded_process.spawn(stop_requested=...)`, not another process-tree
supervisor. Scoped signal handlers request cancellation, remain installed while
cleanup and evidence publication run, and restore the previous handlers. The
developer dispatcher retains its existing outer cleanup budget and lease.
The summary's additive `cancelled` flag observes the cancellation request at
summary construction; child exit/failure fields retain their original meaning.
Non-catchable termination, host loss and failed storage do not acquire a
successful-evidence claim. A surviving child or a successful cancellation exit
falsifies this decision; CI must exercise the real nested process path.

Reject 401 for every trusted campaign request before the generic authentication
response branch. Do not reject all POST 403 responses: Origin, Cookie, media
type and role admission still own their documented refusal. Envelope validity
does not prove embedded-policy validity. A schema-valid false 401 with no port
calls must fail for authentication, not for an unrelated response defect.

Read expectations come from the serialized request URL, independently of the
port ledger and response. Decode path components and query values; use Pydantic
integer admission to preserve the HTTP boundary's admitted numeric coercions.
Scalar query parameters use the last occurrence, with documented default limits
10 for workbench and 50 for config status. Exact call-list equality binds actor,
scope, limit, cursor and cardinality. Existing response checks then bind output
to that admitted call. This deliberately small projection is preferable to a
second router or a general schema interpreter. Revisit it when operation paths,
defaults, scalar query semantics or the public boundary contract change.

## Implementation And Evidence Plan

1. Repair campaign cancellation in its current owner; add handler restoration
   controls and nested dispatcher SIGINT/SIGTERM witnesses with a controlled
   long-running child. Observe cleanup and lease ownership, not only exit code.
2. Strengthen the trusted-request oracle and add false-401/healthy controls.
3. Bind read calls to wire input. Add unequal non-default repository IDs,
   constant/swap/actor/limit/cursor/cardinality mutants, plus default, numeric
   coercion and repeated-query controls. Consistent wrong response and port
   values must fail without relying on schema invalidity.
4. Register new witnesses under existing verification-core ownership. Run
   permitted static gates, one independent review under `AGENTS.md`, then native
   GitHub behavioral qualification on the exact PR head. Correct confirmed
   findings as a group; retain ordinary coverage and required checks.
5. Squash merge only the qualified head. No deployment or dependency upgrade
   belongs to this repair. Report CI evidence separately from static reasoning.

## Proof Boundary

The argument is bounded: cancellation reaches the existing supervisor;
trusted-auth refusal is rejected; and request, call and response identities
are independently connected. It is not a proof of every API operation, every
possible process failure, whole-project optimality or production readiness.
The earlier omissions were oracle sensitivity and nested-lifecycle modeling
gaps, not missing generic quality rules. The regression corpus must preserve
the original counterexamples rather than merely increase generated examples.

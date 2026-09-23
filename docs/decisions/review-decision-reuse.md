# Review Decision Reuse

Status: accepted review-process decision; not a product-policy waiver

Date: 2026-09-05

Owner: repository review process under
[Architecture Decision Quality](../architecture/cross-cutting/architecture-decision-quality.md).

## Decision

Keep a small [decision register](review-decisions.v1.json) for exact arguments
already resolved by a current owner. It indexes existing decisions, their costs,
assumptions and falsifiers; it neither replaces the owning specification nor
marks code safe. The [audit ledger](../adoption/independent-sota-adjudication-2026-09-05.md)
retains factual findings, refutations and unknowns. Do not turn every rejected
audit bullet into a new ADR or copy product authority into this register.

Every material decision asks: **Is this the most logical and least costly
sufficient trajectory among the compared alternatives?** Record the protected
observables, simplest viable alternative, reason for selection, residual cost
and a falsifier. This is a bounded comparison, not a global-optimality claim.

## Reuse Predicate

For finding `f`, record `d` and current context `c`:

```text
ReferenceCurrent(d) := all pinned owner bytes still match

ReusableReason(f, d, c) :=
  ReferenceCurrent(d)
  and ExactArgumentAndScopeMatch(f, d)
  and AllAssumptionsRevalidated(d, c)
  and NoRevisionTriggerObserved(d, c)
  and NoNewCounterexample(f, d, c)

ReusableReason(f, d, c)
  => cite the prior argument disposition for this argument only

ReusableReason(f, d, c)
  !=> skip file, capability, quality dimension, tests or security review
```

Any false or unknown operand requires fresh review. Absence of a noticed trigger
does not prove absence of a trigger. A changed caller, trust boundary, topology,
library contract, quality budget or owner can invalidate the argument without
changing the pinned document. Therefore matching hashes are only a navigation
signal, never an automatically accepted semantic verdict.

A business choice cannot authorize violating a hard requirement. For example,
choosing one audit chain settles the objection "a global lock is intrinsically
wrong" but not a demonstrated throughput or deadlock defect. Choosing one Python
minor settles "a newer minor exists" but not an applicable security advisory.

Explicit authorization and deterministic selection alone do not prove least
privilege: a deterministic `AllPermissions` profile is a counterexample. Bind
necessary capabilities to the task before comparing them with the granted set.
A stable required gate excludes ungated dynamic execution, not every generated
workflow that preserves that gate. Compare only alternatives preserving the
actual hard constraints; rejecting one invalid option proves no global optimum.

## Record Classes And Lifecycle

- `owner-choice` records a currently selected product/platform boundary.
- `conditional-tradeoff` records a mechanism selected under named costs and
  assumptions, with an explicit alternative and reconsideration condition.

There is deliberately no `permanently-safe` class. A mathematical implication
may remain valid for fixed premises; future applicability of those premises is
not thereby proved. Every class needs assumptions, protected observations,
falsifiers, review triggers and a residual-review statement.

The initial entries project already admitted repository decisions; this ADR
does not approve new runtime business behavior. On owner drift, the static
checker reports `review-required`; it does not refresh a digest, accept a
successor, block ordinary development or weaken another gate. A reviewer must
re-adjudicate the changed scope before referencing the decision again. Record
an actual changed decision through its canonical owner first, then update the
reference projection. Delete an obsolete reference if it no longer saves work.

Previously published designs and plans remain unchanged. Routing a successor
belongs to the documentation index. This register is not the architecture
profile's waiver mechanism and cannot change merge admission.

## Enforcement And Dependencies

The existing repository JSON gate admits a bounded, strict Pydantic register:
unknown fields, coercible wrong types, duplicate IDs, missing reasoning fields,
unsafe/missing owner paths and malformed digests are rejected. The existing
bounded file reader and strict JSON parser retain path, size, duplicate-key and
Unicode protection; Pydantic supplies shape/type constraints instead of a new
hand-written schema walker. Do not use `model_construct`, unchecked copies or
exception-to-success fallback for this boundary.

The checker returns only reference freshness, never `safe`, `waived` or
`finding-suppressed`. Semantic assumption checks and dominance comparisons stay
with the reviewer. Proofkit binds this gate and its negative tests; it does not
prove that a decision is correct. No new dependency, background service,
workflow or generic policy engine is required.

## Alternatives

| Alternative                                     | Why not selected                                                     |
|-------------------------------------------------|----------------------------------------------------------------------|
| Ignore previously rejected findings             | Repeats work and loses owner context.                                |
| Suppress whole topics or files                  | Hides new safety defects and changes in applicability.               |
| One new ADR for every disputed bullet           | Duplicates existing decisions and fragments navigation.              |
| Hash match means decision remains valid         | Confuses unchanged bytes with unchanged environment and semantics.   |
| Build an automatic semantic waiver engine       | Unjustified complexity and an unsound authority claim.               |
| Register plus strict structural/freshness check | Selected: reuses arguments while retaining fresh-review obligations. |

For the structural boundary, the incumbent choices are manual validation,
`jsonschema`, and Pydantic. Manual validation repeats nested type, key and bound
checks. JSON Schema is suitable for a language-neutral public contract, but
this Python-only internal record would also need a typed projection after
validation. Pydantic keeps that declaration and checked Python value together
under the existing mypy plugin. It does not replace the strict JSON/path reader
or relational validators. This is a bounded maintenance choice, not a measured
throughput or globally optimal dependency claim; reopen it if the register
becomes a cross-language API or measured startup/validation cost exceeds budget.

## Acceptance And Revision

The implementation plan is B5 of the
[operational-closure plan](../features/evidence-led-operational-closure-implementation-plan.md).
Acceptance includes malformed shape, unknown fields, duplicate JSON keys/IDs,
owner traversal/symlink, missing owner, owner-byte drift and unchanged-owner
cases. Changes to a record must not create a suppression output. Review the
initial arguments against their actual owners, not their titles alone.

The new record and checker must remain cheaper than the repeated review they
remove. If records duplicate owners, become stale noise or attract blind hash
refreshes, consolidate or remove them. If an external owner supplies an admitted
equivalent registry, compare migration without weakening these non-claims.

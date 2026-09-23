# GitHub Runner Observation

> Public-export boundary: historical source, PR, run, provider and rollout
> observations retained below are design context only, not acceptance evidence
> for `research-engineering/ci-coordinator`. Former private receipts are revoked.
> Synthetic pilot archetypes are proposed examples, not renamed executions.
> Requalify applicable requirements and open tasks against the new exact source.

Status: current boundary correction
Date: 2026-09-09
Owner: GitHub job admission; REQ-CI-RUNTIME-038 and REQ-CI-RUNTIME-042

## Decision

Normalize GitHub's zero runner-reference sentinel before constructing a
canonical runner identity. Preserve positive identifiers and their names;
never persist zero as an identity. REST and webhook admission implement the
same conversion under their existing, separate module owners.

This corrects provider compatibility, not CI selection. Valid jobs previously
rejected as malformed become available to economics and reconciliation.
No required signal, job conclusion, omission rule, credential or permission
is changed. This does not authorize selective execution.

## Evidence And Scope

A synthetic counterexample supplies completed jobs with positive runner IDs and
a zero runner-group sentinel. A positive-only REST decoder rejects otherwise
valid timestamps, conclusions and identity; webhook normalization can share the
same assumption. Positive self-hosted group fixtures alone do not cover this
case. No former live collection receipt is carried into this export.

The [GitHub jobs API](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt)
owns the provider wire format. Its example is not an exhaustive value-domain
specification. The observed zero is evidence of a supported wire form, not
proof that zero identifies an independently addressable runner group.

## Normalization Law

For each raw runner or group pair `(id, name)`, first validate both operands:

- ID is absent/null or an exact JSON-safe nonnegative integer, never bool.
- Name is absent/null, empty, or bounded nonempty Unicode scalar text.
- Invalid operands reject even when the other operand is a sentinel.

Then normalize:

```text
id = 0                         -> (None, None)
id = None and name absent/empty -> (None, None)
id > 0 and name nonempty        -> (id, name)
every other pair                -> reject

canonical group present -> canonical runner present
```

A bounded display name accompanying zero is not an identity and is discarded.
In particular, a hosted group label does not become a fabricated group ID.
No runner label, organization name or repository ID controls this conversion.
Null with a nonempty name, positive ID with an absent/empty name, negative or
unsafe IDs, invalid strings and a group without a runner remain rejected.
`0.0` and `False` must not take the integer-zero branch.

For admitted input `x`, `N(N(x)) = N(x)` when canonical absent references are
projected back to absent wire fields. Positive pairs are unchanged. Therefore
the domain's existing positive-ID-or-absent invariant still holds without a
migration, API schema change or new persistent representation. REST and
webhook facts for the same raw references must have equal canonical identities;
otherwise their semantic comparison could create a false conflict.

## Owner And Alternative Analysis

| Option                                                             | Consequence                                                 | Decision                          |
|--------------------------------------------------------------------|-------------------------------------------------------------|-----------------------------------|
| Keep rejecting zero                                                | Valid hosted jobs block whole-attempt collection            | Reject                            |
| Admit zero throughout domain, storage and UI                       | Sentinel becomes identity; expands unrelated contracts      | Reject                            |
| Share a new provider package or import ingestion from integrations | New ownership surface or currently forbidden dependency     | Not justified for this conversion |
| Normalize at both existing provider boundaries                     | Small bounded conversion, no new dependency or stored shape | Selected                          |

The deliberate boundary-local implementation is not a claim of semantic
independence: one conformance corpus exercises both owners. Revisit extraction
if another consumer needs this policy and an admissible shared owner offers a
lower total maintenance cost. Revisit sentinel semantics if GitHub establishes
a meaningful addressable zero identity or a new contradictory representation.

Pydantic is not added here: these bounded decoders already distinguish malformed
outcomes and raw JSON types. Another model would not establish provider meaning
and is unnecessary for the two-field conversion.

## Proof And Non-Claims

Use independent expected references, one-field invalid mutants and a composed
REST transport-to-economics witness. Preserve exact run/SHA binding, attempt
path, terminal status, pagination completeness and two-read stability. Native
tests run in GitHub; static admission is not their substitute.

After exact-head CI, review and immutable deployment, reread an independently admitted, previously
registered pilot attempt. It must become captured without resetting its source,
extending retention or dispatching CI. If its existing retry budget expires,
report that lifecycle outcome; do not alter durable rows to manufacture success.
GitHub job elapsed time is not measured CPU time. See the
[implementation plan](github-runner-observation-implementation-plan.md).

# Qodana Confirmed Contract Remediation

Status: implementation design

Date: 2026-09-20

Owner: repository type-boundary and test-oracle owners

Implementation plan:
[Qodana Confirmed Contract Remediation Implementation Plan](qodana-confirmed-contract-remediation-implementation-plan.md)

## 1. Decision

Repair the confirmed, source-bound Qodana findings without changing product
behaviour, provider authority, persistence semantics, or negative-test oracles.
The repair aligns declared callable contracts with the operations their current
owners consume, makes one installed-package origin check explicit, and applies
isolated source-hygiene simplifications whose language is unchanged.

The frozen input is the 2026-09-20 Qodana report for commit
`9547a85b1f9b2a8c79c59bf8c8633167731c9021`. Its individual validation ledger
classifies 100 current High results as confirmed: 83 contract findings and 17
source-hygiene findings. The ledger is evidence for selection, not a new source
of product authority. Any report result classified as refuted or intentional is
outside this change.

## 2. Admission Rule

For a confirmed finding `f` and repair `r`:

```text
Admitted(r, f) iff
  ConfirmedAtFrozenBaseline(f)
  and OwnsChangedContract(r, f)
  and PreservesProtectedObservables(r)
  and EliminatesTheDeclaredMismatch(r, f)
  and HasAnOwnerLocalRegressionWitness(r)
  and NoLowerCostSafeRepairExistsIn(r, f, ReviewedAlternatives(f))
```

`ReviewedAlternatives(f)` is the finite repair set considered for that finding
in the validation ledger. It does not claim a global search or optimum.

The selected repairs are local renames, positional-only declarations, an
explicit boundary guard, and language-preserving cleanup. They are preferable
to broad casts, `Any`, protocol weakening, rule-wide suppression, or changing
third-party dependencies because they make the actual contract explicit while
preserving the existing consumers.

## 3. Protected Observables

Unless a row below says otherwise, the implementation preserves:

1. production request, persistence, provider, and transaction behaviour;
2. existing test positive paths, negative inputs, timing, cancellation, and
   cleanup oracles;
3. the current positional call semantics of Prometheus timers, socket methods,
   webhook stores, and test doubles;
4. repository-owned Protocol authority rather than adapting consumers to a
   locally mismatched double;
5. all regex languages, capture structure, and full-match callers; and
6. generated artifacts and every finding classified as intentional or refuted.

No repair claims a Qodana profile correction, a successful Qodana rerun, a CI
pass, or a runtime incident before the required exact-head evidence exists.

## 4. Contract Deltas

| Group                        | Before                                                                                                                                          | After                                                                                                                                             | Reason                                                                                              |
|------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| Production Protocols         | `_StageTimer` and `TransactionalProposalReviewStore` declare keyword-capable signatures that their concrete consumers do not uniformly provide. | The private timer declares its consumed positional contract; proposal-review adapter parameter names match its public Protocol.                   | A Protocol must not promise calls that an assigned implementation rejects.                          |
| Test and witness doubles     | Doubles use underscore parameter names where their Protocol or standard-library base exposes keyword-capable names.                             | Doubles preserve the same values and effects while accepting the owned keyword vocabulary, or declare externally positional APIs positional-only. | Tests remain valid substitutes for their injected contracts.                                        |
| Installed-package inspection | A generic module `__file__` attribute reaches `Path` before its origin is admitted.                                                             | The inspection requires a string module origin before path construction.                                                                          | The artifact boundary rejects malformed module origins deliberately and with a stable failure path. |
| Literal SQL fixture          | A broad `str` annotation loses the closed literal provenance required for the unescaped SQL fragment.                                           | The fixture declares its two accepted literal values.                                                                                             | The test contract states the input restriction already enforced by its parametrization.             |
| Source hygiene               | A small set of patterns, local aliases, and test HTML lack the clearest equivalent representation.                                              | Equivalent source is written directly.                                                                                                            | The repair removes accurate low-priority diagnostics without changing language or test meaning.     |

## 5. Explicit Exclusions

The following are not implementation work in this design:

- Qodana environment/profile diagnosis or suppression configuration;
- the 635 refuted and 279 intentional current High results;
- the seven historical results absent from the current report;
- all Critical, Moderate, Info, and Low results from the report;
- dependency upgrades, generated-bundle edits, database changes, deployment,
  provider operations, or external workflow changes; and
- refactoring files merely because multiple diagnostics occur nearby.

## 6. Falsifiers And Revision Conditions

Reopen the selected repair if a focused test shows that a renamed parameter is
part of an externally consumed keyword API, a positional-only declaration
rejects a supported keyword call, a regex change alters a match/capture, or an
installed-package witness depends on a non-file module origin. A dependency
signature change, a new caller, or a future Qodana report with a materially
different claim starts a fresh review epoch.

The design intentionally does not claim that every static warning disappears:
the analyzer's effective interpreter, stubs, plugins, and profile were not
available in the frozen report. A future environment investigation must bind
those inputs and compare a same-source run before it proposes an analyzer
configuration change.

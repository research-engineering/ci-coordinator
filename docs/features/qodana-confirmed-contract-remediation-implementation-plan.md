# Qodana Confirmed Contract Remediation Implementation Plan

Status: active implementation plan

Date: 2026-09-20

Design authority:
[Qodana Confirmed Contract Remediation](qodana-confirmed-contract-remediation.md)

Frozen baseline: `9547a85b1f9b2a8c79c59bf8c8633167731c9021`

## 1. Scope And Completion Rule

Implement every confirmed finding selected by the design, then prove the
result on the exact candidate head. The unit of closure is the semantic repair
group, while the validation ledger retains every individual Qodana ID that the
group covers.

```text
Closed(group) iff
  SourceDeltaMatchesDesign(group)
  and ProtectedObservablesHold(group)
  and FocusedWitnessesPass(group)
  and RequiredExactHeadGatesPass(group)
  and IndependentReviewsDisposition(group)
```

The plan does not close refuted or intentional results by deletion, baseline,
or broad suppression.

## 2. Ordered Work

| Step | Owners and files                                                                                                            | Change                                                                                                                                                                              | Acceptance                                                                                                                                        |
|------|-----------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------|
| 1    | `docs/features/*`, `docs/INDEX.md`                                                                                          | Add this design and plan, route both from the documentation index.                                                                                                                  | New documents have distinct design and execution roles; historical documents remain byte-identical.                                               |
| 2    | `observability/runtime_metrics.py`, `persistence/proposal_review_adapter.py`, adjacent tests                                | Make `_StageTimer.__exit__` positional-only and align the three proposal-review adapter parameter names with `ProposalReviewStore`.                                                 | Keyword capability is neither overpromised nor silently weakened; timer and attestation behaviour are unchanged.                                  |
| 3    | HTTP support, CI-history support, integration helper, replay, runner-capacity, webhook, shadow, and dev-environment doubles | Rename keyword-capable double parameters to their Protocol names and explicitly discard unused values. Declare only standard-library socket/text-output operations positional-only. | Existing injection and negative-test oracles retain their paths and values; each fake accepts every call admitted by its owner Protocol.          |
| 4    | `scripts/package_public_api_inspection.py`, runtime-principal-access fixture                                                | Admit `module_file` before `Path`; declare the two literal SQL attributes.                                                                                                          | Invalid package origin fails closed; valid installed-wheel inspection and both principal fixture cases remain represented.                        |
| 5    | named Python, TypeScript, JavaScript, CSS, HTML, and test files selected in the ledger                                      | Apply language-preserving regex, alias, set-literal, and `lang` cleanup.                                                                                                            | Regex language/captures and test markup/oracles are unchanged; generated artifacts are untouched.                                                 |
| 6    | focused tests and static owner gates                                                                                        | Run owner-local static checks; execute behavioural tests only through the repository-owned GitHub route.                                                                            | Every changed contract has a direct witness; failed/absent remote evidence remains unverified.                                                    |
| 7    | frozen candidate                                                                                                            | Run two pre-PR reviews: a clean-room `gpt-6-astra`/`xhigh` review and an independent Academic Engineering checklist review.                                                         | Both reviews bind the exact candidate head and all changed owner groups; each finding is fixed, rejected with evidence, or explicitly unresolved. |
| 8    | exact final head                                                                                                            | Push, run required remote gates, then open the PR only after metadata admission and provider readback.                                                                              | PR title/body, candidate head, review records, and remote evidence bind to the same commit.                                                       |

## 3. Repair Groups And Witnesses

| Repair group                           | Finding count and Qodana IDs                                                                            | Primary witness                                                                                                                           |
|----------------------------------------|---------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------|
| Production callable contracts          | 2: QH-0597, QH-0783                                                                                     | history-stage metrics and repository-attestation tests                                                                                    |
| Shared HTTP support doubles            | 44: 29 authenticator and 15 mutation-admission injections resolved by the two shared support signatures | affected HTTP route, CI-history, and persistence HTTP tests plus Protocol signature review                                                |
| Config-management authenticator double | 2: QH-0615, QH-0780                                                                                     | config-management route tests and signature review                                                                                        |
| Other Python doubles and witness ports | 33: 13 runner-capacity, 11 text-output, 4 replay, and 5 single-owner contract findings                  | owning unit or integration tests for runner capacity, replay, webhook, shadow, GitHub context, log handler, and dev-environment witnesses |
| Explicit admission boundaries          | 2: QH-0338, QH-0516                                                                                     | runtime-principal access and package public-API witness                                                                                   |
| Source hygiene                         | 17 named style occurrences                                                                              | static syntax/type/lint checks and focused frontend/browser tests where the source is a fixture                                           |

The contract rows account for 83 findings (2 + 44 + 2 + 33 + 2); the source
hygiene row accounts for the remaining 17. The detailed individual-ID mapping
remains in the external frozen validation ledger. This plan owns only the
complete repair grouping and does not create a second semantic ledger.

## 4. Verification Placement

Local verification is limited to owner-admitted static analysis, formatting,
documentation, and bounded contract checks. Unit, integration, browser,
subprocess, database, and full-suite execution must use the repository-owned
GitHub Actions route. A local static success never substitutes for remote test
evidence.

Before each code group is changed, record its owner, preserved observables,
dependent tests, and cheapest sufficient final gate. After a source change,
rebind the exact candidate head before using any review or CI evidence.

## 5. Review Admission

The clean-room reviewer receives the changed-path manifest, exact baseline and
candidate OIDs, the requested confirmed-finding groups, and no primary-review
conclusions. The Academic Engineering reviewer receives the exact owner
contracts, protected observables, falsifiers, plan mapping, changed paths, and
validation obligations. Both reviews use `gpt-6-astra` with `xhigh` reasoning,
as requested, and must report their real coverage and any unresolved premise.

No review result authorizes a source mutation after the candidate head changes.
Any accepted repair after review starts creates a new candidate epoch and
requires fresh review of the changed surface.

## 6. Closeout Boundaries

The PR reports the code and documentation delta, passed exact-head gates, and
remaining analyzer-environment investigation separately. It makes no claim
that Qodana is fully configured, that every report severity is closed, or that
the candidate has been deployed. Retrospective routing is evaluated at PR
closeout because this is a material review-correction and publication cycle.

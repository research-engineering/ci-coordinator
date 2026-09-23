# GitHub Repository Pagination Identity Plan

Design: [repository pagination identity](github-pagination-identity.md).

1. Add the bounded exact path relation to `_routes`; reject invalid optional
   numeric binding and retain exact-only behavior without that binding.
2. Extend the shared next-page validator with the optional verified repository
   ID. Keep non-repository callers unchanged. Propagate current IDs through
   run discovery, historical jobs, reconciliation jobs, workflow choices and
   governance rule pagination.
3. Reuse the same path relation in workflow-inventory and diff pagination,
   carrying the ID from their existing repository authority or epoch. Preserve
   their remaining validation, failure, limit and snapshot semantics.
4. Add parameterized path/URL falsifiers and per-caller positive alias fixtures.
   Include a full 100-run page, exact created query and next-page reconstruction.
   No response-size increase or weaker completeness oracle is allowed.
   Inventory must assert exact requests and reject a skipped page. Exercise
   both reconciliation subject and economics attempt entrypoints. Native
   fault probes must reject page 3 instead of page 2 and an omitted numeric
   identity operand, bracketed by successful restored positive controls.
5. Bind the new documents and changed witnesses to existing runtime/provider
   requirements and refresh Proofkit source hashes and documentation routes.
   Retarget CE26's original string to the shared exact path predicate while
   retaining its foreign-runner-path mutation, witness and admission budgets.
   The static all-suite applicability preflight must find each current anchor.
6. Obtain one bounded independent review, native GitHub qualification, exact
   squash merge and immutable release. Deploy with the current admission path,
   then observe the an independently admitted pilot's import progress without configuration reset.

## Readiness And Acceptance

The changed owner is repository resource identity, not queue state or provider
authorization. Protected observables are current repository binding, exact
operation suffix, query values/cardinality, page ordering, response budgets,
failure classification and no direct Link following. Independent native tests
must fail for each substituted operand and preserve valid named requests.

The static review must trace each supplied ID to its earlier admitted owner;
merely adding an optional integer parameter does not prove that relation.
Unknown callers retain strict named equality. Provider alias support is not a
waiver for foreign hosts or paths. Local lint/type/contract checks do not run
behavioral tests and cannot replace native adapter or real-deployment evidence.

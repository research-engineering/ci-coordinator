# Operator Visual System Implementation Plan

## Scope And Entry Conditions

Implement [the visual design](operator-visual-system.md) above exact presentation
base `85edcd4280dde4a26521b8ecc4f5712d98c2a60c`. Preserve the PR #146 navigation
and PR #147 evidence behavior. Use an isolated worktree; leave the user's dirty
checkout and neighboring projects unchanged. No backend or public API change is
needed for the selected trajectory.

The design's recommendation ledger is validated against source owners before
mutation: the missing runtime-mode operand rejects the screenshot badge;
request-generation rules reject active stale controls but preserve the existing
read-only same-scope Governance refresh and its form/receipt; comparison
tables and native disclosures preserve the existing accessibility contracts.
These are deliberate departures from the prototype to avoid false UI claims.

## Writer Readiness

| Owner                                                        | Intended delta                                                                        | Protected observations                                                                  | Derived surfaces and sufficient gate                                                             |
|--------------------------------------------------------------|---------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| Existing CSS files and local font assets                     | Navy/light visual tokens, Lato, card hierarchy, native disclosure and control styling | Contrast, focus, responsive navigation, readable exact identity and status meaning      | Production asset manifest; browser geometry, CSP/font and axe checks through Operator workbench. |
| Loading presentation component and existing loading branches | Indeterminate progress and inert skeletons                                            | Query state, generation invalidation, no false ready/empty state, no duplicate commands | Component and browser pending-to-settled/error witnesses; existing identity/command suites.      |
| Operator UI requirement and route owners                     | Bind the new visual/loading contract and exact artifact paths                         | Existing requirement identities, command vocabulary and witness placement               | Static Proofkit admission, selective planning, text policy, then exact-head GitHub gate.         |
| New design/plan and documentation index                      | Record bounded decisions, ordered work and evidence                                   | Historical document bytes, one owner per decision, English tracked text                 | Documentation links, requirement binding and addition-only design/plan delta.                    |

The independent validator is the single batch reviewer selected by `AGENTS.md`,
with root adjudication against exact frozen source. It does not replace native
browser proof. No changed aggregator owns business state; the loading predicate
uses each already-admitted query's `kind === loading` branch, plus Governance's
existing `settled.refreshing` flag for compact progress over retained content.

## Ordered Work

1. Freeze source and validate the recommendation ledger, alternatives and
   writer-readiness rows. Inspect local related-product theme files and primary
   MUI documentation. Confirm the font archive's exact integrity and retain its
   license; extract only the named Latin/Latin-ext 400/700 WOFF2 files.
2. Update existing token/style owners directly. Apply the same palette and
   surface hierarchy across all six destinations and economics detail views.
   Preserve the compact sidebar state model and native organization select.
3. Add the stateless loading primitive and use it only at existing loading
   branches and the existing same-scope Governance refresh. Keep the operation-specific
   readable labels and no progress value. Preserve the baseline form and receipt
   across its automatic post-approval refresh, with commands disabled.
   Add reduced-motion styles and native disclosure affordances.
4. Add meaningful browser witnesses and screenshot attachments for catalog,
   overview, workflows, governance and economics at the existing desktop,
   mobile and narrow profiles. Include long-content geometry, loading lifecycle,
   text contrast/axe, reduced motion and same-origin font/CSP checks. Reuse
   admitted fixtures; screenshots must be labelled synthetic evidence.
5. Update the existing UI requirement, route sources and exact source digests;
   add links in the documentation index. Do not edit historical design/plan files.
6. Run admitted local static checks only: formatting, no-emit type checking,
   requirement/binding/route admission and diff hygiene. Browser, component,
   build and full behavioral checks execute through the tracked GitHub route.
7. Freeze the complete batch and its manifest for one independent reviewer
   selected by the repository policy. Admit findings against owner source;
   reopen only the material affected scope after a causal repair.
8. Publish the isolated candidate through the repository-owned GitHub workflow,
   bind evidence to the exact tested head or admitted merge subject, inspect
   retained screenshot artifacts and close only after the required checks pass.
   A dependent PR remains explicitly dependent until its base is integrated.

## Acceptance Matrix

| Claim                                         | Required evidence and falsifier                                                                                                                                                                                                                                                                                                                                                                                                      |
|-----------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Requested visual appearance is implemented    | Inspect actual screenshot artifacts, not an illustration: navy sidebar, Lato, light canvas, white framed summaries/forms, restrained chips/tables on all affected views.                                                                                                                                                                                                                                                             |
| Assets preserve the browser policy            | Production HTML CSP remains unchanged; both font weights load from same-origin hashed assets under that CSP, with no external font/style request or inline style injection.                                                                                                                                                                                                                                                          |
| Loading remains honest                        | Hold a real fixture response: accessible progress has no numeric completion, initial/replacement loading exposes no old evidence; success, empty or failure replaces it. A held same-scope Governance refresh preserves previous observations, form reason and admitted receipt, labels the previous observation and disables baseline commands; completion clears the indicator and scope/authority replacement discards old state. |
| Motion is optional                            | With reduced motion, computed progress animation is absent and loading text remains visible.                                                                                                                                                                                                                                                                                                                                         |
| Navigation and tables remain usable           | Existing compact/manual/mobile/back-forward tests plus viewport overflow and long-content geometry at Desktop Chrome, Pixel 7 and 320x800.                                                                                                                                                                                                                                                                                           |
| Evidence semantics are preserved              | Existing exact-ID/copy/UTC, proposal-source, generation-fencing, command-retry and governance information-tone witnesses pass.                                                                                                                                                                                                                                                                                                       |
| Visual contrast is sufficient                 | Axe checks on ready and pending states; active navigation/controls retain visible focus on dark and light surfaces.                                                                                                                                                                                                                                                                                                                  |
| Native behavior is validated at the candidate | Exact-subject Operator workbench and required Full Check/CodeQL results. Static admission alone is insufficient.                                                                                                                                                                                                                                                                                                                     |

## Delivery Boundary

The requested implementation produces a reviewable source change and GitHub
evidence. Merge, release and live deployment remain separately evidenced
operations; this plan does not assert them from a local screenshot or green job.
The final report names any remaining dependency, unrun gate or unverified live
condition. Preserve original files and record new successor documentation when
future work changes this decision.

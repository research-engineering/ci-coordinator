# Documentation Diagram Validation Plan

Status: implementation plan

The [design](diagram-validation.md) owns the validation contract, alternatives,
registry decision and local-browser exception. This plan owns delivery order and
acceptance. Reviewers follow the current repository reviewer policy.

## Stage 1: Freeze And Review The Design

1. Bind master, the complete documentation corpus and existing CI/toolchain owners.
2. Observe available GitHub rendering evidence without publishing probe content.
3. Decide inventory, rule/renderer versions, input limits and pre-push identity.
4. Independently review the design and this plan; resolve material findings before
   semantic implementation. Unknown provider compatibility remains explicit.

Acceptance: one owner per predicate, non-duplicated requirements, safe lower-cost
alternatives considered, and a falsifier for each proposed success claim.

## Stage 2: Implement One Validation Path

1. Extract CommonMark Mermaid blocks with source identity from worktree or commit.
2. Implement a bounded official renderer and selected core rules with explicit
   unsupported-construct and suppression handling.
3. Expose a local command and retain machine-readable diagnostics/SVG artifacts.
4. Add meaningful positive and negative witnesses for extraction, identity,
   configuration, bounds, parser failures, rendering and semantic false positives.

Acceptance: every inventory identity receives one terminal result per required
stage; no missing/unknown input or render fallback produces success.

## Stage 3: Repair The Corpus And Integrate Feedback

1. Run the checker on the frozen master corpus and retain actual failures.
2. Correct every observed source defect, preserving intended labels and topology;
   the user explicitly authorizes these diagram-only corrections in existing docs.
3. Add regression cases for distinct repaired mechanisms and rerun the full corpus.
4. Implement a nonpersistent opt-in push entrypoint and exact-commit hook, with
   positive/negative cases for dirty worktrees, deletions, new refs, missing bases
   and refusal to supersede an existing custom hook.
5. Wire explicit CI steps and repository-owned requirements, routes and bindings.

Acceptance: source repairs pass the same local/CI validator; the hook cannot claim
a different tree, silently overwrite a hook, or omit a checker/dependency change.

## Stage 4: Independent Review And Publication

1. Freeze the final implementation and have one independent reviewer inspect the
   full feature boundary, admission tests, changed diagrams and plan conformance.
2. Resolve admitted findings and revalidate only affected evidence before the
   final complete gate. Record remaining non-claims without claiming perfection.
3. Run admitted local static checks and the explicitly requested diagram witness.
4. Publish one coherent commit on a new project-native branch and open a pull
   request through the repository publication procedure; retain exact-head CI.
5. Confirm all required checks for that head, attach the PR and report results.

Acceptance: the reviewed source equals the published source, design/plan remain
current to the final behavior, and no local result is presented as GitHub parity.

## Qualification Matrix

| Obligation                   | Independent witness                                                                                                                                 |
|------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------|
| CommonMark membership        | Backtick/tilde, list/quote, longer outer fence, non-Mermaid example, malformed fence                                                                |
| Inventory completeness       | Missing/unreadable source, document/evaluator changes during one check, comparison-only mutation, duplicate/missing result, unexpected empty corpus |
| Selected semantic policy     | Conflicting IDs and valid repeated/quoted/frontmatter/self-transition controls                                                                      |
| Nonstructural text           | Literal metadata markers, accessibility fields and multiline labels; genuine conflicts and source lines preserved                                   |
| Official rendering           | Actual invalid master examples, invalid syntax, oversize fallback, exception, missing SVG                                                           |
| Failure classification       | Valid error-named CSS classes and literal/entity-encoded fallback labels; actual API errors, error result type and effective text-limit boundary    |
| Original-source preservation | SVG graph title and accessibility title/description; single body-to-shadow mutation rejected                                                        |
| Resource closure             | Full push/hook/Node/Chromium cancellation and timeout; delayed setup/evaluation/cleanup joined before further work                                  |
| Relative time                | Monotonic elapsed boundaries independent of forward/backward wall-clock steps                                                                       |
| Commit identity              | Dirty worktree, blob/commit replacements, multiple refs, deletion, new branch, missing remote object, changed checker/lock                          |
| Push repository identity     | Conflicting Git environment, common hook directory, linked worktree and preserved transport settings                                                |
| Production hook              | Actual valid/invalid push pair and an exit-zero hook mutation through the tracked shell entrypoint                                                  |
| Local/CI equivalence         | Same command/policy/source identities; complete corpus rather than a fixed list                                                                     |
| Publication                  | Final manifest, independent review, native CI, exact base/head and PR metadata                                                                      |

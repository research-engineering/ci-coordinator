# Qodana High Findings: 2026-09-20

Status: bounded inspection adjudication; not a waiver or production-readiness claim

## Scope And Result

The IntelliJ Qodana report identifies source revision
`0fde9d0937243ab3e9ce658abd0ddb18832b1507` and engine `IU 262.10968.63`.
Its SARIF SHA-256 is
`ca764736b74ee18befe9a3956dbaf8d05bae4d1a9e64bbadf6b050bc9565405a`.
Scope is all 81 High findings outside the **Python** category; the 944 Python
category findings and other severities are not adjudicated here. RegExp findings
in Python files remain in scope. No user-facing accessibility defect or changed
business policy is established by these 81 findings.

The patch makes 28 bounded source improvements and retains 53 occurrences whose
context defeats the proposed defect or whose simplification would remove a
required responsibility. A Qodana severity is a discovery signal, not the
repository's defect severity. Retained findings are not automatically suppressed.

| Report category                         | Findings | Source improvements | Retained |
|-----------------------------------------|---------:|--------------------:|---------:|
| Accessibility                           |        7 |                   7 |        0 |
| CSS                                     |        8 |                   0 |        8 |
| Probable bugs                           |        1 |                   0 |        1 |
| Data flow                               |        2 |                   2 |        0 |
| GitHub actions                          |       11 |                   0 |       11 |
| HTML                                    |        7 |                   0 |        7 |
| Potentially undesirable code constructs |        9 |                   0 |        9 |
| Try statement issues                    |       10 |                   0 |       10 |
| Unused symbols                          |       18 |                  11 |        7 |
| RegExp                                  |        8 |                   8 |        0 |

## Source Improvements

Coordinates below identify the report's source revision, not mutable current
line numbers. IDs order the selected SARIF rows by category, path and start line.

| IDs   | Owner and original coordinates                                                                                                                                                                                                                                                  | Decision and protected observation                                                                                                                                                |
|-------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 28-30 | [demoBoundary.spec.ts](../../frontend/tests/browser/demoBoundary.spec.ts), 102/147; [workbench.spec.ts](../../frontend/tests/browser/workbench.spec.ts), 599                                                                                                                    | Add `lang="en"` to synthetic documents. The malformed API fixture remains HTML rather than JSON, with the same media type and rejection assertion.                                |
| 31-32 | [configurationReadState.test.tsx](../../frontend/tests/configurationReadState.test.tsx), 69; [usageChart.test.tsx](../../frontend/tests/usageChart.test.tsx), 102                                                                                                               | Add empty `alt` to hostile image strings. These are escaped data, not admitted images. Preserve the `src`, `onerror`, text assertions and explicit absence of an image node.      |
| 33-34 | [diagrams-process-checks.mjs](../../frontend/tools/diagrams-process-checks.mjs), 23; [diagrams-render.mjs](../../frontend/tools/diagrams-render.mjs), 143                                                                                                                       | Declare the language of the actual private renderer documents; preserve network, lifecycle and rendering contracts.                                                               |
| 35-36 | [activityPanel.test.tsx](../../frontend/tests/activityPanel.test.tsx), 98/172                                                                                                                                                                                                   | Inline `NativeURL` in the class base. Argument evaluation constructs the subclass before `vi.stubGlobal` changes the global, preserving constructor capture and static overrides. |
| 56    | [activity/schema.ts](../../frontend/src/api/activity/schema.ts), 169                                                                                                                                                                                                            | Move the `ActivityPageResponse` assignability assertion onto the schema using `satisfies`.                                                                                        |
| 57    | [analyticsSchema.ts](../../frontend/src/api/ciEconomics/analyticsSchema.ts), 321                                                                                                                                                                                                | Preserve the `HistoryAnalyticsResponse` assertion without an unused runtime alias.                                                                                                |
| 58    | [archiveGapRepairSchema.ts](../../frontend/src/api/ciEconomics/archiveGapRepairSchema.ts), 74                                                                                                                                                                                   | Preserve the `HistoryGapRepairResult` assertion with `satisfies`.                                                                                                                 |
| 59-60 | [archiveReadSchema.ts](../../frontend/src/api/ciEconomics/archiveReadSchema.ts), 256/258                                                                                                                                                                                        | Preserve both history-page and attempt-detail response assertions.                                                                                                                |
| 61-63 | [archiveRecordSchema.ts](../../frontend/src/api/ciEconomics/archiveRecordSchema.ts), 156/158/160                                                                                                                                                                                | Preserve summary, job and attempt-detail assertions.                                                                                                                              |
| 64-65 | [archiveRetentionSchema.ts](../../frontend/src/api/ciEconomics/archiveRetentionSchema.ts), 91/94                                                                                                                                                                                | Preserve preview/result assertions and inferred schema types.                                                                                                                     |
| 66    | [observationWorkflowSchema.ts](../../frontend/src/api/ciEconomics/observationWorkflowSchema.ts), 7                                                                                                                                                                              | Remove only the unreferenced singular type alias; keep the used plural response type and schema.                                                                                  |
| 74-76 | [capacity model](../../backend/src/ci_coordinator/capacity_qualification/model.py), 14; [identity model](../../backend/src/ci_coordinator/control_plane_identity/model.py), 44; [evidence lookup](../../backend/src/ci_coordinator/production_admission/evidence_lookup.py), 29 | Remove redundant whole-pattern noncapturing groups, preserving full-match consumers.                                                                                              |
| 77    | [workflow syntax](../../backend/src/ci_coordinator/repo_context/workflow_syntax.py), 36                                                                                                                                                                                         | Replace a two-character alternation with the equivalent character class; preserve the captured context name and lookbehind.                                                       |
| 78-79 | [route observation tests](../../backend/tests/unit/api/http/test_observability_route.py), 208/254                                                                                                                                                                               | Remove redundant closing-brace escapes; route replacement and assertions remain unchanged.                                                                                        |
| 80-81 | [Compose parser](../../scripts/dev_environment/compose.py), 35; [diagram push](../../scripts/diagram_push.py), 17                                                                                                                                                               | Remove redundant closing-bracket escape and whole-pattern group; preserve named captures and accepted object IDs.                                                                 |

For IDs 56-65, the old typed assignment and new `satisfies` check have the same
assignability target. The underlying schema initializer AST is unchanged;
`satisfies` does not widen its inferred type or execute a new validator.
Native TypeScript 7 no-emit assertions compare the full inferred schema types
before and after in both directions; a distinct-type negative control rejects
an unequal pair. This qualifies these ten expressions, not every possible use
of contextual typing with `satisfies`.
For IDs 74-81, the normalized Python regex parse trees, flags, capture count and
named captures agree before and after. These static comparisons do not replace
native tests of the surrounding behavior.
Mutation CE20's source selector follows the simplified spelling; its incorrect
40-to-64-length replacement and the exact-length witness remain unchanged.

## Retained Findings

| IDs      | Owner and original coordinates                                                                                                                                                                                                                                                                               | Defense and condition requiring fresh review                                                                                                                                                                                                                                                     |
|----------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1        | [analytics.css](../../frontend/src/styles/analytics.css), 119                                                                                                                                                                                                                                                | `usage-estimate-key` is a literal class in [UsageChart](../../frontend/src/features/ciEconomics/UsageChart.tsx). Recheck if that consumer is removed or renamed.                                                                                                                                 |
| 2-3      | [workbench.css](../../frontend/src/styles/workbench.css), 751/763                                                                                                                                                                                                                                            | [DataTable](../../frontend/src/components/DataTable.tsx) constructs `column-${presentation}`; [WorkbenchEvidence](../../frontend/src/features/workbench/WorkbenchEvidence.tsx) supplies `prose`. Recheck the complete producer/consumer relation, not a literal-only search.                     |
| 4-8      | [workbench.css](../../frontend/src/styles/workbench.css), 801/805/809/813/817                                                                                                                                                                                                                                | [StatusBadge](../../frontend/src/components/StatusBadge.tsx) constructs all five typed tone classes. Removing these rules changes observable status styling.                                                                                                                                     |
| 9        | [configuration.css](../../frontend/src/styles/configuration.css), 158                                                                                                                                                                                                                                        | `var(--font-mono, monospace)` already provides a generic fallback. Recheck if an override defines the custom property without an appropriate fallback chain.                                                                                                                                     |
| 10-14    | [Full Check](../../.github/workflows/python-persistence.yml), 48/50/56/58/59                                                                                                                                                                                                                                 | GitHub.com supports `$/` same-repository calls; `profile`, `installation_id` and `plan_url` are declared by the called workflows. An unresolved IDE reference does not prove an undefined input. Recheck on provider/platform or called-input changes.                                           |
| 15-20    | [native fixture](../../fixtures/native-target-repository/.github/workflows/full-check.yml), 48/50/51; [bootstrap fixture](../../fixtures/target-repository/.github/workflows/ci-coordinator-bootstrap.yml), 48/50/51                                                                                         | The all-ones SHA is a synthetic fixture coordinate, not a deployable workflow pin. Artifact generation and exact fixture conformance own its meaning. A real consumer using this placeholder is a defect, not covered by this disposition.                                                       |
| 21       | [index.html](../../frontend/index.html), 11                                                                                                                                                                                                                                                                  | Vite resolves `/src/main.tsx` relative to the frontend root. Verify the actual Vite root/build rather than inventing a file at the repository root.                                                                                                                                              |
| 22       | [demoBoundary.spec.ts](../../frontend/tests/browser/demoBoundary.spec.ts), 102                                                                                                                                                                                                                               | The test creates the referenced `src/main.tsx` in a temporary Vite root. This is not a missing tracked file.                                                                                                                                                                                     |
| 23       | [demoNetwork.spec.ts](../../frontend/tests/browser/demoNetwork.spec.ts), 37                                                                                                                                                                                                                                  | The canary serves `/src/redirect.js` dynamically to test redirect rejection. Adding a static file would change the tested boundary.                                                                                                                                                              |
| 24-27    | The two hostile-string tests above, 69/102                                                                                                                                                                                                                                                                   | `onerror` and nonexistent `x` are intentional adversarial input. Do not remove the event handler or provide a working image to appease HTML inspections. Recheck if this data ever becomes actual HTML.                                                                                          |
| 37-45    | [packaged bundle](../../backend/src/ci_coordinator/target_artifacts/resources/ci-coordinator.cjs), [native fixture bundle](../../fixtures/native-target-repository/.ci-coordinator/ci-coordinator.cjs), [bootstrap fixture bundle](../../fixtures/target-repository/.ci-coordinator/ci-coordinator.cjs), 6/8 | Esbuild owns the CommonJS wrapper and its comma expressions. [The source/bundle owner](../../scripts/target_control_bundle.py) binds generated bytes. Change the generator only for an independently demonstrated defect.                                                                        |
| 46       | [network.ts](../../frontend/dev/network.ts), 24                                                                                                                                                                                                                                                              | The catch sends invariant and scenario-delivery failures to the existing error owner. A replacement must preserve both failure sources and exactly-once reporting.                                                                                                                               |
| 47       | [server.ts](../../frontend/dev/server.ts), 104                                                                                                                                                                                                                                                               | Startup rejection passes through server/cache cleanup and rethrow. Deleting the throw or returning normally would falsely admit an invalid endpoint.                                                                                                                                             |
| 48       | [governance schema](../../frontend/src/api/governanceObservation/schema.ts), 43                                                                                                                                                                                                                              | Parsing, canonical resource limits and object admission share one Zod issue boundary. Refactoring must not let canonicalization exceptions escape `safeParse` or alter the diagnostic.                                                                                                           |
| 49       | [diagrams-render.mjs](../../frontend/tools/diagrams-render.mjs), 113                                                                                                                                                                                                                                         | Browser-close failure is translated to a lifecycle failure with cause after bounded drain. This is not a discarded exception.                                                                                                                                                                    |
| 50-52    | The same renderer, 198/200/202                                                                                                                                                                                                                                                                               | Network, browser and output-budget failures survive context cleanup; external-request evidence retains precedence and causes. Preserve both cleanup and rejection if reorganizing this boundary.                                                                                                 |
| 53-55    | [diagrams.mjs](../../frontend/tools/diagrams.mjs), 318/332/342                                                                                                                                                                                                                                               | Worker failure, fatal lifecycle propagation and aggregate size rejection have distinct result/abort semantics plus browser cleanup. A blanket no-throw rewrite would conflate them.                                                                                                              |
| 67       | [events.ts](../../frontend/src/api/workflowDiscovery/events.ts), 16                                                                                                                                                                                                                                          | The type alias intentionally instantiates an exact generated/allowed event-union assertion. No runtime reference is necessary. Removing it weakens compile-time drift detection.                                                                                                                 |
| 68/72    | [stryker.config.mjs](../../frontend/stryker.config.mjs), 3; [globalSetup.ts](../../frontend/tests/browser/globalSetup.ts), 6                                                                                                                                                                                 | Tools load these default exports by configuration. [Playwright](../../frontend/playwright.config.ts) names the setup file; normal source imports are not the complete caller universe.                                                                                                           |
| 69-71/73 | [activityPanel.test.tsx](../../frontend/tests/activityPanel.test.tsx), 102/103/176; [configurationWorkspace.test.tsx](../../frontend/tests/configurationWorkspace.test.tsx), 312                                                                                                                             | Static URL methods are called through the stubbed global by [ActivityPanel](../../frontend/src/features/activity/ActivityPanel.tsx) and [RetainedEpochs](../../frontend/src/features/configuration/RetainedEpochs.tsx). Keep the positive export/revocation and negative late-download controls. |

## Analyzer Exceptions And CI

Do not baseline the entire report, disable High, exclude all tests, or disable
unused-symbol/exception inspections for a whole language. A retained row is a
reviewed argument, not permission to hide later defects. Apply the existing
[decision-reuse predicate](../decisions/review-decision-reuse.md) before reuse.

Prefer correcting the IDE's Vite/module model or qualifying a newer engine.
For a reproducible analyzer limitation, the narrowest candidate exception is
one inspection on one declaration/statement, with the owner rationale beside
it. Recheck the actual suppression scope: injected HTML may have a different
owner from the enclosing TypeScript statement. Do not claim a suppression works
until that exact engine re-analyzes it.

For generated bundles, an optional inspection profile may exclude only
`CommaExpressionJS` on the three exact bundle paths above. Do not edit generated
bytes, exclude their handwritten sources or disable their native integrity and
behavior checks. Re-adjudicate after any generator, source or bundle change:
matching generated bytes does not prove their new semantics are correct.
For fixture workflows, restrict any exception to the particular synthetic call
and its inputs, not every unresolved reference or parameter in either file.
If the analyzer cannot express that scope, keep the findings visible. Never
exclude the real target repository or all workflows. If fixture generation
stops enforcing these bytes, the justification expires. No such automatic
exclusion or stale SARIF baseline is installed by this source patch.

The available untracked `qodana-python:2026.2` configuration is not a cloud-free
CI solution: the Python/JavaScript/TypeScript linter requires Ultimate licensing
and a Qodana Cloud project token. Community Python can run without that account,
but does not provide TypeScript coverage. Substituting it would silently reduce
the claimed scan scope. Therefore this batch adds no Qodana CI workflow,
Cloud token, report upload or paid linter dependency. Existing Ruff, mypy,
TypeScript, Biome, ESLint, workflow and native witnesses remain authoritative.

References: [GitHub reusable-workflow syntax](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows),
[Qodana Python editions](https://www.jetbrains.com/help/qodana/python.html),
[Qodana token requirements](https://www.jetbrains.com/help/qodana/project-token.html),
[local exception inspection](https://www.jetbrains.com/help/inspectopedia/ExceptionCaughtLocallyJS.html).

## Verification Boundary

The report is a snapshot of candidate locations, not an exhaustive code audit.
Source comparison and static checks support the bounded equivalence claims;
new exact-head CI owns unit/browser/lifecycle qualification. The historical
IntelliJ report does not automatically refresh when another worktree changes.
Do not report zero current Qodana findings without a fresh, scope-equivalent
scan, and do not confuse fewer warnings with improved runtime correctness.

# Blocking Browser Proof Admission

Status: implementation design; native qualification pending

Owner: operator-UI proof, `REQ-CI-UI-006`.
Delivery: [implementation plan](browser-proof-admission-plan.md).

## Decision

Use Playwright's `forbidOnly` in the blocking browser configuration and set
Biome's existing `noFocusedTests` rule to error. Reuse the production asset
bundle already built in the same operator job, verifying its exact manifest
before browser execution rather than building it again.

This adopts the concrete browser-policy improvement justified by the local
owner and acceptance witnesses. It does not import another repository's proof
portfolio. Product code, browser projects, test assertions and the separate
interactive developer configuration remain unchanged.

## Focused-Test Invariant

The prior blocking configuration omitted `forbidOnly`; its CLI supplied no
equivalent flag. Biome's recommended focused-test rule has warning severity,
while the current check does not fail on warnings. Thus these mechanisms allow
an accidental `.only` to narrow browser execution without rejecting the run.
No currently committed focused test is alleged.

```text
BlockingBrowserPass -> NoFocusedTestDeclaration
NoFocusedTestDeclaration !=> CompleteRequiredBrowserInventory
```

The first implication becomes an executable library guard, not a custom AST
policy. It is necessary but not sufficient for complete selection: explicit
grep, skipped tests, missing owner mappings and browser fault coverage remain
separate obligations. Interactive debugging retains its own non-blocking
configuration; it is not merge evidence.

Pinned Playwright 1.63.0 source resolves `forbidOnly` to false when neither CLI
nor user configuration specifies it. Its focused-item rejection occurs during
suite admission. The native witness must distinguish that exact failure from
an import, fixture or configuration-loader failure.

## Asset Reuse Invariant

The current operator job already builds the production bundle before frontend
mutation and browser stages. The standalone `test:browser` script builds it
again. Keep that script self-sufficient for independent callers; the workflow
instead invokes the existing read-only bundle verifier and then Playwright.

```text
Build(Source) -> Manifest(ExactBundle)
  -> ExistingMutationWitnesses
  -> Verify(ExactBundle, Manifest)
  -> ExistingBrowserWitnesses
```

The verifier checks complete membership and exact bytes, not just existence of
`dist`. Mutation execution remains isolated and cannot silently repair bundle
drift. A missing, extra or modified asset still fails before browser use. This
removes one build invocation, not a test or native job. Any elapsed-time benefit
must be measured; no CPU or wall-time percentage follows from source alone.

## Alternatives And Boundaries

- Biome warning only does not enforce failure. Error severity is a cheap early
  diagnostic; native Playwright admission independently closes actual `.only`.
- A custom source analyzer duplicates an existing library capability.
- Rebuilding is unnecessary when the exact existing bundle is verified in the
  same job. Reusing an unverified or cross-job directory is not equivalent.
- Moving the sole build after mutation would also remove duplication, but
  delays asset-build failures until after the costly mutation stage. Retain
  the current fail-fast order and use the bounded existing manifest verifier.
  The mutation runner's detached worktree and dependency-only links preserve
  the original source/build; it does not consume or write the original bundle.
- Globally changing the standalone script would remove its independent build
  prerequisite and broaden this change without benefit.

The native test harness uses owned temporary files, exact package-resolved
executables, no shell, finite time/output bounds and an explicit credential-free
environment. It overrides only corpus/output/worker plumbing and omits the
unneeded preview server; the guard-off case is an isolated negative control.
It launches no browser and grants no provider/network capability.

The Biome witness uses an actual scratch file, not the stdin transformation
interface. It supplies no rule-selection flag: `--only` can re-enable a rule
disabled in configuration and therefore cannot prove that configuration.
Independent copies changing only severity to warning or off must pass; warning
retains the exact focused-test diagnostic, off removes it. The unchanged
blocking configuration must reject focused input and accept unfocused input.
This separates configuration authority, source focus and CLI error identity.

Reconsider if the blocking config, CLI selection, build ordering, mutation
isolation or asset contract changes. A focused test passing under the blocking
config, an unfocused valid corpus failing, or a mutated asset reaching browser
execution falsifies the relevant claim. No new production representation or
data migration exists; reverting only restores the weaker CI policy or the
redundant build.

## Primary References

- [Playwright configuration](https://playwright.dev/docs/api/class-testconfig#test-config-forbid-only).
- [Biome focused-test rule](https://biomejs.dev/linter/rules/no-focused-tests/javascript/).

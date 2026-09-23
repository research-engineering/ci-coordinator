# Documentation Diagram Validation

Status: implementation design

## Problem And Scope

Mermaid blocks in README, ROADMAP and documentation currently bypass diagram
validation. The initial master snapshot contains 82 blocks in 43 files, including
flowcharts, state diagrams and sequence diagrams. A documentation link check does
not establish that these blocks render. The delivery scope includes repairing
existing diagram syntax while preserving labels, nodes, edges and intended meaning.

The [delivery plan](diagram-validation-plan.md) owns execution order. This design
owns the input, validation, identity and failure contracts. Existing documentation
navigation, quality execution and dependency locks remain their respective owners.

## Decision And Alternatives

Reuse the existing CommonMark parser to extract one finite inventory. Run selected
rules from `@mermaid-lint/core` and the official Mermaid renderer against exactly
those bodies. Reuse the admitted Playwright/Chromium pair. Expose the same check
locally, in CI and through an optional pre-push hook.

| Alternative                        | Decision and reconsideration condition                                                                                                                                          |
|------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Only the documentation graph       | Insufficient: it does not inspect diagram bodies.                                                                                                                               |
| Only Mermaid lint CLI              | Insufficient: its alternate-parser fast path and Markdown extraction do not establish official rendering of the complete CommonMark inventory.                                  |
| Only official render               | Insufficient for the selected structural lint policy and source-inventory completeness.                                                                                         |
| Core rules and existing Playwright | Selected: public library APIs, one extraction owner and the existing browser provider. A bounded adapter is the added maintenance cost.                                         |
| Core rules and mmdc                | Valid fallback if the adapter becomes more complex than the CLI integration. External Chromium is supported but Puppeteer compatibility is a separate qualification.            |
| Several independent syntax linters | No unique obligation has been identified beyond the official render path.                                                                                                       |
| A tracked per-diagram registry     | Rejected: duplicates source membership and Git history and introduces stale-entry failure modes. Revisit only for externally referenced stable diagram identities or approvals. |

This comparison is bounded to repository requirements. It does not claim a global
optimum, measured lower total cost, or complete semantic verification.

## Inventory And Identity

Use the existing documentation scope: `README.md`, `ROADMAP.md`, and `docs/**/*.md`.
Discover the complete Git-owned path set, including new non-ignored documents in
worktree mode. Commit mode reads Git objects at one resolved commit, independent
of dirty working files. Reject unreadable, malformed UTF-8, unsafe paths, symlinks,
unsupported records and inventory bounds before emitting success.
Resolve commits, select changed paths and read trees/blobs with Git's
`--no-replace-objects` option: local replacement refs are a different view from
the object graph transferred by push. Scope this policy to the diagram path;
other consumers of the shared Git helper retain their existing semantics.

CommonMark fence tokens determine membership, including tilde fences, list and
blockquote containers, and excluding examples nested inside another code block.
Every Mermaid block has its source path, start/end lines, exact body and SHA-256.
The run identity includes source mode/revision, all admitted document digests,
checker/configuration versions and the ordered block identities. Empty individual
documents are normal. An unexpectedly empty repository corpus is an error.

For each required stage, result identifiers must be unique and equal the inventory
identifier set. Equal counts alone are insufficient. Inventory is generated for
the run and can be retained as a diagnostic artifact; it is not tracked authority.
Git remains the owner of change history. A worktree run rechecks input identity
before reporting success; a commit run uses immutable object identities.
Changed document and evaluator inputs must each cause a single in-flight check
to fail. Qualify that comparison with a mutation that retains revalidation while
disabling only its rejection; a second manifest call alone is insufficient.

## Parser, Rules And Renderer

Pin `@mermaid-lint/core` 0.53.1 and official Mermaid 11.17.2 independently.
GitHub's viewscreen Mermaid bundle observed on 2026-09-19 identifies 11.17.2;
its source digest is `0803a3a20192d3b75d92e034cdde89ba572a3656c2789310144a71136e9a44a1`.
The bundle is `mermaidMarkdown-0cdab810ac992b6822c1.js` from
`https://viewscreen.githubusercontent.com/static/assets/`.
Core currently depends on Mermaid 11.16.1. Mermaid 12 and mmdc 11.17.0
are different version contracts; do not silently override dependencies or infer
GitHub compatibility from the newest release number.

Use the observed GitHub initialization settings in a declared checked profile:
`securityLevel: antiscript`, `startOnLoad: false`, flowchart padding 48, sequence
vertical margin 40, gantt/pie width 1200, and the default light theme. Pin the text
limit and reject diagram-level configuration overrides and external requests.
Use a single declared renderer version/configuration for initial qualification.
Record it in output. GitHub's actual renderer version, configuration, fonts and
sanitization remain external observations; the local witness cannot prove provider
parity. Repair reproducible source errors without presenting that repair as proof
that every future GitHub renderer behaves identically.

The semantic pass uses the public core API, not its Merman syntax fast path.
Rules are a finite explicit policy with per-rule severity. The initial admitted
types are `flowchart`/`graph`, `stateDiagram-v2`/`stateDiagram`, and
`sequenceDiagram`. Other types fail explicitly until separately qualified.
The official parser owns grammar acceptance within these types. Lint heuristics
do not expose construct coverage and must not claim complete semantic inspection.
Qualify the selected rules against both invalid and valid constructs, including
frontmatter, quoted labels, repeated declarations and legal self-transitions.
Do not treat a policy warning as proof of incorrect architecture.

The pinned official lexers own accessibility and label boundaries in the semantic
projection. Blank accessibility tokens while preserving line breaks. Escape
flowchart label tokens injectively before applying line-based rules, retaining
shape delimiters, label distinctions and subsequent source-line coordinates.
This adapter uses version-qualified lexer states; an unavailable lexer fails
explicitly. The original body remains the renderer input.

Suppressions are not accepted in the initial contract: a directive must cause an
explicit error rather than silently bypass a stage. Support for suppressions can
be added only with complete malformed, unknown, unused and file-scope semantics.
Frontmatter and initialization configuration require explicit handling; they must
not change the checker security, size limits, renderer identity or acceptance
policy. Unsupported overrides fail visibly rather than being ignored. Metadata
allows only string `title`, `accTitle`, and `accDescr`; configuration overrides
and initialization directives are rejected. Blank the admitted metadata span in
the semantic shadow body, preserving lines, and render the original body.
Literal marker text in YAML values is data. Outside YAML, distinguish actual
line-comment suppression syntax from literal text. Mermaid preprocesses actual
`%%{` initialization directives before grammar parsing, including inside quoted
labels; those remain forbidden. Unknown or malformed suppression comments fail.

The checked profile owns exact limits and rule severities: at most 512 diagrams,
50,000 UTF-16 code units per body, a 15-second per-diagram deadline, a 600-second
run deadline and 16 MiB captured process output. Existing documentation limits
bound files and aggregate source bytes. Exceeding any bound fails the entire run;
it never truncates a successful inventory. Blocking rules are `duplicate-ids`,
`require-direction`, and `sequence-duplicate-participant`. Warnings are
`no-duplicate-edges`, `no-duplicate-node-declarations`, `no-empty-labels`,
`no-activate-without-deactivate`, `state-duplicate-transition`,
`state-empty-composite`, and `state-duplicate-state`. Other rules are disabled.
The inventory/preflight owns metadata placement and empty-body errors.

Official rendering parses the original body. An additional parse-only validator is
unnecessary only when input/configuration bounds prevent Mermaid's `maxTextSize`
fallback from replacing that body with a successful placeholder diagram. Reject
oversize/empty source, render exceptions, error/fallback output, missing SVG,
timeouts and result-identity mismatches. Reset diagram state and unique SVG IDs
between bodies. Produce actionable source-line diagnostics and optional SVGs.

After initialization, read the pinned renderer's effective `maxTextSize` and
require a positive safe integer. Reject an original body above that UTF-16 limit
before calling render. In Mermaid 11.17.2, preprocessing before the fallback
check only preserves or reduces code length; metadata titles are separate from
that code, and admitted sources cannot override configuration. Thus
`preprocessed.length <= original.length <= effectiveLimit` excludes the fallback
without guessing from SVG text. The exposed `mermaidAPI.getConfig()` accessor is
a version-qualified compatibility surface, not an upstream stability promise.
Requalify the accessor and preprocessing premise on upgrades.
Thrown API errors and the returned `error` diagram type identify renderer failure.
User CSS classes and decoded label text do not: `error-text`, `error-icon`, and
labels spelling the fallback message remain valid content. Prefer this input/API
contract over a second parser or an SVG error-pattern matcher.

Use locally installed locked resources. Diagram rendering must not fetch remote
assets, open application services, or obtain credentials. Browser/network failure
is a failed check. Bound inventory bytes, diagram bytes/count, execution time and
output. Close owned browser/process resources on both success and failure.
Check commit document counts before reading blobs, and bound each successive blob
by the remaining aggregate budget. Inventory, Git calls, rendering and final
identity admission share the run deadline. A disposable semantic worker makes
synchronous lint execution cancellable within the per-diagram budget. The JSON
transport has an additional 64 MiB ceiling; exceeding it is an explicit failure.

Relative deadlines use monotonic clocks. A render deadline covers context
creation, evaluation and context cleanup. On expiry, close the browser and join
the pending operation within a two-second cleanup attempt, then abort the batch;
never advance with a losing render promise. Final browser cleanup has its own
two-second bound, so failed render cleanup can consume at most four seconds of
additional waiting. Failed cleanup is a failed CLI execution. SIGINT and SIGTERM
become cancellation requests in the Python supervisors and propagate through
every owned Git query and renderer child, including admission and final input
revalidation before or after rendering. The Node CLI exits normally
on cancellation so the pinned Playwright process-exit handlers terminate its
detached Chromium groups before Python's forced-kill escalation. Qualify this
complete chain using actual Git, hook, Node and Chromium processes.

The run deadline bounds subprocess work and is checked between synchronous
Python operations. It is not a hard preemption guarantee for filesystem,
CommonMark or YAML calls. No claim covers an uncatchable kill of the supervisor
or an operating system that cannot terminate an owned process.

## Local And CI Execution

The user explicitly requests local detection of diagram rendering failures. This
is a bounded exception for this diagram witness to the general GitHub-only browser
test placement. It does not authorize local application, database or full-suite
tests. Finite checker-specific qualification is part of this exception. The local
command and CI execute the same admission logic and corpus.

Install dependencies through the existing locked toolchain and browser setup.
Missing dependencies fail with a preparation command; checks and hooks do not
install packages, browsers or hooks implicitly.
Admit the actually used Python parser/validation package versions against their
locked requirements and compare the installed pnpm lock with the repository lock.
Matching source lockfiles alone does not establish the installed evaluator.

Add explicit commands to the existing quality/provider routes. Browser rendering
uses the existing `operator-workbench` browser setup with a narrow entrypoint,
without starting the application's Vite server or multiplying the corpus across
the UI viewport matrix. Existing required jobs and `Pull Request Gate` remain the
merge boundary. Proofkit selection metadata is not execution evidence.
The separate process qualification runs its exact declared pre-browser and
browser scenarios through an explicit required command. They are outside ordinary
Python test discovery; missing prerequisites, skipped or partial qualification
cannot pass.

Run the full inventory when documentation, checker, policy, renderer, dependency
lock or workflow inputs change. Do not freeze the initial 43 paths or 82 count as
a permanent allowlist. Optimize after measuring cost, retaining complete identity
coverage and invalidation; do not add a persistent cache initially.

## Optional Pre-Push Contract

Provide a nonpersistent push entrypoint that refuses an existing custom pre-push
hook or foreign hooks configuration, then invokes Git with this repository's
hook directory for that invocation only. It does not change shared repository
configuration or install/overwrite hooks. CI remains authoritative. A permanent
installer has no unique requirement and is not included.
Freeze the inherited environment for repository admission, hook lookup and push.
Compare Git's resolved worktree, git directory and common directory with the
source-root identity before inspecting hooks; reject any conflicting redirection.
Use the admitted environment for both hook queries and push, preserving transport
and credential configuration. This is narrower than sanitizing transport settings
or introducing a new repository registry. Linked worktrees remain supported.
Read all standard pre-push update records, skip deletions, deduplicate local commit
objects, and check every relevant proposed update tip. This does not validate each
intermediate historical commit in a pushed range. Reject malformed input.

Compare the remote object with the proposed local commit when both are available.
An absent/unknown remote object or a new branch conservatively selects a full
check. Include documentation and every checker/dependency/policy input in the
trigger. A documentation deletion is a change. Resolve annotated tags to commits
or reject unsupported objects explicitly. A dirty worktree must never substitute
for the pushed tree. If the installed checker/toolchain does not match the pushed
checker and dependency bytes, fail with an actionable checkout/install instruction.

The hook is a fast feedback mechanism and can be bypassed by Git options or a
different client. It cannot replace protected CI. No persistent success receipt
may exempt a different source, checker, configuration or dependency identity.
Qualify a successful push and a rejected invalid-diagram push through the exact
tracked shell hook in a disposable repository. Replacing that hook with `exit 0`
must defeat the rejection oracle; lifecycle test adapters alone do not prove the
production entrypoint. Keep those lifecycle scenarios for their separate signal
and cleanup obligations.

## Acceptance And Non-Claims

```text
DiagramCheckPassed(snapshot) :=
  CompleteCommonMarkInventory(snapshot)
  and NonEmptyCorpus
  and InputsUnchanged
  and for every block identity:
      ExactlyOneSemanticResult
      and SemanticPolicyPassed
      and ExactlyOneOfficialRenderResult
      and OriginalSourceRendered
```

Validate both document reasoning and implementation independently under the
repository's current reviewer policy. Acceptance requires extraction/container
falsifiers, valid-construct controls, renderer failure/limit witnesses, exact
commit hook witnesses, the repaired complete corpus, and native CI at the final
head. Preserve negative fixtures from every repaired root cause.
The original-source oracle observes the rendered graph title and accessibility
title/description. It must reject a mutation that substitutes only the semantic
shadow for the original renderer body. Positive grammar controls must also retain
true duplicate-node and participant conflicts after nonstructural text is masked.

The check does not prove architectural truth, visual clarity, full accessibility,
provider rendering parity or the correctness of unselected lint heuristics. Review
the generated diagrams for meaning and readability; a successful tool result is
evidence for the declared predicates only.
The selected core rules do not inspect conflicts between annotation-only label
updates such as repeated `A@{label: ...}`. Shape metadata is excluded from the
line-based lint projection; genuine declarations outside it remain checked. Original
annotations still reach the official renderer unchanged. This existing heuristic
coverage limit is not treated as evidence of a conflict or a reason to reject
otherwise valid metadata text.

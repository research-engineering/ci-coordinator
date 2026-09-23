# Documentation Authority Graph

Status: cross-cutting specification

Date: 2026-07-21

## 1. Decision

Repository documentation is one bounded directed graph. `README.md` and
`ROADMAP.md` are its roots, while `docs/INDEX.md` is the human navigation
router. Every admitted Markdown document MUST be reachable from at least one
root through repository-local links.

Capability readiness uses one controlled declaration syntax. A paragraph that
starts with `State:` is a readiness declaration, and `ROADMAP.md` is its sole
owner. Other documents may link to or explain an owned state, but they may not
declare another state.

`ROADMAP.md` also owns the sole current task register: stable `CI-` identifiers,
state, prerequisites and closure. Feature plans are execution/acceptance
references, not independent work queues. Audit records retain scoped evidence
and hypotheses; only an admitted task can turn them into scheduled work.
When consolidation deletes a task-bearing document, preserve each still-live
requirement and falsifier in an existing owner before removing its last source.
Shared external inputs remain outside repository-only deletion authority.

The exact machine policy is
`docs/specs/ci-coordinator-proofkit-adoption/documentation-graph-profile.v1.json`.

## 2. Formal Contract

Let:

- `P` be the bounded repository path inventory returned by Git;
- `D` be the bounded set of admitted Markdown documents;
- `R` be the non-empty root set;
- `E` be valid repository-local Markdown links;
- `S(d)` be readiness declarations in document `d`;
- `O` be the configured readiness owner.

```text
AdmittedDocumentationGraph :=
  Bounded(P) and D subset P and Bounded(D)
  and R subset D
  and EveryLocalTargetExistsWithExactCase
  and NoLocalTargetEscapesRepository
  and NoLocalTargetTraversesSymlink
  and EveryLocalFragmentResolves
  and for every d in D: Reachable(R, d, E)
  and for every d in D - {O}: S(d) = empty
  and UniqueSubject(S(O))
```

If any conjunct is false or unknown, the gate fails. Admission therefore cannot
silently skip raw HTML, malformed local URLs, unsupported external schemes, or
an over-budget repository inventory, document set, or link set.

## 3. Link Semantics

The validator parses CommonMark link and image tokens through the locked
`markdown-it-py` dependency. Regex extraction is rejected because reference
links, nested inline markup, code spans, and fenced examples have different
semantics.

Repository-local targets are resolved relative to the source document. Exact
Git path spelling is authoritative even on a case-insensitive filesystem.
Tracked directories are valid navigation targets, but only Markdown files form
graph edges. Every source and target path is checked component by component;
any symlink in the path rejects the claim. Percent escapes must decode as strict
UTF-8. Fragment identifiers are admitted only for bounded ASCII headings under
the repository slug profile.

Raw HTML is rejected rather than partially interpreted. CommonMark permits
multiple link-bearing HTML elements and attributes, so admitting only a subset
would make graph completeness dependent on an incomplete second parser.

External HTTPS links are syntax-admitted but are not fetched in the blocking
gate. Network reachability is time-dependent and would make deterministic local
and provider results disagree.

## 4. Readiness Ownership

`State:` is deliberately narrower than natural-language status prose. Only a
top-level paragraph can declare readiness; list items, quotations, tables, and
code examples remain prose. The nearest configured heading identifies the claim
subject, the value must be non-empty, and one subject may have at most one
declaration. This creates a deterministic uniqueness oracle:

```text
StateClaim(subject, value) => document = ROADMAP.md
```

Arbitrary prose may still become semantically stale. Detecting semantic
equivalence across unrestricted language is outside this deterministic gate;
reviews and future agent analysis may report stricter findings but cannot make
the native gate pass.

The gate's readiness checks do not parse task-table states or prove that every
natural-language obligation has a task. A consolidation review must separately
check unique task IDs, dependency consistency and complete source-to-task
conservation. Do not claim that link reachability proves those relations.

## 5. Failure And Bounds

Repository path count and bytes, document count, individual and aggregate
document bytes, and parsed link count have positive finite limits below fixed
implementation ceilings. The policy source itself is limited to 64 KiB. Git
inventory capture has a finite timeout and combined output bound. Documents are
read only
through descriptor-relative, no-follow traversal; the gate compares the opened
object identity before parsing retained bytes and rejects a concurrent path or
content change. Link resolution starts only after source and parser bounds hold.
Images are validated targets but never navigation edges. Diagnostics contain no
checkout-specific absolute paths and are deduplicated and sorted, so the same
tree produces the same result.

The gate validates tracked and untracked non-ignored files in a worktree. A
passing worktree result does not prove branch-head identity, provider execution,
external-link availability, semantic freshness of unrestricted prose, or
production readiness.

## 6. Module Ownership

| Module                              | Sole change authority                                                     |
|-------------------------------------|---------------------------------------------------------------------------|
| `documentation_graph_contract.py`   | Immutable policy, parse-result, and report vocabulary shared by the gate. |
| `documentation_graph_policy.py`     | Profile admission, canonical fields, relationships, and hard ceilings.    |
| `documentation_graph_filesystem.py` | Bounded Git inventory and descriptor-relative filesystem snapshots.       |
| `documentation_graph_markdown.py`   | CommonMark parsing, URL admission, fragments, and local target semantics. |
| `documentation_graph.py`            | Public API, graph orchestration, reachability, and CLI result.            |

The split follows independently falsifiable trust boundaries. No module owns a
second parser, path traversal implementation, or policy interpretation.

## 7. Proof Surface

The blocking witness is:

```text
backend/.venv/bin/python -m scripts.documentation_graph
```

Unit falsifiers cover missing and case-mismatched targets, repository escape,
all symlink components, unsupported schemes, malformed encoding, unresolved
fragments, graph orphans, image/non-navigation separation, concurrent source
replacement, misplaced, empty, and duplicate readiness declarations, raw HTML
bypasses, and every resource-bound class. The exact falsifier command and the
graph command are mandatory in portable and local quality plans rather than
being delegated to an optional editor extension:

```text
backend/.venv/bin/python -m pytest -q scripts/tests/test_documentation_graph.py
```

Selective planning routes the graph command for every configured Markdown
surface, including all roots and both documentation indexes. Four independent
single-path probes keep that routing implication executable.

## 8. Rejected Alternatives

| Alternative                      | Rejection proof                                                                            |
|----------------------------------|--------------------------------------------------------------------------------------------|
| Regex link extraction            | It does not preserve Markdown syntax semantics and admits bypasses in references and code. |
| Network-check every external URL | Provider availability and rate limits make the gate non-deterministic.                     |
| AI-only authority review         | Model output has no complete deterministic oracle and cannot own merge admission.          |
| Front matter in every document   | It duplicates path identity across the entire corpus without adding a required behavior.   |
| One index per directory          | It increases routing surfaces without improving reachability proof.                        |

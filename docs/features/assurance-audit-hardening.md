# Assurance Audit Hardening

> Public-export boundary: historical source, PR, run, provider and rollout
> observations retained below are design context only, not acceptance evidence
> for `research-engineering/ci-coordinator`. Former private receipts are revoked.
> Synthetic pilot archetypes are proposed examples, not renamed executions.
> Requalify applicable requirements and open tasks against the new exact source.

Status: design
Date: 2026-09-23

## Evidence And Scope

The external audit dated 2026-09-21, revision r3 agreed on 2026-09-23,
has SHA-256 `14bc75e86d6ecaeb76704743307e3cb72c7f6618d39aa1e33d854bf26b43c53f`.
Its target and the implementation base are
`065a4d9c6c384c059ab94e4ec4547c6d88ef205f`. Historical assertions in that
report are not current defect verdicts. This design adopts narrow improvements
after source review; it does not claim an exhaustive assurance frontier.

The new public source target is `research-engineering/ci-coordinator` with clean
initial history. No former redirect/readback, App admission, OIDC trust, package
ownership, release attestation or deployment authorization transfers with source
text. Each external identity requires fresh exact-subject admission.

## Decisions

1. **Complete the runtime Python typecheck surface.** Include `docker/runtime`
   in the existing mypy command. Add precise annotations at filesystem, JSON,
   subprocess and intentionally private stdlib witness boundaries. Do not add
   a second checker/configuration or blanket ignores. Keep the vulnerability
   witnesses' before/after behavior, source pins and admission thresholds.
2. **Make descendant timeout tests non-vacuous.** Observe a real descendant
   ready handshake before starting the short measured execution deadline.
   Assert the handshake and use a parent-only termination counterexample.
   Bound setup and cleanup independently; no production-only test hook or
   larger arbitrary sleep to hide the problem.
3. **Complement the shared API projection oracle.** Retain the useful
   transport oracle but add generated, domain-valid policies with independent
   expected identity/scope and explicit domain-invalid contrasts. Use existing
   Hypothesis budgets; do not enable arbitrary mutations or weaken auth.
4. **Bind structured runtime logs to the packaged build.** Constructor-owned
   release/source fields cannot be spoofed by event payloads. Composition
   supplies validated packaged identity; unavailable diagnostic identity is
   explicit and cannot authorize production. Keep SHA out of metric labels.
5. **Use the existing locale formatter.** Observation counters follow the same
   `en-US` integer contract as neighboring economics views, regardless of host
   locale. No new localization framework is needed.
6. **Maintain qualification routes after merge.** Qualify runtime-input changes
   on PRs and master rather than one old feature branch; retain manual dispatch.
   Separate cache scopes by ref. Keep the exploratory comparison manual-only.
   Use isolated host Python for standalone witness scripts and declare the expected
   allocator in candidate performance measurements. Qualification is not an
   image release or production authority. The repository-importing final
   admission heredoc remains a separate L10 hardening item: adding `-I`
   without an admitted import root would break it.
7. **Protect the embedded actionlint timezone dependency.** A focused witness
   must resolve a non-UTC IANA zone and independently reject removal of the
   embedded-data import. Source admission proves the import, not hermetic
   execution without host zone files. Do not remove `time/tzdata` as an
   apparently unused import.
8. **Rebind dependency rationale and remaining evidence.** A new current
   clarification replaces stale present-tense SQLAlchemy/Psycopg assertions;
   historical designs remain byte-preserved. Runtime/remote qualification
   remains pending where no current receipt exists.

These changes preserve business planning, omission admission, database schema,
authentication and dependency versions. The observable deltas are stronger
test/check coverage, additional bounded log fields, consistent UI formatting
and event-based qualification scheduling. No coverage/mutation floor is lowered.

## Finding Dispositions

| Report coordinates | Independent disposition and action |
| --- | --- |
| A1; R1/R2/R8 | Intentional discovery/static-evidence boundaries. Preserve execution-failure guards; no invented mutation score or runtime proof. |
| A2/A5-A8/A10 | Limited measurement scopes are real, not absence of tests. Risk-based extension belongs to B7 after measurable baseline; do not impose arbitrary whole-tree thresholds. |
| A3/A4; R3 | Shared admission function and missing generated positive policy lane limit independent evidence. Add the complementary lane in this batch. |
| A9 | Two timeout witnesses can pass without a descendant ever starting. Repair handshakes and causal controls here; do not label every sleep a flake. |
| B1; R4/R5 | Exact witness admission and prior guards exist. Retain evidence schema; constant success normalization alone is not a defect. |
| B2/B4; R6/R12 | Source defenses refute the reported broad bypass/UID/build-graph concerns. No safety relaxation or gratuitous rewrite. |
| B3 | Version-update policy and provider security settings are distinct decisions. Runner/account migration must revalidate both before changing automation. |
| C1/C2/C4 | Pins and compiler roles are deliberate. No version upgrade follows from audit age or an API compatibility alias. |
| C3; L3 | Main pytest corpus on the delivered userspace remains a qualification gap. Design its bounded image-compatible subset in B7; host tests/smoke cannot substitute for it. |
| R7; L5 | Package inventories and signed snapshots already carry evidence. Reproducibility/consumer improvements need their own precise predicate. |
| R9/R10/R11; L4/L10 | Apply allocator assertion, current event routing, ref-scoped cache and isolated Python here. Native execution remains blocked by runners. |
| L1/L2/L9 | Historical performance data is not current-head proof. Preserve the measured Alpine tradeoff; record historical receipt and requalification separately. |
| L6 | Repair expiry and Dockerfile source binding intentionally fail closed. No silent policy renewal. |
| L7/L8 | Disposable-checkout interruption and unversioned-library candidates are hardening opportunities, not established image defects. Retain for B7 runtime qualification. |
| SARIF; d2; IV-03 | Expand actual mypy scope with narrow annotation repairs. No claim that every raw SARIF diagnostic is a product defect. Remaining occurrence/clauses ledger stays open. |
| IV-01/IV-02 | Global duplicate-key/redirect policy would change accepted API behavior; no shown bypass. Defer policy unification to explicit API compatibility design. |
| IV-04/IV-05 | Analyzer/database and cost-hint provenance are real bounded gaps. Keep test partition total; B7 must bind exact measurement inputs before claiming reproducible balancing/advisory verdicts. |
| IV-06/J11 | Add log build identity without forbidden SHA metric labels. No claim about all six deployment telemetry identities. |
| IV-07/IV-09 | RC YAML rationale exists; preserve narrow disclosed exception. Add embedded timezone witness. |
| IV-08 | Stale current dependency rationale is valid; publish current successor clarification without rewriting historical design. |
| IV-10 | Package SCC is not a demonstrated forbidden module cycle. Preserve allowed boundaries absent a concrete violating dependency. |
| IV-11 | Consistent observation integer formatting is justified; repair and test with hostile ambient formatting. |
| IV-12/IV-13/IV-14 | Database collation, provisioner environment hardening and restore drills belong to deployment B7; no claimed injection/leak/recovery absence. |
| IV-15/IV-16 | Optional hook and monotonic timing assertions are not defects by themselves. Keep exact-budget witnesses. |

## Runner And Transfer Gate

GitHub Actions is enabled, but current check annotations explicitly say hosted
runners are disabled by the Enterprise administrator. The repository has no
registered self-hosted runners. Do not change `runs-on` to an invented label,
turn failures into skips, run behavioral suites locally, or deploy a persistent
privileged runner into the application Swarm without an approved isolation model.

Prefer restoring the existing hosted execution contract when the administrator
allows it. Otherwise admit ephemeral Linux runners on a dedicated corporate
host/pool with clean per-job state, untrusted-PR isolation, bounded capacity,
required Docker/BuildKit support and separately scoped release credentials.
Requalify all runner families, caches, cancellation/cleanup and exact-head
required checks before merge. That is a distinct infrastructure task, not a
reason to loosen this batch's acceptance. No billing or host provisioning is
authorized by this document.

## Current Dependency And Runtime Evidence

For the current lock, SQLAlchemy is 2.0.54 and Psycopg is 3.3.6. This paragraph
supersedes only the present-tense version claims in
[Administrator Activity](administrator-activity-product.md#exceptional-stream-cleanup);
its historical evidence and selected-row/transaction contract are preserved.
Source readback of the matching installed distributions confirms that
`AsyncConnection.stream` yields the result and performs its closing task in
the normal-completion branch, while `AsyncCommon.close` calls the underlying
result's close operation through `greenlet_spawn`. The explicit owned
`finally: await result.close()` in `persistence/activity_write.py` remains
justified; no cleanup abstraction or behavioral rewrite is required.

Inspected source SHA-256 values:

| Distribution and source | Digest |
| --- | --- |
| SQLAlchemy 2.0.54, ext/asyncio/engine.py | `774764174dfc203efb833ac916bd0ede4c681cda8e1fa71f68420aa35ac0e2a6` |
| SQLAlchemy 2.0.54, ext/asyncio/result.py | `c457d360bf22f72aac9820fd12b1dbd89aa12a1030ba5786cf5aaeb604382920` |
| Psycopg 3.3.6, _server_cursor_base.py | `6514d1e38e8899aaf52353949c8db2a43dccb0445d885986433d5ba0dce19cde` |

Psycopg's SELECT server cursor uses DECLARE/FETCH; bounded DML and repeated
cancellation remain separate test obligations. These source reads are not a
fresh PostgreSQL run. Recheck this rationale when either locked dependency or
the stream lifecycle changes.

The former private Alpine/mimalloc comparison receipt is not exported. Allocator
tradeoffs remain hypotheses until the new exact-source campaign records its
subjects, samples, immutable artifacts and controlled environment. No historical
run or artifact digest qualifies this public source.

## Alternatives And Falsifiers

A full test-framework rewrite, global 100% threshold, retry-to-green policy or
new broker adds cost without proving the identified predicates. Prefer local
owner changes. Reopen this choice if the new witnesses still accept the named
counterexamples, static typing needs broad suppression, or bounded runtime
qualification demonstrably duplicates more expensive equivalent evidence.

Implementation and acceptance are ordered by the
[implementation plan](assurance-audit-hardening-plan.md).

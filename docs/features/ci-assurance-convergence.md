# CI Assurance Convergence

> Public-export boundary: historical source, PR, run, provider and rollout
> observations retained below are design context only, not acceptance evidence
> for `research-engineering/ci-coordinator`. Former private receipts are revoked.
> Synthetic pilot archetypes are proposed examples, not renamed executions.
> Requalify applicable requirements and open tasks against the new exact source.

Status: implementation design

Date: 2026-09-21

## 1. Decision And Ownership

Adopt the useful non-deployment assurance invariants identified in
extension-pilot while preserving the stronger native coordinator gates.
Use the [implementation plan](ci-assurance-convergence-plan.md) for delivery
order and acceptance. Existing
[testing contracts](../architecture/cross-cutting/testing-and-proofkit.md),
[module ownership](../architecture/cross-cutting/module-ownership-and-decomposition.md)
and [proof command execution](../architecture/cross-cutting/proof-command-execution.md)
retain their authority. This design owns the integration delta.

Former private comparison revisions are removed from this export. A mechanism
is selected by preserved observables and independently rejected counterexamples,
not by tool count or file size. Global superiority, whole-program perfection
and lower total cost are not inferred from this design.

## 2. Finite Transfer Register

Useful CE patterns and invariants derived from their comparison receive one of three dispositions:
implement a missing predicate; preserve an existing stronger predicate; or
retain a separately owned operational condition. The register is the finite
scope of this implementation, not an exhaustive catalog of all possible checks.

| ID | Transfer or comparison-derived invariant | Coordinator disposition and acceptance |
| --- | --- | --- |
| CE01 | Runtime dependencies match actual imports | Add pinned deptry with explicit source roots; reject missing, transitive and development-only runtime dependencies. No usage exceptions are needed at this epoch. |
| CE02 | Standard executable module boundaries | Transfer only a module contract with demonstrated parity to Import Linter; retain custom capability, symbol, dynamic import and ownership rules. |
| CE03 | Secrets absent from tree and intermediate PR commits | Add a pinned, checksum-admitted scanner with explicit tree and base/head history scopes, redacted output and protected scanner defaults. |
| CE04 | Audit errors and unexpected unauditable dependencies cannot pass | Preserve native uv/npm/Go scanners; admit their actual machine-output contracts instead of copying pip-audit-specific assumptions. |
| CE05 | Exception scope is explicit | Admit no dependency-audit exceptions; bind each benign secret-scanner fixture allowance to an exact value, rule and owner path. A future time-limited vulnerability exception requires a separately reviewed policy. |
| CE06 | Test partitions cover the intended population | Preserve exact native node accounting; additionally bind collection to an independently enumerated candidate-file population. |
| CE07 | Different fixture lifecycles are isolated | Preserve existing PostgreSQL isolation and separate the destructive connected administrator fixture from mocked browser tests. |
| CE08 | Browser selection includes supported authored tests | Use native project discovery with explicit test roots; retain focused-test rejection and make intended exclusions visible. |
| CE09 | Real login composes with a persistent business effect | Exercise a real disposable identity provider, application and PostgreSQL through the existing budget-policy UI. |
| CE10 | Effect integrity survives reload | Independently reread policy bytes/version and audit after browser reload rather than accepting an echoed HTTP response. |
| CE11 | Repeated business commands do not duplicate effects | Replay the exact operation identity and require one policy transition and one audit event. |
| CE12 | Synthetic sensitive data stays out of logs | Admit only synthetic fixture values and check owned service diagnostics for their disclosure without printing matched bytes. |
| CE13 | Failed/flaky tests leave actionable diagnostics | Preserve raw native failure diagnostics in a separate namespace; retain browser attempt diagnostics without treating them as admitted success. |
| CE14 | Workflow reuse is a real interface | Expose narrowly typed, credential-free reusable mechanics; keep product test roots, fixtures and native aggregate local. |
| CE15 | Required static checks fail on new defects | Preserve strict lint/type/format gates; do not import advisory steps or warning-count ratchets. |
| CE16 | Real concurrency and SQL-plan regressions are observable | Retain current PostgreSQL conflict, rollback, EXPLAIN and bounded workload witnesses; do not add a duplicate benchmark merely to match a tool name. |
| CE17 | Source, tools and artifacts have stable identities | Preserve exact source/run/attempt/lock binding and immutable action/image/tool inputs; regenerate derived CI through existing owners. |
| CE18 | Privileged setup is separated from candidate execution | General quality execution receives no product secrets or id-token capability; dependency installation must not inherit deployment authority. |
| CE19 | CI changes receive independent admission | Independent batch review is required for this change; provider-enforced corporate policy ownership remains an enrollment condition. CODEOWNERS is explicitly deferred by the user. |
| CE20 | A result is bound to the actual merge composition | Preserve merge_group support and document strict/queue enrollment separately from source-level event support. |
| CE21 | Findings, execution errors and absence are distinct | Required wrappers reject empty/malformed/missing results and unexpected skip states; a successful scanner process does not automatically mean accepted findings. |
| CE22 | Test-oracle sensitivity has the same test population | Extend Vitest mutation admission with baseline/mutant identity and skip parity, retaining the stronger existing pytest contract. |

## 3. Protected Observations

The native Full Check remains the source of required job groups. The generated
Coordinated Checks projection does not acquire additional omission authority.
Existing Python branch/owner coverage, curated fault operators, typed promise
checks, browser safety cases, PostgreSQL role restrictions and artifact identity
checks remain required. Optional deep campaigns retain their existing semantics.

Production authentication, issuer, TLS validation, cookie security, database
transactions and API error contracts do not change to make a CI fixture easier.
No test endpoint, production credential, deployment or CODEOWNERS assignment is
introduced by this design. Merge authority is conditional on Section 10.

Existing design and implementation-plan documents remain byte-for-byte
unchanged. New documentation is routed through the existing index.
The two new documents increased the initial diagram source inventory from 319
to 321 documents; integrating master 050b7ef3947ad69b5d1e7762308a75a57503ee4e
adds its editor guide, for 322. Raise only the count capacity from 320 to 352; retain
the byte, link, path and execution limits and every content admission rule.

## 4. Evidence Contract

For an execution epoch E and its admitted obligation set O(E):

    Accept(E) =
        sourceAndExecutionIdentityMatch(E)
        AND applicabilityIsAccounted(E)
        AND everyRequiredOccurrenceIsPresentExactlyOnce(E)
        AND everyRequiredOutcomeIsAdmitted(E)
        AND everyExceptionHasAnApplicableOwnerReason(E)

Policy authority is a separate precondition. A candidate cannot establish
independent authority merely by passing a validator that it also changed.
A pinned workflow that trusts mutable candidate scripts, thresholds or
allowlists does not by itself close that boundary.

Configured source, selected population, executed evidence and provider-enforced
merge admission are separate facts. This change supplies executable witnesses;
only their exact-subject native GitHub runs can establish execution.

### Collection Before Sharding

Derive expected native candidate files independently from the Git-observed
declared test roots and filename grammar. The collection report records
module outcomes, the population before selection hooks, deselected identities,
the final selected population and completed collection.

Reject collection errors, unapproved module skips, missing or empty candidate
modules, unexplained deselection and silent removal by selection hooks.
The plan binds admitted candidate files and nodes; each shard admits exactly
its assigned files and identities. Preserve the two specifically owned
call-phase skips. This is a native-universe contract, not discovery of every
possible dynamically generated test in arbitrary Python source.

### Failed Execution And Mutation

Write diagnostic reports to an untrusted failure namespace before temporary
cleanup. Never add them to the exact successful shard-artifact inventory.
Failure to preserve diagnostics does not turn a failed test into success.
Persist the process receipt before publishing captured output to the runner
console. Each captured-output tail, in both the console and receipt, is limited
to 65,536 UTF-8 bytes; a separate fixed-format marker identifies truncation.
Record the captured text's encoded byte counts without claiming to recover the
original subprocess bytes. Pytest prints
only its 25 slowest phases, while the native report retains every phase and
duration. Oversized header/configuration fixtures use explicit case IDs instead
of embedding their full payloads into native identities and reports. Their
payloads, assertions and test count are unchanged; native identities remain
bound to the current source epoch. Console limits do not relax outcome
admission. Keeping the complete captured buffer in the console was simpler but
made receipt preservation depend on publishing potentially large output first.
The bounded alternative adds byte accounting and preserves a more useful
failure boundary. It does not prove the cause of historical runner cancellation,
nonblocking console writes or provider log retention. Reconsider it if native
phase evidence becomes incomplete or retained failure tails cannot diagnose an
actual failure.
Nonserial native test processes use the existing 900-second test-process budget
inside their 20-minute job. The manually requested serial qualification uses a
3600-second process budget inside a 75-minute job; normal PR shards and their
gate are unchanged. On source `9a26f1b`, run `35940367926` reached only 83% by
the former 2400-second deadline. The same-head eleven shards recorded 2819.67
seconds of total shard-session time and 2733.90 seconds of test phases. The
prior serial test loop finished in 2340.82 seconds on `9939a25`, with the
session-finished marker at 2342.61 seconds. Neither shard measure is a serial
runtime bound, but these observations falsify a reliable 2400-second budget.
The new finite budget provides about 28% headroom over measured shard-session time;
the outer job retains 900 seconds beyond it for setup and comparison. A timeout
still fails closed, and success on a fresh exact head remains to be observed.
Pytest's native faulthandler emits a stack after
120 seconds in one test protocol. This does not diagnose collection or shutdown
hangs by itself; phase markers and bounded process tails remain separate clues.
The native shard step additionally uses the platform's GNU `timeout` at 960
seconds, with INT then KILL after five seconds, independently of the inner
Python wrapper. An atomic bounded progress file records phase and the current
test's source/name and full node-ID digest before fixture setup. It remains
diagnostic only. The outer timeout keeps its nonzero status and does not prove
cleanup of descendants that created separate process sessions. The isolated
GitHub runner owns final disposal; failed shards never publish admitted success.
The GNU-specific conformance probe belongs to the required Linux native universe;
the portable developer test command does not require GNU tools on macOS.
The supervisor also passes an owned duplicate of its output descriptor through
an explicit pytest option. The diagnostic plugin writes only bounded progress
metadata directly, before fixture setup, with a 4 MiB session cap and 4 KiB
record cap. Parameters remain hashed. The child makes this descriptor
non-inheritable for later execs and closes it during unconfigure; the supervisor
closes its own copy. Raw forks can still retain a descriptor. Pipe backpressure
can delay a child write, so this is best-effort observation under the existing
supervisory deadlines, not a nonblocking or provider-log retention guarantee.
These bounds preserve failure evidence when the wrapper can finish before the
runner stops; they do not guarantee artifacts after runner loss or cancellation.

A mutation requires the admitted baseline population and skip disposition.
Missing tests, changed identities, collection failure and timeout are invalid
evidence. The pytest reporter additionally distinguishes lifecycle phases.
Vitest's native JSON does not always distinguish a beforeEach assertion from
a test-body assertion; identity parity does not claim that stronger phase proof.
Vitest also omits its task ID from this report. Repeated parameterized tests
with the same file, suite path and title are bound by occurrence ordinal within
that identity, preserving multiplicity and skip order. This is reporter-order
identity, not a claim of independently recovered source coordinates.

## 5. Dependency And Architecture Mechanics

Use deptry for dependency declarations and native vulnerability scanners for
advisories. These predicates are independent. Locks and supported runtimes
remain owned by the existing toolchain profile.

The first standard architecture candidate is the direct-import restriction:
httpx2 is accessible only from ci_coordinator.integrations and its descendants.
Qualify permitted and forbidden import forms against the incumbent rule before
removing that one custom predicate. No broad architectural scanner replacement
is admitted. Import Linter must not accidentally introduce indirect-import
restrictions that change the current policy.

Native audit output determines the adapter shape. A pip-audit skipped-package
field cannot be assumed to exist in uv audit. Any inventory reconciliation must
identify its expected package universe independently and disclose marker,
group, source and local-project boundaries.

## 6. Secret Scanning And Reuse

The reusable secret-scanning mechanism owns tool installation, checksum
verification, redaction, exit/finding admission and explicit scope selection.
The caller owns source identity and whether its event requires history.

PR and merge-group history binds validated base/head Git objects. Unknown
history identity fails when history is required. Tree-only manual inspection
must not claim intermediate-commit coverage. A candidate-owned ignore file or
scanner configuration must not silently override the admitted scanner policy.

The action resolves its own packaged tooling through the action source path;
it never assumes that caller checkout contains the platform implementation.
General quality jobs use read-only contents access and no product credentials.
This interface can later move to a shared repository without moving product
fixtures or changing their meaning.

The reusable `source-assurance.yml` derives required PR/merge-group history
from the event and accepts only an optional manual base input. Its action uses
GitHub Cloud's `$/` reference to bind the action to the defining workflow's
repository and running commit. This interface is not qualified for GitHub
Enterprise Server. See the [native reference contract](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax).

## 7. Connected Administrator Witness

Use the existing budget-policy command for repository scope 1:1, an isolated
PostgreSQL instance and disposable real identity provider. The browser performs
the normal login redirect and existing UI mutation. Its response is only one
observation: a separate database reader verifies retained policy contents,
version, audit actor, operation identity and event cardinality.

On the GitHub Linux host, the independent SQL reader uses the PostgreSQL
container's address from the exact owned internal bridge. It verifies the
network name, project label, bridge driver, internal flag and container membership
before connecting. No PostgreSQL host port or external network is published.
This uses the [Docker host-to-bridge contract](https://docs.docker.com/engine/network/port-publishing/#gateway-modes),
and does not claim equivalent host routing on Docker Desktop.

After reload, the UI must show the retained value. Replaying the identical
command must preserve one effect and audit event. An unauthenticated mutation
must have no persistent effect. Synthetic credential/log canaries are scoped to
this fixture and omitted from reports.

Preserve the production issuer and TLS contract using isolated CI networking
and an explicitly trusted disposable certificate authority where necessary.
No connection to the real issuer or other development services is allowed.
Provider setup or readiness failure is a failed witness, never a skip.

The existing development/HMR and deterministic browser suites remain distinct.
This fixture proves one critical composition, not every UI path or production
deployment.

The connected fixture publishes structured stages, failure codes, process
outcome and image/log digests. Browser diagnostics retain only an allowlisted
phase, bounded HTTP status codes, a closed JSON/HTML/other media category,
fixed SDK error-marker labels, same-request completion/failure/abort observations
and numeric coordinates in the owned spec. Marker
presence is a diagnostic observation, not a root-cause conclusion; arbitrary
error text, header values and response content are excluded. Raw logs, traces, HAR, video and screenshots are
not published because login creates additional sensitive values. Setup failures
may require a scoped diagnostic follow-up; these receipts do not provide full
log-level diagnosis. The browser starts body acquisition in the matching response continuation,
while awaiting the real button click in parallel. Write status, media and
completion/failure/abort observations refer to that exact first UI mutation
request; later replay and rejection requests cannot replace them. The committed-JSON
assertion and subsequent independent SQL checkpoint remain required. Native
qualification must establish the behavior; observer timing alone does not prove
a historical CDP failure's cause. Canary checks cover the explicitly generated raw and
URL-encoded values, not every possible encoding or disclosure channel.

### Volatile Synthetic Credentials And Consumer Copies

Generate fresh synthetic credentials for one fixture run only after admitting
two host tmpfs mounts.
The producer-owned mode-0700 root has a 16 MiB limit and
`noswap,nosuid,nodev,noexec`; its nested `runtime` mount has a 2 GiB limit and
`noswap,nosuid,nodev`, permitting execution needed by JVM/native temporary
libraries. Evidence and checkpoint ACKs stay on the first mount. The browser keeps
umask 077 for its private runtime files. It publishes checkpoint/diagnostic
files by exclusive temporary creation, explicit mode 0644 and atomic rename;
the host-owned mode-0700 evidence parent limits traversal to the authorized
producer and browser root. Explicit chmod before publication is necessary:
creation mode alone would be masked to 0600 and deny the independent reader. These are
filesystem capacity bounds, not a bound on total VM memory. Linux tmpfs can
otherwise swap; `noswap` must be observed, not inferred from the filesystem name.
See the [kernel tmpfs contract](https://docs.kernel.org/filesystems/tmpfs.html).

This privileged host setup is admitted only on the dedicated, disposable
GitHub-hosted Linux VM, never a developer machine or shared/self-hosted runner.
Before generating secrets, disable all host swap, set `kernel.core_pattern` to
`/dev/null` and `kernel.core_uses_pid` to zero, and verify the discard target is
character device 1:3. Set the producer and every consumer core limit to zero.
Recheck host policy, mount identity/device, ownership, flags and capacity before
consumer execution. Global no-swap also covers process heaps and browser shared
memory; no separate `noswap` claim is made for Docker `/dev/shm`. These controls
do not protect against host root, the daemon, a hostile kernel or hypervisor.

The [consumer contract](../../scripts/ci_business_witness/consumers.py) declares
the entire source/target/RW bind population and exact image `Config.User`.
Container inspection must agree before start: read-only rootfs, no privileged
mode, only those binds, admitted image identity, project ownership and core
limits. No consumer receives a broad mount of the secret root. Required secret
files are mode 0400; public certificates and the credential-free `backend.env`
are producer-readable mode 0444. Secret values are not Docker configuration
environment values: production `_FILE` inputs and consumer-local process
environment remain the existing interfaces.

| Consumer | Required volatile storage and identity |
| --- | --- |
| Backend and migration | Config.User `10001:10001`; corresponding secret files owned by that UID/GID; separate writable temporary directories. |
| PostgreSQL | Empty Config.User for the root entrypoint, then PostgreSQL UID/GID `999:999`; the superuser password file remains readable after `gosu`. Bind the complete `/var/lib/postgresql` volume, socket directory and temporary directory to RAM. |
| Database provisioner | Root entrypoint; only its three password files and public SQL input, with separate RAM mounts covering the inherited PostgreSQL volume and temporary directory. |
| Keycloak | Config.User `1000`, realm owner `1000:0`, `--db=dev-mem`; data/import, Quarkus and temporary paths are RAM-backed. Seed the exact image's public Quarkus files before transferring ownership, preserving file modes. An empty mount cannot hide required bundled files. |
| Proxy | Root entrypoint with its public config/certificate and private TLS key; cache owner `101:101` and separate RAM run/temporary directories. |
| Browser | Root; only its public CA, browser fixture, evidence and temporary directory. HOME/NSS/XDG, profile, downloads and crash paths stay in RAM; browser binaries remain in the built image. |
| HTTPS readiness | Root; only the public CA and its own RAM temporary directory, without the browser credential file or evidence mount. |

Disable JVM heap/core dump generation; remaining JVM error and browser crash
paths are confined to the consumer's RAM temporary directory.

PostgreSQL retains ordinary transactions, role restrictions, WAL, independent
SQL reads and exact-operation replay. Its RAM data bind survives a container
process restart while that host mount exists. The business oracle establishes
committed effects and audit cardinality within this fixture; it does not prove
power-loss durability or retention after VM disposal. Keycloak's development
database choice remains fixture-only; production authentication, issuer and TLS
validation stay unchanged. `dev-mem` is an admitted
[Keycloak database option](https://www.keycloak.org/server/db).

### Attached Output And Disposal

Use `logging: none` and disable automatic image healthchecks so neither the log
driver nor Docker health history stores consumer output. Explicit captured
`pg_isready` and CA-validated HTTPS probes retain readiness admission. For each
of the four long-running services, create and inspect the owned container, then
use standard `docker start --attach`. The CLI establishes attachment before
starting the container; this ordering is visible in the
[Docker CLI implementation](https://github.com/docker/cli/blob/v28.5.1/cli/command/container/start.go).
The native probe qualifies the installed provider rather than inferring parity
from that reference alone.

Retain stdout and stderr bytes separately in bounded process memory, with a
combined 4 MiB ceiling per service. Preserve bytes through surrogateescape;
make no claim about global ordering between the streams. Reject early completion
before the owner requests stop, missing/empty captures, transport errors,
overflow, incomplete drain and disagreement between CLI status and the inspected
container exit status. A matching nonzero status after intentional stop is valid
transport evidence. Canary and nonempty checks still cover all four services.

Before the real journey, native probes using the already-built application
image require byte-exact replay of early output, blank lines, multiline
PEM-like data, 17,000-byte framing crossings, both streams, invalid UTF-8 and
missing final newlines. A second probe must reject actual output overflow at a
small limit. Both probes are isolated, read-only, logging-free, without automatic
healthchecks, and removed only after exact ownership checks. Raw service/browser
output remains unpublished; artifacts contain only admitted diagnostics/digests.

Cleanup requests bounded capture cancellation/join, stops/removes only owned
containers and unmounts the admitted runtime mount before the secret mount.
Cleanup failure remains visible; deletion after arbitrary runner termination
and physical memory erasure are not claimed. Plaintext still exists in RAM and
consumer processes. This removes the intended regular-disk storage path under
the admitted host/consumer model; it does not prove the absence of every copy.
CodeQL may still report `py/clear-text-storage-sensitive-data` at `write_private`
because filesystem admission does not change the visible plaintext sink.
No taint suppression, query exclusion, dismissal or ignore is authorized to make
that finding green. Risk mitigation and analyzer clearance are separate facts.

## 8. File And Authority Structure

| Surface | Responsibility |
| --- | --- |
| Existing native test plan/report/execution modules | Candidate-file, node and outcome admission. |
| Existing mutation report/runner modules | Mutation population and failure classification. |
| Dependency hygiene adapter and native configuration | Package-usage and admitted module-boundary commands. |
| Packaged secret-scan action | Portable scanner mechanics and immutable policy inputs. |
| CI-only business fixture and connected Playwright project | Real isolated identity/application/storage composition. |
| Native workflow | Required execution groups and result aggregation. |
| Existing generators and proof catalogs | Derived workflow, runtime entrypoint and requirement projections. |

Do not create a universal CI DSL, duplicate dependency resolver, second product
authorization model or new benchmark framework. Use native tool contracts and
existing bounded process/filesystem primitives when they preserve the required
observables. Each new helper must have a distinct failure boundary.

## 9. Alternatives And Revision Conditions

| Alternative | Decision |
| --- | --- |
| Copy CE workflows wholesale | Reject: project roots, private installation privileges, advisory steps and selectors differ. |
| Add every scanner and maximize percentages | Reject: duplicates cost without proving additional invariants. |
| Replace all custom architecture checks with Import Linter | Reject: module reachability does not preserve symbol/capability policy. |
| Keep every incumbent mechanism unchanged | Reject for proved collection/diagnostic gaps and missing dependency/business predicates. |
| Modify production auth to permit an easier CI identity provider | Reject: changes a protected security contract. |
| Build a general reusable test scheduler now | Defer: two product fixture lifecycles do not establish a common lower-cost engine. |
| Require CODEOWNERS for this implementation | Defer by user decision; do not claim independent provider authority is solved. |
| Retain private disk-backed fixture files and delete them afterward | Simpler, but leaves the demonstrated storage sink and consumer copies on disk; cleanup cannot establish absence after interruption. |
| Encrypt only producer files or replace real consumers with mocks | Reject: decryption recreates consumer plaintext, while mocks lose the production authentication and SQL oracle. |
| Use admitted host tmpfs and exact consumer binds | Select for this boundary: standard filesystem/container controls preserve real consumers. Costs are privileged dedicated-VM setup, bounded RAM demand, seeded Keycloak files and provider qualification. |
| RAM syslog collector or a custom attach protocol | Reject for this fixture: adds framing, transport and loss-accounting obligations when the standard pre-start CLI attachment can preserve the required bytes. |

The volatile trajectory is preferable to the simpler private-file baseline
only under the admitted host and complete consumer contract. Falsifiers include
swap/core-policy drift, an unaccounted writable mount or daemon output history,
wrong UID or unreadable entrypoint secret, missing seeded files, native output
byte loss, RAM exhaustion, or changed login/SQL/replay behavior. Any such failure
blocks admission and requires repair or reconsideration of this bounded design.
Analyzer persistence also blocks the requested merge even if storage mitigation
works; it is not permission to weaken analysis or claim a global optimum.

Measure collection/setup/test/combine duration, first actionable signal,
full-gate latency, runner minutes and retry cost separately. Reuse existing
fixture preparation before adding jobs or shards. Revisit an adapter if it
needs more bespoke policy than the incumbent or produces unexplained false
positives. Security and correctness predicates cannot be traded for latency.

The serial `quality.branch-head` catalog envelope remains the sum of its child
budgets plus the existing orchestration reserve. Its vocabulary ceiling follows
that derived envelope; native job limits and incumbent child budgets do not
increase. This accounting is not a claim that a single GitHub job may run beyond
the provider limit or that the parallel native workflow consumes that sum.

## 10. Acceptance Boundary

Acceptance requires the paired plan, independent review of an immutable
candidate, native GitHub evidence and current generated projections. No local
behavioral suite substitutes for GitHub execution.

The user authorizes merge only if the CodeQL error disappears without
suppression/dismissal and all required native gates pass for the current merge
subject. CodeQL analysis must have completed for that subject; absent results
cannot establish disappearance. A previous green head or successful
volatile-storage probe cannot
substitute for that conjunction. Keep the PR unmerged while either condition is
false or unknown. This authority does not permit deployment; CODEOWNERS remains
deferred. Independent review and existing provider admission still apply.

Corporate enrollment still requires a separately admitted policy authority,
appropriate required review/merge-subject settings and explicit forbidden
security-finding admission for all supported events. Source presence does not
apply those provider settings. GitHub CodeQL and secret-scanning merge rules
have event and availability limits; they must be qualified at enrollment.

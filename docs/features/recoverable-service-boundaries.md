# Recoverable Service Boundaries

Status: implementation design for the operational portion of batch B2.

Owners: runtime composition, observability and the local developer environment.
Execution order belongs to the [implementation plan](recoverable-service-boundaries-implementation-plan.md).
The [operational closure plan](evidence-led-operational-closure-implementation-plan.md)
retains webhook, revocation and deployment work not closed by this change.

## 1. Intended Delta

Separate a restart decision from traffic eligibility; preserve unexpected
failure classes at the existing private diagnostic boundary; detect missing
telemetry; stop putting local database role passwords in process arguments.
No planning, signature, lease, permission or initial-reconciliation predicate
changes. No new service, storage table, public API or dependency is introduced.

This document refines only the Docker probe and private failure propagation
described below. The runtime and observability module specifications continue
to own all other behavior. Previously published designs remain unchanged.

## 2. Liveness Is Not Readiness

The selected topology has one admitted Uvicorn application per container. Its
listener opens only after successful ASGI startup. Uvicorn's concurrency gate
can return HTTP 503 before application dispatch, including for `/healthz`.
That response demonstrates local protocol progress, not application readiness.
The current probe mistakes it for a restart-recoverable failure.

For this exact listener and probe invocation:

```text
ProbePass := InterpreterAdmitted and ListenerAdmitted
  and ResponseBeforeProbeDeadline and status in {200, 503}

ProbePass !=> Ready
ProbePass !=> MayIssuePlan
ProbePass !=> DependencyHealthy

InitialReconciliationRejected => NoServingListener => not ProbePass
NoProtocolProgressWithinBudget => not ProbePass
ConcurrencyRejected and Timely503 => ProbePass
```

The last implication is an intentional operator-visible change: sustained
overload no longer causes restarts merely because the HTTP admission gate is
working. Readiness and availability alerts remain independent. HTTP 503 alone
does not identify its cause; the probe does not report an overload diagnosis.
Other status codes, invalid settings, transport errors and timeouts still fail.
The probe does not read a response body, follow redirects or use a proxy.

Keep the existing three-second socket timeout and five-second Docker process
deadline. The latter also bounds interpreter import and DNS work. A direct CLI
invocation does not independently impose Docker's total process deadline.
DNS/proxy ownership and the admitted listener identity remain deployment
premises; an unrelated process answering on that listener invalidates them.

Remove the eager application exports from `runtime/__init__.py`; the production
entrypoint already imports their actual owner, `runtime.application`.
Update the three internal test consumers to the same explicit import. Retain
the existing interpreter and listener validators without copying them, adding
a second admission schema or introducing blanket lazy imports.

The cold probe must not import FastAPI, SQLAlchemy, Uvicorn or HTTPX. Remaining
pure settings/kernel imports are not declared free: the exact image still has
to satisfy its healthcheck deadline. Reopen this choice if measured startup
cost exceeds that budget after removal of application construction imports.
The lock admits h11, not httptools. Exercise that actual production parser;
adding an unused parser solely to enlarge this test matrix is not justified.
Admitting another parser reopens the native admission witness.

| Alternative                                                    | Disposition                                                                                      |
|----------------------------------------------------------------|--------------------------------------------------------------------------------------------------|
| Remove the Uvicorn concurrency limit                           | Rejected: loses a finite workload bound.                                                         |
| Increase limits until probes usually pass                      | Rejected: does not eliminate the counterexample.                                                 |
| Count every HTTP status as healthy                             | Rejected: needlessly hides other protocol/application failures.                                  |
| Dedicated health server or heartbeat file                      | Deferred: adds a lifecycle, identity and cleanup contract not required by this observed failure. |
| Recognize bounded protocol progress plus remove unused exports | Selected for the current single-listener topology and independent readiness contract.            |

Existing Uvicorn graceful shutdown and the partitioned termination budget remain
unchanged. Swarm routing withdrawal and signal-to-drain behavior need an exact
deployment witness; accepting 503 does not establish either property.

## 3. Preserve Failures At Their Observation Owner

`DynamicPlanService` already catches registrar exceptions, emits a redacted
`reconciliation_registration` diagnostic and withholds selected execution.
The registrar's inner catch converts the same exception to `False` too early.
Remove that catch; retain `False` for a domain conflict or absent provider
signals. Cancellation continues to propagate.

```text
PersistenceException
  -> existing application diagnostic
  -> registration unavailable
  -> no selected-plan authority
```

This changes the internal registrar's exception surface, not the HTTP result or
planning policy. Its known production caller owns that translation. Reopen the
decision if another production caller is introduced without an equivalent
failure boundary. Adding another observer port to the registrar would duplicate
the caller's existing responsibility.

The runtime maintenance wrapper is the existing outer boundary of scheduled
work. Give it the existing `RuntimeDiagnosticObserver`: report an unexpected
primary failure once and re-raise it; report a contained maintenance failure
under a finite operation-specific stage. Preserve failed/timed-out counters,
startup gating and cancellation. Do not add logging dependencies to the
reconciliation scheduler or convert an instrumentation problem into authority.

An operation may itself raise `TimeoutError` before its wrapper deadline.
Retain the native `asyncio.Timeout` context and classify that exception as
`timed_out` only when `deadline.expired()` is true; otherwise it is a failed
operation with the same private diagnostic as another unexpected exception.
Cancellation continues to propagate without a failure or timeout observation.
The direct falsifier raises `TimeoutError` immediately under an unexpired
deadline; a separate native case waits for actual deadline cancellation.
An exception-name-only classifier is cheaper syntactically but merges these
owner-distinguishable states. No new timeout wrapper or error hierarchy is
justified. Reconsider this projection if operations gain typed outcomes or the
runtime replaces its native deadline mechanism.

Diagnostic operands are stage and exception class only. Messages, arguments,
tracebacks, tokens, request bodies and database strings remain excluded.
The existing bounded structured logger owns serialization and sink failure.

## 4. Observe Absence Explicitly

An empty error series is not a successful observation. The deployment contract
names the Prometheus scrape job `ci-coordinator`; it must enumerate every
expected serving replica with stable `job` and `instance` labels.

Add three distinct warning conditions:

- No target exists: `absent(up{job="ci-coordinator"})`.
- A known target cannot be scraped: `up{job="ci-coordinator"} == 0`.
- Scrape succeeds but readiness telemetry is absent: an `up == 1` series
  without a matching `ci_coordinator_ready` series by `job, instance`.

Each condition persists for five minutes before alerting. Existing readiness,
safety and burn-rate rules retain their meanings and volume guards. A fully
removed replica is invisible without an external expected-target inventory;
Prometheus itself being down is also outside these rules. The deployment owner
must monitor the monitoring system separately. A syntax gate is not alert
delivery evidence; promtool fixtures must falsify healthy, missing, failed and
partially missing target states.

Native promtool tests own disposable TSDB scratch storage. Keep the container
root and input mount read-only; grant only `/tmp` as a 64 MiB tmpfs with
`nosuid,nodev,noexec`, removed with the container. This is preferable to a
writable root or host volume because the fixture requires neither executable
files nor persistent state. Fixture growth that exceeds this bound requires
explicit review, not an unbounded fallback. Deployment inventory projections
must include the new fixture and the exact rules/Compose hashes without
changing their existing `non_runtime` classification.

## 5. Keep Development Credentials Out Of Argv

The provisioner already owns mounted files for the two role passwords. Read
those files through psql's native backquoted meta-command input instead of
passing their contents with `--set`. Run psql with `--no-psqlrc` so startup files
cannot enable query echo or replace the intended script. Fail before role DDL
if either read fails or either password is empty. Preserve SQL literal quoting with `:'variable'`
and `format('%L', ...)`, role privileges, database ownership and existing state.

```text
RolePassword -> mounted file -> psql variable -> quoted SQL
RolePassword not in psql argv
InvalidCredentialInput => no role DDL
```

This is not a claim that secrets disappear from process memory, database admin
visibility or server-side audit logs. Existing superuser connection custody is
unchanged. Native file input avoids another wrapper or secret store for this
local-only provisioning step. File custody and database logging remain explicit
environment responsibilities.

## 6. Proof And Reopening Boundaries

Use direct counterexamples, not a full new application model: a saturated real
Uvicorn listener, a cold import in a fresh interpreter, the real registrar
through its caller, a failing scheduled operation, Prometheus input series and
the unchanged connected-development provisioning workflow.

Protected observations include startup rejection, readiness, valid probe host
mapping, all rejected status classes, resource closure, registration conflict,
cancellation, private redaction, role privileges and volume preservation.
The implementation plan binds each to a witness before mutation.

This slice does not close B2's failed-webhook redelivery, authenticated logout
capacity, distributed revocation cleanup, edge headers or live drain proof.
GitHub does not automatically retry failed deliveries; those paths must retain
their explicit recovery work rather than inherit a readiness claim from this
change. The administrator-deferred webhook remains untouched.

Relevant source-profile constraints are FastAPI D05, D07-D10, D14-D17 and
K06-K10. Kubernetes-only K03/K11/K14 and Gunicorn K04 do not apply to this Swarm
topology. Runtime-load and production topology evidence remain unproven here;
this is not complete FastAPI-profile conformance.

## References

- [Runtime composition](../architecture/modules/runtime-composition.md)
- [Observability](../architecture/modules/observability.md)
- [Uvicorn concurrency settings](https://www.uvicorn.org/settings/)
- [Docker healthcheck contract](https://docs.docker.com/reference/dockerfile/#healthcheck)
- [psql command and variable semantics](https://www.postgresql.org/docs/18/app-psql.html)
- [GitHub failed webhook deliveries](https://docs.github.com/en/webhooks/using-webhooks/handling-failed-webhook-deliveries)

The installed pinned Uvicorn h11/httptools sources were inspected for the
pre-ASGI concurrency gate and startup ordering. Its documentation endpoint was
unavailable during this design session; the link is navigation, not a claim of
successful live documentation retrieval.
